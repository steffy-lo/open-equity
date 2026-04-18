from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, col, select

from config import (
    AUTONOMY_ACCOUNT_NAME,
    AUTONOMY_COOLDOWN_MINUTES,
    AUTONOMY_MAX_DAILY_NEW_BUYS,
    AUTONOMY_MAX_DAILY_NOTIONAL_PCT,
    AUTONOMY_MAX_NEW_BUYS_PER_RUN,
    AUTONOMY_MAX_POSITION_PCT,
    AUTONOMY_MIN_CONFIDENCE,
    AUTONOMY_MODE,
    AUTONOMY_POSITION_SIZE_PCT,
    AUTONOMY_SOURCE_MODE,
    AUTONOMY_SOURCE_PLAN,
    AUTONOMY_SOURCE_PRESET,
    STARTING_CASH,
)
from database import DerivativeIdea, PipelineRun, Signal, Trade, TradePlan, TradeProposal
from services.portfolio_engine import execute_order, get_portfolio_state
from services.scan_presets import get_scan_mode, get_scan_preset
from services.screener import load_watchlist, run_screen

VALID_MODES = {"manual", "propose_only", "auto_paper"}
_SIGNAL_ORDER = {"buy": 0, "flag": 1, "neutral": 2, "error": 3}


@dataclass
class RiskCheck:
    approved: bool
    notes: list[str]
    capped_target_pct: float
    proposed_qty: float


def get_autonomy_config() -> dict[str, Any]:
    return {
        "account_name": AUTONOMY_ACCOUNT_NAME,
        "mode": AUTONOMY_MODE,
        "source_preset": AUTONOMY_SOURCE_PRESET,
        "source_mode": AUTONOMY_SOURCE_MODE,
        "source_plan": AUTONOMY_SOURCE_PLAN,
        "max_position_pct": AUTONOMY_MAX_POSITION_PCT,
        "position_size_pct": AUTONOMY_POSITION_SIZE_PCT,
        "min_confidence": AUTONOMY_MIN_CONFIDENCE,
        "max_new_buys_per_run": AUTONOMY_MAX_NEW_BUYS_PER_RUN,
    }


def format_run_summary(result: dict[str, Any]) -> str:
    proposals = result.get("proposals", []) or []
    lines = [
        f"🤖 Autonomy run, {result.get('mode')}",
        f"Account, {result.get('account_name')}",
        f"Summary, {result.get('summary')}",
    ]

    if not proposals:
        lines.append("No proposals this cycle.")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"Top proposals, {len(proposals)}")
    for item in proposals:
        combos = item.get("source_combos") or []
        combo_text = ", ".join(combos[:3]) if combos else "n/a"
        lines.append(
            f"• {item.get('ticker')}  conf {item.get('confidence'):.2f}  qty {int(item.get('proposed_qty') or 0)}  sources {item.get('source_hits') or 1}"
        )
        lines.append(f"  why, {item.get('rationale')}")
        lines.append(f"  from, {combo_text}")

    return "\n".join(lines)


def _build_trade_plan(candidate: dict[str, Any]) -> dict[str, Any]:
    price = float(candidate.get("price") or 0.0)
    rsi = float(candidate.get("rsi_14") or 50.0)
    source_hits = int(candidate.get("source_hits") or 1)
    direction = "long"
    timeframe = "swing" if source_hits >= 2 else "day_swing"

    stop_buffer = 0.035 if rsi <= 70 else 0.045
    target_buffer = 0.08 if timeframe == "swing" else 0.05

    stop_price = round(price * (1 - stop_buffer), 4)
    target_price = round(price * (1 + target_buffer), 4)
    thesis_type = "breakout_continuation" if "breakout" in " ".join(candidate.get("source_combos") or []) else "momentum_recovery"

    rationale = (
        f"Entry near {price:.2f}, stop {stop_price:.2f}, target {target_price:.2f}. "
        f"Built from source agreement {source_hits} and signal confidence {float(candidate.get('confidence') or 0.0):.2f}."
    )

    return {
        "ticker": candidate.get("ticker"),
        "thesis_type": thesis_type,
        "timeframe": timeframe,
        "direction": direction,
        "entry_price": round(price, 4),
        "stop_price": stop_price,
        "target_price": target_price,
        "conviction": float(candidate.get("confidence") or 0.0),
        "status": "planned",
        "lifecycle_stage": "entry_candidate",
        "rationale": rationale,
        "source_context": ", ".join(candidate.get("source_combos") or []),
    }


