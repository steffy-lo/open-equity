"""
portfolio_engine.py
===================
Handles all portfolio state mutations and read queries.

  execute_order()        → fill a buy or sell at live price
  get_portfolio_state()  → positions + P&L snapshot
  get_benchmark_alpha()  → portfolio return vs SPY return
"""
import uuid
import logging
from datetime import datetime, date
from sqlmodel import Session, select
from database import Trade, Position, PortfolioState, BenchmarkSnapshot, engine
from services.market_data import get_price_data, invalidate
from config import TRADING_FEE_PCT, STARTING_CASH, BENCHMARK_TICKER

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Order execution
# ─────────────────────────────────────────────────────────────

def execute_order(
    session:    Session,
    account_name: str,
    ticker:     str,
    side:       str,
    qty:        float,
    note:       str = None,
    skill_used: str = None,
) -> dict:
    """
    Fill a buy or sell order at the current live price.
    Raises ValueError on bad input or insufficient funds/position.
    Returns the filled order summary dict.
    """
    ticker = ticker.upper()
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got '{side}'")
    if qty <= 0:
        raise ValueError("qty must be > 0")

    price_data  = get_price_data(ticker)
    fill_price  = price_data["price"]
    fee         = round(fill_price * qty * TRADING_FEE_PCT, 6)

    # Fetch current cash
    state = session.exec(
        select(PortfolioState)
        .where(PortfolioState.account_name == account_name)
        .order_by(PortfolioState.id.desc())
    ).first()
    if not state:
        state = PortfolioState(account_name=account_name, cash=STARTING_CASH)
        session.add(state)

    # ── BUY ───────────────────────────────────────────────────
    if side == "buy":
        total_cost = fill_price * qty + fee
        if total_cost > state.cash:
            raise ValueError(
                f"Insufficient cash: need ${total_cost:,.2f}, "
                f"have ${state.cash:,.2f}"
            )

        pos = session.exec(
            select(Position)
            .where(Position.account_name == account_name)
            .where(Position.ticker == ticker)
        ).first()
        if pos:
            # Weighted average cost basis
            new_total_qty  = pos.qty + qty
            pos.avg_cost   = (pos.qty * pos.avg_cost + qty * fill_price) / new_total_qty
            pos.qty        = new_total_qty
        else:
            pos = Position(account_name=account_name, ticker=ticker, qty=qty, avg_cost=fill_price, realized_pnl=0.0)
            session.add(pos)

        state.cash -= total_cost

    # ── SELL ──────────────────────────────────────────────────
    else:
        pos = session.exec(
            select(Position)
            .where(Position.account_name == account_name)
            .where(Position.ticker == ticker)
        ).first()
        available = pos.qty if pos else 0
        if available < qty:
            raise ValueError(
                f"Insufficient position in {ticker}: "
                f"have {available}, trying to sell {qty}"
            )

        realized_pnl    = (fill_price - pos.avg_cost) * qty - fee
        pos.realized_pnl += realized_pnl
        pos.qty          -= qty
        proceeds         = fill_price * qty - fee
        state.cash      += proceeds

        if pos.qty <= 1e-9:     # fully exited — clean up
            session.delete(pos)

    state.updated_at = datetime.utcnow()

    # Persist trade record
    order_id = str(uuid.uuid4())[:8].upper()
    trade = Trade(
        account_name= account_name,
        order_id   = order_id,
        ticker     = ticker,
        side       = side,
        qty        = qty,
        fill_price = fill_price,
        fee        = fee,
        note       = note,
        skill_used = skill_used,
        timestamp  = datetime.utcnow(),
    )
    session.add(trade)
    session.commit()

    # Expire cache so next /portfolio fetch gets fresh price
    invalidate(ticker)

    return {
        "order_id":       order_id,
        "ticker":         ticker,
        "account_name":   account_name,
        "side":           side,
        "qty":            qty,
        "fill_price":     round(fill_price, 4),
        "fee":            fee,
        "status":         "filled",
        "cash_remaining": round(state.cash, 2),
        "note":           note,
        "skill_used":     skill_used,
        "timestamp":      trade.timestamp.isoformat() + "Z",
    }


# ─────────────────────────────────────────────────────────────
# Portfolio snapshot
# ─────────────────────────────────────────────────────────────

