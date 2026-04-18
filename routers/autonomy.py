from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from database import get_session
from services.autonomy import (
    format_run_summary,
    get_autonomy_config,
    list_derivative_ideas,
    list_pipeline_runs,
    list_trade_plans,
    list_trade_proposals,
    run_autonomous_cycle,
)


router = APIRouter(tags=["Autonomy"])


class AutonomyRunRequest(BaseModel):
    mode: Optional[str] = None
    account_name: Optional[str] = None


@router.get("/autonomy/config")
def autonomy_config():
    return get_autonomy_config()


@router.post("/autonomy/run")
def autonomy_run(payload: AutonomyRunRequest, session: Session = Depends(get_session)):
    try:
        return run_autonomous_cycle(session=session, mode=payload.mode, account_name=payload.account_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/autonomy/run/summary")
def autonomy_run_summary(payload: AutonomyRunRequest, session: Session = Depends(get_session)):
    try:
        result = run_autonomous_cycle(session=session, mode=payload.mode, account_name=payload.account_name)
        return {"result": result, "summary_text": format_run_summary(result)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/autonomy/runs")
def autonomy_runs(limit: int = 20, account_name: Optional[str] = None, session: Session = Depends(get_session)):
    return {"runs": list_pipeline_runs(session=session, limit=limit, account_name=account_name)}


@router.get("/autonomy/proposals")
def autonomy_proposals(limit: int = 50, account_name: Optional[str] = None, session: Session = Depends(get_session)):
    return {"proposals": list_trade_proposals(session=session, limit=limit, account_name=account_name)}


@router.get("/autonomy/trade-plans")
def autonomy_trade_plans(limit: int = 50, account_name: Optional[str] = None, session: Session = Depends(get_session)):
    return {"trade_plans": list_trade_plans(session=session, limit=limit, account_name=account_name)}


@router.get("/autonomy/derivative-ideas")
def autonomy_derivative_ideas(limit: int = 50, account_name: Optional[str] = None, session: Session = Depends(get_session)):
    return {"derivative_ideas": list_derivative_ideas(session=session, limit=limit, account_name=account_name)}
