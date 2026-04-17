"""
scheduler.py
============
Background jobs using APScheduler (in-process, no Redis needed).

Jobs:
  • Nightly screener   — full watchlist screen at market close (8pm ET Mon–Fri)
  • Intraday screener  — lightweight re-screen every 15 min during market hours
  • Benchmark snapshot — daily portfolio vs SPY snapshot at 9pm ET Mon–Fri

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

    _scheduler.start()
    logger.info(
        "[scheduler] 🚀 APScheduler started with 3 jobs: "
        "nightly_screen | intraday_screen | benchmark_snapshot"
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
