"""
screener.py
===========
Two modes:

  1. LOCAL SCREENING  — run_screen(tickers)
     Computes buy/flag/neutral signals from yfinance technicals + fundamentals.
     This is the fallback when OpenClaw's TradingView Screener skill isn't reachable,
     or can be run on-demand to provide raw data context to ClaWHub skills.

  2. SIGNAL INGESTION — ingest_signals(signals, session)
     OpenClaw calls POST /screen with signals already scored by ClaWHub skills
     (TradingView Screener, Equity Valuation Framework, etc.).
     Server stores them, marks acted_on when an order follows.

Watchlist is stored as watchlist.json (editable via PUT /watchlist or directly).
"""
import json
import logging
import os
from datetime import datetime
from typing import Literal, Optional
from sqlmodel import Session, select, col
from database import Signal, engine
from services.market_data import get_price_data, get_technicals
from config import WATCHLIST_PATH, MIN_SIGNAL_CONFIDENCE

logger = logging.getLogger(__name__)

SignalType = Literal["buy", "flag", "neutral"]

_SIGNAL_ORDER = {"buy": 0, "flag": 1, "neutral": 2, "error": 3}


# ─────────────────────────────────────────────────────────────
# Watchlist helpers
# ─────────────────────────────────────────────────────────────

def load_watchlist() -> list:
    if not os.path.exists(WATCHLIST_PATH):
        return []
    with open(WATCHLIST_PATH) as f:
        data = json.load(f)
    return [t.upper() for t in data.get("tickers", [])]


def save_watchlist(tickers: list):
    with open(WATCHLIST_PATH, "w") as f:
        json.dump(
            {"tickers": tickers, "updated_at": datetime.utcnow().isoformat()},
            f, indent=2
        )


def add_to_watchlist(tickers: list) -> list:
    current = set(load_watchlist())
    current.update(t.upper() for t in tickers)
    updated = sorted(current)
    save_watchlist(updated)
    return updated


def remove_from_watchlist(tickers: list) -> list:
    current = set(load_watchlist())
    for t in tickers:
        current.discard(t.upper())
    updated = sorted(current)
    save_watchlist(updated)
    return updated


# ─────────────────────────────────────────────────────────────
# Confidence scoring — local technical + fundamental model
# ─────────────────────────────────────────────────────────────

