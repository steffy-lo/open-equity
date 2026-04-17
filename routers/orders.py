from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from database import get_session
from services.portfolio_engine import execute_order
from services.screener import mark_signal_acted_on


router = APIRouter(tags=["Orders"])


class OrderRequest(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    qty: float = Field(gt=0)
    note: Optional[str] = None
    skill_used: Optional[str] = None


@router.post("/order")
def submit_order(payload: OrderRequest, session: Session = Depends(get_session)):
    try:
        result = execute_order(
            session=session,
            ticker=payload.ticker,
            side=payload.side,
            qty=payload.qty,
            note=payload.note,
            skill_used=payload.skill_used,
        )
        mark_signal_acted_on(session, payload.ticker)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
