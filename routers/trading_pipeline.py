"""
routers/trading_pipeline.py
====================
New endpoints for the autonomous trading pipeline.
All AI reasoning lives in OpenClaw — this router is pure data in/out.

Research endpoints:
  GET  /research/context   — fetch pre-packaged context for OpenClaw to reason over
  POST /research           — OpenClaw posts its structured strategy brief
  GET  /research           — list stored research briefs (newest first)
  GET  /research/latest    — latest brief

Execution endpoints:
  POST /pipeline/entry     — manually trigger entry pass (normally scheduled)
  POST /pipeline/exit      — manually trigger exit pass  (normally scheduled)
  GET  /pipeline/status    — summary of last entry/exit pass results

Blog endpoints:
  GET  /blog/context       — fetch pre-packaged context for OpenClaw to write the review
  POST /blog               — OpenClaw posts the written markdown blog post
  GET  /blog               — list all weekly review posts
  GET  /blog/{id}          — full post with markdown content
"""

import logging

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, Field
from typing import Optional

from services.research_agent import (
    build_research_context,
    ingest_brief,
    get_latest_brief,
)
from services.blog_agent import (
    build_blog_context,
    ingest_blog_post,
    list_blog_posts,
    get_blog_post,
)
from services.execution_agent import run_entry_pass, run_exit_pass
from services.markets import Market

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Trading Pipeline"])

# ── In-memory cache for last pass results (lightweight) ───────
_last_entry_result: dict[str, dict] = {}
_last_exit_result:  dict[str, dict] = {}


# ─────────────────────────────────────────────────────────────
# Research
# ─────────────────────────────────────────────────────────────

@router.get("/research/context", summary="Fetch research context for OpenClaw")
def get_research_context(market: Market = Query("US")):
    """
    Returns a pre-packaged data bundle: watchlist technicals, recent signals,
    benchmark trend, and the prior week's brief.

    OpenClaw should:
      1. Fetch this context
      2. Reason about macro, strategy, and which tickers to watch
      3. POST the resulting brief to POST /research
    """
    return build_research_context(market=market)


class ResearchBriefPayload(BaseModel):
    market:           Market              = "US"
    week_of:          Optional[str]       = None
    macro_summary:    Optional[str]       = None
    strategy:         Optional[str]       = "mixed"   # momentum|mean_reversion|sector_rotation|defensive|mixed
    time_horizon:     Optional[str]       = None      # e.g. "3-5 day swing"
    risk_posture:     Optional[str]       = "moderate"  # aggressive|moderate|conservative
    themes:           list[str] = Field(default_factory=list)
    focus_sectors:    list[str] = Field(default_factory=list)
    avoid_sectors:    list[str] = Field(default_factory=list)
    watchlist_add:    list[str] = Field(default_factory=list)
    watchlist_remove: list[str] = Field(default_factory=list)
    earnings_watch:   list[str] = Field(default_factory=list)
    key_risks:        Optional[str]       = None
    rationale:        Optional[str]       = None


@router.post("/research", summary="OpenClaw posts its weekly strategy brief")
def post_research_brief(payload: ResearchBriefPayload):
    """
    Store the strategy brief produced by OpenClaw and apply watchlist mutations.

    Returns the stored brief ID and a summary of watchlist changes made.
    """
    result = ingest_brief(payload.dict(exclude_none=True))
    return result


@router.get("/research/latest", summary="Get the latest research brief")
def get_latest_research(market: Market = Query("US")):
    brief = get_latest_brief(market=market)
    if not brief:
        raise HTTPException(status_code=404, detail="No research briefs found")
    return brief


# ─────────────────────────────────────────────────────────────
# Blog
# ─────────────────────────────────────────────────────────────

@router.get("/blog/context", summary="Fetch blog writing context for OpenClaw")
def get_blog_context():
    """
    Returns a pre-packaged data bundle: week's trades, portfolio P&L,
    benchmark comparison, all signals, and the research brief.

    OpenClaw should:
      1. Fetch this context
      2. Write the weekly performance review in markdown
      3. POST the result to POST /blog
    """
    return build_blog_context()


class BlogPostPayload(BaseModel):
    content:  str             # full markdown — required
    summary:  Optional[str]  = ""
    week_pnl: Optional[float] = 0.0


@router.post("/blog", summary="OpenClaw posts the written weekly review")
def post_blog(payload: BlogPostPayload):
    """
    Store the markdown blog post written by OpenClaw.
    Also writes the post to blogs/YYYY-WNN.md on disk.
    """
    try:
        result = ingest_blog_post(payload.dict())
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/blog", summary="List all weekly review posts")
def list_posts(limit: int = 20):
    posts = list_blog_posts(limit=limit)
    return {"count": len(posts), "posts": posts}


@router.get("/blog/{post_id}", summary="Get a specific weekly review post")
def get_post(post_id: int):
    post = get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail=f"Blog post {post_id} not found")
    return post


# ─────────────────────────────────────────────────────────────
# Execution (manual triggers + status)
# ─────────────────────────────────────────────────────────────

@router.post("/pipeline/entry", summary="Manually trigger the entry pass")
def trigger_entry(background_tasks: BackgroundTasks, market: Market = Query("US")):
    """
    Runs the market-specific entry pass in the background.
    Returns immediately, check GET /pipeline/status for results.
    """
    background_tasks.add_task(_run_entry_bg, market)
    return {"status": "started", "market": market, "message": f"{market} entry pass running in background. Check GET /pipeline/status."}


@router.post("/pipeline/exit", summary="Manually trigger the exit pass")
def trigger_exit(background_tasks: BackgroundTasks, market: Market = Query("US")):
    """
    Runs the market-specific exit pass in the background.
    """
    background_tasks.add_task(_run_exit_bg, market)
    return {"status": "started", "market": market, "message": f"{market} exit pass running in background. Check GET /pipeline/status."}


@router.get("/pipeline/status", summary="Last entry and exit pass results")
def pipeline_status():
    return {
        "last_entry_pass": {
            "US": _last_entry_result.get("US", {"note": "Not run yet"}),
            "HK": _last_entry_result.get("HK", {"note": "Not run yet"}),
        },
        "last_exit_pass": {
            "US": _last_exit_result.get("US", {"note": "Not run yet"}),
            "HK": _last_exit_result.get("HK", {"note": "Not run yet"}),
        },
    }


# ─────────────────────────────────────────────────────────────
# Background task helpers
# ─────────────────────────────────────────────────────────────

def _run_entry_bg(market: Market = "US"):
    global _last_entry_result
    try:
        _last_entry_result[market] = run_entry_pass(market=market)
        logger.info(f"[pipeline_router] {market} entry pass done: {_last_entry_result[market]}")
    except Exception as exc:
        logger.error(f"[pipeline_router] {market} entry pass failed: {exc}", exc_info=True)
        _last_entry_result[market] = {"market": market, "error": str(exc)}


def _run_exit_bg(market: Market = "US"):
    global _last_exit_result
    try:
        _last_exit_result[market] = run_exit_pass(market=market)
        logger.info(f"[pipeline_router] {market} exit pass done: {_last_exit_result[market]}")
    except Exception as exc:
        logger.error(f"[pipeline_router] {market} exit pass failed: {exc}", exc_info=True)
        _last_exit_result[market] = {"market": market, "error": str(exc)}
