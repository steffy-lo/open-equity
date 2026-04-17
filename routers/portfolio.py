from fastapi import APIRouter, Depends
from sqlmodel import Session

from database import get_session
from services.portfolio_engine import get_benchmark_alpha, get_portfolio_state


router = APIRouter(tags=["Portfolio"])


@router.get("/portfolio")
def portfolio(session: Session = Depends(get_session)):
    return get_portfolio_state(session)


@router.get("/benchmark")
def benchmark(session: Session = Depends(get_session)):
    return get_benchmark_alpha(session)
