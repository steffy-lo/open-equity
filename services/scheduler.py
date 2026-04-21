"""
scheduler.py
============
Background jobs using APScheduler (in-process, no Redis needed).

Shared market-session structure:
  • weekly research context prep
  • pre-open screen
  • midday screen refresh
  • market-specific entry / exit execution

US also keeps benchmark snapshot and weekly blog prep.
HK retains AM/PM entry and exit windows because the market has a lunch break.
"""

import logging
from datetime import datetime, timedelta

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
    US_MIDDAY_SCREEN_CRON,
    US_PREOPEN_SCREEN_CRON,
)
from database import Signal, Position, engine
from services.markets import Market, filter_market_tickers
from services.portfolio_engine import take_benchmark_snapshot
from services.screener import load_watchlist, run_momentum_discovery_screen, run_screen

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler = None

US_TIMEZONE = "America/New_York"
HK_TIMEZONE = "Asia/Hong_Kong"


def _build_signal_candidates(session: Session, market: Market) -> list[str]:
    cutoff = datetime.utcnow() - timedelta(days=5)
    recent_buys = session.exec(
        select(Signal.ticker)
        .where(Signal.signal == "buy")
        .where(Signal.timestamp >= cutoff)
        .distinct()
    ).all()
    positions = session.exec(select(Position.ticker)).all()
    watchlist = load_watchlist(market=market)
    candidates = list(set(recent_buys) | set(positions) | set(watchlist[:30]))
    return filter_market_tickers(candidates, market)


# ─────────────────────────────────────────────────────────────
# Job definitions
# ─────────────────────────────────────────────────────────────

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



def _job_blog_context():
    logger.info("[scheduler] 📝 Blog context ready — OpenClaw should call GET /blog/context then POST /blog")
    try:
        from services.blog_agent import build_blog_context

        context = build_blog_context()
        logger.info(
            f"[scheduler] ✅ Blog context built: {len(context.get('trades', []))} trades this week, benchmark alpha={context.get('benchmark', {}).get('alpha_pct')}%"
        )
    except Exception as exc:
        logger.error(f"[scheduler] Blog context build failed: {exc}", exc_info=True)



def _job_research_context_us():
    _run_research_context_job("US")



def _job_research_context_hk():
    _run_research_context_job("HK")



def _job_preopen_screen_us():
    logger.info("[scheduler] 🇺🇸 Running US pre-open screen...")
    _run_market_screen(
        market="US",
        tickers_factory=lambda _session: load_watchlist(market="US"),
        screen_label="us-preopen",
        empty_message="[scheduler] No US tickers in watchlist, skipping US pre-open screen.",
        success_template="[scheduler] ✅ US pre-open screen: {ticker_count} tickers → {buy_count} buys, {flag_count} flags",
        include_momentum_discovery=True,
    )



def _job_midday_screen_us():
    logger.info("[scheduler] 🇺🇸 Running US midday screen refresh...")
    _run_market_screen(
        market="US",
        tickers_factory=lambda session: _build_signal_candidates(session, market="US"),
        screen_label="us-midday-refresh",
        empty_message="[scheduler] No US candidates for midday screen refresh.",
        success_template="[scheduler] ✅ US midday screen: {ticker_count} tickers, {buy_count} buy signals",
        include_momentum_discovery=True,
    )



def _job_preopen_screen_hk():
    logger.info("[scheduler] 🇭🇰 Running HK pre-open screen...")
    _run_market_screen(
        market="HK",
        tickers_factory=lambda _session: load_watchlist(market="HK"),
        screen_label="hk-preopen",
        empty_message="[scheduler] No HK tickers in watchlist, skipping HK pre-open screen.",
        success_template="[scheduler] ✅ HK pre-open screen: {ticker_count} tickers → {buy_count} buys, {flag_count} flags",
        include_momentum_discovery=True,
    )



def _job_midday_screen_hk():
    logger.info("[scheduler] 🇭🇰 Running HK midday screen refresh...")
    _run_market_screen(
        market="HK",
        tickers_factory=lambda session: _build_signal_candidates(session, market="HK"),
        screen_label="hk-midday-refresh",
        empty_message="[scheduler] No HK candidates for midday screen refresh.",
        success_template="[scheduler] ✅ HK midday screen: {ticker_count} tickers, {buy_count} buy signals",
        include_momentum_discovery=True,
    )



def _job_entry_pass_us():
    _run_entry_pass_job("US", "US entry pass")



def _job_exit_pass_us():
    _run_exit_pass_job("US", "US exit pass")



def _job_hk_entry_pass_am():
    _run_entry_pass_job("HK", "HK AM entry pass")



def _job_hk_exit_pass_am():
    _run_exit_pass_job("HK", "HK lunch exit pass")



def _job_hk_entry_pass_pm():
    _run_entry_pass_job("HK", "HK PM entry pass")



def _job_hk_exit_pass_pm():
    _run_exit_pass_job("HK", "HK close exit pass")



def _run_research_context_job(market: Market) -> None:
    logger.info(
        f"[scheduler] {'🇭🇰' if market == 'HK' else '🇺🇸'} {market} research context ready — "
        f"OpenClaw should call GET /research/context?market={market} then POST /research"
    )
    try:
        from services.research_agent import build_research_context

        context = build_research_context(market=market)
        logger.info(
            f"[scheduler] ✅ {market} research context built: "
            f"{len(context.get('watchlist', []))} watchlist tickers, "
            f"{len(context.get('recent_signals', []))} recent signals, "
            f"{len(context.get('momentum_candidates', []))} momentum candidates"
        )
    except Exception as exc:
        logger.error(f"[scheduler] {market} research context build failed: {exc}", exc_info=True)



