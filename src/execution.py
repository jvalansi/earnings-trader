"""
Order placement and trade logging. Routes to Alpaca paper/live or local simulation.

    OrderResult                                                  dataclass: ticker, action, quantity, fill_price, timestamp, mode, success, error

    place_order(ticker, action, quantity, fill_price, mode='paper') -> OrderResult
    execute_signals(signals, actions, current_prices=None, mode='paper') -> None
"""
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from config import POSITION_SIZE_USD, TRADES_LOG_FILE, ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL
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


def _place_alpaca_order(
    ticker: str,
    action: Literal["buy", "sell"],
    quantity: int,
    fill_price: float,
    mode: Literal["paper", "live"],
) -> OrderResult:
    """Submit a market order to Alpaca and wait for fill."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    paper = (mode == "paper")
    client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=paper)

    side = OrderSide.BUY if action == "buy" else OrderSide.SELL
    req = MarketOrderRequest(symbol=ticker, qty=quantity, side=side, time_in_force=TimeInForce.DAY)

    ts = datetime.now(timezone.utc).isoformat()
    try:
        order = client.submit_order(req)
        # Use submitted fill_price as estimate; Alpaca fills may differ slightly
        actual_fill = float(order.filled_avg_price) if order.filled_avg_price else fill_price
        result = OrderResult(
            ticker=ticker, action=action, quantity=quantity, fill_price=actual_fill,
            timestamp=ts, mode=mode, success=True, error=None,
        )
        logger.info(f"[ALPACA {mode.upper()}] {action.upper()} {quantity} {ticker} @ {actual_fill:.2f} (order_id={order.id})")
    except Exception as e:
        result = OrderResult(
            ticker=ticker, action=action, quantity=quantity, fill_price=fill_price,
            timestamp=ts, mode=mode, success=False, error=str(e),
        )
        logger.error(f"[ALPACA {mode.upper()}] Order failed for {ticker}: {e}")

    _append_trade_log(result)
    return result


def place_order(
    ticker: str,
    action: Literal["buy", "sell"],
    quantity: int,
    fill_price: float,
    mode: Literal["paper", "live"] = "paper",
) -> OrderResult:
    """Place a buy or sell order via Alpaca (paper or live) if credentials are configured,
    otherwise fall back to local simulation."""
    if ALPACA_API_KEY:
        return _place_alpaca_order(ticker, action, quantity, fill_price, mode)

    # Local simulation fallback
    ts = datetime.now(timezone.utc).isoformat()
    result = OrderResult(
        ticker=ticker, action=action, quantity=quantity, fill_price=fill_price,
        timestamp=ts, mode="paper", success=True, error=None,
    )
    logger.info(f"[PAPER SIM] {action.upper()} {quantity} shares of {ticker} @ {fill_price:.2f}")
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