def get_portfolio_state(session: Session, account_name: str = "default") -> dict:
    """
    Returns a full portfolio snapshot:
    cash, equity, total value, total return %, all positions with P&L.
    """
    state = session.exec(
        select(PortfolioState)
        .where(PortfolioState.account_name == account_name)
        .order_by(PortfolioState.id.desc())
    ).first()
    cash  = state.cash if state else STARTING_CASH

    positions     = session.exec(select(Position).where(Position.account_name == account_name)).all()
    positions_out = []
    total_equity  = 0.0
    total_realized= 0.0

    for pos in positions:
        try:
            current_price = get_price_data(pos.ticker)["price"]
        except Exception as exc:
            logger.warning(f"Could not fetch price for {pos.ticker}: {exc}")
            current_price = pos.avg_cost   # fallback: mark at cost

        market_value    = pos.qty * current_price
        unrealized_pnl  = (current_price - pos.avg_cost) * pos.qty
        unrealized_pct  = ((current_price - pos.avg_cost) / pos.avg_cost) * 100

        total_equity   += market_value
        total_realized += pos.realized_pnl

        positions_out.append({
            "ticker":          pos.ticker,
            "qty":             pos.qty,
            "avg_cost":        round(pos.avg_cost, 4),
            "current_price":   round(current_price, 4),
            "market_value":    round(market_value, 2),
            "unrealized_pnl":  round(unrealized_pnl, 2),
            "unrealized_pct":  round(unrealized_pct, 2),
            "realized_pnl":    round(pos.realized_pnl, 2),
        })

    # Sort positions by market value, largest first
    positions_out.sort(key=lambda x: x["market_value"], reverse=True)

    total_value       = cash + total_equity
    total_return_pct  = ((total_value - STARTING_CASH) / STARTING_CASH) * 100
    total_pnl         = total_value - STARTING_CASH

    return {
        "cash":               round(cash, 2),
        "account_name":       account_name,
        "equity":             round(total_equity, 2),
        "total_value":        round(total_value, 2),
        "starting_cash":      STARTING_CASH,
        "total_pnl":          round(total_pnl, 2),
        "total_return_pct":   round(total_return_pct, 2),
        "realized_pnl":       round(total_realized, 2),
        "unrealized_pnl":     round(total_equity - sum(
                                  p["avg_cost"] * p["qty"] for p in positions_out
                              ), 2),
        "position_count":     len(positions_out),
        "positions":          positions_out,
    }


# ─────────────────────────────────────────────────────────────
# Benchmark snapshot (nightly job stores these)
# ─────────────────────────────────────────────────────────────

def take_benchmark_snapshot(session: Session, account_name: str = "default") -> dict:
    """
    Store today's portfolio value vs benchmark (SPY).
    Called by the nightly scheduler.
    """
    today = date.today().isoformat()

    # Avoid duplicate snapshots on the same day
    snapshot_key = f"{account_name}:{today}"
    existing = session.exec(
        select(BenchmarkSnapshot).where(BenchmarkSnapshot.date == snapshot_key)
    ).first()
    if existing:
        return {"date": today, "skipped": True, "reason": "snapshot already taken today"}

    spy_price  = get_price_data(BENCHMARK_TICKER)["price"]
    portfolio  = get_portfolio_state(session, account_name=account_name)

    snap = BenchmarkSnapshot(
        date            = snapshot_key,
        spy_price       = spy_price,
        portfolio_value = portfolio["total_value"],
        cash            = portfolio["cash"],
        equity          = portfolio["equity"],
    )
    session.add(snap)
    session.commit()

    return {
        "date":            today,
        "account_name":    account_name,
        "spy_price":       spy_price,
        "portfolio_value": portfolio["total_value"],
        "total_return_pct":portfolio["total_return_pct"],
    }


def get_benchmark_alpha(session: Session, account_name: str = "default") -> dict:
    """
    Compare portfolio return vs SPY return since first snapshot.
    Returns alpha (portfolio return − SPY return) in percentage points.
    """
    prefix = f"{account_name}:"
    snapshots = [
        snap for snap in session.exec(
            select(BenchmarkSnapshot).order_by(BenchmarkSnapshot.date)
        ).all()
        if snap.date.startswith(prefix)
    ]

    if len(snapshots) < 2:
        return {
            "message": "Not enough benchmark data yet — check back after a few trading days.",
            "account_name": account_name,
            "snapshots": len(snapshots),
        }

    first  = snapshots[0]
    latest = snapshots[-1]

    spy_return  = ((latest.spy_price       - first.spy_price)      / first.spy_price)      * 100
    port_return = ((latest.portfolio_value - first.portfolio_value) / first.portfolio_value) * 100
    alpha       = port_return - spy_return

    return {
        "period":             f"{first.date} → {latest.date}",
        "account_name":       account_name,
        "spy_return_pct":     round(spy_return, 2),
        "portfolio_return_pct": round(port_return, 2),
        "alpha_pct":          round(alpha, 2),
        "alpha_label":        f"{'▲' if alpha >= 0 else '▼'} {abs(alpha):.2f}pp vs {BENCHMARK_TICKER}",
        "days_tracked":       len(snapshots),
        "first_portfolio_value": first.portfolio_value,
        "latest_portfolio_value":latest.portfolio_value,
    }
