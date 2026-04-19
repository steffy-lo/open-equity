"""
scheduler.py
============
Background jobs using APScheduler (in-process, no Redis needed).

Jobs:
  • Nightly screener   — full watchlist screen at market close (8pm ET Mon–Fri)
  • Intraday screener  — lightweight re-screen every 15 min during market hours
  • Benchmark snapshot — daily portfolio vs SPY snapshot at 9pm ET Mon–Fri

  Autonomous Trading Pipeline (AI-driven)
  • Monday 7am — prep research context (OpenClaw fetches + reasons + POSTs brief)
  • Entries — Mon–Fri 9:35am ET — entries
  • Exits — Mon–Fri 3:45pm ET
  • Friday 6pm — prep blog context


All job output is written to the DB so OpenClaw can query /signals and /portfolio
for up-to-date context even between user sessions.
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session

from database import engine
from services.screener import run_screen, load_watchlist
from services.portfolio_engine import take_benchmark_snapshot
from config import (
    SCREEN_SCHEDULE_CRON,
    SCREEN_INTRADAY_CRON,
    BENCHMARK_SNAPSHOT_CRON,
    RESEARCH_CONTEXT_CRON,
    ENTRY_PASS_CRON,
    EXIT_PASS_CRON,
    BLOG_CONTEXT_CRON,
)

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler = None


# ─────────────────────────────────────────────────────────────
# Job definitions
# ─────────────────────────────────────────────────────────────

def _job_nightly_screen():
    logger.info("[scheduler] 🔍 Running nightly full watchlist screen...")
    try:
        with Session(engine) as session:
            tickers = load_watchlist()
            if not tickers:
                logger.warning("[scheduler] Watchlist is empty — skipping screen.")
                return
            results = run_screen(tickers=tickers, session=session)
            buys    = [r for r in results if r["signal"] == "buy"]
            flags   = [r for r in results if r["signal"] == "flag"]
            logger.info(
                f"[scheduler] ✅ Screen complete: {len(tickers)} tickers → "
                f"{len(buys)} buys, {len(flags)} flags"
            )
    except Exception as exc:
        logger.error(f"[scheduler] Nightly screen failed: {exc}", exc_info=True)


def _job_intraday_screen():
    """
    Lightweight version — only scans tickers that already have a recent buy
    signal or are in open positions, to catch intraday breakouts quickly.
    """
    logger.info("[scheduler] ⚡ Intraday screen...")
    try:
        from database import Signal, Position
        from sqlmodel import select
        from datetime import datetime, timedelta

        with Session(engine) as session:
            # Tickers with recent buy signals (last 5 days)
            cutoff = datetime.utcnow() - timedelta(days=5)
            recent_buys = session.exec(
                select(Signal.ticker)
                .where(Signal.signal == "buy")
                .where(Signal.timestamp >= cutoff)
                .distinct()
            ).all()

            # Plus any currently held positions
            positions = session.exec(select(Position.ticker)).all()

            watchlist  = load_watchlist()
            candidates = list(set(recent_buys) | set(positions) | set(watchlist[:30]))

            if not candidates:
                return

            results = run_screen(tickers=candidates, session=session)
            buys    = [r for r in results if r["signal"] == "buy"]
            logger.info(f"[scheduler] ⚡ Intraday: {len(candidates)} tickers, {len(buys)} buy signals")

    except Exception as exc:
        logger.error(f"[scheduler] Intraday screen failed: {exc}", exc_info=True)


def _job_benchmark_snapshot():
    logger.info("[scheduler] 📸 Taking benchmark snapshot...")
    try:
        with Session(engine) as session:
            result = take_benchmark_snapshot(session)
            if result.get("skipped"):
                logger.info("[scheduler] Benchmark snapshot already taken today.")
            else:
                logger.info(
                    f"[scheduler] ✅ Snapshot: portfolio=${result['portfolio_value']:,.0f} | "
                    f"return={result['total_return_pct']:.2f}%"
                )
    except Exception as exc:
        logger.error(f"[scheduler] Benchmark snapshot failed: {exc}", exc_info=True)


# ─────────────────────────────────────────────────────────────
# Monday: prep research context (OpenClaw fetches + reasons + POSTs brief)
# ─────────────────────────────────────────────────────────────
 
def _job_research_context():
    """
    Snapshot watchlist technicals and log that GET /research/context is ready.
    OpenClaw should then fetch it, reason about strategy, and POST /research.
    """
    logger.info(
        "[scheduler] 🔬 Research context ready — "
        "OpenClaw should call GET /research/context then POST /research"
    )
    try:
        from services.research_agent import build_research_context
        ctx = build_research_context()
        logger.info(
            f"[scheduler] ✅ Research context built: "
            f"{len(ctx.get('watchlist', []))} watchlist tickers, "
            f"{len(ctx.get('recent_signals', []))} recent signals"
        )
    except Exception as exc:
        logger.error(f"[scheduler] Research context build failed: {exc}", exc_info=True)

# ─────────────────────────────────────────────────────────────
# Mon–Fri 9:35am: entry pass (fully automatic, no AI)
# ─────────────────────────────────────────────────────────────
 
def _job_entry_pass():
    logger.info("[scheduler] 📈 Running entry pass...")
    try:
        from services.execution_agent import run_entry_pass
        result  = run_entry_pass()
        placed  = result.get("placed", [])
        skipped = result.get("skipped", [])
        logger.info(
            f"[scheduler] ✅ Entry pass: {len(placed)} orders placed, {len(skipped)} skipped"
        )
        for t in placed:
            logger.info(
                f"[scheduler]   BUY {t['qty']} × {t['ticker']} "
                f"@ ${t['price']:.2f} conf={t['confidence']:.2f}"
            )
    except Exception as exc:
        logger.error(f"[scheduler] Entry pass failed: {exc}", exc_info=True)
 
 
# ─────────────────────────────────────────────────────────────
# Mon–Fri 3:45pm: exit pass (fully automatic, no AI)
# ─────────────────────────────────────────────────────────────
 
def _job_exit_pass():
    logger.info("[scheduler] 📉 Running exit pass...")
    try:
        from services.execution_agent import run_exit_pass
        result = run_exit_pass()
        exits  = result.get("exits", [])
        held   = result.get("still_held", [])
        logger.info(
            f"[scheduler] ✅ Exit pass: {len(exits)} closed, {len(held)} held"
        )
        for e in exits:
            logger.info(
                f"[scheduler]   SELL {e['qty']} × {e['ticker']} "
                f"pnl={e['pnl_pct']:+.1%} ({e['reason']})"
            )
    except Exception as exc:
        logger.error(f"[scheduler] Exit pass failed: {exc}", exc_info=True)
 
 
# ─────────────────────────────────────────────────────────────
# Friday 6pm: prep blog context (OpenClaw fetches + writes + POSTs post)
# ─────────────────────────────────────────────────────────────
 
def _job_blog_context():
    """
    Package up the week's data and log that GET /blog/context is ready.
    OpenClaw should then fetch it, write the review, and POST /blog.
    """
    logger.info(
        "[scheduler] 📝 Blog context ready — "
        "OpenClaw should call GET /blog/context then POST /blog"
    )
    try:
        from services.blog_agent import build_blog_context
        ctx = build_blog_context()
        logger.info(
            f"[scheduler] ✅ Blog context built: "
            f"{len(ctx.get('trades', []))} trades this week, "
            f"benchmark alpha={ctx.get('benchmark', {}).get('alpha_pct')}%"
        )
    except Exception as exc:
        logger.error(f"[scheduler] Blog context build failed: {exc}", exc_info=True)



# ─────────────────────────────────────────────────────────────
# Scheduler lifecycle
# ─────────────────────────────────────────────────────────────

def _parse_cron(cron_str: str) -> CronTrigger:
    """Parse '0 20 * * 1-5' style cron into APScheduler trigger."""
    parts = cron_str.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron expression, got: {cron_str!r}")
    minute, hour, day, month, day_of_week = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone="America/New_York",
    )


def start_scheduler():
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="America/New_York")

    _scheduler.add_job(
        _job_nightly_screen,
        trigger=_parse_cron(SCREEN_SCHEDULE_CRON),
        id="nightly_screen",
        name="Nightly full watchlist screen",
        replace_existing=True,
        misfire_grace_time=300,
    )

    _scheduler.add_job(
        _job_intraday_screen,
        trigger=_parse_cron(SCREEN_INTRADAY_CRON),
        id="intraday_screen",
        name="Intraday signal refresh",
        replace_existing=True,
        misfire_grace_time=60,
    )

    _scheduler.add_job(
        _job_benchmark_snapshot,
        trigger=_parse_cron(BENCHMARK_SNAPSHOT_CRON),
        id="benchmark_snapshot",
        name="Daily benchmark snapshot",
        replace_existing=True,
        misfire_grace_time=300,
    )
 
    _scheduler.add_job(
        _job_research_context,
        trigger=_parse_cron(RESEARCH_CONTEXT_CRON),
        id="research_context",
        name="Monday research context prep",
        replace_existing=True,
        misfire_grace_time=600,
    )
    
    _scheduler.add_job(
        _job_entry_pass,
        trigger=_parse_cron(ENTRY_PASS_CRON),
        id="entry_pass",
        name="Daily entry pass — signal → order",
        replace_existing=True,
        misfire_grace_time=120,
    )
    
    _scheduler.add_job(
        _job_exit_pass,
        trigger=_parse_cron(EXIT_PASS_CRON),
        id="exit_pass",
        name="Daily exit pass — stop-loss / take-profit",
        replace_existing=True,
        misfire_grace_time=120,
    )
    
    _scheduler.add_job(
        _job_blog_context,
        trigger=_parse_cron(BLOG_CONTEXT_CRON),
        id="blog_context",
        name="Sunday blog context prep",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    _scheduler.start()
    logger.info(
        "[scheduler] 🚀 APScheduler started with 7 jobs: "
        "nightly_screen | intraday_screen | benchmark_snapshot | research context | entry pass | exit pass | blog context"
    )


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[scheduler] APScheduler stopped.")


def get_scheduler_status() -> dict:
    if not _scheduler:
        return {"running": False}
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id":       job.id,
            "name":     job.name,
            "next_run": str(job.next_run_time),
        })
    return {"running": _scheduler.running, "jobs": jobs}
