"""
services/execution_agent.py
============================
Autonomous trade execution layer — pure rule-based, no AI calls.

OpenClaw surfaces signals via POST /screen (as before).
This agent converts those signals into orders and manages exits.

Runs twice a day on market days:
  ENTRY PASS  9:35am ET — converts high-confidence unacted buy signals → orders
  EXIT PASS   3:45pm ET — checks open positions for stop-loss / take-profit

Risk parameters (all configurable via .env):
  EXEC_MAX_POSITION_PCT   max single position as % of portfolio (default 10%)
  EXEC_MAX_EXPOSURE_PCT   stop new entries above this total equity % (default 80%)
  EXEC_STOP_LOSS_PCT      sell if position falls this much below avg cost (default 8%)
  EXEC_TAKE_PROFIT_PCT    sell if position rises this much above avg cost (default 20%)
  EXEC_MAX_TRADES_PER_DAY max new buy orders per calendar day (default 3)
  EXEC_MIN_SIGNAL_CONFIDENCE minimum confidence to act on a stored buy signal (default 0.58)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import select, col

from config import (
    EXEC_MAX_POSITION_PCT,
    EXEC_MAX_EXPOSURE_PCT,
    EXEC_MIN_SIGNAL_CONFIDENCE,
    EXEC_STOP_LOSS_PCT,
    EXEC_TAKE_PROFIT_PCT,
    EXEC_MAX_TRADES_PER_DAY,
    TRADE_UPDATE_ACCOUNT_ID,
    TRADE_UPDATE_CHANNEL,
    TRADE_UPDATE_TOPIC,
)
from database import Signal, Trade, Position, session_scope
from services.market_data import get_price_data
from services.markets import Market, infer_market, market_currency_prefix
from services.openclaw_notify import send_openclaw_message
from services.portfolio_engine import execute_order, get_portfolio_state
from services.research_agent import get_latest_brief

logger = logging.getLogger(__name__)

def _send_trade_update(message: str) -> dict:
    # Delivery is optional so the example config can stay generic.
    if not TRADE_UPDATE_TOPIC:
        return {"ok": False, "skipped": True, "reason": "trade_updates_disabled"}

    result = send_openclaw_message(
        account_id=TRADE_UPDATE_ACCOUNT_ID,
        channel=TRADE_UPDATE_CHANNEL,
        target=TRADE_UPDATE_TOPIC,
        message=message,
        wait=False,
    )
    if result.get("ok"):
        return {"ok": True, "target": TRADE_UPDATE_TOPIC}

    logger.warning(f"[exec_agent] Trade update delivery failed: {result.get('error')}")
    return {"ok": False, "target": TRADE_UPDATE_TOPIC, "error": result.get("error")}


def _format_trade_update(*, side: str, ticker: str, qty: int | float, fill_price: float, note: str, portfolio: dict, position: Optional[dict] = None, market: Market = "US", currency: str | None = None) -> str:
    side_label = "BUY" if side == "buy" else "SELL"
    icon = "🟢" if side == "buy" else "🔴"

    money_prefix = market_currency_prefix(market, currency)

    lines = [
        f"{icon} Trade update",
        "",
        f"• Market: {market}",
        f"• Ticker: {ticker}",
        f"• Side: {side_label}",
        f"• Size: {qty}",
        f"• Execution price: {money_prefix}{fill_price:.2f}",
    ]

    clean_note = (note or "").strip()
    if clean_note:
        lines.append(f"• Reason: {clean_note[:220]}")

    cash = portfolio.get("cash")
    total_value = portfolio.get("total_value")
    if cash is not None:
        context = f"• Cash remaining: ${cash:,.2f}"
        if total_value is not None and total_value > 0:
            context += f" | Portfolio: ${total_value:,.2f}"
        lines.append(context)

    if position:
        lines.append(
            f"• Position: {position.get('ticker')} {position.get('qty')} shares @ avg {money_prefix}{position.get('avg_cost', 0):.2f}"
        )
    elif side == "sell":
        lines.append(f"• Position: {ticker} closed or reduced")

    return "\n".join(lines)


def _notify_trade(*, side: str, ticker: str, qty: int | float, fill_price: float, note: str, session) -> dict:
    portfolio = get_portfolio_state(session)
    position = next((p for p in portfolio.get("positions", []) if p.get("ticker") == ticker), None)
    price_data = get_price_data(ticker)
    market = infer_market(ticker)
    message = _format_trade_update(
        side=side,
        ticker=ticker,
        qty=qty,
        fill_price=fill_price,
        note=note,
        portfolio=portfolio,
        position=position,
        market=market,
        currency=price_data.get("currency"),
    )
    return _send_trade_update(message)


def _dedupe_signals_by_ticker(signals: list[Signal]) -> list[Signal]:
    """Keep the highest-confidence, most recent signal per ticker."""
    best_by_ticker: dict[str, Signal] = {}
    for signal in signals:
        existing = best_by_ticker.get(signal.ticker)
        if existing is None:
            best_by_ticker[signal.ticker] = signal
            continue
        if signal.confidence > existing.confidence:
            best_by_ticker[signal.ticker] = signal
            continue
        if signal.confidence == existing.confidence and signal.timestamp > existing.timestamp:
            best_by_ticker[signal.ticker] = signal
    return list(best_by_ticker.values())


# ─────────────────────────────────────────────────────────────
# Entry pass
# ─────────────────────────────────────────────────────────────

def run_entry_pass(market: Market = "US") -> dict:
    """
    Read unacted buy signals for a market, apply risk rules, and place orders.
    Returns a summary dict with trades placed and any skips.
    """
    placed  = []
    skipped = []

    with session_scope() as session:

        # ── Portfolio snapshot ─────────────────────────────────
        portfolio     = get_portfolio_state(session)
        total_value   = portfolio["total_value"]
        cash          = portfolio["cash"]
        equity_value  = portfolio["equity"]
        exposure_pct  = equity_value / total_value if total_value > 0 else 0

        if exposure_pct >= EXEC_MAX_EXPOSURE_PCT:
            logger.info(
                f"[exec_agent] Max exposure reached "
                f"({exposure_pct:.1%} ≥ {EXEC_MAX_EXPOSURE_PCT:.1%}). Skipping entry pass."
            )
            return {"placed": [], "skipped": [], "reason": "max_exposure_reached"}

        # ── Daily trade count ──────────────────────────────────
        today_start  = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        trades_today = session.exec(
            select(Trade)
            .where(Trade.side == "buy")
            .where(Trade.timestamp >= today_start)
        ).all()

        if len(trades_today) >= EXEC_MAX_TRADES_PER_DAY:
            logger.info(f"[exec_agent] Daily limit hit ({EXEC_MAX_TRADES_PER_DAY}). Skipping.")
            return {"placed": [], "skipped": [], "reason": "daily_limit_reached"}

        remaining_slots = EXEC_MAX_TRADES_PER_DAY - len(trades_today)

        # ── Research brief for context ─────────────────────────
        brief           = get_latest_brief(market=market) or {}
        earnings_blackout = set(brief.get("earnings_watch", []))
        avoid_sectors     = set(brief.get("avoid_sectors", []))
        risk_posture      = brief.get("risk_posture", "moderate")

        # ── Unacted buy signals (last 2 days) ──────────────────
        cutoff  = datetime.now(timezone.utc) - timedelta(days=2)
        signals = session.exec(
            select(Signal)
            .where(Signal.signal == "buy")
            .where(Signal.acted_on == False)        # noqa: E712
            .where(Signal.confidence >= EXEC_MIN_SIGNAL_CONFIDENCE)
            .where(Signal.timestamp >= cutoff)
            .order_by(col(Signal.confidence).desc(), col(Signal.timestamp).desc())
        ).all()
        signals = _dedupe_signals_by_ticker(signals)

        held_tickers = {p.ticker for p in session.exec(select(Position)).all() if p.qty > 0}

        for sig in signals:
            if infer_market(sig.ticker) != market:
                continue
            if len(placed) >= remaining_slots:
                break

            skip_reason = _entry_gate(
                ticker=sig.ticker,
                held_tickers=held_tickers,
                total_value=total_value,
                cash=cash,
                earnings_blackout=earnings_blackout,
                avoid_sectors=avoid_sectors,
            )
            if skip_reason:
                skipped.append({"ticker": sig.ticker, "reason": skip_reason, "confidence": sig.confidence})
                logger.info(f"[exec_agent] SKIP {sig.ticker}: {skip_reason}")
                continue

            price_data    = get_price_data(sig.ticker)
            current_price = price_data.get("price")
            if not current_price or current_price <= 0:
                skipped.append({"ticker": sig.ticker, "reason": "price_unavailable"})
                continue

            qty = _size_position(
                confidence=sig.confidence,
                risk_posture=risk_posture,
                total_value=total_value,
                cash=cash,
                price=current_price,
            )
            if qty <= 0:
                skipped.append({"ticker": sig.ticker, "reason": "insufficient_cash_for_min_qty"})
                continue

            note = (
                f"Auto-entry {market} | conf={sig.confidence:.2f} | {sig.reason[:120]} | "
                f"profile={sig.buy_profile or 'external'} | "
                f"strategy={brief.get('strategy', 'n/a')} | "
                f"horizon={brief.get('time_horizon', 'n/a')}"
            )

            try:
                result = execute_order(
                    ticker=sig.ticker,
                    side="buy",
                    qty=qty,
                    note=note,
                    skill_used=f"execution_agent:{sig.skill_used}",
                    session=session,
                )
                sig.acted_on = True
                session.commit()

                held_tickers.add(sig.ticker)
                cash -= qty * current_price
                notification = _notify_trade(
                    side="buy",
                    ticker=sig.ticker,
                    qty=qty,
                    fill_price=result.get("fill_price", current_price),
                    note=note,
                    session=session,
                )

                placed.append({
                    "ticker":     sig.ticker,
                    "qty":        qty,
                    "price":      result.get("fill_price", current_price),
                    "confidence": sig.confidence,
                    "order_id":   result.get("order_id"),
                    "notification": notification,
                })
                logger.info(
                    f"[exec_agent] ✅ BUY {qty} × {sig.ticker} @ ${result.get('fill_price', current_price):.2f} "
                    f"conf={sig.confidence:.2f}"
                )
            except Exception as exc:
                logger.error(f"[exec_agent] Order failed for {sig.ticker}: {exc}", exc_info=True)
                skipped.append({"ticker": sig.ticker, "reason": f"order_error: {exc}"})

    return {
        "pass":      "entry",
        "market":    market,
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        "placed":    placed,
        "skipped":   skipped,
    }


# ─────────────────────────────────────────────────────────────
# Exit pass
# ─────────────────────────────────────────────────────────────

def run_exit_pass(market: Market = "US") -> dict:
    """
    Review open positions for a market. Sell anything that has breached
    stop-loss or take-profit thresholds.
    """
    exits = []
    held  = []

    with session_scope() as session:
        positions = session.exec(select(Position).where(Position.qty > 0)).all()

        for pos in positions:
            if infer_market(pos.ticker) != market:
                continue
            try:
                price_data    = get_price_data(pos.ticker)
                current_price = price_data.get("price")
                if not current_price:
                    continue

                pnl_pct = (current_price - pos.avg_cost) / pos.avg_cost
                should_exit, exit_reason = _exit_gate(pnl_pct)

                if should_exit:
                    note = (
                        f"Auto-exit {market} | {exit_reason} | "
                        f"avg_cost=${pos.avg_cost:.2f} "
                        f"current=${current_price:.2f} "
                        f"pnl={pnl_pct:+.1%}"
                    )
                    result = execute_order(
                        ticker=pos.ticker,
                        side="sell",
                        qty=pos.qty,
                        note=note,
                        skill_used="execution_agent:exit",
                        session=session,
                    )
                    notification = _notify_trade(
                        side="sell",
                        ticker=pos.ticker,
                        qty=pos.qty,
                        fill_price=result.get("fill_price", current_price),
                        note=note,
                        session=session,
                    )
                    exits.append({
                        "ticker":     pos.ticker,
                        "qty":        pos.qty,
                        "avg_cost":   pos.avg_cost,
                        "exit_price": result.get("fill_price", current_price),
                        "pnl_pct":    round(pnl_pct, 4),
                        "reason":     exit_reason,
                        "order_id":   result.get("order_id"),
                        "notification": notification,
                    })
                    logger.info(
                        f"[exec_agent] 🔴 SELL {pos.qty} × {pos.ticker} "
                        f"@ ${result.get('fill_price', current_price):.2f} ({pnl_pct:+.1%}) — {exit_reason}"
                    )
                else:
                    held.append({"ticker": pos.ticker, "qty": pos.qty, "pnl_pct": round(pnl_pct, 4)})

            except Exception as exc:
                logger.error(f"[exec_agent] Exit check failed for {pos.ticker}: {exc}", exc_info=True)

    return {
        "pass":       "exit",
        "market":     market,
        "timestamp":  datetime.now(timezone.utc).isoformat() + "Z",
        "exits":      exits,
        "still_held": held,
    }


# ─────────────────────────────────────────────────────────────
# Risk gates
# ─────────────────────────────────────────────────────────────

def _entry_gate(
    ticker: str,
    held_tickers: set,
    total_value: float,
    cash: float,
    earnings_blackout: set,
    avoid_sectors: set,
) -> Optional[str]:
    if ticker in held_tickers:
        return "already_held"
    if ticker in earnings_blackout:
        return "earnings_blackout"

    price_data = get_price_data(ticker)
    sector     = price_data.get("sector", "")
    if sector and sector in avoid_sectors:
        return f"avoid_sector:{sector}"

    current_price = price_data.get("price", 0)
    if current_price <= 0:
        return "price_unavailable"

    max_pos_value = total_value * EXEC_MAX_POSITION_PCT
    if current_price > max_pos_value:
        return f"price_exceeds_max_position (${current_price:.0f} > ${max_pos_value:.0f})"

    if cash < current_price:
        return "insufficient_cash"

    return None


def _exit_gate(pnl_pct: float) -> tuple[bool, str]:
    if pnl_pct <= -EXEC_STOP_LOSS_PCT:
        return True, f"stop_loss ({pnl_pct:+.1%})"
    if pnl_pct >= EXEC_TAKE_PROFIT_PCT:
        return True, f"take_profit ({pnl_pct:+.1%})"
    return False, ""


# ─────────────────────────────────────────────────────────────
# Position sizing
# ─────────────────────────────────────────────────────────────

def _size_position(
    confidence: float,
    risk_posture: str,
    total_value: float,
    cash: float,
    price: float,
) -> int:
    """
    Fixed-fractional sizing scaled by signal confidence and risk posture.

    Base allocation = EXEC_MAX_POSITION_PCT × portfolio value
    Scaled by confidence: 0.70 signal → ~0% of max, 1.0 → 100% of max
    Risk posture multiplier: aggressive 1.0×  moderate 0.7×  conservative 0.45×
    """
    posture_mult = {"aggressive": 1.0, "moderate": 0.7, "conservative": 0.45}.get(risk_posture, 0.7)
    conf_scale   = (confidence - MIN_SIGNAL_CONFIDENCE) / (1.0 - MIN_SIGNAL_CONFIDENCE)
    conf_scale   = max(0.1, min(conf_scale, 1.0))

    target_value = total_value * EXEC_MAX_POSITION_PCT * posture_mult * conf_scale
    affordable   = min(target_value, cash * 0.95)   # never use more than 95% of remaining cash

    return max(int(affordable / price), 0)
