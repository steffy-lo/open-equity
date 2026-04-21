import re
from typing import Literal


Market = Literal["US", "HK"]
_HK_TICKER_RE = re.compile(r"^(?:0|\d)[0-9]{0,4}(?:\.HK)?$")


def normalize_ticker(ticker: str, market: Market | None = None) -> str:
    raw = (ticker or "").upper().strip()
    if not raw:
        return ""

    collapsed = raw.replace(" ", "").replace("/", ".").replace("-", ".")
    inferred_market = market or _guess_market_from_raw(collapsed)

    if inferred_market == "HK":
        if collapsed.endswith(".HK"):
            code = collapsed[:-3]
        else:
            code = collapsed.replace("HK", "")
        code = code.replace(".", "")
        if code.isdigit() and 1 <= len(code) <= 5:
            return f"{code.zfill(4)}.HK"

    return collapsed



def normalize_market_tickers(tickers: list[str], market: Market) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for ticker in tickers:
        candidate = normalize_ticker(ticker, market=market)
        if not candidate or candidate in seen:
            continue
        if infer_market(candidate) != market:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized



def infer_market(ticker: str) -> Market:
    ticker_upper = (ticker or "").upper().strip()
    normalized = normalize_ticker(ticker_upper, market=_guess_market_from_raw(ticker_upper)) if ticker_upper else ""
    if normalized.endswith(".HK"):
        return "HK"
    return "US"



def filter_market_tickers(tickers: list[str], market: Market) -> list[str]:
    return normalize_market_tickers(tickers, market)



def _guess_market_from_raw(ticker: str) -> Market:
    ticker_upper = (ticker or "").upper().strip()
    if ticker_upper.endswith(".HK"):
        return "HK"
    if _HK_TICKER_RE.match(ticker_upper) and any(ch.isdigit() for ch in ticker_upper):
        return "HK"
    return "US"



def market_currency_prefix(market: Market, currency: str | None = None) -> str:
    if market == "HK" or (currency or "").upper() == "HKD":
        return "HK$"
    return "$"