def _build_exit_plan(position: dict[str, Any]) -> dict[str, Any] | None:
    ticker = position.get("ticker")
    qty = float(position.get("qty") or 0.0)
    current_price = float(position.get("current_price") or 0.0)
    avg_cost = float(position.get("avg_cost") or 0.0)
    unrealized_pct = float(position.get("unrealized_pct") or 0.0)
    market_value = float(position.get("market_value") or 0.0)

    if qty <= 0 or current_price <= 0 or avg_cost <= 0:
        return None

    peak_reference = max(current_price, avg_cost * (1 + max(unrealized_pct, 0.0) / 100.0))
    trailing_drawdown_pct = ((current_price - peak_reference) / peak_reference) * 100 if peak_reference > 0 else 0.0

    if unrealized_pct <= -7:
        return {
            "ticker": ticker,
            "thesis_type": "stop_loss_exit",
            "timeframe": "active",
            "direction": "exit",
            "entry_price": current_price,
            "stop_price": current_price,
            "target_price": current_price,
            "conviction": 0.85,
            "status": "planned",
            "lifecycle_stage": "exit_candidate",
            "rationale": f"Position is down {unrealized_pct:.2f}%, triggering capital-protection exit logic.",
            "source_context": "position_management",
            "linked_position_qty": qty,
            "proposal_side": "sell",
            "proposal_qty": qty,
        }

    if unrealized_pct >= 6 and trailing_drawdown_pct <= -3.5:
        return {
            "ticker": ticker,
            "thesis_type": "trailing_stop_exit",
            "timeframe": "active",
            "direction": "exit",
            "entry_price": current_price,
            "stop_price": current_price,
            "target_price": current_price,
            "conviction": 0.8,
            "status": "planned",
            "lifecycle_stage": "exit_candidate",
            "rationale": f"Position had open gains and has now retraced {abs(trailing_drawdown_pct):.2f}% from its working peak, triggering trailing-stop protection.",
            "source_context": "position_management.trailing_stop",
            "linked_position_qty": qty,
            "proposal_side": "sell",
            "proposal_qty": qty,
        }

    if unrealized_pct >= 12:
        return {
            "ticker": ticker,
            "thesis_type": "take_profit_trim",
            "timeframe": "active",
            "direction": "trim",
            "entry_price": current_price,
            "stop_price": round(current_price * 0.97, 4),
            "target_price": current_price,
            "conviction": 0.72,
            "status": "planned",
            "lifecycle_stage": "trim_candidate",
            "rationale": f"Position is up {unrealized_pct:.2f}%, triggering profit-protection trim logic.",
            "source_context": "position_management",
            "linked_position_qty": qty,
            "proposal_side": "sell",
            "proposal_qty": max(1.0, float(int(qty / 2))),
        }

    if market_value > 0 and unrealized_pct <= -3 and current_price < avg_cost * 0.985:
        return {
            "ticker": ticker,
            "thesis_type": "thesis_deterioration_exit",
            "timeframe": "active",
            "direction": "exit",
            "entry_price": current_price,
            "stop_price": current_price,
            "target_price": current_price,
            "conviction": 0.68,
            "status": "planned",
            "lifecycle_stage": "exit_candidate",
            "rationale": f"Position is trading below cost with weakening price structure, triggering thesis-deterioration review/exit logic at {unrealized_pct:.2f}% unrealized P&L.",
            "source_context": "position_management.thesis_deterioration",
            "linked_position_qty": qty,
            "proposal_side": "sell",
            "proposal_qty": max(1.0, float(int(qty / 2))),
        }

    return None