def _score(price_data: dict, tech: dict) -> tuple:
    """
    Returns (signal: str, confidence: float, reason: str)

    FLAG gates are evaluated first — one strong flag immediately classifies
    the ticker without a buy score.  Two or more flags → higher confidence flag.

    BUY scoring is additive across four dimensions:
      • Breakout proximity   (max 0.25)
      • Volume surge         (max 0.20)
      • RSI zone             (max 0.20)
      • Trend alignment      (max 0.25)
      • Fundamental bonus    (max 0.10)
                             ─────────
                             total  1.00
    """
    rsi              = tech.get("rsi_14")     or 50.0
    vol_ratio        = tech.get("volume_ratio") or 1.0
    pct_52w          = tech.get("pct_from_52w_high") or -50.0
    above_sma20      = tech.get("above_sma20")
    above_sma50      = tech.get("above_sma50")
    above_sma200     = tech.get("above_sma200")
    macd_bullish     = tech.get("macd_bullish")
    fcf              = price_data.get("fcf")
    div_yield        = price_data.get("div_yield") or 0.0
    debt_to_equity   = price_data.get("debt_to_equity") or 0.0
    pe               = price_data.get("pe")
    rev_growth       = price_data.get("revenue_growth") or 0.0
    earnings_growth  = price_data.get("earnings_growth") or 0.0
    country          = (price_data.get("country") or "").upper()

    # ── FLAG GATES ────────────────────────────────────────────
    flags = []

    if rsi > 78:
        flags.append(f"RSI extremely overbought ({rsi:.1f}) — pullback risk")
    if div_yield > 0.08:
        flags.append(f"Yield {div_yield*100:.1f}% may be a yield trap — check payout ratio")
    if fcf is not None and fcf < 0:
        flags.append("Negative free cash flow — burning through cash")
    if debt_to_equity > 350:
        flags.append(f"Dangerous leverage: D/E {debt_to_equity:.0f}%")
    if above_sma200 is False and pct_52w < -30:
        flags.append(f"Price {abs(pct_52w):.1f}% below 52w high, below SMA200 — downtrend")
    if pe and pe > 80:
        flags.append(f"Extreme valuation: P/E {pe:.1f} — priced for perfection")

    if len(flags) >= 2:
        confidence = min(0.55 + len(flags) * 0.08, 0.95)
        return ("flag", round(confidence, 2), " | ".join(flags))
    elif len(flags) == 1:
        return ("flag", 0.65, flags[0])

    # ── BUY SCORING ───────────────────────────────────────────
    confidence = 0.0
    reasons    = []

    # 1) Breakout proximity (0–0.25)
    if pct_52w >= -1.5:
        confidence += 0.25
        reasons.append(f"At/near 52w high (only {abs(pct_52w):.1f}% away)")
    elif pct_52w >= -5:
        confidence += 0.18
        reasons.append(f"Approaching 52w high ({pct_52w:.1f}%)")
    elif pct_52w >= -10:
        confidence += 0.10
        reasons.append(f"Within 10% of 52w high")

    # 2) Volume surge (0–0.20)
    if vol_ratio >= 2.5:
        confidence += 0.20
        reasons.append(f"Exceptional volume surge ({vol_ratio:.1f}× avg)")
    elif vol_ratio >= 1.8:
        confidence += 0.15
        reasons.append(f"Strong volume surge ({vol_ratio:.1f}× avg)")
    elif vol_ratio >= 1.4:
        confidence += 0.10
        reasons.append(f"Above-avg volume ({vol_ratio:.1f}× avg)")

    # 3) RSI zone (0–0.20)
    if 58 <= rsi <= 70:
        confidence += 0.20
        reasons.append(f"RSI in momentum sweet spot ({rsi:.1f})")
    elif 50 <= rsi < 58:
        confidence += 0.12
        reasons.append(f"RSI building momentum ({rsi:.1f})")
    elif rsi < 35:
        confidence += 0.15
        reasons.append(f"RSI oversold — mean-reversion potential ({rsi:.1f})")
    elif 35 <= rsi < 45:
        confidence += 0.08
        reasons.append(f"RSI near oversold ({rsi:.1f})")

    # 4) Trend alignment (0–0.25)
    if above_sma20 and above_sma50 and above_sma200:
        confidence += 0.25
        reasons.append("Above SMA20/50/200 — full trend alignment")
    elif above_sma20 and above_sma50:
        confidence += 0.16
        reasons.append("Above SMA20 and SMA50")
    elif above_sma20:
        confidence += 0.08
        reasons.append("Above SMA20 only")

    if macd_bullish:
        confidence += 0.05
        reasons.append("MACD bullish crossover")

    # 5) Fundamental bonus (0–0.10)
    if pe and 8 < pe < 22:
        confidence += 0.04
        reasons.append(f"Reasonable P/E ({pe:.1f})")
    if rev_growth > 0.12:
        confidence += 0.03
        reasons.append(f"Revenue growing {rev_growth*100:.1f}%")
    if earnings_growth > 0.10:
        confidence += 0.03
        reasons.append(f"EPS growing {earnings_growth*100:.1f}%")

    # China overlay hint (full analysis via ClaWHub skill)
    if country in ("CN", "HK", "CHINA", "HONG KONG"):
        reasons.append("⚠ CN/HK ticker — route to China Stock Analysis skill for regulatory overlay")

    confidence = min(round(confidence, 2), 1.0)
    signal     = "buy" if confidence >= MIN_SIGNAL_CONFIDENCE else "neutral"
    reason     = " | ".join(reasons) if reasons else "No strong signal detected"

    return (signal, confidence, reason)


# ─────────────────────────────────────────────────────────────
# Local screener run
# ─────────────────────────────────────────────────────────────

