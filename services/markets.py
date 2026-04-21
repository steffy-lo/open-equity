from typing import Literal


Market = Literal["US", "HK"]


def infer_market(ticker: str) -> Market:
    ticker_upper = (ticker or "").upper().strip()
    return "HK" if ticker_upper.endswith(".HK") else "US"


def filter_market_tickers(tickers: list[str], market: Market) -> list[str]:
    seen: set[str] = set()
    filtered: list[str] = []
    for ticker in tickers:
        ticker_upper = ticker.upper().strip()
        if not ticker_upper or ticker_upper in seen:
            continue
        if infer_market(ticker_upper) != market:
            continue
        seen.add(ticker_upper)
        filtered.append(ticker_upper)
    return filtered


def market_currency_prefix(market: Market, currency: str | None = None) -> str:
    if market == "HK" or (currency or "").upper() == "HKD":
        return "HK$"
    return "$"
