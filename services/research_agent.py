"""
services/research_agent.py
===========================
Research agent — stateful data layer only. No AI calls made here.

The flow is:
  1. OpenClaw calls GET /research/context
     → gets a pre-packaged bundle: watchlist technicals, recent signals,
       benchmark trend, prior brief — everything needed to reason about strategy
  2. OpenClaw reasons about strategy, themes, picks, watchlist changes
  3. OpenClaw calls POST /research with a structured brief JSON
  4. This module stores the brief and applies watchlist mutations

The scheduler fires Monday morning to log that context is ready,
but the AI reasoning step belongs entirely to OpenClaw.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import select, col

from database import ResearchBrief, Signal, BenchmarkSnapshot, session_scope
from services.screener import load_watchlist, add_to_watchlist, remove_from_watchlist
from services.market_data import get_price_data, get_technicals

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Context builder  (GET /research/context)
# ─────────────────────────────────────────────────────────────

def build_research_context() -> dict:
    """
    Package up everything OpenClaw needs to write a strategy brief:
      - Watchlist with live technicals for each ticker
      - Recent buy/flag signals (last 5 days)
      - 7-day portfolio vs SPY benchmark trend
      - Prior week's brief (so OpenClaw can iterate on strategy)

    Returns a dict serialised directly into GET /research/context.
    """
    now           = datetime.now(timezone.utc)
    cutoff_signals = now - timedelta(days=5)
    cutoff_bench   = now - timedelta(days=7)

    with session_scope() as session:

        # ── Watchlist snapshot with live technicals ────────────
        watchlist = load_watchlist()
        watchlist_snapshot = []
        for ticker in watchlist:
            try:
                price = get_price_data(ticker)
                tech  = get_technicals(ticker)
                watchlist_snapshot.append({
                    "ticker":            ticker,
                    "price":             price.get("price"),
                    "change_pct":        price.get("change_pct"),
                    "rsi_14":            tech.get("rsi_14"),
                    "above_sma200":      tech.get("above_sma200"),
                    "macd_bullish":      tech.get("macd_bullish"),
                    "volume_ratio":      tech.get("volume_ratio"),
                    "pct_from_52w_high": tech.get("pct_from_52w_high"),
                    "sector":            price.get("sector"),
                    "pe":                price.get("pe"),
                })
            except Exception as exc:
                logger.warning(f"[research_agent] Could not snapshot {ticker}: {exc}")
                watchlist_snapshot.append({"ticker": ticker, "error": str(exc)})

        # ── Recent signals ─────────────────────────────────────
        recent_signals = session.exec(
            select(Signal)
            .where(Signal.timestamp >= cutoff_signals)
            .order_by(col(Signal.confidence).desc())
            .limit(30)
        ).all()

        signals_summary = [
            {
                "ticker":     s.ticker,
                "signal":     s.signal,
                "confidence": s.confidence,
                "reason":     s.reason,
                "acted_on":   s.acted_on,
                "timestamp":  s.timestamp.isoformat() + "Z",
            }
            for s in recent_signals
        ]

        # ── Benchmark trend ────────────────────────────────────
        snapshots = session.exec(
            select(BenchmarkSnapshot)
            .where(col(BenchmarkSnapshot.date) >= cutoff_bench.strftime("%Y-%m-%d"))
            .order_by(col(BenchmarkSnapshot.date).asc())
        ).all()

        benchmark_trend = [
            {
                "date":            s.date,
                "portfolio_value": s.portfolio_value,
                "spy_price":       s.spy_price,
            }
            for s in snapshots
        ]

        # ── Prior brief ────────────────────────────────────────
        last_brief_row = session.exec(
            select(ResearchBrief).order_by(col(ResearchBrief.created_at).desc())
        ).first()

        prior_brief = None
        if last_brief_row:
            prior_brief = {
                "week_of":      last_brief_row.week_of,
                "strategy":     last_brief_row.strategy,
                "time_horizon": last_brief_row.time_horizon,
                "risk_posture": last_brief_row.risk_posture,
                "themes":       json.loads(last_brief_row.themes or "[]"),
                "rationale":    last_brief_row.rationale,
            }

    return {
        "generated_at":   now.isoformat() + "Z",
        "week_of":        _this_monday(),
        "watchlist":      watchlist_snapshot,
        "recent_signals": signals_summary,
        "benchmark_trend": benchmark_trend,
        "prior_brief":    prior_brief,
        "next_step": (
            "Analyse this data, then POST your strategy brief to POST /research. "
            "See /docs for the expected JSON schema."
        ),
    }


# ─────────────────────────────────────────────────────────────
# Brief ingestion  (POST /research)
# ─────────────────────────────────────────────────────────────

def ingest_brief(brief: dict) -> dict:
    """
    Store a research brief submitted by OpenClaw and apply watchlist mutations.

    Required key: none (all fields have defaults), but OpenClaw should supply:
      week_of, strategy, time_horizon, risk_posture, themes, focus_sectors,
      avoid_sectors, watchlist_add, watchlist_remove, earnings_watch,
      macro_summary, key_risks, rationale
    """
    today   = _this_monday()
    added   = [t.upper() for t in (brief.get("watchlist_add")    or [])]
    removed = [t.upper() for t in (brief.get("watchlist_remove") or [])]

    if added:
        new_list = add_to_watchlist(added)
        logger.info(f"[research_agent] Watchlist +{added}  total={len(new_list)}")

    if removed:
        new_list = remove_from_watchlist(removed)
        logger.info(f"[research_agent] Watchlist -{removed}  total={len(new_list)}")

    with session_scope() as session:
        record = ResearchBrief(
            week_of          = brief.get("week_of", today),
            strategy         = brief.get("strategy", "mixed"),
            time_horizon     = brief.get("time_horizon", ""),
            risk_posture     = brief.get("risk_posture", "moderate"),
            themes           = json.dumps(brief.get("themes", [])),
            focus_sectors    = json.dumps(brief.get("focus_sectors", [])),
            avoid_sectors    = json.dumps(brief.get("avoid_sectors", [])),
            watchlist_add    = json.dumps(added),
            watchlist_remove = json.dumps(removed),
            earnings_watch   = json.dumps(brief.get("earnings_watch", [])),
            macro_summary    = brief.get("macro_summary", ""),
            key_risks        = brief.get("key_risks", ""),
            rationale        = brief.get("rationale", ""),
        )
        session.add(record)
        session.commit()
        record_id = record.id

    logger.info(
        f"[research_agent] ✅ Brief stored id={record_id} "
        f"strategy={brief.get('strategy')} posture={brief.get('risk_posture')}"
    )
    return {
        "id":                record_id,
        "week_of":           brief.get("week_of", today),
        "watchlist_added":   added,
        "watchlist_removed": removed,
        "stored_at":         datetime.now(timezone.utc).isoformat() + "Z",
    }


def get_latest_brief() -> dict | None:
    with session_scope() as session:
        row = session.exec(
            select(ResearchBrief).order_by(col(ResearchBrief.created_at).desc())
        ).first()
        if not row:
            return None
        return {
            "id":               row.id,
            "week_of":          row.week_of,
            "strategy":         row.strategy,
            "time_horizon":     row.time_horizon,
            "risk_posture":     row.risk_posture,
            "themes":           json.loads(row.themes or "[]"),
            "focus_sectors":    json.loads(row.focus_sectors or "[]"),
            "avoid_sectors":    json.loads(row.avoid_sectors or "[]"),
            "watchlist_add":    json.loads(row.watchlist_add or "[]"),
            "watchlist_remove": json.loads(row.watchlist_remove or "[]"),
            "earnings_watch":   json.loads(row.earnings_watch or "[]"),
            "macro_summary":    row.macro_summary,
            "key_risks":        row.key_risks,
            "rationale":        row.rationale,
            "created_at":       row.created_at.isoformat() + "Z",
        }


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _this_monday() -> str:
    today  = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()