def run_screen(tickers: list = None, session: Session = None) -> list:
    """
    Score every ticker in `tickers` (or full watchlist if None).
    Persists signals to DB if session is provided.
    Returns list sorted: buys first → flags → neutral, highest confidence first.
    """
    if not tickers:
        tickers = load_watchlist()

    if not tickers:
        return []

    results = []
    for ticker in tickers:
        t = ticker.upper()
        try:
            price_data = get_price_data(t)
            tech       = get_technicals(t)
            signal, confidence, reason = _score(price_data, tech)

            result = {
                "ticker":            t,
                "signal":            signal,
                "confidence":        confidence,
                "reason":            reason,
                "price":             price_data.get("price"),
                "change_pct":        price_data.get("change_pct"),
                "rsi_14":            tech.get("rsi_14"),
                "volume_ratio":      tech.get("volume_ratio"),
                "pct_from_52w_high": tech.get("pct_from_52w_high"),
                "macd_bullish":      tech.get("macd_bullish"),
                "above_sma200":      tech.get("above_sma200"),
                "pe":                price_data.get("pe"),
                "div_yield":         price_data.get("div_yield"),
                "sector":            price_data.get("sector"),
                "skill_used":        "local_screener",
                "timestamp":         datetime.utcnow().isoformat() + "Z",
            }
            results.append(result)

            if session:
                session.add(Signal(
                    ticker          = t,
                    signal          = signal,
                    confidence      = confidence,
                    reason          = reason,
                    skill_used      = "local_screener",
                    price_at_signal = price_data.get("price"),
                ))

        except Exception as exc:
            logger.warning(f"[screener] Failed to score {t}: {exc}")
            results.append({
                "ticker":    t,
                "signal":    "error",
                "confidence":0.0,
                "reason":    str(exc),
                "skill_used":"local_screener",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            })

    if session:
        session.commit()

    results.sort(key=lambda x: (_SIGNAL_ORDER.get(x["signal"], 3), -x.get("confidence", 0)))
    return results


# ─────────────────────────────────────────────────────────────
# Signal ingestion (from OpenClaw / ClaWHub skills)
# ─────────────────────────────────────────────────────────────

def ingest_signals(signals: list, session: Session) -> dict:
    """
    Store signals pushed by OpenClaw after running ClaWHub skills:
      - TradingView Screener
      - Equity Valuation Framework
      - Fundamental Stock Analysis
      - China Stock Analysis

    Each signal dict must have: ticker, signal, confidence, reason, skill_used.
    Optional: price_at_signal.
    """
    stored = 0
    errors = []

    for s in signals:
        try:
            sig = Signal(
                ticker          = s["ticker"].upper(),
                signal          = s["signal"],
                confidence      = float(s["confidence"]),
                reason          = s.get("reason", ""),
                skill_used      = s.get("skill_used", "openclaw"),
                price_at_signal = s.get("price_at_signal"),
            )
            session.add(sig)
            stored += 1
        except Exception as exc:
            errors.append({"ticker": s.get("ticker"), "error": str(exc)})

    session.commit()

    return {
        "ingested": stored,
        "errors":   errors,
        "timestamp":datetime.utcnow().isoformat() + "Z",
    }


# ─────────────────────────────────────────────────────────────
# Signal retrieval helpers
# ─────────────────────────────────────────────────────────────

def get_latest_signals(session: Session, signal_type: str = None, limit: int = 50) -> list:
    """
    Return the most recent signal per ticker (deduped), optionally filtered by type.
    """
    query = select(Signal).order_by(col(Signal.timestamp).desc())
    if signal_type:
        query = query.where(Signal.signal == signal_type)

    all_signals = session.exec(query).all()

    # Dedupe: keep only the most recent signal per ticker
    seen    = set()
    results = []
    for sig in all_signals:
        if sig.ticker not in seen:
            seen.add(sig.ticker)
            results.append({
                "id":               sig.id,
                "ticker":           sig.ticker,
                "signal":           sig.signal,
                "confidence":       sig.confidence,
                "reason":           sig.reason,
                "skill_used":       sig.skill_used,
                "price_at_signal":  sig.price_at_signal,
                "acted_on":         sig.acted_on,
                "timestamp":        sig.timestamp.isoformat() + "Z",
            })
        if len(results) >= limit:
            break

    return results


def mark_signal_acted_on(session: Session, ticker: str):
    """Called after an order is placed so OpenClaw can track acted-on signals."""
    sig = session.exec(
        select(Signal)
        .where(Signal.ticker == ticker.upper())
        .order_by(col(Signal.timestamp).desc())
    ).first()
    if sig:
        sig.acted_on = True
        session.commit()