def _manage_active_trade_plan(session: Session, account_name: str, position: dict[str, Any]) -> dict[str, Any] | None:
    active_plan = _find_active_trade_plan(session, account_name, position.get("ticker"))
    if not active_plan or active_plan.lifecycle_stage not in {"active", "entry_candidate"}:
        return None

    current_price = float(position.get("current_price") or 0.0)
    unrealized_pct = float(position.get("unrealized_pct") or 0.0)
    qty = float(position.get("qty") or 0.0)

    if qty <= 0 or current_price <= 0:
        return None

    updated = False
    notes: list[str] = []

    if unrealized_pct >= 4 and active_plan.stop_price < active_plan.entry_price:
        active_plan.stop_price = active_plan.entry_price
        notes.append("Moved stop to breakeven after trade reached initial profit threshold")
        updated = True

    age_hours = None
    if active_plan.created_at:
        age_hours = (datetime.utcnow() - active_plan.created_at).total_seconds() / 3600

    if age_hours is not None and age_hours >= 72 and abs(unrealized_pct) < 2:
        return {
            "ticker": position.get("ticker"),
            "thesis_type": "time_stop_exit",
            "timeframe": "active",
            "direction": "exit",
            "entry_price": current_price,
            "stop_price": current_price,
            "target_price": current_price,
            "conviction": 0.6,
            "status": "planned",
            "lifecycle_stage": "exit_candidate",
            "rationale": f"Trade has been open for {age_hours:.1f}h with limited progress ({unrealized_pct:.2f}%), triggering time-stop review.",
            "source_context": "position_management.time_stop",
            "linked_position_qty": qty,
            "proposal_side": "sell",
            "proposal_qty": qty,
            "parent_trade_plan_id": active_plan.id,
        }

    if updated:
        active_plan.last_reviewed_at = datetime.utcnow()
        session.add(active_plan)
        session.commit()
        session.refresh(active_plan)
        return {
            "ticker": position.get("ticker"),
            "thesis_type": active_plan.thesis_type,
            "timeframe": active_plan.timeframe,
            "direction": active_plan.direction,
            "entry_price": active_plan.entry_price,
            "stop_price": active_plan.stop_price,
            "target_price": active_plan.target_price,
            "conviction": active_plan.conviction,
            "status": active_plan.status,
            "lifecycle_stage": active_plan.lifecycle_stage,
            "rationale": " | ".join(notes),
            "source_context": "position_management.breakeven",
            "linked_position_qty": qty,
            "parent_trade_plan_id": active_plan.id,
            "management_only": True,
        }

    return None


def _build_derivative_idea(candidate: dict[str, Any], trade_plan: dict[str, Any]) -> dict[str, Any] | None:
    confidence = float(candidate.get("confidence") or 0.0)
    price = float(candidate.get("price") or 0.0)
    if confidence < 0.75:
        return None

    structure = "long_call_or_bull_call_spread"
    rationale = (
        f"If options are liquid, this setup can express upside with defined risk around entry {price:.2f} and target {trade_plan['target_price']:.2f}."
    )
    risk_note = "Only use when options liquidity and spreads are acceptable. Leverage/options remain idea-level in MVP, not auto-executable."
    return {
        "ticker": candidate.get("ticker"),
        "idea_type": "options_upside_expression",
        "structure": structure,
        "rationale": rationale,
        "risk_note": risk_note,
        "conviction": confidence,
    }


def _find_active_trade_plan(session: Session, account_name: str, ticker: str) -> TradePlan | None:
    return session.exec(
        select(TradePlan)
        .where(TradePlan.account_name == account_name)
        .where(TradePlan.ticker == ticker)
        .where(TradePlan.lifecycle_stage.in_(["entry_candidate", "active", "trim_candidate", "exit_candidate"]))
        .order_by(col(TradePlan.created_at).desc())
    ).first()


def _mark_trade_plan_reviewed(session: Session, trade_plan: TradePlan, lifecycle_stage: str | None = None, status: str | None = None) -> TradePlan:
    trade_plan.last_reviewed_at = datetime.utcnow()
    if lifecycle_stage:
        trade_plan.lifecycle_stage = lifecycle_stage
    if status:
        trade_plan.status = status
    session.add(trade_plan)
    session.commit()
    session.refresh(trade_plan)
    return trade_plan


def _recent_trade_for_ticker(session: Session, account_name: str, ticker: str, side: str, cooldown_minutes: int) -> Trade | None:
    cutoff = datetime.utcnow() - timedelta(minutes=cooldown_minutes)
    return session.exec(
        select(Trade)
        .where(Trade.account_name == account_name)
        .where(Trade.ticker == ticker)
        .where(Trade.side == side)
        .where(Trade.timestamp >= cutoff)
        .order_by(col(Trade.timestamp).desc())
    ).first()


def _daily_buy_budget_state(session: Session, account_name: str) -> dict[str, float]:
    cutoff = datetime.utcnow() - timedelta(hours=24)
    rows = session.exec(
        select(Trade)
        .where(Trade.account_name == account_name)
        .where(Trade.side == "buy")
        .where(Trade.timestamp >= cutoff)
    ).all()
    return {
        "buy_count": float(len(rows)),
        "notional": float(sum((row.fill_price * row.qty) + row.fee for row in rows)),
    }


