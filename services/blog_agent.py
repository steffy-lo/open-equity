"""
services/blog_agent.py
=======================
Blog agent — stateful data layer only. No AI calls made here.

The flow is:
  1. OpenClaw calls GET /blog/context
     → gets a pre-packaged bundle: week's trades, portfolio P&L,
       benchmark comparison, all signals (acted on and not), research briefs
  2. OpenClaw writes the weekly review markdown
  3. OpenClaw calls POST /blog with { "content": "...", "summary": "..." }
  4. This module stores the post and writes it to disk

The scheduler fires Sunday evening to log that context is ready,
but the writing step belongs entirely to OpenClaw.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import col, select

from config import (
    BLOG_FORWARD_SUMMARY_MAX_CHARS,
    BLOG_FORWARD_TOPIC,
    BLOG_OUTPUT_DIR,
)
from database import BenchmarkSnapshot, BlogPost, Position, Signal, Trade, session_scope
from services.markets import infer_market
from services.openclaw_notify import send_openclaw_message
from services.portfolio_engine import get_portfolio_state
from services.research_agent import get_latest_brief

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Context builder  (GET /blog/context)
# ─────────────────────────────────────────────────────────────

def build_blog_context() -> dict:
    """Assemble the weekly context consumed by the autonomous `/blog` writer."""
    now = datetime.now(timezone.utc)
    week_start = _last_monday(now)

    with session_scope() as session:
        trades = session.exec(
            select(Trade)
            .where(Trade.timestamp >= week_start)
            .order_by(col(Trade.timestamp).asc())
        ).all()
        signals = session.exec(
            select(Signal)
            .where(Signal.timestamp >= week_start)
            .order_by(col(Signal.confidence).desc())
            .limit(100)
        ).all()
        snapshots = session.exec(
            select(BenchmarkSnapshot)
            .where(col(BenchmarkSnapshot.date) >= week_start.strftime("%Y-%m-%d"))
            .order_by(col(BenchmarkSnapshot.date).asc())
        ).all()
        positions = session.exec(select(Position).order_by(col(Position.ticker).asc())).all()

        portfolio_data = {}
        try:
            portfolio_data = get_portfolio_state(session)
        except Exception as exc:
            logger.warning(f"[blog_agent] Portfolio fetch failed: {exc}")

    trades_data = [_serialize_trade(t) for t in trades]
    signals_data = [_serialize_signal(s) for s in signals]
    benchmark_data = _compute_benchmark(snapshots)
    week_label = week_start.strftime("Week of %B %d, %Y")

    # Top-level fields remain available for backwards compatibility, while
    # `market_breakdown` gives the blog writer an explicit US/HK split.
    research_briefs = {
        "US": get_latest_brief("US") or {},
        "HK": get_latest_brief("HK") or {},
    }
    serialized_positions = _serialize_positions(positions)
    market_breakdown = {
        market: _build_market_breakdown(
            market=market,
            trades=trades_data,
            signals=signals_data,
            positions=serialized_positions,
            research_brief=research_briefs[market],
        )
        for market in ("US", "HK")
    }

    return {
        "generated_at": now.isoformat() + "Z",
        "week_of": week_start.strftime("%Y-%m-%d"),
        "week_label": week_label,
        "trades": trades_data,
        "portfolio": portfolio_data,
        "benchmark": benchmark_data,
        "signals": signals_data,
        "research_brief": research_briefs.get("US", {}),
        "research_briefs": research_briefs,
        "market_breakdown": market_breakdown,
        "next_step": (
            "Write a weekly trading review in markdown with explicit US and HK sections, then POST it to POST /blog "
            "with { 'content': '<markdown>', 'summary': '<executive summary>' }."
        ),
    }


# ─────────────────────────────────────────────────────────────
# Blog post ingestion  (POST /blog)
# ─────────────────────────────────────────────────────────────

def ingest_blog_post(payload: dict) -> dict:
    """Store the generated markdown review and optionally forward a summary."""
    now = datetime.now(timezone.utc)
    week_start = _last_monday(now)
    week_of = week_start.strftime("%Y-%m-%d")
    iso_week = week_start.strftime("%Y-W%W")
    week_label = week_start.strftime("Week of %B %d, %Y")

    content = payload.get("content", "")
    summary = payload.get("summary", "")
    week_pnl = float(payload.get("week_pnl", 0.0))

    if not content:
        raise ValueError("'content' is required and must be a non-empty markdown string")

    with session_scope() as session:
        trades_count = len(session.exec(select(Trade).where(Trade.timestamp >= week_start)).all())
        snapshots = session.exec(
            select(BenchmarkSnapshot)
            .where(col(BenchmarkSnapshot.date) >= week_of)
            .order_by(col(BenchmarkSnapshot.date).asc())
        ).all()
        benchmark = _compute_benchmark(snapshots)
        spy_return_pct = benchmark.get("spy_week_return_pct")
        brief = get_latest_brief("US") or get_latest_brief("HK") or {}
        strategy = brief.get("strategy", "mixed")

        post = BlogPost(
            week_of=week_of,
            iso_week=iso_week,
            title=f"Weekly Trading Review — {week_label}",
            summary=summary,
            content=content,
            week_pnl=week_pnl,
            spy_return_pct=spy_return_pct,
            trades_count=trades_count,
            strategy=strategy,
        )
        session.add(post)
        session.commit()
        post_id = post.id

    _write_to_disk(iso_week, content)

    forward_result = _forward_blog_post(
        title=f"Weekly Trading Review — {week_label}",
        summary=summary,
        content=content,
        target=BLOG_FORWARD_TOPIC,
    )

    logger.info(f"[blog_agent] ✅ Blog post stored id={post_id}: {week_label}")

    return {
        "id": post_id,
        "week_of": week_of,
        "iso_week": iso_week,
        "title": f"Weekly Trading Review — {week_label}",
        "trades_count": trades_count,
        "file": f"{BLOG_OUTPUT_DIR}/{iso_week}.md",
        "stored_at": now.isoformat() + "Z",
        "forwarded": forward_result.get("ok", False),
        "forward_target": forward_result.get("target"),
        "forward_error": forward_result.get("error"),
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
                "id": p.id,
                "week_of": p.week_of,
                "iso_week": p.iso_week,
                "title": p.title,
                "summary": p.summary,
                "week_pnl": p.week_pnl,
                "spy_return_pct": p.spy_return_pct,
                "trades_count": p.trades_count,
                "strategy": p.strategy,
                "created_at": p.created_at.isoformat() + "Z",
            }
            for p in posts
        ]


def get_blog_post(post_id: int) -> dict | None:
    with session_scope() as session:
        post = session.get(BlogPost, post_id)
        if not post:
            return None
        return {
            "id": post.id,
            "week_of": post.week_of,
            "iso_week": post.iso_week,
            "title": post.title,
            "summary": post.summary,
            "content": post.content,
            "week_pnl": post.week_pnl,
            "spy_return_pct": post.spy_return_pct,
            "trades_count": post.trades_count,
            "strategy": post.strategy,
            "created_at": post.created_at.isoformat() + "Z",
        }


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _serialize_trade(trade: Trade) -> dict:
    return {
        "order_id": trade.order_id,
        "ticker": trade.ticker,
        "side": trade.side,
        "qty": trade.qty,
        "fill_price": trade.fill_price,
        "fee": trade.fee,
        "note": trade.note,
        "skill_used": trade.skill_used,
        "timestamp": trade.timestamp.isoformat() + "Z",
    }



def _serialize_signal(signal: Signal) -> dict:
    return {
        "ticker": signal.ticker,
        "signal": signal.signal,
        "confidence": signal.confidence,
        "reason": signal.reason,
        "skill_used": signal.skill_used,
        "acted_on": signal.acted_on,
        "timestamp": signal.timestamp.isoformat() + "Z",
    }



def _build_market_breakdown(
    *,
    market: str,
    trades: list[dict],
    signals: list[dict],
    positions: list[dict],
    research_brief: dict,
) -> dict:
    market_trades = [trade for trade in trades if infer_market(trade["ticker"]) == market]
    market_signals = [signal for signal in signals if infer_market(signal["ticker"]) == market]
    market_positions = [position for position in positions if infer_market(position["ticker"]) == market]
    return {
        "trades": market_trades,
        "signals": market_signals,
        "positions": market_positions,
        "research_brief": research_brief,
        "summary": {
            "trade_count": len(market_trades),
            "signal_count": len(market_signals),
            "position_count": len(market_positions),
        },
    }



def _serialize_positions(positions: list[Position]) -> list[dict]:
    # Position rows currently store ticker, size, and average cost only.
    # Keep the blog context aligned with the actual schema instead of assuming
    # an `opened_at` column exists.
    return [
        {
            "ticker": position.ticker,
            "qty": position.qty,
            "avg_cost": position.avg_cost,
        }
        for position in positions
    ]



def _last_monday(now: datetime) -> datetime:
    day = now.date()
    monday = day - timedelta(days=day.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)



def _compute_benchmark(snapshots: list) -> dict:
    if not snapshots:
        return {"note": "No benchmark snapshots available for this week"}

    first, last = snapshots[0], snapshots[-1]
    pf_return = (
        (last.portfolio_value - first.portfolio_value) / first.portfolio_value * 100
        if first.portfolio_value > 0
        else None
    )
    spy_return = (
        (last.spy_price - first.spy_price) / first.spy_price * 100
        if first.spy_price > 0
        else None
    )
    alpha = pf_return - spy_return if pf_return is not None and spy_return is not None else None

    return {
        "week_start": first.date,
        "week_end": last.date,
        "portfolio_start": first.portfolio_value,
        "portfolio_end": last.portfolio_value,
        "portfolio_week_return_pct": round(pf_return, 2) if pf_return is not None else None,
        "spy_week_return_pct": round(spy_return, 2) if spy_return is not None else None,
        "alpha_pct": round(alpha, 2) if alpha is not None else None,
        "snapshots_count": len(snapshots),
    }



def _write_to_disk(iso_week: str, content: str):
    output_dir = Path(BLOG_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{iso_week}.md"
    path.write_text(content, encoding="utf-8")
    logger.info(f"[blog_agent] Written to {path}")



def _forward_blog_post(title: str, summary: str, content: str, target: str) -> dict:
    if not target:
        return {"ok": False, "skipped": True, "reason": "blog_forward_disabled"}

    message = _build_forward_message(title=title, summary=summary, content=content)
    result = send_openclaw_message(
        account_id="default",
        channel="telegram",
        target=target,
        message=message,
    )
    if result.get("ok"):
        logger.info(f"[blog_agent] Forwarded blog post to {target}")
        return {"ok": True, "target": target}

    logger.warning(f"[blog_agent] Blog forward failed for {target}: {result.get('error')}")
    return {"ok": False, "target": target, "error": result.get("error")}



def _build_forward_message(title: str, summary: str, content: str) -> str:
    cleaned_summary = (summary or "").strip() or _extract_summary_from_markdown(content)
    if len(cleaned_summary) > BLOG_FORWARD_SUMMARY_MAX_CHARS:
        cleaned_summary = cleaned_summary[: BLOG_FORWARD_SUMMARY_MAX_CHARS - 1].rstrip() + "…"

    parts = [title.strip()]
    if cleaned_summary:
        parts.extend(["", cleaned_summary])
    return "\n".join(parts)



def _extract_summary_from_markdown(content: str) -> str:
    lines = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            if lines:
                break
            continue
        if line.startswith("#"):
            continue
        lines.append(line)
        if len(" ".join(lines)) >= BLOG_FORWARD_SUMMARY_MAX_CHARS:
            break
    return " ".join(lines)
