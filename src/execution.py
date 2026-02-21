import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from config import POSITION_SIZE_USD, TRADES_LOG_FILE
from notifier import notify
from decision import EntrySignal, PositionAction
from state import Position, add_position, remove_position, update_stop

logger = logging.getLogger(__name__)

_log_path = Path(TRADES_LOG_FILE)


@dataclass
class OrderResult:
    ticker: str
    action: Literal["buy", "sell"]
    quantity: int
    fill_price: float
    timestamp: str          # ISO 8601 UTC
    mode: Literal["paper", "live"]
    success: bool
    error: str | None


def _append_trade_log(result: OrderResult) -> None:
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    with _log_path.open("a") as f:
        f.write(json.dumps(asdict(result)) + "\n")


def place_order(
    ticker: str,
    action: Literal["buy", "sell"],
    quantity: int,
    fill_price: float,
    mode: Literal["paper", "live"] = "paper",
) -> OrderResult:
    """Place a buy or sell order.

    In paper mode, logs to trades_log.jsonl and returns a simulated fill.
    In live mode, raises NotImplementedError (Phase 3).
    """
    if mode == "live":
        raise NotImplementedError("Live broker integration not implemented in Phase 2")

    ts = datetime.now(timezone.utc).isoformat()
    result = OrderResult(
        ticker=ticker,
        action=action,
        quantity=quantity,
        fill_price=fill_price,
        timestamp=ts,
        mode="paper",
        success=True,
        error=None,
    )
    logger.info(f"[PAPER] {action.upper()} {quantity} shares of {ticker} @ {fill_price:.2f}")
    _append_trade_log(result)
    return result


def execute_signals(
    signals: list[EntrySignal],
    actions: list[PositionAction],
    current_prices: dict[str, float] | None = None,
    mode: Literal["paper", "live"] = "paper",
) -> None:
    """Process a batch of entry signals and position actions.

    Places orders and updates state for each.
    current_prices is required for sell orders (to log the fill price).
    """
    # --- BUY: process entry signals ---
    for sig in signals:
        if not sig.should_enter:
            logger.debug(f"Skipping {sig.ticker}: {sig.filters_passed}")
            continue

        price = sig.entry_price
        quantity = max(1, int(POSITION_SIZE_USD / price))

        place_order(sig.ticker, "buy", quantity, fill_price=price, mode=mode)

        new_pos = Position(
            ticker=sig.ticker,
            entry_price=price,
            current_stop=sig.initial_stop,
            entry_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            day_count=0,
            quantity=quantity,
        )
        add_position(new_pos)
        logger.info(
            f"Opened position: {sig.ticker} @ {price:.2f}, "
            f"stop={sig.initial_stop:.2f}, qty={quantity}"
        )
        notify(f"📈 *BUY {sig.ticker}* — {quantity} shares @ ${price:.2f} | stop ${sig.initial_stop:.2f}")

    # --- SELL / UPDATE_STOP: process position actions ---
    prices = current_prices or {}

    for act in actions:
        if act.action == "sell":
            fill_price = prices.get(act.ticker, 0.0)
            # Look up quantity from state
            from state import load_positions
            open_positions = load_positions()
            pos = next((p for p in open_positions if p.ticker == act.ticker), None)
            qty = pos.quantity if pos else 0

            place_order(act.ticker, "sell", qty, fill_price=fill_price, mode=mode)
            remove_position(act.ticker)
            logger.info(f"Closed position: {act.ticker} @ {fill_price:.2f}, reason={act.reason}")
            notify(f"📉 *SELL {act.ticker}* — {qty} shares @ ${fill_price:.2f} | reason: {act.reason}")

        elif act.action == "update_stop":
            update_stop(act.ticker, act.new_stop)
            logger.info(f"Updated stop for {act.ticker} to {act.new_stop:.2f}")
