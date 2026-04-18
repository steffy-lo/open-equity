from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, col, select

from database import Trade, get_session


router = APIRouter(tags=["History"])


@router.get("/history")
def history(
    account_name: Optional[str] = None,
    ticker: Optional[str] = None,
    side: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
):
    query = select(Trade)
    if account_name:
        query = query.where(Trade.account_name == account_name)
    if ticker:
        query = query.where(Trade.ticker == ticker.upper())
    if side:
        query = query.where(Trade.side == side.lower())
    query = query.order_by(col(Trade.timestamp).desc()).limit(limit)

    trades = session.exec(query).all()
    items = [
        {
            "order_id": trade.order_id,
            "account_name": trade.account_name,
            "ticker": trade.ticker,
            "side": trade.side,
            "qty": trade.qty,
            "fill_price": trade.fill_price,
            "fee": trade.fee,
            "note": trade.note,
            "skill_used": trade.skill_used,
            "timestamp": trade.timestamp.isoformat() + "Z",
        }
        for trade in trades
    ]

    return {"count": len(items), "trades": items}
