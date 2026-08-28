"""
Risk controls. Blocks new entries when a limit is breached; never blocks exits.

Three independent gates:
  1. Daily loss limit    — realized + unrealized P&L for the day <= -DAILY_LOSS_LIMIT_PCT
  2. Drawdown breaker    — equity <= peak equity * (1 - MAX_DRAWDOWN_PCT)
  3. Manual kill switch  — data/HALT exists, or TRADING_HALTED=1 in the environment

Gates 1 and 2 latch: once tripped they stay tripped (persisted in data/risk_state.json)
until `resume()` is called, so a bad day cannot silently resume trading the next morning.
The kill switch is not latched — it is on exactly while the file/env var is present.

Equity is measured from the risk epoch — the moment of the first evaluation, stored in
risk_state.json — so pre-deployment simulation P&L and the positions opened under the old
capital base neither mask nor fake a drawdown.

    RiskStatus                       dataclass: entries_allowed, reasons, equity, peak_equity,
                                                drawdown_pct, daily_pnl, daily_pnl_pct, halted_since
    evaluate_risk(...)               -> RiskStatus   full check; latches breaches
    mark_to_market(positions, prices) -> float       unrealized P&L of post-epoch positions
    entries_allowed()                -> bool         cheap gate used by execution.py
    halt(reason) / resume()          -> None         manual kill switch / latch reset
    status_line(status)              -> str          one-line summary for notifications
"""
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config import (
    ACCOUNT_CAPITAL_USD,
    DAILY_LOSS_LIMIT_PCT,
    MAX_DRAWDOWN_PCT,
    RISK_STATE_FILE,
    HALT_FILE,
)
from trade_history import closed_trades

logger = logging.getLogger(__name__)

_state_path = Path(RISK_STATE_FILE)
_halt_path = Path(HALT_FILE)


@dataclass
class RiskStatus:
    entries_allowed: bool
    reasons: list[str] = field(default_factory=list)
    equity: float = 0.0
    peak_equity: float = 0.0
    drawdown_pct: float = 0.0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    halted_since: str | None = None


def _load_state() -> dict:
    if not _state_path.exists():
        return {}
    try:
        with _state_path.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not read risk state ({e}); starting fresh")
        return {}


def _save_state(state: dict) -> None:
    _state_path.parent.mkdir(parents=True, exist_ok=True)
    with _state_path.open("w") as f:
        json.dump(state, f, indent=2)


def _kill_switch_reason() -> str | None:
    if _halt_path.exists():
        note = _halt_path.read_text().strip()
        return f"manual halt ({note})" if note else "manual halt (data/HALT present)"
    if os.getenv("TRADING_HALTED", "").lower() in ("1", "yes", "true"):
        return "manual halt (TRADING_HALTED set)"
    return None


