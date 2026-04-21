"""
scheduler.py
============
Background jobs using APScheduler (in-process, no Redis needed).

US jobs:
  • Nightly screener   — full watchlist screen at market close
  • Intraday screener  — lightweight re-screen every 15 min during market hours
  • Benchmark snapshot — daily portfolio vs SPY snapshot
  • Monday research context prep
  • Entry / exit execution
  • Friday blog context prep

HK parity jobs:
  • Monday HK research context prep
  • HK pre-open screen
  • HK midday screen refresh
  • HK AM / PM entry execution
  • HK lunch / close exit execution
"""

import logging
from datetime import datetime, timedelta
from typing import Literal

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from config import (
    BENCHMARK_SNAPSHOT_CRON,
    BLOG_CONTEXT_CRON,
    ENTRY_PASS_CRON,
    EXIT_PASS_CRON,
    HK_ENTRY_PASS_CRON_AM,
    HK_ENTRY_PASS_CRON_PM,
    HK_EXIT_PASS_CRON_AM,
    HK_EXIT_PASS_CRON_PM,
    HK_MIDDAY_SCREEN_CRON,
    HK_PREOPEN_SCREEN_CRON,
    HK_RESEARCH_CONTEXT_CRON,
    RESEARCH_CONTEXT_CRON,
    SCREEN_INTRADAY_CRON,
    SCREEN_SCHEDULE_CRON,
)
from database import Signal, Position, engine
from services.portfolio_engine import take_benchmark_snapshot
from services.screener import load_watchlist, run_screen

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler = None
Market = Literal["US", "HK"]


def infer_market(ticker: str) -> Market:
    t = (ticker or "").upper().strip()
    return "HK" if t.endswith(".HK") else "US"


def _filter_market_tickers(tickers: list[str], market: Market) -> list[str]:
    seen = set()
    filtered = []
    for ticker in tickers:
        upper = ticker.upper()
        if infer_market(upper) == market and upper not in seen:
            seen.add(upper)
            filtered.append(upper)
    return filtered


def _build_intraday_candidates(session: Session, market: Market) -> list[str]:
    cutoff = datetime.utcnow() - timedelta(days=5)
    recent_buys = session.exec(
        select(Signal.ticker)
        .where(Signal.signal == "buy")
        .where(Signal.timestamp >= cutoff)
        .distinct()
    ).all()
    positions = session.exec(select(Position.ticker)).all()
    watchlist = load_watchlist()
    candidates = list(set(recent_buys) | set(positions) | set(watchlist[:30]))
    return _filter_market_tickers(candidates, market)


# ─────────────────────────────────────────────────────────────
# Job definitions
# ─────────────────────────────────────────────────────────────

def _job_nightly_screen():
    logger.info("[scheduler] 🔍 Running nightly full watchlist screen...")
    try:
        with Session(engine) as session:
            tickers = load_watchlist()
            if not tickers:
                logger.warning("[scheduler] Watchlist is empty, skipping screen.")
                return
            results = run_screen(tickers=tickers, session=session)
            buys = [r for r in results if r["signal"] == "buy"]
            flags = [r for r in results if r["signal"] == "flag"]
            logger.info(
                f"[scheduler] ✅ Screen complete: {len(tickers)} tickers → {len(buys)} buys, {len(flags)} flags"
            )
    except Exception as exc:
        logger.error(f"[scheduler] Nightly screen failed: {exc}", exc_info=True)



def _job_intraday_screen():
    logger.info("[scheduler] ⚡ Intraday screen...")
    try:
        with Session(engine) as session:
            candidates = _build_intraday_candidates(session, market="US")
            if not candidates:
                return
            results = run_screen(tickers=candidates, session=session)
            buys = [r for r in results if r["signal"] == "buy"]
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
                    f"[scheduler] ✅ Snapshot: portfolio=${result['portfolio_value']:,.0f} | return={result['total_return_pct']:.2f}%"
                )
    except Exception as exc:
        logger.error(f"[scheduler] Benchmark snapshot failed: {exc}", exc_info=True)



def _job_research_context():
    logger.info("[scheduler] 🔬 US research context ready — OpenClaw should call GET /research/context?market=US then POST /research")
    try:
        from services.research_agent import build_research_context

        ctx = build_research_context(market="US")
        logger.info(
            f"[scheduler] ✅ US research context built: {len(ctx.get('watchlist', []))} watchlist tickers, {len(ctx.get('recent_signals', []))} recent signals"
        )
    except Exception as exc:
        logger.error(f"[scheduler] US research context build failed: {exc}", exc_info=True)



def _job_entry_pass():
    logger.info("[scheduler] 📈 Running US entry pass...")
    try:
        from services.execution_agent import run_entry_pass

        result = run_entry_pass(market="US")
        placed = result.get("placed", [])
        skipped = result.get("skipped", [])
        logger.info(f"[scheduler] ✅ US entry pass: {len(placed)} orders placed, {len(skipped)} skipped")
    except Exception as exc:
        logger.error(f"[scheduler] US entry pass failed: {exc}", exc_info=True)



