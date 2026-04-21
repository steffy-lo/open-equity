"""
services/research_agent.py
===========================
Research agent, stateful data layer only. No AI calls made here.

The flow is:
  1. OpenClaw calls GET /research/context?market=US|HK
     → gets a pre-packaged bundle: watchlist technicals, recent signals,
       benchmark trend, prior brief, everything needed to reason about strategy
  2. OpenClaw reasons about strategy, themes, picks, watchlist changes
  3. OpenClaw calls POST /research with a structured brief JSON
  4. This module stores the brief and applies watchlist mutations

The scheduler only logs that context is ready. The AI reasoning step belongs
entirely to OpenClaw.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlmodel import col, select

from database import BenchmarkSnapshot, ResearchBrief, Signal, session_scope
from services.market_data import get_price_data, get_technicals
from services.screener import add_to_watchlist, load_watchlist, remove_from_watchlist

logger = logging.getLogger(__name__)

Market = Literal["US", "HK"]


def infer_market(ticker: str) -> Market:
    t = (ticker or "").upper().strip()
    return "HK" if t.endswith(".HK") else "US"


# ─────────────────────────────────────────────────────────────
# Context builder  (GET /research/context)
# ─────────────────────────────────────────────────────────────

def build_research_context(market: Market = "US") -> dict:
    """
    Package up everything OpenClaw needs to write a strategy brief for a market:
      - Market-filtered watchlist with live technicals for each ticker
      - Recent market-filtered buy/flag signals (last 5 days)
      - 7-day portfolio vs SPY benchmark trend
      - Prior brief for the same market
    """
    now = datetime.now(timezone.utc)
    cutoff_signals = now - timedelta(days=5)
    cutoff_bench = now - timedelta(days=7)

    with session_scope() as session:
        watchlist = [ticker for ticker in load_watchlist() if infer_market(ticker) == market]
        watchlist_snapshot = []
        for ticker in watchlist:
            try:
                price = get_price_data(ticker)
                tech = get_technicals(ticker)
                watchlist_snapshot.append(
                    {
                        "ticker": ticker,
                        "price": price.get("price"),
                        "change_pct": price.get("change_pct"),
                        "rsi_14": tech.get("rsi_14"),
                        "above_sma200": tech.get("above_sma200"),
                        "macd_bullish": tech.get("macd_bullish"),
                        "volume_ratio": tech.get("volume_ratio"),
                        "pct_from_52w_high": tech.get("pct_from_52w_high"),
                        "sector": price.get("sector"),
                        "pe": price.get("pe"),
                        "exchange": price.get("exchange"),
                        "currency": price.get("currency"),
                    }
                )
            except Exception as exc:
                logger.warning(f"[research_agent] Could not snapshot {ticker}: {exc}")
                watchlist_snapshot.append({"ticker": ticker, "error": str(exc)})

        recent_signal_rows = session.exec(
            select(Signal)
            .where(Signal.timestamp >= cutoff_signals)
            .order_by(col(Signal.confidence).desc(), col(Signal.timestamp).desc())
            .limit(60)
        ).all()

        signals_summary = [
            {
                "ticker": s.ticker,
                "signal": s.signal,
                "confidence": s.confidence,
                "reason": s.reason,
                "acted_on": s.acted_on,
                "timestamp": s.timestamp.isoformat() + "Z",
            }
            for s in recent_signal_rows
            if infer_market(s.ticker) == market
        ][:30]

        snapshots = session.exec(
            select(BenchmarkSnapshot)
            .where(col(BenchmarkSnapshot.date) >= cutoff_bench.strftime("%Y-%m-%d"))
            .order_by(col(BenchmarkSnapshot.date).asc())
        ).all()

        benchmark_trend = [
            {
                "date": s.date,
                "portfolio_value": s.portfolio_value,
                "spy_price": s.spy_price,
            }
            for s in snapshots
        ]

        last_brief_row = session.exec(
            select(ResearchBrief)
            .where(ResearchBrief.market == market)
            .order_by(col(ResearchBrief.created_at).desc())
        ).first()

        prior_brief = None
        if last_brief_row:
            prior_brief = {
                "market": last_brief_row.market,
                "week_of": last_brief_row.week_of,
                "strategy": last_brief_row.strategy,
                "time_horizon": last_brief_row.time_horizon,
                "risk_posture": last_brief_row.risk_posture,
                "themes": json.loads(last_brief_row.themes or "[]"),
                "rationale": last_brief_row.rationale,
            }

    return {
        "generated_at": now.isoformat() + "Z",
        "market": market,
        "week_of": _this_monday(),
        "watchlist": watchlist_snapshot,
        "recent_signals": signals_summary,
        "benchmark_trend": benchmark_trend,
        "prior_brief": prior_brief,
        "next_step": (
            f"Analyse this {market} market data, then POST your strategy brief to POST /research. "
            "See /docs for the expected JSON schema."
        ),
    }


# ─────────────────────────────────────────────────────────────
# Brief ingestion  (POST /research)
# ─────────────────────────────────────────────────────────────

def ingest_brief(brief: dict) -> dict:
    """
    Store a market-specific research brief submitted by OpenClaw and apply
    watchlist mutations.
    """
    today = _this_monday()
    market: Market = brief.get("market", "US")
    added = [t.upper() for t in (brief.get("watchlist_add") or [])]
    removed = [t.upper() for t in (brief.get("watchlist_remove") or [])]

    if added:
        new_list = add_to_watchlist(added)
        logger.info(f"[research_agent] {market} watchlist +{added} total={len(new_list)}")

    if removed:
        new_list = remove_from_watchlist(removed)
        logger.info(f"[research_agent] {market} watchlist -{removed} total={len(new_list)}")

    with session_scope() as session:
        record = ResearchBrief(
            market=market,
            week_of=brief.get("week_of", today),
            strategy=brief.get("strategy", "mixed"),
            time_horizon=brief.get("time_horizon", ""),
            risk_posture=brief.get("risk_posture", "moderate"),
            themes=json.dumps(brief.get("themes", [])),
            focus_sectors=json.dumps(brief.get("focus_sectors", [])),
            avoid_sectors=json.dumps(brief.get("avoid_sectors", [])),
            watchlist_add=json.dumps(added),
            watchlist_remove=json.dumps(removed),
            earnings_watch=json.dumps(brief.get("earnings_watch", [])),
            macro_summary=brief.get("macro_summary", ""),
            key_risks=brief.get("key_risks", ""),
            rationale=brief.get("rationale", ""),
            raw_json=json.dumps(brief),
        )
        session.add(record)
        session.commit()
        record_id = record.id

    logger.info(
        f"[research_agent] ✅ {market} brief stored id={record_id} "
        f"strategy={brief.get('strategy')} posture={brief.get('risk_posture')}"
    )
    return {
        "id": record_id,
        "market": market,
        "week_of": brief.get("week_of", today),
        "watchlist_added": added,
        "watchlist_removed": removed,
        "stored_at": datetime.now(timezone.utc).isoformat() + "Z",
    }



def get_latest_brief(market: Market = "US") -> dict | None:
    with session_scope() as session:
        row = session.exec(
            select(ResearchBrief)
            .where(ResearchBrief.market == market)
            .order_by(col(ResearchBrief.created_at).desc())
        ).first()
        if not row:
            return None
        return {
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
            "created_at": row.created_at.isoformat() + "Z",
        }


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _this_monday() -> str:
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()