def _latest_signal_for_ticker(session: Session, ticker: str) -> Signal | None:
    return session.exec(
        select(Signal)
        .where(Signal.ticker == ticker)
        .order_by(col(Signal.timestamp).desc())
    ).first()


def _parse_source_plan() -> list[tuple[str, str]]:
    entries = []
    for raw in AUTONOMY_SOURCE_PLAN.split(","):
        item = raw.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(f"Invalid AUTONOMY_SOURCE_PLAN entry: {item}")
        preset, mode = item.split(":", 1)
        preset = preset.strip()
        mode = mode.strip()
        get_scan_preset(preset)
        get_scan_mode(mode)
        entries.append((preset, mode))
    return entries


def _signal_rank(signal: str | None) -> int:
    return _SIGNAL_ORDER.get(signal or "neutral", 3)


def _merge_candidates(screen_sets: list[tuple[str, str, list[dict[str, Any]]]]) -> tuple[list[dict[str, Any]], str, int]:
    merged: dict[str, dict[str, Any]] = {}
    total_universe = 0
    source_labels: list[str] = []

    for preset_name, mode_name, rows in screen_sets:
        total_universe += len(rows)
        source_labels.append(f"{preset_name}:{mode_name}")
        for row in rows:
            ticker = row["ticker"]
            item = merged.get(ticker)
            if not item:
                item = dict(row)
                item["source_hits"] = 0
                item["source_combos"] = []
                item["max_confidence_seen"] = float(row.get("confidence") or 0.0)
                merged[ticker] = item

            item["source_hits"] += 1
            item["source_combos"].append(f"{preset_name}:{mode_name}")
            item["max_confidence_seen"] = max(item["max_confidence_seen"], float(row.get("confidence") or 0.0))

            if float(row.get("confidence") or 0.0) > float(item.get("confidence") or 0.0):
                keep_hits = item["source_hits"]
                keep_combos = item["source_combos"]
                keep_max = item["max_confidence_seen"]
                item.update(row)
                item["source_hits"] = keep_hits
                item["source_combos"] = keep_combos
                item["max_confidence_seen"] = keep_max

    merged_list = []
    for item in merged.values():
        boosted_conf = min(float(item.get("max_confidence_seen") or 0.0) + 0.05 * max(item["source_hits"] - 1, 0), 0.99)
        item["confidence"] = round(boosted_conf, 2)
        item["source_hits"] = int(item["source_hits"])
        item["source_combos"] = sorted(set(item["source_combos"]))
        item["reason"] = (item.get("reason") or "") + f" | Source agreement {item['source_hits']}"
        if item.get("signal") != "flag":
            item["signal"] = "buy" if item["confidence"] >= AUTONOMY_MIN_CONFIDENCE else item.get("signal", "neutral")
        merged_list.append(item)

    merged_list.sort(key=lambda x: (_signal_rank(x.get("signal")), -(x.get("confidence") or 0.0), -x.get("source_hits", 0)))
    return merged_list, ", ".join(source_labels), total_universe


def _risk_check(candidate: dict[str, Any], portfolio: dict[str, Any], existing_position: dict[str, Any] | None) -> RiskCheck:
    notes: list[str] = []

    total_value = max(portfolio.get("total_value", STARTING_CASH), 1)
    cash = portfolio.get("cash", 0.0)
    reference_price = float(candidate.get("price") or 0.0)
    confidence = float(candidate.get("confidence") or 0.0)
    signal = candidate.get("signal")

    if signal != "buy":
        notes.append("Rejected, signal is not buy")
    if confidence < AUTONOMY_MIN_CONFIDENCE:
        notes.append(f"Rejected, confidence {confidence:.2f} below minimum {AUTONOMY_MIN_CONFIDENCE:.2f}")
    if reference_price <= 0:
        notes.append("Rejected, invalid reference price")

    current_position_value = (existing_position or {}).get("market_value", 0.0)
    current_position_pct = current_position_value / total_value if total_value else 0.0
    concentration_room = max(AUTONOMY_MAX_POSITION_PCT - current_position_pct, 0.0)
    target_position_pct = min(AUTONOMY_POSITION_SIZE_PCT, concentration_room)

    if target_position_pct <= 0:
        notes.append("Rejected, no concentration room left under max position cap")

    desired_notional = total_value * target_position_pct
    desired_notional = min(desired_notional, cash)
    proposed_qty = desired_notional / reference_price if reference_price > 0 else 0.0
    proposed_qty = int(proposed_qty)

    if proposed_qty <= 0:
        notes.append("Rejected, insufficient cash for at least 1 share")

    approved = not notes
    if approved:
        notes.append(f"Approved, target size {target_position_pct*100:.2f}% of portfolio, qty {proposed_qty}")

    return RiskCheck(
        approved=approved,
        notes=notes,
        capped_target_pct=target_position_pct,
        proposed_qty=float(proposed_qty),
    )


