"""
Trade log reader. Pairs buys with sells (FIFO per ticker) into closed trades.

Shared by risk.py (realized P&L for the circuit breakers) and
analysis/track_record.py (performance stats), so both see identical numbers.

    ClosedTrade                       dataclass: ticker, entry_date, exit_date, exit_ts, entry_price,
                                                 exit_price, quantity, pnl, ret, mode
    load_orders(path=None)            -> list[dict]        raw successful order records
    closed_trades(path=None)          -> list[ClosedTrade] FIFO-matched round trips
    realized_pnl(trades, since, until) -> float
"""
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from config import TRADES_LOG_FILE

logger = logging.getLogger(__name__)


@dataclass
class ClosedTrade:
    ticker: str
    entry_date: str        # 'YYYY-MM-DD' (UTC date of the buy)
    exit_date: str         # 'YYYY-MM-DD' (UTC date of the sell)
    exit_ts: str           # full ISO 8601 UTC timestamp of the sell
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float             # dollars
    ret: float             # simple return, e.g. 0.043 == +4.3%
    mode: str              # execution mode of the sell: 'sim' | 'paper' | 'live'


def load_orders(path: str | Path | None = None) -> list[dict]:
    """All successful order records from the trade log, in file order."""
    p = Path(path or TRADES_LOG_FILE)
    if not p.exists():
        return []
    orders = []
    with p.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(f"Skipping malformed trade log line: {line[:80]}")
                continue
            if rec.get("success"):
                orders.append(rec)
    return orders


def closed_trades(path: str | Path | None = None) -> list[ClosedTrade]:
    """Round trips, matched FIFO per ticker. Unmatched buys (open positions) are ignored."""
    open_buys: dict[str, list[dict]] = defaultdict(list)
    trades: list[ClosedTrade] = []

    for rec in load_orders(path):
        ticker = rec["ticker"]
        if rec["action"] == "buy":
            open_buys[ticker].append(rec)
            continue

        if not open_buys[ticker]:
            logger.warning(f"Sell for {ticker} with no matching buy — skipped")
            continue

        buy = open_buys[ticker].pop(0)
        qty = min(buy["quantity"], rec["quantity"]) or buy["quantity"]
        entry, exit_ = buy["fill_price"], rec["fill_price"]
        trades.append(ClosedTrade(
            ticker=ticker,
            entry_date=buy["timestamp"][:10],
            exit_date=rec["timestamp"][:10],
            exit_ts=rec["timestamp"],
            entry_price=entry,
            exit_price=exit_,
            quantity=qty,
            pnl=(exit_ - entry) * qty,
            ret=(exit_ - entry) / entry if entry else 0.0,
            mode=rec.get("mode", "sim"),
        ))
    return trades


def realized_pnl(trades: list[ClosedTrade], since: str | None = None, until: str | None = None) -> float:
    """Summed P&L of trades whose exit_date falls in [since, until] (inclusive, string dates)."""
    total = 0.0
    for t in trades:
        if since and t.exit_date < since:
            continue
        if until and t.exit_date > until:
            continue
        total += t.pnl
    return total
