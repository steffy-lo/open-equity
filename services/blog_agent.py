"""
services/blog_agent.py
=======================
Blog agent — stateful data layer only. No AI calls made here.

The flow is:
  1. OpenClaw calls GET /blog/context
     → gets a pre-packaged bundle: week's trades, portfolio P&L,
       benchmark comparison, all signals (acted on and not), research brief
  2. OpenClaw writes the weekly review markdown
  3. OpenClaw calls POST /blog with { "content": "...", "summary": "..." }
  4. This module stores the post and writes it to disk

The scheduler fires Sunday evening to log that context is ready,
but the writing step belongs entirely to OpenClaw.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import select, col

from config import BLOG_OUTPUT_DIR
from database import (
    BlogPost, BenchmarkSnapshot, Trade, Signal, Position,
    session_scope,
)
from services.portfolio_engine import get_portfolio_state
from services.research_agent import get_latest_brief

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Context builder  (GET /blog/context)
# ─────────────────────────────────────────────────────────────

def build_blog_context() -> dict:
    """
    Package up everything OpenClaw needs to write the weekly review:
      - Week's trades (all buys and sells)
      - Current portfolio state (positions, cash, total value)
      - Portfolio vs SPY performance for the week
      - All signals generated this week (acted on and not — for signal quality analysis)
      - The research brief from Monday (strategy, themes, risk posture)

    Returns a dict serialised directly into GET /blog/context.
    """
    now        = datetime.now(timezone.utc)
    week_start = _last_monday(now)

    with session_scope() as session:

        # ── Week's trades ──────────────────────────────────────
        trades = session.exec(
            select(Trade)
            .where(Trade.timestamp >= week_start)
            .order_by(col(Trade.timestamp).asc())
        ).all()

        trades_data = [
            {
                "order_id":   t.order_id,
                "ticker":     t.ticker,
                "side":       t.side,
                "qty":        t.qty,
                "fill_price": t.fill_price,
                "fee":        t.fee,
                "note":       t.note,
                "skill_used": t.skill_used,
                "timestamp":  t.timestamp.isoformat() + "Z",
            }
            for t in trades
        ]

        # ── Portfolio snapshot ─────────────────────────────────
        portfolio_data = {}
        try:
            portfolio_data = get_portfolio_state(session)
        except Exception as exc:
            logger.warning(f"[blog_agent] Portfolio fetch failed: {exc}")

        # ── Benchmark (portfolio vs SPY for the week) ──────────
        week_start_str = week_start.strftime("%Y-%m-%d")
        snapshots      = session.exec(
            select(BenchmarkSnapshot)
            .where(col(BenchmarkSnapshot.date) >= week_start_str)
            .order_by(col(BenchmarkSnapshot.date).asc())
        ).all()

        benchmark_data = _compute_benchmark(snapshots)

        # ── Signals this week ──────────────────────────────────
        signals = session.exec(
            select(Signal)
            .where(Signal.timestamp >= week_start)
            .order_by(col(Signal.confidence).desc())
            .limit(60)
        ).all()

        signals_data = [
            {
                "ticker":     s.ticker,
                "signal":     s.signal,
                "confidence": s.confidence,
                "reason":     s.reason,
                "skill_used": s.skill_used,
                "acted_on":   s.acted_on,
                "timestamp":  s.timestamp.isoformat() + "Z",
            }
            for s in signals
        ]

    # ── Research brief ─────────────────────────────────────────
    research_brief = get_latest_brief() or {}

    week_label = week_start.strftime("Week of %B %d, %Y")

    return {
        "generated_at":   now.isoformat() + "Z",
        "week_of":        week_start.strftime("%Y-%m-%d"),
        "week_label":     week_label,
        "trades":         trades_data,
        "portfolio":      portfolio_data,
        "benchmark":      benchmark_data,
        "signals":        signals_data,
        "research_brief": research_brief,
        "next_step": (
            "Write a weekly trading review in markdown, then POST it to POST /blog "
            "with { 'content': '<markdown>', 'summary': '<executive summary>' }. "
            "See /docs for the full schema."
        ),
    }


# ─────────────────────────────────────────────────────────────
# Blog post ingestion  (POST /blog)
# ─────────────────────────────────────────────────────────────

def ingest_blog_post(payload: dict) -> dict:
    """
    Store a blog post submitted by OpenClaw.

    Expected payload keys:
      content   (required) — full markdown string
      summary   (optional) — executive summary paragraph
      week_pnl  (optional) — float, net realized P&L for the week
    """
    now        = datetime.now(timezone.utc)
    week_start = _last_monday(now)
    week_of    = week_start.strftime("%Y-%m-%d")
    iso_week   = week_start.strftime("%Y-W%W")
    week_label = week_start.strftime("Week of %B %d, %Y")

    content  = payload.get("content", "")
    summary  = payload.get("summary", "")
    week_pnl = float(payload.get("week_pnl", 0.0))

    if not content:
        raise ValueError("'content' is required and must be a non-empty markdown string")

    # ── Gather supplementary metadata ─────────────────────────
    with session_scope() as session:
        trades_count = len(
            session.exec(
                select(Trade).where(Trade.timestamp >= week_start)
            ).all()
        )

        snapshots = session.exec(
            select(BenchmarkSnapshot)
            .where(col(BenchmarkSnapshot.date) >= week_of)
            .order_by(col(BenchmarkSnapshot.date).asc())
        ).all()
        benchmark = _compute_benchmark(snapshots)
        spy_return_pct = benchmark.get("spy_week_return_pct")

        brief    = get_latest_brief() or {}
        strategy = brief.get("strategy", "mixed")

        post = BlogPost(
            week_of        = week_of,
            iso_week       = iso_week,
            title          = f"Weekly Trading Review — {week_label}",
            summary        = summary,
            content        = content,
            week_pnl       = week_pnl,
            spy_return_pct = spy_return_pct,
            trades_count   = trades_count,
            strategy       = strategy,
        )
        session.add(post)
        session.commit()
        post_id = post.id

    # ── Write to disk ──────────────────────────────────────────
    _write_to_disk(iso_week, content)

    logger.info(f"[blog_agent] ✅ Blog post stored id={post_id}: {week_label}")

    return {
        "id":           post_id,
        "week_of":      week_of,
        "iso_week":     iso_week,
        "title":        f"Weekly Trading Review — {week_label}",
        "trades_count": trades_count,
        "file":         f"{BLOG_OUTPUT_DIR}/{iso_week}.md",
        "stored_at":    now.isoformat() + "Z",
    }


def list_blog_posts(limit: int = 20) -> list:
    with session_scope() as session:
        posts = session.exec(
            select(BlogPost)
            .order_by(col(BlogPost.created_at).desc())
            .limit(limit)
        ).all()
        return [
            {
                "id":            p.id,
                "week_of":       p.week_of,
                "iso_week":      p.iso_week,
                "title":         p.title,
                "summary":       p.summary,
                "week_pnl":      p.week_pnl,
                "spy_return_pct": p.spy_return_pct,
                "trades_count":  p.trades_count,
                "strategy":      p.strategy,
                "created_at":    p.created_at.isoformat() + "Z",
            }
            for p in posts
        ]


def get_blog_post(post_id: int) -> dict | None:
    with session_scope() as session:
        post = session.get(BlogPost, post_id)
        if not post:
            return None
        return {
            "id":            post.id,
            "week_of":       post.week_of,
            "iso_week":      post.iso_week,
            "title":         post.title,
            "summary":       post.summary,
            "content":       post.content,
            "week_pnl":      post.week_pnl,
            "spy_return_pct": post.spy_return_pct,
            "trades_count":  post.trades_count,
            "strategy":      post.strategy,
            "created_at":    post.created_at.isoformat() + "Z",
        }


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _last_monday(now: datetime) -> datetime:
    day   = now.date()
    monday = day - timedelta(days=day.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)


def _compute_benchmark(snapshots: list) -> dict:
    if not snapshots:
        return {"note": "No benchmark snapshots available for this week"}

    first, last = snapshots[0], snapshots[-1]

    pf_return = (
        (last.portfolio_value - first.portfolio_value) / first.portfolio_value * 100
        if first.portfolio_value > 0 else None
    )
    spy_return = (
        (last.spy_price - first.spy_price) / first.spy_price * 100
        if first.spy_price > 0 else None
    )
    alpha = (
        pf_return - spy_return
        if pf_return is not None and spy_return is not None else None
    )

    return {
        "week_start":                 first.date,
        "week_end":                   last.date,
        "portfolio_start":            first.portfolio_value,
        "portfolio_end":              last.portfolio_value,
        "portfolio_week_return_pct":  round(pf_return, 2)  if pf_return  is not None else None,
        "spy_week_return_pct":        round(spy_return, 2) if spy_return is not None else None,
        "alpha_pct":                  round(alpha, 2)      if alpha      is not None else None,
        "snapshots_count":            len(snapshots),
    }


def _write_to_disk(iso_week: str, content: str):
    output_dir = Path(BLOG_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{iso_week}.md"
    path.write_text(content, encoding="utf-8")
    logger.info(f"[blog_agent] Written to {path}")
