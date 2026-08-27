from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import broker
import execution
from state import Position


def _account(status="ACTIVE", equity="5000", cash="1000", buying_power="10000",
             trading_blocked=False, account_blocked=False):
    return SimpleNamespace(status=status, equity=equity, cash=cash, buying_power=buying_power,
                           trading_blocked=trading_blocked, account_blocked=account_blocked)


def _remote(symbol, qty, price="100"):
    return SimpleNamespace(symbol=symbol, qty=str(qty), avg_entry_price=price)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(execution, "ALPACA_API_KEY", "key")
    monkeypatch.setattr(execution, "ALPACA_SECRET_KEY", "secret")
    mock = MagicMock()
    mock.get_account.return_value = _account()
    mock.get_all_positions.return_value = []
    monkeypatch.setattr(broker, "_client", lambda paper: mock)
    return mock


def _position(ticker, quantity, mode="paper"):
    return Position(ticker=ticker, entry_price=100.0, current_stop=95.0,
                    entry_date="2026-05-01", day_count=1, quantity=quantity, mode=mode)


def test_preflight_without_credentials_reports_sim():
    info = broker.preflight("paper")
    assert info.mode == "sim"
    assert "simulated" in info.reason


def test_preflight_live_without_confirmation_is_blocked(client):
    info = broker.preflight("live")
    assert info.blocked is True
    assert "LIVE_TRADING_CONFIRMED" in info.reason


def test_preflight_paper_reports_account(client):
    info = broker.preflight("paper")
    assert info.mode == "paper"
    assert info.equity == 5000.0
    assert info.blocked is False
    assert "paper-api" in info.endpoint


def test_preflight_flags_blocked_account(client):
    client.get_account.return_value = _account(trading_blocked=True)
    info = broker.preflight("paper")
    assert info.blocked is True


def test_preflight_handles_unreachable_broker(client):
    client.get_account.side_effect = RuntimeError("timeout")
    info = broker.preflight("paper")
    assert info.blocked is True
    assert "unreachable" in info.reason


def test_reconcile_clean(client):
    client.get_all_positions.return_value = [_remote("AAPL", 10)]
    drift = broker.reconcile([_position("AAPL", 10)])
    assert not any(drift.values())


def test_reconcile_detects_drift(client):
    client.get_all_positions.return_value = [_remote("AAPL", 5), _remote("MSFT", 3)]
    drift = broker.reconcile([_position("AAPL", 10), _position("TSLA", 2)])
    assert drift["only_local"] == ["TSLA"]
    assert drift["only_broker"] == ["MSFT"]
    assert drift["qty_mismatch"] == ["AAPL: local 10 vs broker 5"]


def test_reconcile_ignores_simulated_positions(client):
    client.get_all_positions.return_value = []
    drift = broker.reconcile([_position("AAPL", 10, mode="sim")])
    assert not any(drift.values())


def test_reconcile_without_broker_reports_everything_local():
    drift = broker.reconcile([_position("AAPL", 10)])
    assert drift["only_local"] == ["AAPL"]


def test_summary_line_shapes():
    assert "SIM" in broker.summary_line(broker.BrokerInfo(mode="sim", endpoint="local", reason="no keys"))
    blocked = broker.BrokerInfo(mode="live", endpoint="x", blocked=True, reason="nope")
    assert "unusable" in broker.summary_line(blocked)
