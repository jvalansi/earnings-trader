"""
Entry and position management logic. Pure functions — no side effects.

    EntrySignal                             dataclass: ticker, should_enter, filters_passed, entry_price, initial_stop
    PositionAction                          dataclass: ticker, action ('hold'|'sell'|'update_stop'), new_stop, reason

    evaluate_entry(ticker, surprise, ah_move, prior_runup, sector_move,
                   atr, current_price, open_positions) -> EntrySignal

    evaluate_positions(positions, current_prices, current_atrs) -> list[PositionAction]
"""
from dataclasses import dataclass
from typing import Literal

from config import (
    MIN_EPS_BEAT_PCT,
    MIN_AH_MOVE_PCT,
    MAX_PRIOR_RUNUP_PCT,
    SECTOR_ETF_MIN,
    ATR_STOP_MULTIPLIER,
    HOLD_DAYS,
    MAX_POSITIONS,
)
from data.earnings import EarningsSurprise
from state import Position


@dataclass
class EntrySignal:
    ticker: str
    should_enter: bool
    filters_passed: dict[str, bool]  # per-filter pass/fail breakdown
    entry_price: float | None
    initial_stop: float | None       # entry_price - (ATR_STOP_MULTIPLIER * ATR)


@dataclass
class PositionAction:
    ticker: str
    action: Literal["hold", "sell", "update_stop"]
    new_stop: float | None  # set when action == "update_stop"
    reason: str             # e.g. "stop_hit", "max_days_reached", "trailing_stop_updated"


def evaluate_entry(
    ticker: str,
    surprise: EarningsSurprise,
    ah_move: float,
    prior_runup: float,
    sector_move: float,
    atr: float,
    current_price: float,
    open_positions: list[Position],
    min_eps_beat_pct: float = MIN_EPS_BEAT_PCT,
    min_ah_move_pct: float = MIN_AH_MOVE_PCT,
    max_prior_runup_pct: float = MAX_PRIOR_RUNUP_PCT,
    sector_etf_min: float = SECTOR_ETF_MIN,
    atr_stop_multiplier: float = ATR_STOP_MULTIPLIER,
    max_positions: int = MAX_POSITIONS,
) -> EntrySignal:
    """Evaluate all six entry filters and return a signal.

    Returns EntrySignal with should_enter=False if MAX_POSITIONS is already reached.
    Thresholds default to production config values but can be overridden (e.g. for backtesting).
    """
    filters: dict[str, bool] = {}

    filters["eps_beat"] = surprise.eps_beat_pct >= min_eps_beat_pct
    filters["rev_beat"] = surprise.rev_beat_pct > 0
    filters["ah_move"] = ah_move >= min_ah_move_pct
    filters["prior_runup"] = prior_runup <= max_prior_runup_pct
    filters["sector_etf"] = sector_move > sector_etf_min

    # Guidance: skip filter if data unavailable (treat as passing)
    if surprise.guidance_weak is None:
        filters["guidance"] = True
    else:
        filters["guidance"] = not surprise.guidance_weak

    filters["capacity"] = len(open_positions) < max_positions

    if all(filters.values()):
        initial_stop = current_price - (atr_stop_multiplier * atr)
        return EntrySignal(
            ticker=ticker,
            should_enter=True,
            filters_passed=filters,
            entry_price=current_price,
            initial_stop=initial_stop,
        )

    return EntrySignal(
        ticker=ticker,
        should_enter=False,
        filters_passed=filters,
        entry_price=None,
        initial_stop=None,
    )


def evaluate_positions(
    positions: list[Position],
    current_prices: dict[str, float],
    current_atrs: dict[str, float],
    atr_stop_multiplier: float = ATR_STOP_MULTIPLIER,
    hold_days: int = HOLD_DAYS,
) -> list[PositionAction]:
    """Evaluate each open position and return the appropriate action for each.

    Thresholds default to production config values but can be overridden (e.g. for backtesting).
    """
    actions = []

    for pos in positions:
        price = current_prices.get(pos.ticker)

        if price is None:
            actions.append(PositionAction(
                ticker=pos.ticker,
                action="hold",
                new_stop=None,
                reason="price_unavailable",
            ))
            continue

        if price <= pos.current_stop:
            actions.append(PositionAction(
                ticker=pos.ticker,
                action="sell",
                new_stop=None,
                reason="stop_hit",
            ))
            continue

        if pos.day_count >= hold_days:
            actions.append(PositionAction(
                ticker=pos.ticker,
                action="sell",
                new_stop=None,
                reason="max_days_reached",
            ))
            continue

        # Only raise the stop, never lower it
        atr = current_atrs.get(pos.ticker)
        if atr is not None:
            new_stop = price - (atr_stop_multiplier * atr)
            if new_stop > pos.current_stop:
                actions.append(PositionAction(
                    ticker=pos.ticker,
                    action="update_stop",
                    new_stop=new_stop,
                    reason="trailing_stop_updated",
                ))
                continue

        actions.append(PositionAction(
            ticker=pos.ticker,
            action="hold",
            new_stop=None,
            reason="no_action",
        ))

    return actions
