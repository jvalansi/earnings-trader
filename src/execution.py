"""
Order placement and trade logging. Routes to Alpaca (live/paper) or local simulation.

    OrderResult                                                  dataclass: ticker, action, quantity,
                                                                 fill_price, intended_price, slippage_pct,
                                                                 timestamp, mode, status, order_id, success, error

    resolve_mode(mode) -> ('live'|'paper'|'sim')                 what will actually happen, given credentials
    place_order(ticker, action, quantity, fill_price, mode='paper', notional=None) -> OrderResult
    execute_signals(signals, actions, current_prices=None, mode='paper', risk_status=None) -> None

Modes:
    live   real money via Alpaca. Requires ALPACA_* keys and LIVE_TRADING_CONFIRMED=yes.
           Never silently degrades — a live order that cannot reach the broker raises.
    paper  Alpaca paper endpoint when keys are set, otherwise falls back to 'sim'.
    sim    local simulation: fills assumed at the intended price, no broker involved.

Exits are placed in the mode recorded on the position, not the mode of the current run.

Entries are sized by dollar amount (POSITION_SIZE_USD). Where the broker supports
fractional shares the order is sent as a notional order, so a $500 slot buys $500 of a
$1,800 stock instead of rounding to one share or skipping it.

Entries are blocked when risk.py reports a breach; exits are always executed.
"""
import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import broker
import risk
from config import (
    POSITION_SIZE_USD,
    TRADES_LOG_FILE,
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    LIVE_TRADING_CONFIRMED,
    ORDER_FILL_TIMEOUT_SEC,
)
from notifier import notify
from decision import EntrySignal, PositionAction
from state import Position, add_position, remove_position, update_stop

logger = logging.getLogger(__name__)

_log_path = Path(TRADES_LOG_FILE)

Mode = Literal["live", "paper", "sim"]

_TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected", "done_for_day"}


@dataclass
class OrderResult:
    ticker: str
    action: Literal["buy", "sell"]
    quantity: float             # fractional where the broker supports it
    fill_price: float           # actual fill (broker) or assumed fill (sim)
    timestamp: str              # ISO 8601 UTC
    mode: Mode
    success: bool
    error: str | None
    intended_price: float = 0.0     # price the signal was generated at
    slippage_pct: float | None = None  # positive == worse than intended
    status: str | None = None       # broker order status
    order_id: str | None = None


def _append_trade_log(result: OrderResult) -> None:
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    with _log_path.open("a") as f:
        f.write(json.dumps(asdict(result)) + "\n")


def _fmt_qty(quantity: float) -> str:
    """Share counts read as integers when they are whole, and as decimals when fractional."""
    return str(int(quantity)) if float(quantity).is_integer() else f"{quantity:.4f}"


def _slippage(action: str, intended: float, fill: float) -> float | None:
    """Signed slippage as a fraction of the intended price. Positive == paid up / sold down."""
    if not intended:
        return None
    raw = (fill - intended) / intended
    return raw if action == "buy" else -raw


def resolve_mode(mode: str) -> Mode:
    """Map the requested mode to what will actually execute, given available credentials."""
    if mode == "sim":
        return "sim"
    if mode == "live":
        if not (ALPACA_API_KEY and ALPACA_SECRET_KEY):
            raise RuntimeError("live mode requested but ALPACA_API_KEY/ALPACA_SECRET_KEY are not set")
        if not LIVE_TRADING_CONFIRMED:
            raise RuntimeError("live mode requested but LIVE_TRADING_CONFIRMED is not set to 'yes'")
        return "live"
    if ALPACA_API_KEY and ALPACA_SECRET_KEY:
        return "paper"
    return "sim"


def _place_alpaca_order(
    ticker: str,
    action: Literal["buy", "sell"],
    quantity: float,
    intended_price: float,
    mode: Literal["live", "paper"],
    notional: float | None = None,
) -> OrderResult:
    """Submit a market order to Alpaca and poll until it fills or the timeout expires.

    `notional` buys a dollar amount rather than a share count (fractional symbols only).
    """
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=(mode == "paper"))
    side = OrderSide.BUY if action == "buy" else OrderSide.SELL
    if notional is not None:
        req = MarketOrderRequest(symbol=ticker, notional=round(notional, 2), side=side,
                                 time_in_force=TimeInForce.DAY)
    else:
        req = MarketOrderRequest(symbol=ticker, qty=quantity, side=side,
                                 time_in_force=TimeInForce.DAY)

    ts = datetime.now(timezone.utc).isoformat()
    try:
        order = client.submit_order(req)
        deadline = time.monotonic() + ORDER_FILL_TIMEOUT_SEC
        while order.status.value not in _TERMINAL_STATUSES and time.monotonic() < deadline:
            time.sleep(1)
            order = client.get_order_by_id(order.id)

        status = order.status.value
        filled_qty = float(order.filled_qty or 0)
        fill_price = float(order.filled_avg_price) if order.filled_avg_price else intended_price
        filled = status == "filled" or filled_qty > 0

        result = OrderResult(
            ticker=ticker, action=action, quantity=filled_qty or quantity,
            fill_price=fill_price, timestamp=ts, mode=mode,
            success=filled, error=None if filled else f"order not filled (status={status})",
            intended_price=intended_price,
            slippage_pct=_slippage(action, intended_price, fill_price) if filled else None,
            status=status, order_id=str(order.id),
        )
        slip = f"{result.slippage_pct:+.2%}" if result.slippage_pct is not None else "n/a"
        logger.info(
            f"[ALPACA {mode.upper()}] {action.upper()} {_fmt_qty(result.quantity)} {ticker} @ {fill_price:.2f} "
            f"(intended {intended_price:.2f}, slippage {slip}, status={status}, id={order.id})"
        )
        if not filled:
            logger.error(f"[ALPACA {mode.upper()}] {ticker} did not fill within {ORDER_FILL_TIMEOUT_SEC}s: {status}")
    except Exception as e:
        result = OrderResult(
            ticker=ticker, action=action, quantity=quantity, fill_price=intended_price,
            timestamp=ts, mode=mode, success=False, error=str(e),
            intended_price=intended_price, status="error",
        )
        logger.error(f"[ALPACA {mode.upper()}] Order failed for {ticker}: {e}")

    _append_trade_log(result)
    return result