def _job_exit_pass():
    logger.info("[scheduler] 📉 Running US exit pass...")
    try:
        from services.execution_agent import run_exit_pass

        result = run_exit_pass(market="US")
        exits = result.get("exits", [])
        held = result.get("still_held", [])
        logger.info(f"[scheduler] ✅ US exit pass: {len(exits)} closed, {len(held)} held")
    except Exception as exc:
        logger.error(f"[scheduler] US exit pass failed: {exc}", exc_info=True)



def _job_blog_context():
    logger.info("[scheduler] 📝 Blog context ready — OpenClaw should call GET /blog/context then POST /blog")
    try:
        from services.blog_agent import build_blog_context

        ctx = build_blog_context()
        logger.info(
            f"[scheduler] ✅ Blog context built: {len(ctx.get('trades', []))} trades this week, benchmark alpha={ctx.get('benchmark', {}).get('alpha_pct')}%"
        )
    except Exception as exc:
        logger.error(f"[scheduler] Blog context build failed: {exc}", exc_info=True)



def _job_hk_research_context():
    logger.info("[scheduler] 🇭🇰 HK research context ready — OpenClaw should call GET /research/context?market=HK then POST /research")
    try:
        from services.research_agent import build_research_context

        ctx = build_research_context(market="HK")
        logger.info(
            f"[scheduler] ✅ HK research context built: {len(ctx.get('watchlist', []))} watchlist tickers, {len(ctx.get('recent_signals', []))} recent signals"
        )
    except Exception as exc:
        logger.error(f"[scheduler] HK research context build failed: {exc}", exc_info=True)



def _job_hk_preopen_screen():
    logger.info("[scheduler] 🇭🇰 Running HK pre-open screen...")
    try:
        with Session(engine) as session:
            tickers = _filter_market_tickers(load_watchlist(), market="HK")
            if not tickers:
                logger.info("[scheduler] No HK tickers in watchlist, skipping HK pre-open screen.")
                return
            results = run_screen(
                tickers=tickers,
                session=session,
                screen_scope="watchlist",
                screen_label="hk-preopen",
                use_watchlist=True,
            )
            buys = [r for r in results if r["signal"] == "buy"]
            flags = [r for r in results if r["signal"] == "flag"]
            logger.info(
                f"[scheduler] ✅ HK pre-open screen: {len(tickers)} tickers → {len(buys)} buys, {len(flags)} flags"
            )
    except Exception as exc:
        logger.error(f"[scheduler] HK pre-open screen failed: {exc}", exc_info=True)



def _job_hk_midday_screen():
    logger.info("[scheduler] 🇭🇰 Running HK midday screen refresh...")
    try:
        with Session(engine) as session:
            candidates = _build_intraday_candidates(session, market="HK")
            if not candidates:
                logger.info("[scheduler] No HK candidates for midday screen refresh.")
                return
            results = run_screen(
                tickers=candidates,
                session=session,
                screen_scope="watchlist",
                screen_label="hk-midday-refresh",
            )
            buys = [r for r in results if r["signal"] == "buy"]
            logger.info(f"[scheduler] ✅ HK midday screen: {len(candidates)} tickers, {len(buys)} buy signals")
    except Exception as exc:
        logger.error(f"[scheduler] HK midday screen failed: {exc}", exc_info=True)



def _job_hk_entry_pass_am():
    logger.info("[scheduler] 🇭🇰 Running HK AM entry pass...")
    try:
        from services.execution_agent import run_entry_pass

        result = run_entry_pass(market="HK")
        placed = result.get("placed", [])
        skipped = result.get("skipped", [])
        logger.info(f"[scheduler] ✅ HK AM entry pass: {len(placed)} orders placed, {len(skipped)} skipped")
    except Exception as exc:
        logger.error(f"[scheduler] HK AM entry pass failed: {exc}", exc_info=True)



def _job_hk_exit_pass_am():
    logger.info("[scheduler] 🇭🇰 Running HK lunch exit pass...")
    try:
        from services.execution_agent import run_exit_pass

        result = run_exit_pass(market="HK")
        exits = result.get("exits", [])
        held = result.get("still_held", [])
        logger.info(f"[scheduler] ✅ HK lunch exit pass: {len(exits)} closed, {len(held)} held")
    except Exception as exc:
        logger.error(f"[scheduler] HK lunch exit pass failed: {exc}", exc_info=True)



def _job_hk_entry_pass_pm():
    logger.info("[scheduler] 🇭🇰 Running HK PM entry pass...")
    try:
        from services.execution_agent import run_entry_pass

        result = run_entry_pass(market="HK")
        placed = result.get("placed", [])
        skipped = result.get("skipped", [])
        logger.info(f"[scheduler] ✅ HK PM entry pass: {len(placed)} orders placed, {len(skipped)} skipped")
    except Exception as exc:
        logger.error(f"[scheduler] HK PM entry pass failed: {exc}", exc_info=True)