def _run_market_screen(
    *,
    market: Market,
    tickers_factory,
    screen_label: str,
    empty_message: str,
    success_template: str,
    include_momentum_discovery: bool = False,
) -> None:
    try:
        with Session(engine) as session:
            tickers = tickers_factory(session)
            if not tickers and not include_momentum_discovery:
                logger.info(empty_message)
                return

            discovery_results = []
            if include_momentum_discovery:
                discovery_results = run_momentum_discovery_screen(
                    market=market,
                    session=session,
                    exclude_tickers=tickers,
                    screen_label=f"{screen_label}-momentum",
                )
                discovery_buys = [result for result in discovery_results if result["signal"] == "buy"]
                if discovery_results:
                    logger.info(
                        f"[scheduler] ✅ {market} momentum discovery: {len(discovery_results)} candidates, {len(discovery_buys)} buy signals"
                    )

            results = []
            if tickers:
                results = run_screen(
                    tickers=tickers,
                    session=session,
                    screen_scope="watchlist",
                    screen_label=screen_label,
                    use_watchlist=True,
                    market=market,
                )
            buys = [result for result in results if result["signal"] == "buy"]
            flags = [result for result in results if result["signal"] == "flag"]
            if results:
                logger.info(
                    success_template.format(
                        ticker_count=len(tickers),
                        buy_count=len(buys),
                        flag_count=len(flags),
                    )
                )
            elif not discovery_results:
                logger.info(empty_message)
    except Exception as exc:
        logger.error(f"[scheduler] {market} screen failed: {exc}", exc_info=True)



def _run_entry_pass_job(market: Market, label: str) -> None:
    logger.info(f"[scheduler] Running {label}...")
    try:
        from services.execution_agent import run_entry_pass

        result = run_entry_pass(market=market)
        logger.info(
            f"[scheduler] ✅ {label}: {len(result.get('placed', []))} orders placed, {len(result.get('skipped', []))} skipped"
        )
    except Exception as exc:
        logger.error(f"[scheduler] {label} failed: {exc}", exc_info=True)



def _run_exit_pass_job(market: Market, label: str) -> None:
    logger.info(f"[scheduler] Running {label}...")
    try:
        from services.execution_agent import run_exit_pass

        result = run_exit_pass(market=market)
        logger.info(
            f"[scheduler] ✅ {label}: {len(result.get('exits', []))} closed, {len(result.get('still_held', []))} held"
        )
    except Exception as exc:
        logger.error(f"[scheduler] {label} failed: {exc}", exc_info=True)


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
    return _parse_cron_for_timezone(cron_str, US_TIMEZONE)



def _parse_cron_hk(cron_str: str) -> CronTrigger:
    return _parse_cron_for_timezone(cron_str, HK_TIMEZONE)



def start_scheduler():
    global _scheduler
    _scheduler = BackgroundScheduler(timezone=US_TIMEZONE)

    jobs = [
        (_job_benchmark_snapshot, _parse_cron(BENCHMARK_SNAPSHOT_CRON), "benchmark_snapshot", "Daily benchmark snapshot", 300),
        (_job_research_context_us, _parse_cron(RESEARCH_CONTEXT_CRON), "research_context_us", "US weekly research context prep", 600),
        (_job_preopen_screen_us, _parse_cron(US_PREOPEN_SCREEN_CRON), "preopen_screen_us", "US pre-open screen", 300),
        (_job_midday_screen_us, _parse_cron(US_MIDDAY_SCREEN_CRON), "midday_screen_us", "US midday screen refresh", 300),
        (_job_entry_pass_us, _parse_cron(ENTRY_PASS_CRON), "entry_pass_us", "US entry pass", 120),
        (_job_exit_pass_us, _parse_cron(EXIT_PASS_CRON), "exit_pass_us", "US exit pass", 120),
        (_job_blog_context, _parse_cron(BLOG_CONTEXT_CRON), "blog_context", "Sunday blog context prep", 1800),
        (_job_research_context_hk, _parse_cron_hk(HK_RESEARCH_CONTEXT_CRON), "research_context_hk", "HK weekly research context prep", 600),
        (_job_preopen_screen_hk, _parse_cron_hk(HK_PREOPEN_SCREEN_CRON), "preopen_screen_hk", "HK pre-open screen", 300),
        (_job_midday_screen_hk, _parse_cron_hk(HK_MIDDAY_SCREEN_CRON), "midday_screen_hk", "HK midday screen refresh", 300),
        (_job_hk_entry_pass_am, _parse_cron_hk(HK_ENTRY_PASS_CRON_AM), "entry_pass_hk_am", "HK AM entry pass", 120),
        (_job_hk_exit_pass_am, _parse_cron_hk(HK_EXIT_PASS_CRON_AM), "exit_pass_hk_am", "HK AM exit pass", 120),
        (_job_hk_entry_pass_pm, _parse_cron_hk(HK_ENTRY_PASS_CRON_PM), "entry_pass_hk_pm", "HK PM entry pass", 120),
        (_job_hk_exit_pass_pm, _parse_cron_hk(HK_EXIT_PASS_CRON_PM), "exit_pass_hk_pm", "HK PM exit pass", 120),
    ]

    for func, trigger, job_id, name, grace in jobs:
        _scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            name=name,
            replace_existing=True,
            misfire_grace_time=grace,
        )

    _scheduler.start()
    logger.info("[scheduler] 🚀 APScheduler started with harmonized US and HK session pipelines")



def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[scheduler] APScheduler stopped.")



def get_scheduler_status() -> dict:
    if not _scheduler:
        return {"running": False}
    return {
        "running": _scheduler.running,
        "jobs": [
            {"id": job.id, "name": job.name, "next_run": str(job.next_run_time)}
            for job in _scheduler.get_jobs()
        ],
    }