def place_order(
    ticker: str,
    action: Literal["buy", "sell"],
    quantity: float,
    fill_price: float,
    mode: str = "paper",
    notional: float | None = None,
) -> OrderResult:
    """Place a buy or sell order. `fill_price` is the intended (signal) price."""
    effective = resolve_mode(mode)

    if effective in ("live", "paper"):
        return _place_alpaca_order(ticker, action, quantity, fill_price, effective, notional)

    ts = datetime.now(timezone.utc).isoformat()
    result = OrderResult(
        ticker=ticker, action=action, quantity=quantity, fill_price=fill_price,
        timestamp=ts, mode="sim", success=True, error=None,
        intended_price=fill_price, slippage_pct=0.0, status="sim_filled",
    )
    logger.info(f"[SIM] {action.upper()} {_fmt_qty(quantity)} shares of {ticker} @ {fill_price:.2f}")
    _append_trade_log(result)
    return result


def _size_entry(ticker: str, price: float, mode: str) -> tuple[float, float | None]:
    """Size one entry to POSITION_SIZE_USD.

    Returns (quantity, notional). Fractional symbols are ordered by dollar amount, so a
    $500 slot buys $500 of an $1,800 stock. Whole-share symbols round down and warn when
    a single share overshoots the slot.
    """
    if broker.is_fractionable(ticker, mode):
        return POSITION_SIZE_USD / price, POSITION_SIZE_USD

    quantity = max(1, int(POSITION_SIZE_USD / price))
    notional = quantity * price
    if notional > POSITION_SIZE_USD * 1.5:
        logger.warning(
            f"{ticker} @ ${price:.2f}: 1 share is ${notional:.0f} vs ${POSITION_SIZE_USD:.0f} "
            f"target slot and the symbol is not fractionable — position is oversized"
        )
    return quantity, None


def execute_signals(
    signals: list[EntrySignal],
    actions: list[PositionAction],
    current_prices: dict[str, float] | None = None,
    mode: str = "paper",
    risk_status: risk.RiskStatus | None = None,
) -> None:
    """Process a batch of entry signals and position actions.

    Places orders and updates state for each. current_prices is required for sell
    orders (to log the fill price). Entries are skipped while risk controls are
    tripped; exits and stop updates always run.
    """
    # --- BUY: process entry signals ---
    wanted = [s for s in signals if s.should_enter]
    if wanted:
        allowed = risk_status.entries_allowed if risk_status else risk.entries_allowed()
        if not allowed:
            reasons = "; ".join(risk_status.reasons) if risk_status else "risk controls tripped"
            tickers = ", ".join(s.ticker for s in wanted)
            logger.error(f"Entries blocked by risk controls ({reasons}); skipped: {tickers}")
            notify(f"🛑 *Entries blocked* — {reasons}\nSkipped: {tickers}")
            wanted = []

    for sig in wanted:
        price = sig.entry_price
        quantity, notional = _size_entry(sig.ticker, price, mode)

        result = place_order(sig.ticker, "buy", quantity, fill_price=price, mode=mode, notional=notional)
        if not result.success:
            logger.error(f"Buy failed for {sig.ticker}, no position opened: {result.error}")
            notify(f"⚠️ *BUY FAILED {sig.ticker}* — {result.error}")
            continue

        new_pos = Position(
            ticker=sig.ticker,
            entry_price=result.fill_price,
            current_stop=sig.initial_stop,
            entry_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            day_count=0,
            quantity=result.quantity,
            mode=result.mode,
        )
        add_position(new_pos)
        logger.info(
            f"Opened position: {sig.ticker} @ {result.fill_price:.2f}, "
            f"stop={sig.initial_stop:.2f}, qty={_fmt_qty(result.quantity)}"
        )
        notify(
            f"📈 *BUY {sig.ticker}* — {_fmt_qty(result.quantity)} shares @ ${result.fill_price:.2f} "
            f"| stop ${sig.initial_stop:.2f}"
        )

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
            # Exit where the entry happened: a position opened in simulation is not
            # sold into a broker account that never held it.
            exit_mode = pos.mode if pos else mode

            result = place_order(act.ticker, "sell", qty, fill_price=fill_price, mode=exit_mode)
            if not result.success:
                # Keep the position in state so the next cycle retries the exit.
                logger.error(f"Sell failed for {act.ticker}, position kept open: {result.error}")
                notify(f"⚠️ *SELL FAILED {act.ticker}* — {result.error} (position kept open)")
                continue

            remove_position(act.ticker)
            logger.info(f"Closed position: {act.ticker} @ {result.fill_price:.2f}, reason={act.reason}")
            notify(
                f"📉 *SELL {act.ticker}* — {_fmt_qty(result.quantity)} shares @ ${result.fill_price:.2f} "
                f"| reason: {act.reason}"
            )

        elif act.action == "update_stop":
            update_stop(act.ticker, act.new_stop)
            logger.info(f"Updated stop for {act.ticker} to {act.new_stop:.2f}")
