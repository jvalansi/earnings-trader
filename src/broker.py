"""
Broker connectivity preflight and position reconciliation (Alpaca).

    BrokerInfo                     dataclass: mode, endpoint, account_status, equity, cash,
                                              buying_power, open_positions, blocked, reason
    preflight(mode)                -> BrokerInfo   verify credentials/account before trading
    broker_positions()             -> dict[str, dict]  ticker -> {qty, avg_entry_price}
    is_fractionable(ticker, mode)  -> bool         can this symbol be bought by dollar amount?
    reconcile(local_positions)     -> dict         {'only_local': [...], 'only_broker': [...], 'qty_mismatch': [...]}
"""
import logging
from dataclasses import dataclass

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY

logger = logging.getLogger(__name__)


@dataclass
class BrokerInfo:
    mode: str                 # 'live' | 'paper' | 'sim'
    endpoint: str
    account_status: str | None = None
    equity: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    open_positions: int | None = None
    blocked: bool = False     # account cannot trade
    reason: str | None = None


def _client(paper: bool):
    from alpaca.trading.client import TradingClient
    return TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=paper)


def preflight(mode: str = "paper") -> BrokerInfo:
    """Check that the broker is reachable and the account can trade.

    Returns a BrokerInfo describing what execution will actually do. Never raises for
    a mode the credentials cannot support — `blocked`/`reason` carry that instead, so
    callers decide whether to abort.
    """
    from execution import resolve_mode

    try:
        effective = resolve_mode(mode)
    except RuntimeError as e:
        return BrokerInfo(mode=mode, endpoint="n/a", blocked=True, reason=str(e))

    if effective == "sim":
        return BrokerInfo(
            mode="sim", endpoint="local simulation",
            reason="no Alpaca credentials — orders are simulated, nothing reaches a broker",
        )

    endpoint = "https://api.alpaca.markets" if effective == "live" else "https://paper-api.alpaca.markets"
    try:
        client = _client(paper=(effective == "paper"))
        acct = client.get_account()
        positions = client.get_all_positions()
    except Exception as e:
        return BrokerInfo(mode=effective, endpoint=endpoint, blocked=True, reason=f"broker unreachable: {e}")

    blocked = bool(getattr(acct, "trading_blocked", False) or getattr(acct, "account_blocked", False))
    return BrokerInfo(
        mode=effective,
        endpoint=endpoint,
        account_status=str(acct.status),
        equity=float(acct.equity),
        cash=float(acct.cash),
        buying_power=float(acct.buying_power),
        open_positions=len(positions),
        blocked=blocked,
        reason="account is blocked from trading" if blocked else None,
    )


_fractionable_cache: dict[str, bool] = {}


def is_fractionable(ticker: str, mode: str = "paper") -> bool:
    """Whether Alpaca accepts notional (dollar-denominated) orders for this symbol.

    False without a broker: local simulation has no asset database to consult.
    """
    from execution import resolve_mode

    if ticker in _fractionable_cache:
        return _fractionable_cache[ticker]
    try:
        effective = resolve_mode(mode)
    except RuntimeError:
        return False
    if effective == "sim":
        return False
    try:
        asset = _client(paper=(effective == "paper")).get_asset(ticker)
        result = bool(asset.fractionable and asset.tradable)
    except Exception as e:
        logger.warning(f"Could not check fractionability for {ticker}: {e}")
        result = False
    _fractionable_cache[ticker] = result
    return result


def broker_positions(mode: str = "paper") -> dict[str, dict]:
    """Open positions held at the broker, keyed by ticker."""
    from execution import resolve_mode

    if resolve_mode(mode) == "sim":
        return {}
    client = _client(paper=(resolve_mode(mode) == "paper"))
    return {
        p.symbol: {"qty": int(float(p.qty)), "avg_entry_price": float(p.avg_entry_price)}
        for p in client.get_all_positions()
    }


def reconcile(local_positions, mode: str = "paper") -> dict:
    """Compare broker-executed positions in data/positions.json against the broker.

    Any non-empty bucket means local state and the broker disagree — the bot would
    manage stops for shares it does not hold, or leave real shares unmanaged.
    Simulated positions are excluded; they are expected to be absent at the broker.
    """
    remote = broker_positions(mode)
    # Simulated positions were never sent to the broker, so they are not drift.
    local = {p.ticker: p for p in local_positions if getattr(p, "mode", "sim") != "sim"}

    only_local = sorted(set(local) - set(remote))
    only_broker = sorted(set(remote) - set(local))
    qty_mismatch = sorted(
        f"{t}: local {local[t].quantity} vs broker {remote[t]['qty']}"
        for t in set(local) & set(remote)
        if local[t].quantity != remote[t]["qty"]
    )
    return {"only_local": only_local, "only_broker": only_broker, "qty_mismatch": qty_mismatch}


def summary_line(info: BrokerInfo) -> str:
    """One-line broker summary for logs and notifications."""
    if info.mode == "sim":
        return f"⚪ Broker: SIM — {info.reason}"
    if info.blocked:
        return f"🛑 Broker: {info.mode.upper()} unusable — {info.reason}"
    return (
        f"🟢 Broker: Alpaca {info.mode} ({info.endpoint}) | account {info.account_status} | "
        f"equity ${info.equity:,.0f} | buying power ${info.buying_power:,.0f} | "
        f"{info.open_positions} open position(s)"
    )