def run_autonomous_cycle(session: Session, mode: str | None = None, account_name: str | None = None) -> dict[str, Any]:
    selected_mode = mode or AUTONOMY_MODE
    selected_account = account_name or AUTONOMY_ACCOUNT_NAME
    if selected_mode not in VALID_MODES:
        raise ValueError(f"Invalid autonomy mode: {selected_mode}")

    source_tickers = load_watchlist()
    screened: list[dict[str, Any]]
    total_universe = len(source_tickers)

    if source_tickers:
        source_summary = "Watchlist"
        screened = run_screen(
            tickers=source_tickers,
            session=session,
            use_watchlist=True,
            screen_scope="watchlist",
        )
    else:
        screen_sets = []
        for preset_name, mode_name in _parse_source_plan():
            preset = get_scan_preset(preset_name)
            rows = run_screen(
                tickers=preset["tickers"],
                session=session,
                use_watchlist=False,
                screen_scope="custom_universe",
                screen_label=preset["label"],
                universe=preset["universe"],
                scan_mode=mode_name,
            )
            screen_sets.append((preset_name, mode_name, rows))

        screened, source_summary, total_universe = _merge_candidates(screen_sets)

    run = PipelineRun(account_name=selected_account, mode=selected_mode, status="running", universe_size=total_universe)
    session.add(run)
    session.commit()
    session.refresh(run)

    if not screened:
        run.status = "completed"
        run.summary = "No source tickers available"
        session.add(run)
        session.commit()
        return {
            "run_id": run.id,
            "account_name": selected_account,
            "mode": selected_mode,
            "status": run.status,
            "summary": run.summary,
            "proposals": [],
        }

    portfolio = get_portfolio_state(session, account_name=selected_account)
    daily_budget = _daily_buy_budget_state(session, selected_account)
    positions_by_ticker = {p["ticker"]: p for p in portfolio.get("positions", [])}
    candidates = [item for item in screened if item.get("signal") == "buy"]
    run.candidates_considered = len(candidates)
    session.add(run)
    session.commit()

    proposals_out: list[dict[str, Any]] = []
    trade_plans_out: list[dict[str, Any]] = []
    derivative_ideas_out: list[dict[str, Any]] = []
    created = 0
    executed = 0

    exit_candidates = []
    for pos in portfolio.get("positions", []):
        managed_plan = _manage_active_trade_plan(session, selected_account, pos)
        if managed_plan and managed_plan.get("management_only"):
            trade_plans_out.append({
                "trade_plan_id": managed_plan["parent_trade_plan_id"],
                "ticker": managed_plan["ticker"],
                "thesis_type": managed_plan["thesis_type"],
                "timeframe": managed_plan["timeframe"],
                "direction": managed_plan["direction"],
                "entry_price": managed_plan["entry_price"],
                "stop_price": managed_plan["stop_price"],
                "target_price": managed_plan["target_price"],
                "conviction": managed_plan["conviction"],
                "status": managed_plan["status"],
                "lifecycle_stage": managed_plan["lifecycle_stage"],
                "rationale": managed_plan["rationale"],
                "source_context": managed_plan["source_context"],
                "parent_trade_plan_id": managed_plan["parent_trade_plan_id"],
            })
        elif managed_plan:
            exit_candidates.append(managed_plan)

        exit_plan = _build_exit_plan(pos)
        if exit_plan:
            exit_candidates.append(exit_plan)

    for exit_plan in exit_candidates:
        if created >= AUTONOMY_MAX_NEW_BUYS_PER_RUN:
            break

        ticker = exit_plan["ticker"]
        proposal = TradeProposal(
            run_id=run.id,
            account_name=selected_account,
            ticker=ticker,
            side="sell",
            signal="sell",
            confidence=float(exit_plan["conviction"]),
            proposed_qty=float(exit_plan["proposal_qty"]),
            reference_price=float(exit_plan.get("entry_price") or 0.0),
            target_position_pct=0.0,
            status="approved",
            rationale=exit_plan["rationale"],
            risk_notes="Approved from position-management exit logic",
        )
        session.add(proposal)
        session.commit()
        session.refresh(proposal)

        trade_plan = TradePlan(
            run_id=run.id,
            account_name=selected_account,
            ticker=exit_plan["ticker"],
            thesis_type=exit_plan["thesis_type"],
            timeframe=exit_plan["timeframe"],
            direction=exit_plan["direction"],
            entry_price=exit_plan["entry_price"],
            stop_price=exit_plan["stop_price"],
            target_price=exit_plan["target_price"],
            conviction=exit_plan["conviction"],
            status=exit_plan["status"],
            lifecycle_stage=exit_plan["lifecycle_stage"],
            parent_trade_plan_id=(_find_active_trade_plan(session, selected_account, ticker) or TradePlan()).id,
            rationale=exit_plan["rationale"],
            source_context=exit_plan["source_context"],
            linked_position_qty=exit_plan["linked_position_qty"],
        )
        session.add(trade_plan)
        session.commit()
        session.refresh(trade_plan)

        execution = None
        if selected_mode == "auto_paper":
            execution = execute_order(
                session=session,
                account_name=selected_account,
                ticker=ticker,
                side="sell",
                qty=float(exit_plan["proposal_qty"]),
                note=f"Autonomy exit run {run.id}: {exit_plan['rationale']}",
                skill_used="autonomy_engine",
            )
            proposal.status = "executed"
            proposal.execution_order_id = execution["order_id"]
            session.add(proposal)
            session.commit()
            _mark_trade_plan_reviewed(session, trade_plan, lifecycle_stage="closed" if exit_plan["proposal_qty"] >= exit_plan["linked_position_qty"] else "trimmed", status="executed")
            executed += 1

        proposals_out.append({
            "proposal_id": proposal.id,
            "ticker": ticker,
            "status": proposal.status,
            "confidence": proposal.confidence,
            "source_hits": None,
            "source_combos": ["position_management"],
            "proposed_qty": proposal.proposed_qty,
            "reference_price": proposal.reference_price,
            "target_position_pct": proposal.target_position_pct,
            "rationale": proposal.rationale,
            "risk_notes": proposal.risk_notes,
            "execution": execution,
        })
        trade_plans_out.append({
            "trade_plan_id": trade_plan.id,
            "ticker": exit_plan["ticker"],
            "thesis_type": exit_plan["thesis_type"],
            "timeframe": exit_plan["timeframe"],
            "direction": exit_plan["direction"],
            "entry_price": exit_plan["entry_price"],
            "stop_price": exit_plan["stop_price"],
            "target_price": exit_plan["target_price"],
            "conviction": exit_plan["conviction"],
            "status": trade_plan.status,
            "lifecycle_stage": trade_plan.lifecycle_stage,
            "rationale": exit_plan["rationale"],
            "source_context": exit_plan["source_context"],
            "linked_position_qty": exit_plan["linked_position_qty"],
        })
        created += 1

    for candidate in candidates:
        if created >= AUTONOMY_MAX_NEW_BUYS_PER_RUN:
            break

        ticker = candidate["ticker"]
        latest_signal = _latest_signal_for_ticker(session, ticker)
        existing_position = positions_by_ticker.get(ticker)
        risk = _risk_check(candidate, portfolio, existing_position)
        recent_buy = _recent_trade_for_ticker(session, selected_account, ticker, "buy", AUTONOMY_COOLDOWN_MINUTES)

        if recent_buy:
            risk.notes.append(f"Rejected, cooldown active after buy at {recent_buy.timestamp.isoformat()}Z")
            risk.approved = False

        active_plan = _find_active_trade_plan(session, selected_account, ticker)
        if active_plan and active_plan.lifecycle_stage in {"entry_candidate", "active"}:
            risk.notes.append(f"Rejected, duplicate entry suppressed by active trade plan {active_plan.id}")
            risk.approved = False

        if daily_budget["buy_count"] >= AUTONOMY_MAX_DAILY_NEW_BUYS:
            risk.notes.append(f"Rejected, daily buy limit reached ({int(daily_budget['buy_count'])}/{AUTONOMY_MAX_DAILY_NEW_BUYS})")
            risk.approved = False

        max_daily_notional = portfolio.get("total_value", STARTING_CASH) * AUTONOMY_MAX_DAILY_NOTIONAL_PCT
        proposed_notional = float(risk.proposed_qty) * float(candidate.get("price") or 0.0)
        if (daily_budget["notional"] + proposed_notional) > max_daily_notional:
            risk.notes.append(
                f"Rejected, daily notional cap exceeded ({daily_budget['notional'] + proposed_notional:.2f} > {max_daily_notional:.2f})"
            )
            risk.approved = False

        if latest_signal and latest_signal.acted_on:
            risk.notes.append("Rejected, latest signal already acted on")
            risk.approved = False

        status = "approved" if risk.approved else "rejected"
        proposal = TradeProposal(
            run_id=run.id,
            account_name=selected_account,
            ticker=ticker,
            side="buy",
            signal=candidate["signal"],
            confidence=float(candidate["confidence"]),
            proposed_qty=float(risk.proposed_qty),
            reference_price=float(candidate.get("price") or 0.0),
            target_position_pct=float(risk.capped_target_pct),
            status=status if selected_mode != "manual" else "draft",
            rationale=candidate.get("reason") or "",
            risk_notes=" | ".join(risk.notes),
        )
        session.add(proposal)
        session.commit()
        session.refresh(proposal)

        trade_plan_payload = _build_trade_plan(candidate)
        trade_plan = TradePlan(
            run_id=run.id,
            account_name=selected_account,
            ticker=trade_plan_payload["ticker"],
            thesis_type=trade_plan_payload["thesis_type"],
            timeframe=trade_plan_payload["timeframe"],
            direction=trade_plan_payload["direction"],
            entry_price=trade_plan_payload["entry_price"],
            stop_price=trade_plan_payload["stop_price"],
            target_price=trade_plan_payload["target_price"],
            conviction=trade_plan_payload["conviction"],
            status=trade_plan_payload["status"],
            lifecycle_stage=trade_plan_payload["lifecycle_stage"],
            parent_trade_plan_id=None,
            rationale=trade_plan_payload["rationale"],
            source_context=trade_plan_payload["source_context"],
            linked_position_qty=None,
        )
        session.add(trade_plan)
        session.commit()
        session.refresh(trade_plan)

        derivative_payload = _build_derivative_idea(candidate, trade_plan_payload)
        derivative_idea = None
        if derivative_payload:
            derivative_idea = DerivativeIdea(
                run_id=run.id,
                account_name=selected_account,
                ticker=derivative_payload["ticker"],
                idea_type=derivative_payload["idea_type"],
                structure=derivative_payload["structure"],
                rationale=derivative_payload["rationale"],
                risk_note=derivative_payload["risk_note"],
                conviction=derivative_payload["conviction"],
            )
            session.add(derivative_idea)
            session.commit()
            session.refresh(derivative_idea)

        created += 1

        execution = None
        if selected_mode == "auto_paper" and risk.approved and risk.proposed_qty > 0:
            execution = execute_order(
                session=session,
                account_name=selected_account,
                ticker=ticker,
                side="buy",
                qty=risk.proposed_qty,
                note=f"Autonomy run {run.id}: {candidate.get('reason')}",
                skill_used="autonomy_engine",
            )
            proposal.status = "executed"
            proposal.execution_order_id = execution["order_id"]
            session.add(proposal)
            session.commit()
            _mark_trade_plan_reviewed(session, trade_plan, lifecycle_stage="active", status="executed")
            daily_budget["buy_count"] += 1
            daily_budget["notional"] += float(risk.proposed_qty) * float(candidate.get("price") or 0.0)
            executed += 1
        elif proposal.status == "approved":
            _mark_trade_plan_reviewed(session, trade_plan, lifecycle_stage="entry_candidate", status="approved")
        else:
            _mark_trade_plan_reviewed(session, trade_plan, lifecycle_stage="rejected", status=proposal.status)

        proposals_out.append(
            {
                "proposal_id": proposal.id,
                "ticker": ticker,
                "status": proposal.status,
                "confidence": proposal.confidence,
                "source_hits": candidate.get("source_hits"),
                "source_combos": candidate.get("source_combos"),
                "proposed_qty": proposal.proposed_qty,
                "reference_price": proposal.reference_price,
                "target_position_pct": proposal.target_position_pct,
                "rationale": proposal.rationale,
                "risk_notes": proposal.risk_notes,
                "execution": execution,
            }
        )
        trade_plans_out.append(
            {
                "trade_plan_id": trade_plan.id,
                **trade_plan_payload,
                "status": trade_plan.status,
                "lifecycle_stage": trade_plan.lifecycle_stage,
                "parent_trade_plan_id": trade_plan.parent_trade_plan_id,
            }
        )
        if derivative_payload and derivative_idea:
            derivative_ideas_out.append(
                {
                    "derivative_idea_id": derivative_idea.id,
                    **derivative_payload,
                }
            )

    run.proposals_created = created
    run.proposals_executed = executed
    run.status = "completed"
    run.summary = (
        f"Source {source_summary}, universe {total_universe}, buy candidates {len(candidates)}, "
        f"proposals {created}, executed {executed}"
    )
    session.add(run)
    session.commit()

    return {
        "run_id": run.id,
        "account_name": selected_account,
        "mode": selected_mode,
        "status": run.status,
        "summary": run.summary,
        "proposals": proposals_out,
        "trade_plans": trade_plans_out,
        "derivative_ideas": derivative_ideas_out,
    }


