"""
services/research_agent.py
===========================
Research agent, stateful data layer only. No AI calls made here.

The flow is:
  1. OpenClaw calls GET /research/context?market=US|HK
  2. OpenClaw reasons about strategy, themes, picks, watchlist changes
  3. OpenClaw calls POST /research with a structured brief JSON
  4. This module stores the brief and applies watchlist mutations

The scheduler only logs that context is ready. The AI reasoning step belongs
entirely to OpenClaw.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import col, select

from database import BenchmarkSnapshot, ResearchBrief, Signal, session_scope
from services.market_data import get_price_data, get_technicals
from services.markets import (
    Market,
    filter_market_tickers,
    infer_market,
    normalize_market_tickers,
    normalize_ticker,
)
from services.screener import add_to_watchlist, load_watchlist, remove_from_watchlist

logger = logging.getLogger(__name__)


def build_research_context(market: Market = "US") -> dict:
    """Build market-filtered research context for OpenClaw reasoning."""
    now = datetime.now(timezone.utc)
    cutoff_signals = now - timedelta(days=5)
    cutoff_benchmark = now - timedelta(days=7)

    with session_scope() as session:
        watchlist = filter_market_tickers(load_watchlist(), market)
        recent_signals = _load_recent_signals(session, market, cutoff_signals)
        benchmark_trend = _load_benchmark_trend(session, cutoff_benchmark)
        prior_brief = _load_latest_brief_row(session, market)

    return {
        "generated_at": now.isoformat() + "Z",
        "market": market,
        "week_of": _this_monday(),
        "watchlist": _build_watchlist_snapshot(watchlist),
        "recent_signals": recent_signals,
        "benchmark_trend": benchmark_trend,
        "prior_brief": _serialize_brief(prior_brief, include_created_at=False),
        "output_contract": _research_output_contract(market),
        "next_step": (
            f"Analyse this {market} market data, produce a JSON brief that matches output_contract exactly, "
            "then POST that JSON to POST /research."
        ),
    }



def ingest_brief(brief: dict) -> dict:
    """Store a market-specific research brief and apply watchlist changes."""
    normalized = _normalize_brief(brief)
    market: Market = normalized["market"]
    today = _this_monday()
    added = normalized["watchlist_add"]
    removed = normalized["watchlist_remove"]

    if added:
        new_watchlist = add_to_watchlist(added)
        logger.info(f"[research_agent] {market} watchlist +{added} total={len(new_watchlist)}")

    if removed:
        new_watchlist = remove_from_watchlist(removed)
        logger.info(f"[research_agent] {market} watchlist -{removed} total={len(new_watchlist)}")

    with session_scope() as session:
        record = ResearchBrief(
            market=market,
            week_of=normalized["week_of"],
            strategy=normalized["strategy"],
            time_horizon=normalized["time_horizon"],
            risk_posture=normalized["risk_posture"],
            themes=json.dumps(normalized["themes"]),
            focus_sectors=json.dumps(normalized["focus_sectors"]),
            avoid_sectors=json.dumps(normalized["avoid_sectors"]),
            watchlist_add=json.dumps(added),
            watchlist_remove=json.dumps(removed),
            earnings_watch=json.dumps(normalized["earnings_watch"]),
            macro_summary=normalized["macro_summary"],
            key_risks=normalized["key_risks"],
            rationale=normalized["rationale"],
            raw_json=json.dumps(normalized),
        )
        session.add(record)
        session.commit()
        record_id = record.id

    logger.info(
        f"[research_agent] ✅ {market} brief stored id={record_id} "
        f"strategy={normalized['strategy']} posture={normalized['risk_posture']}"
    )
    return {
        "id": record_id,
        "market": market,
        "week_of": normalized["week_of"],
        "watchlist_added": added,
        "watchlist_removed": removed,
        "stored_at": datetime.now(timezone.utc).isoformat() + "Z",
    }



def get_latest_brief(market: Market = "US") -> dict | None:
    with session_scope() as session:
        row = _load_latest_brief_row(session, market)
    return _serialize_brief(row)



def _build_watchlist_snapshot(watchlist: list[str]) -> list[dict]:
    snapshot: list[dict] = []
    for ticker in watchlist:
        try:
            price = get_price_data(ticker)
            technicals = get_technicals(ticker)
            snapshot.append(
                {
                    "ticker": ticker,
                    "price": price.get("price"),
                    "change_pct": price.get("change_pct"),
                    "rsi_14": technicals.get("rsi_14"),
                    "above_sma200": technicals.get("above_sma200"),
                    "macd_bullish": technicals.get("macd_bullish"),
                    "volume_ratio": technicals.get("volume_ratio"),
                    "pct_from_52w_high": technicals.get("pct_from_52w_high"),
                    "sector": price.get("sector"),
                    "pe": price.get("pe"),
                    "exchange": price.get("exchange"),
                    "currency": price.get("currency"),
                }
            )
        except Exception as exc:
            logger.warning(f"[research_agent] Could not snapshot {ticker}: {exc}")
            snapshot.append({"ticker": ticker, "error": str(exc)})
    return snapshot



def _load_recent_signals(session, market: Market, cutoff: datetime) -> list[dict]:
    rows = session.exec(
        select(Signal)
        .where(Signal.timestamp >= cutoff)
        .order_by(col(Signal.confidence).desc(), col(Signal.timestamp).desc())
        .limit(60)
    ).all()
    return [
        {
            "ticker": row.ticker,
            "signal": row.signal,
            "confidence": row.confidence,
            "reason": row.reason,
            "acted_on": row.acted_on,
            "timestamp": row.timestamp.isoformat() + "Z",
        }
        for row in rows
        if infer_market(row.ticker) == market
    ][:30]



def _load_benchmark_trend(session, cutoff: datetime) -> list[dict]:
    rows = session.exec(
        select(BenchmarkSnapshot)
        .where(col(BenchmarkSnapshot.date) >= cutoff.strftime("%Y-%m-%d"))
        .order_by(col(BenchmarkSnapshot.date).asc())
    ).all()
    return [
        {
            "date": row.date,
            "portfolio_value": row.portfolio_value,
            "spy_price": row.spy_price,
        }
        for row in rows
    ]



def _load_latest_brief_row(session, market: Market) -> ResearchBrief | None:
    return session.exec(
        select(ResearchBrief)
        .where(ResearchBrief.market == market)
        .order_by(col(ResearchBrief.created_at).desc())
    ).first()



def _serialize_brief(row: ResearchBrief | None, *, include_created_at: bool = True) -> dict | None:
    if not row:
        return None
    payload = {
        "id": row.id,
        "market": row.market,
        "week_of": row.week_of,
        "strategy": row.strategy,
        "time_horizon": row.time_horizon,
        "risk_posture": row.risk_posture,
        "themes": json.loads(row.themes or "[]"),
        "focus_sectors": json.loads(row.focus_sectors or "[]"),
        "avoid_sectors": json.loads(row.avoid_sectors or "[]"),
        "watchlist_add": json.loads(row.watchlist_add or "[]"),
        "watchlist_remove": json.loads(row.watchlist_remove or "[]"),
        "earnings_watch": json.loads(row.earnings_watch or "[]"),
        "macro_summary": row.macro_summary,
        "key_risks": row.key_risks,
        "rationale": row.rationale,
    }
    if include_created_at:
        payload["created_at"] = row.created_at.isoformat() + "Z"
    return payload



def _normalize_brief(brief: dict) -> dict:
    market: Market = brief.get("market", "US")
    return {
        "market": market,
        "week_of": brief.get("week_of") or _this_monday(),
        "macro_summary": _clean_text(brief.get("macro_summary")),
        "strategy": brief.get("strategy") or "mixed",
        "time_horizon": _clean_text(brief.get("time_horizon")),
        "risk_posture": brief.get("risk_posture") or "moderate",
        "themes": _clean_string_list(brief.get("themes")),
        "focus_sectors": _clean_string_list(brief.get("focus_sectors")),
        "avoid_sectors": _clean_string_list(brief.get("avoid_sectors")),
        "watchlist_add": normalize_market_tickers(_coerce_ticker_list(brief.get("watchlist_add")), market),
        "watchlist_remove": normalize_market_tickers(_coerce_ticker_list(brief.get("watchlist_remove")), market),
        "earnings_watch": _normalize_optional_tickers(brief.get("earnings_watch"), market),
        "key_risks": _clean_text(brief.get("key_risks")),
        "rationale": _clean_text(brief.get("rationale")),
    }



def _research_output_contract(market: Market) -> dict:
    example_ticker = "0700.HK" if market == "HK" else "NVDA"
    return {
        "required_post_target": "/research",
        "format": "json-object",
        "rules": [
            f"Set market to {market}.",
            "Return arrays for list fields, not comma-separated strings.",
            "Only include tickers that belong to the current market in watchlist_add, watchlist_remove, and earnings_watch.",
            f"For {market} watchlist changes, normalize tickers like {example_ticker} before posting.",
            "Keep macro_summary, key_risks, and rationale concise but explicit.",
        ],
        "schema": {
            "market": market,
            "week_of": "YYYY-MM-DD",
            "macro_summary": "string",
            "strategy": "momentum|mean_reversion|sector_rotation|defensive|mixed",
            "time_horizon": "string",
            "risk_posture": "aggressive|moderate|conservative",
            "themes": ["string"],
            "focus_sectors": ["string"],
            "avoid_sectors": ["string"],
            "watchlist_add": [example_ticker],
            "watchlist_remove": [example_ticker],
            "earnings_watch": [example_ticker],
            "key_risks": "string",
            "rationale": "string",
        },
        "examples": {
            "watchlist_add": [example_ticker],
            "watchlist_remove": [],
        },
    }



def _coerce_ticker_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]



def _normalize_optional_tickers(value, market: Market) -> list[str]:
    tickers = _coerce_ticker_list(value)
    normalized = []
    seen: set[str] = set()
    for ticker in tickers:
        candidate = normalize_ticker(ticker, market=market)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized



def _clean_string_list(value) -> list[str]:
    if value is None:
        return []
    items = [value] if isinstance(value, str) else value
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _clean_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned



def _clean_text(value) -> str:
    return " ".join(str(value or "").strip().split())



def _this_monday() -> str:
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()