def _job_hk_exit_pass_pm():
    logger.info("[scheduler] 🇭🇰 Running HK close exit pass...")
    try:
        from services.execution_agent import run_exit_pass

        result = run_exit_pass(market="HK")
        exits = result.get("exits", [])
        held = result.get("still_held", [])
        logger.info(f"[scheduler] ✅ HK close exit pass: {len(exits)} closed, {len(held)} held")
    except Exception as exc:
        logger.error(f"[scheduler] HK close exit pass failed: {exc}", exc_info=True)


# ─────────────────────────────────────────────────────────────
# Scheduler lifecycle
# ─────────────────────────────────────────────────────────────

def _parse_cron_for_timezone(cron_str: str, timezone: str) -> CronTrigger:
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
        timezone=timezone,
    )



def _parse_cron(cron_str: str) -> CronTrigger:
    return _parse_cron_for_timezone(cron_str, "America/New_York")



def _parse_cron_hk(cron_str: str) -> CronTrigger:
    return _parse_cron_for_timezone(cron_str, "Asia/Hong_Kong")



def start_scheduler():
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="America/New_York")

    _scheduler.add_job(_job_nightly_screen, trigger=_parse_cron(SCREEN_SCHEDULE_CRON), id="nightly_screen", name="Nightly full watchlist screen", replace_existing=True, misfire_grace_time=300)
    _scheduler.add_job(_job_intraday_screen, trigger=_parse_cron(SCREEN_INTRADAY_CRON), id="intraday_screen", name="Intraday signal refresh", replace_existing=True, misfire_grace_time=60)
    _scheduler.add_job(_job_benchmark_snapshot, trigger=_parse_cron(BENCHMARK_SNAPSHOT_CRON), id="benchmark_snapshot", name="Daily benchmark snapshot", replace_existing=True, misfire_grace_time=300)
    _scheduler.add_job(_job_research_context, trigger=_parse_cron(RESEARCH_CONTEXT_CRON), id="research_context", name="Monday research context prep", replace_existing=True, misfire_grace_time=600)
    _scheduler.add_job(_job_entry_pass, trigger=_parse_cron(ENTRY_PASS_CRON), id="entry_pass", name="Daily entry pass, US", replace_existing=True, misfire_grace_time=120)
    _scheduler.add_job(_job_exit_pass, trigger=_parse_cron(EXIT_PASS_CRON), id="exit_pass", name="Daily exit pass, US", replace_existing=True, misfire_grace_time=120)
    _scheduler.add_job(_job_blog_context, trigger=_parse_cron(BLOG_CONTEXT_CRON), id="blog_context", name="Sunday blog context prep", replace_existing=True, misfire_grace_time=1800)

    _scheduler.add_job(_job_hk_research_context, trigger=_parse_cron_hk(HK_RESEARCH_CONTEXT_CRON), id="hk_research_context", name="HK Monday research context prep", replace_existing=True, misfire_grace_time=600)
    _scheduler.add_job(_job_hk_preopen_screen, trigger=_parse_cron_hk(HK_PREOPEN_SCREEN_CRON), id="hk_preopen_screen", name="HK pre-open screen", replace_existing=True, misfire_grace_time=300)
    _scheduler.add_job(_job_hk_midday_screen, trigger=_parse_cron_hk(HK_MIDDAY_SCREEN_CRON), id="hk_midday_screen", name="HK midday screen refresh", replace_existing=True, misfire_grace_time=300)
    _scheduler.add_job(_job_hk_entry_pass_am, trigger=_parse_cron_hk(HK_ENTRY_PASS_CRON_AM), id="hk_entry_pass_am", name="HK daily entry pass (AM)", replace_existing=True, misfire_grace_time=120)
    _scheduler.add_job(_job_hk_exit_pass_am, trigger=_parse_cron_hk(HK_EXIT_PASS_CRON_AM), id="hk_exit_pass_am", name="HK daily exit pass (AM)", replace_existing=True, misfire_grace_time=120)
    _scheduler.add_job(_job_hk_entry_pass_pm, trigger=_parse_cron_hk(HK_ENTRY_PASS_CRON_PM), id="hk_entry_pass_pm", name="HK daily entry pass (PM)", replace_existing=True, misfire_grace_time=120)
    _scheduler.add_job(_job_hk_exit_pass_pm, trigger=_parse_cron_hk(HK_EXIT_PASS_CRON_PM), id="hk_exit_pass_pm", name="HK daily exit pass (PM)", replace_existing=True, misfire_grace_time=120)

    _scheduler.start()
    logger.info(
        "[scheduler] 🚀 APScheduler started with US and HK parity jobs: nightly_screen | intraday_screen | benchmark_snapshot | research_context | entry_pass | exit_pass | blog_context | hk_research_context | hk_preopen_screen | hk_midday_screen | hk_entry_pass_am | hk_exit_pass_am | hk_entry_pass_pm | hk_exit_pass_pm"
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
        jobs.append({"id": job.id, "name": job.name, "next_run": str(job.next_run_time)})
    return {"running": _scheduler.running, "jobs": jobs}
