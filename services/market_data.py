"""
market_data.py
==============
Wraps yfinance with:
  • A 60-second in-memory price cache (prevents rate-limiting on bulk screener runs)
  • get_price_data()    → price + fundamentals dict (used by /price/:ticker)
  • get_technicals()   → RSI, SMAs, volume ratio (used by screener)
  • get_fundamentals_batch() → bulk fetch for nightly jobs
"""
import time
import logging
from typing import Optional
import yfinance as yf
from config import PRICE_CACHE_TTL

logger = logging.getLogger(__name__)

# Simple TTL cache: { TICKER: { "data": {...}, "ts": float } }
_price_cache: dict[str, dict] = {}
_tech_cache:  dict[str, dict] = {}


# ─────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────

def _get_cached(cache: dict, key: str) -> Optional[dict]:
    entry = cache.get(key.upper())
    if entry and (time.time() - entry["ts"]) < PRICE_CACHE_TTL:
        return entry["data"]
    return None


def _set_cache(cache: dict, key: str, data: dict):
    cache[key.upper()] = {"data": data, "ts": time.time()}


def invalidate(ticker: str):
    """Force-expire cache for a ticker (e.g. after an order is filled)."""
    _price_cache.pop(ticker.upper(), None)
    _tech_cache.pop(ticker.upper(), None)


# ─────────────────────────────────────────────────────────────
# Price + fundamentals
# ─────────────────────────────────────────────────────────────