def list_pipeline_runs(session: Session, limit: int = 20, account_name: str | None = None) -> list[dict[str, Any]]:
    query = select(PipelineRun).order_by(col(PipelineRun.created_at).desc()).limit(limit)
    if account_name:
        query = select(PipelineRun).where(PipelineRun.account_name == account_name).order_by(col(PipelineRun.created_at).desc()).limit(limit)
    rows = session.exec(query).all()
    return [
        {
            "id": row.id,
            "account_name": row.account_name,
            "mode": row.mode,
            "status": row.status,
            "universe_size": row.universe_size,
            "candidates_considered": row.candidates_considered,
            "proposals_created": row.proposals_created,
            "proposals_executed": row.proposals_executed,
            "summary": row.summary,
            "created_at": row.created_at.isoformat() + "Z",
        }
        for row in rows
    ]


def list_trade_proposals(session: Session, limit: int = 50, account_name: str | None = None) -> list[dict[str, Any]]:
    query = select(TradeProposal).order_by(col(TradeProposal.created_at).desc()).limit(limit)
    if account_name:
        query = select(TradeProposal).where(TradeProposal.account_name == account_name).order_by(col(TradeProposal.created_at).desc()).limit(limit)
    rows = session.exec(query).all()
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "account_name": row.account_name,
            "ticker": row.ticker,
            "side": row.side,
            "signal": row.signal,
            "confidence": row.confidence,
            "proposed_qty": row.proposed_qty,
            "reference_price": row.reference_price,
            "target_position_pct": row.target_position_pct,
            "status": row.status,
            "rationale": row.rationale,
            "risk_notes": row.risk_notes,
            "execution_order_id": row.execution_order_id,
            "created_at": row.created_at.isoformat() + "Z",
        }
        for row in rows
    ]


