from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from database import Signal, get_session
from services.scheduler import get_scheduler_status
from services.screener import (
    add_to_watchlist,
    get_latest_signals,
    ingest_signals,
    load_watchlist,
    remove_from_watchlist,
    run_screen,
)


router = APIRouter(tags=["Screener"])


class SignalIn(BaseModel):
    ticker: str
    signal: str
    confidence: float
    reason: str
    skill_used: Optional[str] = None
    price_at_signal: Optional[float] = None
    screen_scope: Optional[str] = None
    screen_label: Optional[str] = None
    universe: Optional[str] = None
    watchlist_member: Optional[bool] = None


class ScreenRequest(BaseModel):
    tickers: Optional[list[str]] = None
    signals: Optional[list[SignalIn]] = None
    use_watchlist: bool = False
    save_to_watchlist: bool = False
    screen_scope: Optional[Literal["watchlist", "custom_universe", "ad_hoc"]] = None
    screen_label: Optional[str] = None
    universe: Optional[str] = None


class WatchlistRequest(BaseModel):
    tickers: list[str]
    action: Literal["add", "remove"]


@router.post("/screen")
def screen(payload: ScreenRequest, session: Session = Depends(get_session)):
    tickers = [ticker.upper() for ticker in (payload.tickers or [])]

    if payload.save_to_watchlist and tickers:
        add_to_watchlist(tickers)

    if tickers or payload.use_watchlist:
        results = run_screen(
            tickers=tickers or None,
            session=session,
            screen_scope=payload.screen_scope,
            screen_label=payload.screen_label,
            universe=payload.universe,
            use_watchlist=payload.use_watchlist,
        )
        return {
            "screen": {
                "scanned": len(results),
                "screen_scope": payload.screen_scope or ("watchlist" if payload.use_watchlist and not tickers else "custom_universe" if tickers else "watchlist"),
                "screen_label": payload.screen_label,
                "universe": payload.universe,
                "saved_to_watchlist": payload.save_to_watchlist,
                "results": results,
            }
        }

    if payload.signals:
        signal_dicts = []
        for signal in payload.signals:
            item = signal.model_dump()
            item["screen_scope"] = item.get("screen_scope") or payload.screen_scope or "watchlist"
            item["screen_label"] = item.get("screen_label") or payload.screen_label
            item["universe"] = item.get("universe") or payload.universe
            signal_dicts.append(item)

        if payload.save_to_watchlist:
            add_to_watchlist([signal["ticker"] for signal in signal_dicts])

        result = ingest_signals(signal_dicts, session)
        return {"ingested": result}

    raise HTTPException(status_code=400, detail="Provide either tickers or signals")


@router.get("/signals")
def signals(
    signal_type: Optional[str] = None,
    limit: int = 50,
    screen_scope: Optional[str] = None,
    screen_label: Optional[str] = None,
    session: Session = Depends(get_session),
):
    return {
        "signals": get_latest_signals(
            session,
            signal_type=signal_type,
            limit=limit,
            screen_scope=screen_scope,
            screen_label=screen_label,
        )
    }


@router.get("/signals/{ticker}")
def signal_history(ticker: str, session: Session = Depends(get_session)):
    rows = session.exec(
        select(Signal)
        .where(Signal.ticker == ticker.upper())
        .order_by(col(Signal.timestamp).desc())
    ).all()
    return {
        "ticker": ticker.upper(),
        "count": len(rows),
        "signals": [
            {
                "id": row.id,
                "ticker": row.ticker,
                "signal": row.signal,
                "confidence": row.confidence,
                "reason": row.reason,
                "skill_used": row.skill_used,
                "price_at_signal": row.price_at_signal,
                "screen_scope": row.screen_scope,
                "screen_label": row.screen_label,
                "universe": row.universe,
                "watchlist_member": row.watchlist_member,
                "acted_on": row.acted_on,
                "timestamp": row.timestamp.isoformat() + "Z",
            }
            for row in rows
        ],
    }


@router.get("/watchlist")
def get_watchlist():
    tickers = load_watchlist()
    return {"tickers": tickers, "count": len(tickers)}


@router.put("/watchlist")
def update_watchlist(payload: WatchlistRequest):
    tickers = add_to_watchlist(payload.tickers) if payload.action == "add" else remove_from_watchlist(payload.tickers)
    return {"tickers": tickers, "count": len(tickers)}


@router.get("/scheduler")
def scheduler_status():
    return get_scheduler_status()
