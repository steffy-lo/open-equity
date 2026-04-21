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

Watchlists are stored per market and can still fall back to the legacy shared
watchlist.json file for backward compatibility.
"""
import json
import logging
import os
from datetime import datetime
from typing import Literal, Optional
from sqlmodel import Session, select, col
from database import Signal, engine
from services.market_data import get_price_data, get_technicals
from config import (
    BASIC_MIN_SIGNAL_CONFIDENCE,
    HK_WATCHLIST_PATH,
    MOMENTUM_FLAG_RSI_MAX,
    MOMENTUM_MIN_SIGNAL_CONFIDENCE,
    US_WATCHLIST_PATH,
    WATCHLIST_PATH,
)
from services.momentum_discovery import discover_momentum_candidates
from services.markets import Market, infer_market, normalize_market_tickers

logger = logging.getLogger(__name__)

SignalType = Literal["buy", "flag", "neutral"]

_SIGNAL_ORDER = {"buy": 0, "flag": 1, "neutral": 2, "error": 3}


def _normalize_tickers(tickers: list | None) -> list:
    if not tickers:
        return []

    seen = set()
    normalized = []
    for ticker in tickers:
        t = ticker.upper().strip()
        if t and t not in seen:
            seen.add(t)
            normalized.append(t)
    return normalized


def _resolve_watchlist_membership(tickers: list) -> dict[str, bool]:
    watchlist = set(load_watchlist())
    return {ticker: ticker in watchlist for ticker in tickers}


def _resolve_screen_scope(
    tickers: list,
    screen_scope: str | None = None,
    use_watchlist: bool = False,
) -> str:
    if screen_scope:
        return screen_scope
    if use_watchlist:
        return "watchlist"

    watchlist = load_watchlist()
    if tickers and tickers == watchlist:
        return "watchlist"
    if tickers:
        return "custom_universe"
    return "watchlist"


# ─────────────────────────────────────────────────────────────
# Watchlist helpers
# ─────────────────────────────────────────────────────────────

def _watchlist_path(market: Market) -> str:
    return HK_WATCHLIST_PATH if market == "HK" else US_WATCHLIST_PATH


def _read_watchlist_file(path: str, market: Market | None = None) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    tickers = [str(t).upper() for t in data.get("tickers", [])]
    if market is None:
        seen: set[str] = set()
        merged: list[str] = []
        for ticker in tickers:
            if ticker and ticker not in seen:
                seen.add(ticker)
                merged.append(ticker)
        return merged
    return normalize_market_tickers(tickers, market)


def load_watchlist(market: Market | None = None) -> list:
    if market is None:
        merged = (
            _read_watchlist_file(US_WATCHLIST_PATH)
            + _read_watchlist_file(HK_WATCHLIST_PATH)
            + _read_watchlist_file(WATCHLIST_PATH)
        )
        seen: set[str] = set()
        result: list[str] = []
        for ticker in merged:
            if ticker and ticker not in seen:
                seen.add(ticker)
                result.append(ticker)
        return result

    specific = _read_watchlist_file(_watchlist_path(market), market)
    if specific:
        return specific
    return _read_watchlist_file(WATCHLIST_PATH, market)


def save_watchlist(tickers: list, market: Market):
    path = _watchlist_path(market)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    normalized = normalize_market_tickers([str(t) for t in tickers], market)
    with open(path, "w") as f:
        json.dump(
            {"tickers": normalized, "market": market, "updated_at": datetime.utcnow().isoformat()},
            f, indent=2
        )


def add_to_watchlist(tickers: list, market: Market | None = None) -> list:
    if market is not None:
        current = set(load_watchlist(market))
        current.update(normalize_market_tickers([str(t) for t in tickers], market))
        updated = sorted(current)
        save_watchlist(updated, market)
        return updated

    updated: list[str] = []
    for target_market in ("US", "HK"):
        market_tickers = [str(t) for t in tickers if infer_market(str(t)) == target_market]
        if not market_tickers:
            continue
        updated.extend(add_to_watchlist(market_tickers, market=target_market))
    return load_watchlist()


def remove_from_watchlist(tickers: list, market: Market | None = None) -> list:
    if market is not None:
        current = set(load_watchlist(market))
        for ticker in normalize_market_tickers([str(t) for t in tickers], market):
            current.discard(ticker)
        updated = sorted(current)
        save_watchlist(updated, market)
        return updated

    for target_market in ("US", "HK"):
        market_tickers = [str(t) for t in tickers if infer_market(str(t)) == target_market]
        if market_tickers:
            remove_from_watchlist(market_tickers, market=target_market)
    return load_watchlist()


# ─────────────────────────────────────────────────────────────
# Confidence scoring — local technical + fundamental model
# ─────────────────────────────────────────────────────────────

def _score(price_data: dict, tech: dict) -> dict:
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

    hard_flags = []
    soft_flags = []

    if div_yield > 0.08:
        hard_flags.append(f"Yield {div_yield*100:.1f}% may be a yield trap — check payout ratio")
    if debt_to_equity > 500:
        hard_flags.append(f"Dangerous leverage: D/E {debt_to_equity:.0f}%")
    elif debt_to_equity > 350:
        soft_flags.append(f"High leverage: D/E {debt_to_equity:.0f}%")
    if above_sma200 is False and pct_52w < -35:
        hard_flags.append(f"Price {abs(pct_52w):.1f}% below 52w high, below SMA200 — downtrend")
    if pe and pe > 120:
        hard_flags.append(f"Extreme valuation: P/E {pe:.1f} — priced for perfection")
    elif pe and pe > 80:
        soft_flags.append(f"Rich valuation: P/E {pe:.1f}")
    if fcf is not None and fcf < 0:
        soft_flags.append("Negative free cash flow — burning through cash")
    if rsi > MOMENTUM_FLAG_RSI_MAX:
        soft_flags.append(f"RSI extremely overbought ({rsi:.1f}) — pullback risk")

    flag_reasons = hard_flags + soft_flags
    if hard_flags or len(soft_flags) >= 2:
        flag_confidence = min(0.6 + 0.07 * len(flag_reasons), 0.95)
        return {
            "signal": "flag",
            "confidence": round(flag_confidence, 2),
            "reason": " | ".join(flag_reasons),
            "buy_profile": None,
            "basic_confidence": None,
            "momentum_confidence": None,
        }

    basic_confidence = 0.0
    basic_reasons = []

    if pct_52w >= -1.5:
        basic_confidence += 0.25
        basic_reasons.append(f"At/near 52w high (only {abs(pct_52w):.1f}% away)")
    elif pct_52w >= -5:
        basic_confidence += 0.18
        basic_reasons.append(f"Approaching 52w high ({pct_52w:.1f}%)")
    elif pct_52w >= -10:
        basic_confidence += 0.10
        basic_reasons.append("Within 10% of 52w high")

    if vol_ratio >= 2.5:
        basic_confidence += 0.20
        basic_reasons.append(f"Exceptional volume surge ({vol_ratio:.1f}× avg)")
    elif vol_ratio >= 1.8:
        basic_confidence += 0.15
        basic_reasons.append(f"Strong volume surge ({vol_ratio:.1f}× avg)")
    elif vol_ratio >= 1.4:
        basic_confidence += 0.10
        basic_reasons.append(f"Above-avg volume ({vol_ratio:.1f}× avg)")

    if 58 <= rsi <= 70:
        basic_confidence += 0.20
        basic_reasons.append(f"RSI in momentum sweet spot ({rsi:.1f})")
    elif 50 <= rsi < 58:
        basic_confidence += 0.12
        basic_reasons.append(f"RSI building momentum ({rsi:.1f})")
    elif rsi < 35:
        basic_confidence += 0.15
        basic_reasons.append(f"RSI oversold — mean-reversion potential ({rsi:.1f})")
    elif 35 <= rsi < 45:
        basic_confidence += 0.08
        basic_reasons.append(f"RSI near oversold ({rsi:.1f})")

    if above_sma20 and above_sma50 and above_sma200:
        basic_confidence += 0.25
        basic_reasons.append("Above SMA20/50/200 — full trend alignment")
    elif above_sma20 and above_sma50:
        basic_confidence += 0.16
        basic_reasons.append("Above SMA20 and SMA50")
    elif above_sma20:
        basic_confidence += 0.08
        basic_reasons.append("Above SMA20 only")

    if macd_bullish:
        basic_confidence += 0.05
        basic_reasons.append("MACD bullish crossover")

    if pe and 8 < pe < 22:
        basic_confidence += 0.04
        basic_reasons.append(f"Reasonable P/E ({pe:.1f})")
    if rev_growth > 0.12:
        basic_confidence += 0.03
        basic_reasons.append(f"Revenue growing {rev_growth*100:.1f}%")
    if earnings_growth > 0.10:
        basic_confidence += 0.03
        basic_reasons.append(f"EPS growing {earnings_growth*100:.1f}%")

    momentum_confidence = 0.0
    momentum_reasons = []

    if pct_52w >= 0:
        momentum_confidence += 0.28
        momentum_reasons.append("Breaking into or sitting at fresh highs")
    elif pct_52w >= -3:
        momentum_confidence += 0.24
        momentum_reasons.append(f"Within 3% of 52w high ({pct_52w:.1f}%)")
    elif pct_52w >= -8:
        momentum_confidence += 0.16
        momentum_reasons.append("Still close enough to leadership zone")

    if vol_ratio >= 2.0:
        momentum_confidence += 0.22
        momentum_reasons.append(f"Momentum volume confirmation ({vol_ratio:.1f}× avg)")
    elif vol_ratio >= 1.5:
        momentum_confidence += 0.16
        momentum_reasons.append(f"Above-average breakout volume ({vol_ratio:.1f}× avg)")
    elif vol_ratio >= 1.1:
        momentum_confidence += 0.08
        momentum_reasons.append("Volume healthy enough for a momentum continuation")

    if 60 <= rsi <= 78:
        momentum_confidence += 0.18
        momentum_reasons.append(f"RSI supportive for momentum continuation ({rsi:.1f})")
    elif 55 <= rsi < 60:
        momentum_confidence += 0.10
        momentum_reasons.append(f"RSI building toward breakout territory ({rsi:.1f})")
    elif 78 < rsi <= MOMENTUM_FLAG_RSI_MAX:
        momentum_confidence += 0.08
        momentum_reasons.append(f"RSI hot but still acceptable for breakout momentum ({rsi:.1f})")

    if above_sma50 and above_sma200:
        momentum_confidence += 0.22
        momentum_reasons.append("Above SMA50 and SMA200 — momentum trend intact")
    elif above_sma20 and above_sma50:
        momentum_confidence += 0.14
        momentum_reasons.append("Above SMA20 and SMA50 — short-term trend intact")
    elif above_sma20:
        momentum_confidence += 0.06
        momentum_reasons.append("Above SMA20")

    if macd_bullish:
        momentum_confidence += 0.05
        momentum_reasons.append("MACD bullish")
    if rev_growth > 0.10:
        momentum_confidence += 0.03
        momentum_reasons.append(f"Revenue growth confirms tape strength ({rev_growth*100:.1f}%)")
    if earnings_growth > 0.10:
        momentum_confidence += 0.02
        momentum_reasons.append(f"EPS growth confirms tape strength ({earnings_growth*100:.1f}%)")

    if country in ("CN", "HK", "CHINA", "HONG KONG"):
        basic_reasons.append("⚠ CN/HK ticker — route to China Stock Analysis skill for regulatory overlay")
        momentum_reasons.append("⚠ CN/HK ticker — route to China Stock Analysis skill for regulatory overlay")

    basic_confidence = min(round(basic_confidence, 2), 1.0)
    momentum_confidence = min(round(momentum_confidence, 2), 1.0)
    basic_pass = basic_confidence >= BASIC_MIN_SIGNAL_CONFIDENCE
    momentum_pass = momentum_confidence >= MOMENTUM_MIN_SIGNAL_CONFIDENCE

    if basic_pass and momentum_pass:
        buy_profile = "both"
    elif basic_pass:
        buy_profile = "basic"
    elif momentum_pass:
        buy_profile = "momentum"
    else:
        buy_profile = None

    confidence = max(basic_confidence, momentum_confidence)

    if buy_profile:
        triggered_reasons = []
        if buy_profile in ("basic", "both"):
            triggered_reasons.append(f"Basic profile passed ({basic_confidence:.2f})")
            triggered_reasons.extend(basic_reasons[:4])
        if buy_profile in ("momentum", "both"):
            triggered_reasons.append(f"Momentum profile passed ({momentum_confidence:.2f})")
            triggered_reasons.extend(momentum_reasons[:4])
        if soft_flags:
            triggered_reasons.append("Soft risks: " + " | ".join(soft_flags))
        return {
            "signal": "buy",
            "confidence": confidence,
            "reason": " | ".join(triggered_reasons),
            "buy_profile": buy_profile,
            "basic_confidence": basic_confidence,
            "momentum_confidence": momentum_confidence,
        }

    neutral_reasons = []
    if basic_reasons:
        neutral_reasons.append(f"Basic profile {basic_confidence:.2f}: " + " | ".join(basic_reasons[:4]))
    if momentum_reasons:
        neutral_reasons.append(f"Momentum profile {momentum_confidence:.2f}: " + " | ".join(momentum_reasons[:4]))
    if soft_flags:
        neutral_reasons.append("Soft risks: " + " | ".join(soft_flags))

    return {
        "signal": "neutral",
        "confidence": confidence,
        "reason": " | ".join(neutral_reasons) if neutral_reasons else "No strong signal detected",
        "buy_profile": None,
        "basic_confidence": basic_confidence,
        "momentum_confidence": momentum_confidence,
    }


# ─────────────────────────────────────────────────────────────
# Local screener run
# ─────────────────────────────────────────────────────────────

def run_screen(
    tickers: list = None,
    session: Session = None,
    screen_scope: str | None = None,
    screen_label: str | None = None,
    universe: str | None = None,
    use_watchlist: bool = False,
    market: Market | None = None,
) -> list:
    """
    Score every ticker in `tickers` (or full watchlist if None).
    Persists signals to DB if session is provided.
    Returns list sorted: buys first → flags → neutral, highest confidence first.
    """
    normalized_tickers = _normalize_tickers(tickers)
    if not normalized_tickers:
        normalized_tickers = load_watchlist(market=market)
        use_watchlist = True

    if not normalized_tickers:
        return []

    resolved_scope = _resolve_screen_scope(
        tickers=normalized_tickers,
        screen_scope=screen_scope,
        use_watchlist=use_watchlist,
    )
    watchlist_membership = _resolve_watchlist_membership(normalized_tickers)

    results = []
    for t in normalized_tickers:
        try:
            price_data = get_price_data(t)
            tech       = get_technicals(t)
            score = _score(price_data, tech)

            result = {
                "ticker":            t,
                "signal":            score["signal"],
                "confidence":        score["confidence"],
                "reason":            score["reason"],
                "buy_profile":       score.get("buy_profile"),
                "basic_confidence":  score.get("basic_confidence"),
                "momentum_confidence": score.get("momentum_confidence"),
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
                "screen_scope":      resolved_scope,
                "screen_label":      screen_label,
                "universe":          universe,
                "watchlist_member":  watchlist_membership.get(t, False),
                "timestamp":         datetime.utcnow().isoformat() + "Z",
            }
            results.append(result)

            if session:
                session.add(Signal(
                    ticker          = t,
                    signal          = score["signal"],
                    confidence      = score["confidence"],
                    reason          = score["reason"],
                    skill_used      = "local_screener",
                    price_at_signal = price_data.get("price"),
                    screen_scope    = resolved_scope,
                    screen_label    = screen_label,
                    universe        = universe,
                    watchlist_member= watchlist_membership.get(t, False),
                    buy_profile     = score.get("buy_profile"),
                    basic_confidence= score.get("basic_confidence"),
                    momentum_confidence= score.get("momentum_confidence"),
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


def run_momentum_discovery_screen(
    *,
    market: Market,
    session: Session | None = None,
    exclude_tickers: list[str] | None = None,
    screen_label: str | None = None,
) -> list:
    excluded = normalize_market_tickers((exclude_tickers or []) + load_watchlist(market=market), market)
    candidates = discover_momentum_candidates(market, exclude_tickers=excluded)
    tickers = [candidate["ticker"] for candidate in candidates if candidate.get("ticker")]
    if not tickers:
        return []

    return run_screen(
        tickers=tickers,
        session=session,
        screen_scope="custom_universe",
        screen_label=screen_label or f"{market.lower()}-momentum-discovery",
        universe=f"{market} momentum candidates outside watchlist",
        use_watchlist=False,
        market=market,
    )


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
    normalized_tickers = _normalize_tickers([signal.get("ticker", "") for signal in signals])
    watchlist_membership = _resolve_watchlist_membership(normalized_tickers)

    stored = 0
    errors = []

    for s in signals:
        try:
            ticker = s["ticker"].upper()
            sig = Signal(
                ticker          = ticker,
                signal          = s["signal"],
                confidence      = float(s["confidence"]),
                reason          = s.get("reason", ""),
                skill_used      = s.get("skill_used", "openclaw"),
                price_at_signal = s.get("price_at_signal"),
                screen_scope    = s.get("screen_scope") or "watchlist",
                screen_label    = s.get("screen_label"),
                universe        = s.get("universe"),
                watchlist_member= s.get("watchlist_member", watchlist_membership.get(ticker, False)),
                buy_profile     = s.get("buy_profile"),
                basic_confidence= s.get("basic_confidence"),
                momentum_confidence= s.get("momentum_confidence"),
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

def get_latest_signals(
    session: Session,
    signal_type: str = None,
    limit: int = 50,
    screen_scope: str = None,
    screen_label: str = None,
) -> list:
    """
    Return the most recent signal per ticker (deduped), optionally filtered by type.
    """
    query = select(Signal).order_by(col(Signal.timestamp).desc())
    if signal_type:
        query = query.where(Signal.signal == signal_type)
    if screen_scope:
        query = query.where(Signal.screen_scope == screen_scope)
    if screen_label:
        query = query.where(Signal.screen_label == screen_label)

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
                "screen_scope":     sig.screen_scope,
                "screen_label":     sig.screen_label,
                "universe":         sig.universe,
                "watchlist_member": sig.watchlist_member,
                "buy_profile":      sig.buy_profile,
                "basic_confidence": sig.basic_confidence,
                "momentum_confidence": sig.momentum_confidence,
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