def list_trade_plans(session: Session, limit: int = 50, account_name: str | None = None) -> list[dict[str, Any]]:
    query = select(TradePlan).order_by(col(TradePlan.created_at).desc()).limit(limit)
    if account_name:
        query = select(TradePlan).where(TradePlan.account_name == account_name).order_by(col(TradePlan.created_at).desc()).limit(limit)
    rows = session.exec(query).all()
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "account_name": row.account_name,
            "ticker": row.ticker,
            "thesis_type": row.thesis_type,
            "timeframe": row.timeframe,
            "direction": row.direction,
            "entry_price": row.entry_price,
            "stop_price": row.stop_price,
            "target_price": row.target_price,
            "conviction": row.conviction,
            "status": row.status,
            "lifecycle_stage": row.lifecycle_stage,
            "parent_trade_plan_id": row.parent_trade_plan_id,
            "rationale": row.rationale,
            "source_context": row.source_context,
            "linked_position_qty": row.linked_position_qty,
            "last_reviewed_at": row.last_reviewed_at.isoformat() + "Z" if row.last_reviewed_at else None,
            "created_at": row.created_at.isoformat() + "Z",
        }
        for row in rows
    ]


def list_derivative_ideas(session: Session, limit: int = 50, account_name: str | None = None) -> list[dict[str, Any]]:
    query = select(DerivativeIdea).order_by(col(DerivativeIdea.created_at).desc()).limit(limit)
    if account_name:
        query = select(DerivativeIdea).where(DerivativeIdea.account_name == account_name).order_by(col(DerivativeIdea.created_at).desc()).limit(limit)
    rows = session.exec(query).all()
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "account_name": row.account_name,
            "ticker": row.ticker,
            "idea_type": row.idea_type,
            "structure": row.structure,
            "rationale": row.rationale,
            "risk_note": row.risk_note,
            "conviction": row.conviction,
            "created_at": row.created_at.isoformat() + "Z",
        }
        for row in rows
    ]