def evaluate_risk(
    today: str | None = None,
    unrealized_pnl: float = 0.0,
    persist: bool = True,
) -> RiskStatus:
    """Evaluate all risk gates.

    unrealized_pnl  open-position P&L vs entry price, so equity is marked to market.

    Daily P&L is the change in equity since the first evaluation of the previous day,
    which counts each trade once (realized P&L plus the day's mark-to-market move).
    """
    now = datetime.now(timezone.utc)
    # `today` overrides the calendar date (tests, replays); the clock time still comes
    # from now, so an epoch created mid-session excludes trades closed earlier that day.
    stamp = now.isoformat() if today is None else f"{today}T{now.strftime('%H:%M:%S.%f')}+00:00"
    today = today or now.strftime("%Y-%m-%d")
    state = _load_state()

    epoch = state.get("epoch_ts")
    if epoch is None:
        epoch = stamp
        state["epoch_ts"] = epoch
        logger.info(f"Risk epoch initialized at {epoch} with ${ACCOUNT_CAPITAL_USD:,.0f} capital")

    realized = sum(t.pnl for t in closed_trades() if t.exit_ts >= epoch)
    equity = ACCOUNT_CAPITAL_USD + realized + unrealized_pnl
    peak = max(float(state.get("peak_equity", ACCOUNT_CAPITAL_USD)), equity)
    drawdown_pct = (peak - equity) / peak if peak > 0 else 0.0

    # Roll the day's opening equity forward on the first evaluation of a new day.
    if state.get("day_open_date") != today:
        state["day_open_equity"] = float(state.get("last_equity", equity))
        state["day_open_date"] = today
    day_open_equity = float(state["day_open_equity"])

    daily_pnl = equity - day_open_equity
    daily_pnl_pct = daily_pnl / day_open_equity if day_open_equity > 0 else 0.0

    reasons: list[str] = []
    if daily_pnl_pct <= -DAILY_LOSS_LIMIT_PCT:
        reasons.append(
            f"daily loss {daily_pnl_pct:.1%} breached limit {-DAILY_LOSS_LIMIT_PCT:.1%}"
        )
    if drawdown_pct >= MAX_DRAWDOWN_PCT:
        reasons.append(
            f"drawdown {drawdown_pct:.1%} breached limit {MAX_DRAWDOWN_PCT:.1%}"
        )

    halted_since = state.get("halted_since")
    if reasons and not halted_since:
        halted_since = datetime.now(timezone.utc).isoformat()
        state["halted_since"] = halted_since
        state["halt_reasons"] = reasons
        logger.error(f"RISK HALT triggered: {'; '.join(reasons)}")
    elif halted_since:
        reasons = list(state.get("halt_reasons", [])) + reasons

    kill = _kill_switch_reason()
    if kill:
        reasons.append(kill)

    state["peak_equity"] = peak
    state["last_equity"] = equity
    state["last_evaluated"] = today
    if persist:
        _save_state(state)

    return RiskStatus(
        entries_allowed=not reasons,
        reasons=reasons,
        equity=equity,
        peak_equity=peak,
        drawdown_pct=drawdown_pct,
        daily_pnl=daily_pnl,
        daily_pnl_pct=daily_pnl_pct,
        halted_since=halted_since,
    )


def epoch_ts() -> str | None:
    """Timestamp the current capital base started from, or None before the first evaluation."""
    return _load_state().get("epoch_ts")


def mark_to_market(positions, prices: dict[str, float]) -> float:
    """Unrealized P&L of positions opened at or after the risk epoch.

    Positions carried over from an earlier capital base are excluded: their P&L is
    measured against a position size the current capital never funded. Before the epoch
    exists nothing qualifies — every open position predates the capital base about to be
    established, so the answer is zero rather than "all of them".
    """
    epoch = epoch_ts()
    if epoch is None:
        return 0.0
    epoch_date = epoch[:10]
    return sum(
        (prices[p.ticker] - p.entry_price) * p.quantity
        for p in positions
        if p.ticker in prices and p.quantity and p.entry_date >= epoch_date
    )


def entries_allowed() -> bool:
    """Cheap gate: latched halt or kill switch. Does not mark positions to market."""
    if _kill_switch_reason():
        return False
    return not _load_state().get("halted_since")


def halt(reason: str = "") -> None:
    """Engage the manual kill switch — blocks new entries until resume()."""
    _halt_path.parent.mkdir(parents=True, exist_ok=True)
    _halt_path.write_text(reason or "halted manually")
    logger.warning(f"Trading halted: {reason or 'manual'}")


def resume() -> None:
    """Clear the kill switch and any latched breach, and re-baseline peak equity."""
    _halt_path.unlink(missing_ok=True)
    state = _load_state()
    state.pop("halted_since", None)
    state.pop("halt_reasons", None)
    _save_state(state)
    logger.warning("Trading resumed: risk latch cleared")


def status_line(status: RiskStatus) -> str:
    """One-line risk summary for the daily notification."""
    if status.entries_allowed:
        return (
            f"🟢 Risk OK — equity ${status.equity:,.0f} | "
            f"DD {status.drawdown_pct:.1%} of {MAX_DRAWDOWN_PCT:.0%} | "
            f"day {status.daily_pnl_pct:+.1%} of {-DAILY_LOSS_LIMIT_PCT:.0%}"
        )
    return (
        f"🛑 *ENTRIES HALTED* — {'; '.join(status.reasons)} | "
        f"equity ${status.equity:,.0f} | DD {status.drawdown_pct:.1%}"
    )