def get_price_data(ticker: str) -> dict:
    """
    Returns price + fundamentals for a single ticker.
    Result is cached for PRICE_CACHE_TTL seconds.
    """
    t = ticker.upper()
    cached = _get_cached(_price_cache, t)
    if cached:
        return cached

    try:
        yft    = yf.Ticker(t)
        info   = yft.info
        hist   = yft.history(period="5d")

        if hist.empty:
            raise ValueError(f"No market data returned for '{t}'")

        close         = hist["Close"]
        current_price = float(close.iloc[-1])
        prev_close    = float(close.iloc[-2]) if len(close) > 1 else current_price
        change_pct    = ((current_price - prev_close) / prev_close) * 100

        data = {
            # Price
            "ticker":        t,
            "price":         round(current_price, 4),
            "prev_close":    round(prev_close, 4),
            "change_pct":    round(change_pct, 4),
            "volume":        info.get("volume"),
            "avg_volume":    info.get("averageVolume"),
            # Range
            "52w_high":      info.get("fiftyTwoWeekHigh"),
            "52w_low":       info.get("fiftyTwoWeekLow"),
            # Valuation
            "market_cap":    info.get("marketCap"),
            "pe":            info.get("trailingPE"),
            "forward_pe":    info.get("forwardPE"),
            "pb":            info.get("priceToBook"),
            "ev_ebitda":     info.get("enterpriseToEbitda"),
            "ev_revenue":    info.get("enterpriseToRevenue"),
            # Dividends
            "div_yield":     info.get("dividendYield"),
            "payout_ratio":  info.get("payoutRatio"),
            "ex_div_date":   str(info.get("exDividendDate", "")),
            # Health
            "fcf":           info.get("freeCashflow"),
            "debt_to_equity":info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "roe":           info.get("returnOnEquity"),
            "roa":           info.get("returnOnAssets"),
            # Growth
            "revenue_growth":    info.get("revenueGrowth"),
            "earnings_growth":   info.get("earningsGrowth"),
            "earnings_quarterly":info.get("earningsQuarterlyGrowth"),
            # Metadata
            "sector":        info.get("sector"),
            "industry":      info.get("industry"),
            "country":       info.get("country"),
            "exchange":      info.get("exchange"),
            "currency":      info.get("currency"),
            "cached_at":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        _set_cache(_price_cache, t, data)
        return data

    except Exception as exc:
        logger.error(f"[market_data] get_price_data({t}) failed: {exc}")
        raise


# ─────────────────────────────────────────────────────────────
# Technical indicators (computed from OHLCV history)
# ─────────────────────────────────────────────────────────────

def get_technicals(ticker: str, period: str = "6mo") -> dict:
    """
    Returns RSI-14, SMA-20/50/200, volume ratio, breakout proximity.
    Cached separately from price data.
    """
    t = ticker.upper()
    cached = _get_cached(_tech_cache, t)
    if cached:
        return cached

    try:
        hist = yf.Ticker(t).history(period=period)
        if hist.empty:
            raise ValueError(f"No history for '{t}'")

        close  = hist["Close"]
        volume = hist["Volume"]
        n      = len(close)

        # ── RSI-14 ────────────────────────────────────────────
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi   = float((100 - (100 / (1 + rs))).iloc[-1])

        # ── Moving averages ───────────────────────────────────
        sma20  = float(close.rolling(20).mean().iloc[-1])
        sma50  = float(close.rolling(50).mean().iloc[-1]) if n >= 50  else None
        sma200 = float(close.rolling(200).mean().iloc[-1]) if n >= 200 else None

        # ── 52-week high (252 trading days) ──────────────────
        w52_high = float(close.rolling(min(252, n)).max().iloc[-1])
        w52_low  = float(close.rolling(min(252, n)).min().iloc[-1])

        # ── Volume ratio vs 20-day average ───────────────────
        avg_vol20  = float(volume.rolling(20).mean().iloc[-1])
        latest_vol = float(volume.iloc[-1])
        vol_ratio  = (latest_vol / avg_vol20) if avg_vol20 > 0 else 1.0

        # ── MACD (12-26-9) ───────────────────────────────────
        ema12      = close.ewm(span=12, adjust=False).mean()
        ema26      = close.ewm(span=26, adjust=False).mean()
        macd_line  = ema12 - ema26
        signal_line= macd_line.ewm(span=9, adjust=False).mean()
        macd_val   = float(macd_line.iloc[-1])
        macd_sig   = float(signal_line.iloc[-1])

        cur = float(close.iloc[-1])

        data = {
            "ticker":               t,
            "current_price":        round(cur, 4),
            "rsi_14":               round(rsi, 2),
            "sma_20":               round(sma20, 4),
            "sma_50":               round(sma50, 4) if sma50 else None,
            "sma_200":              round(sma200, 4) if sma200 else None,
            "52w_high":             round(w52_high, 4),
            "52w_low":              round(w52_low, 4),
            "pct_from_52w_high":    round(((cur - w52_high) / w52_high) * 100, 2),
            "pct_from_52w_low":     round(((cur - w52_low)  / w52_low)  * 100, 2),
            "volume_ratio":         round(vol_ratio, 2),
            "above_sma20":          cur > sma20,
            "above_sma50":          (cur > sma50)  if sma50  else None,
            "above_sma200":         (cur > sma200) if sma200 else None,
            "macd":                 round(macd_val, 4),
            "macd_signal":          round(macd_sig, 4),
            "macd_bullish":         macd_val > macd_sig,
            "cached_at":            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        _set_cache(_tech_cache, t, data)
        return data

    except Exception as exc:
        logger.error(f"[market_data] get_technicals({t}) failed: {exc}")
        raise


# ─────────────────────────────────────────────────────────────
# Batch fetch (for nightly screener jobs)
# ─────────────────────────────────────────────────────────────

def get_fundamentals_batch(tickers: list) -> list:
    """
    Fetches price + technicals for a list of tickers.
    Returns a merged dict per ticker; errors are captured rather than raised.
    """
    results = []
    for t in tickers:
        try:
            price = get_price_data(t)
            tech  = get_technicals(t)
            # tech keys won't overwrite price keys (both have 'ticker', 'current_price' vs 'price')
            merged = {**price, **{k: v for k, v in tech.items() if k not in price}}
            results.append(merged)
        except Exception as exc:
            results.append({"ticker": t.upper(), "error": str(exc)})
    return results
