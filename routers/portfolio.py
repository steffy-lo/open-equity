from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from database import get_session
from services.portfolio_engine import get_benchmark_alpha, get_portfolio_state


router = APIRouter(tags=["Portfolio"])


@router.get("/portfolio")
def portfolio(session: Session = Depends(get_session)):
    return get_portfolio_state(session, account_name="default")


@router.get("/portfolio/{account_name}")
def portfolio_for_account(account_name: str, session: Session = Depends(get_session)):
    return get_portfolio_state(session, account_name=account_name)


@router.get("/benchmark")
def benchmark(session: Session = Depends(get_session)):
    return get_benchmark_alpha(session, account_name="default")


@router.get("/benchmark/{account_name}")
def benchmark_for_account(account_name: str, session: Session = Depends(get_session)):
    return get_benchmark_alpha(session, account_name=account_name)
