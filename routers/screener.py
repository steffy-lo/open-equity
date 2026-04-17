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


class ScreenRequest(BaseModel):
    tickers: Optional[list[str]] = None
    signals: Optional[list[SignalIn]] = None


class WatchlistRequest(BaseModel):
    tickers: list[str]
    action: Literal["add", "remove"]


@router.post("/screen")
def screen(payload: ScreenRequest, session: Session = Depends(get_session)):
    if payload.tickers:
        results = run_screen(tickers=payload.tickers, session=session)
        return {
            "screen": {
                "scanned": len(payload.tickers),
                "results": results,
            }
        }

    if payload.signals:
        result = ingest_signals([signal.model_dump() for signal in payload.signals], session)
        return {"ingested": result}

    raise HTTPException(status_code=400, detail="Provide either tickers or signals")


@router.get("/signals")
def signals(
    signal_type: Optional[str] = None,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    return {"signals": get_latest_signals(session, signal_type=signal_type, limit=limit)}


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
