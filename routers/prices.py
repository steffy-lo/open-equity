from fastapi import APIRouter, Body, HTTPException

from services.market_data import get_fundamentals_batch, get_price_data, get_technicals


router = APIRouter(tags=["Prices"])


@router.get("/price/{ticker}")
def price(ticker: str):
    try:
        return get_price_data(ticker)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/technicals/{ticker}")
def technicals(ticker: str):
    try:
        return get_technicals(ticker)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/price/batch")
def price_batch(tickers: list[str] = Body(...)):
    if len(tickers) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 tickers per request")
    return get_fundamentals_batch(tickers)
