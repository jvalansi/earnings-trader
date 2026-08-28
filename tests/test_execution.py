import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import execution
from execution import place_order, execute_signals, resolve_mode
from decision import EntrySignal, PositionAction
from state import Position
from config import POSITION_SIZE_USD


def _entry_signal(ticker="AAPL", should_enter=True, entry_price=100.0, initial_stop=97.0):
    return EntrySignal(ticker=ticker, should_enter=should_enter,
                       filters_passed={}, entry_price=entry_price, initial_stop=initial_stop)


def _position(ticker="AAPL", quantity=10, mode="paper"):
    return Position(ticker=ticker, entry_price=100.0, current_stop=95.0,
                    entry_date="2026-01-01", day_count=3, quantity=quantity, mode=mode)


def _alpaca_order(status="filled", filled_qty="10", filled_avg_price="101.0", order_id="abc"):
    return SimpleNamespace(
        id=order_id,
        status=SimpleNamespace(value=status),
        filled_qty=filled_qty,
        filled_avg_price=filled_avg_price,
    )


@pytest.fixture
def with_broker(monkeypatch):
    """Enable the Alpaca code path with a mock client."""
    monkeypatch.setattr(execution, "ALPACA_API_KEY", "key")
    monkeypatch.setattr(execution, "ALPACA_SECRET_KEY", "secret")
    client = MagicMock()
    with patch("alpaca.trading.client.TradingClient", return_value=client):
        yield client


# --- resolve_mode ---

def test_resolve_mode_falls_back_to_sim_without_keys():
    assert resolve_mode("paper") == "sim"


def test_resolve_mode_paper_uses_broker_when_keys_present(with_broker):
    assert resolve_mode("paper") == "paper"


def test_resolve_mode_live_requires_keys():
    with pytest.raises(RuntimeError, match="ALPACA_API_KEY"):
        resolve_mode("live")


def test_resolve_mode_live_requires_confirmation(with_broker, monkeypatch):
    monkeypatch.setattr(execution, "LIVE_TRADING_CONFIRMED", False)
    with pytest.raises(RuntimeError, match="LIVE_TRADING_CONFIRMED"):
        resolve_mode("live")


def test_resolve_mode_live_allowed_when_confirmed(with_broker, monkeypatch):
    monkeypatch.setattr(execution, "LIVE_TRADING_CONFIRMED", True)
    assert resolve_mode("live") == "live"


# --- place_order ---

def test_place_order_without_keys_simulates():
    result = place_order("AAPL", "buy", 10, fill_price=150.0)
    assert result.mode == "sim"
    assert result.fill_price == 150.0
    assert result.slippage_pct == 0.0
    assert result.success is True
    assert result.error is None


def test_place_order_appends_to_trade_log():
    with patch("execution._append_trade_log") as mock_log:
        place_order("AAPL", "sell", 5, fill_price=200.0)
    mock_log.assert_called_once()


def test_place_order_live_without_credentials_raises():
    with pytest.raises(RuntimeError):
        place_order("AAPL", "buy", 10, fill_price=150.0, mode="live")


def test_place_order_broker_records_actual_fill_and_slippage(with_broker):
    with_broker.submit_order.return_value = _alpaca_order(filled_avg_price="101.0")
    result = place_order("AAPL", "buy", 10, fill_price=100.0, mode="paper")
    assert result.mode == "paper"
    assert result.fill_price == 101.0
    assert result.slippage_pct == pytest.approx(0.01)   # paid 1% above the signal price
    assert result.success is True


def test_place_order_sell_slippage_sign(with_broker):
    with_broker.submit_order.return_value = _alpaca_order(filled_avg_price="99.0")
    result = place_order("AAPL", "sell", 10, fill_price=100.0, mode="paper")
    assert result.slippage_pct == pytest.approx(0.01)   # sold 1% below the signal price


def test_place_order_polls_until_filled(with_broker):
    with_broker.submit_order.return_value = _alpaca_order(status="accepted", filled_qty="0", filled_avg_price=None)
    with_broker.get_order_by_id.return_value = _alpaca_order(status="filled", filled_avg_price="100.5")
    with patch("execution.time.sleep"):
        result = place_order("AAPL", "buy", 10, fill_price=100.0, mode="paper")
    assert with_broker.get_order_by_id.called
    assert result.success is True
    assert result.fill_price == 100.5


def test_place_order_unfilled_order_is_not_success(with_broker):
    with_broker.submit_order.return_value = _alpaca_order(status="rejected", filled_qty="0", filled_avg_price=None)
    result = place_order("AAPL", "buy", 10, fill_price=100.0, mode="paper")
    assert result.success is False
    assert "rejected" in result.error


def test_place_order_broker_exception_is_captured(with_broker):
    with_broker.submit_order.side_effect = RuntimeError("connection reset")
    result = place_order("AAPL", "buy", 10, fill_price=100.0, mode="paper")
    assert result.success is False
    assert "connection reset" in result.error


# --- sizing ---

def test_size_entry_uses_notional_for_fractionable_symbols(monkeypatch):
    monkeypatch.setattr(execution.broker, "is_fractionable", lambda t, mode="paper": True)
    quantity, notional = execution._size_entry("ASML", 1800.0, "paper")
    assert notional == POSITION_SIZE_USD
    assert quantity == pytest.approx(POSITION_SIZE_USD / 1800.0)


def test_size_entry_rounds_down_for_whole_share_symbols(monkeypatch):
    monkeypatch.setattr(execution.broker, "is_fractionable", lambda t, mode="paper": False)
    quantity, notional = execution._size_entry("JRSH", 12.0, "paper")
    assert notional is None
    assert quantity == int(POSITION_SIZE_USD / 12.0)


def test_size_entry_never_returns_zero_shares(monkeypatch):
    monkeypatch.setattr(execution.broker, "is_fractionable", lambda t, mode="paper": False)
    quantity, notional = execution._size_entry("ASML", 1800.0, "paper")
    assert quantity == 1


def test_place_order_sends_notional_request(with_broker):
    with_broker.submit_order.return_value = _alpaca_order(filled_qty="0.2757", filled_avg_price="1814.0")
    result = place_order("ASML", "buy", 0.2757, fill_price=1800.0, mode="paper", notional=500.0)
    request = with_broker.submit_order.call_args[0][0]
    assert request.notional == 500.0
    assert request.qty is None
    assert result.quantity == pytest.approx(0.2757)


def test_fmt_qty():
    assert execution._fmt_qty(5.0) == "5"
    assert execution._fmt_qty(0.2757) == "0.2757"


# --- execute_signals ---

def test_execute_signals_buy_sizes_position_from_capital(monkeypatch):
    monkeypatch.setattr(execution.broker, "is_fractionable", lambda t, mode="paper": False)
    sig = _entry_signal(entry_price=100.0)
    expected_qty = max(1, int(POSITION_SIZE_USD / 100.0))
    with patch("execution.place_order") as mock_order, \
         patch("execution.add_position") as mock_add, \
         patch("execution.notify"):
        mock_order.return_value = execution.OrderResult(
            ticker="AAPL", action="buy", quantity=expected_qty, fill_price=100.0,
            timestamp="", mode="sim", success=True, error=None, intended_price=100.0)
        execute_signals([sig], [], mode="paper")
    mock_order.assert_called_once_with("AAPL", "buy", expected_qty, fill_price=100.0,
                                       mode="paper", notional=None)
    mock_add.assert_called_once()


def test_execute_signals_buy_passes_notional_for_fractionable(monkeypatch):
    monkeypatch.setattr(execution.broker, "is_fractionable", lambda t, mode="paper": True)
    sig = _entry_signal(entry_price=100.0)
    with patch("execution.place_order") as mock_order, \
         patch("execution.add_position"), \
         patch("execution.notify"):
        mock_order.return_value = execution.OrderResult(
            ticker="AAPL", action="buy", quantity=5.0, fill_price=100.0,
            timestamp="", mode="paper", success=True, error=None, intended_price=100.0)
        execute_signals([sig], [], mode="paper")
    assert mock_order.call_args.kwargs["notional"] == POSITION_SIZE_USD


def test_execute_signals_buy_uses_actual_fill_as_entry_price():
    sig = _entry_signal(entry_price=100.0)
    with patch("execution.place_order") as mock_order, \
         patch("execution.add_position") as mock_add, \
         patch("execution.notify"):
        mock_order.return_value = execution.OrderResult(
            ticker="AAPL", action="buy", quantity=7, fill_price=102.5,
            timestamp="", mode="paper", success=True, error=None, intended_price=100.0)
        execute_signals([sig], [], mode="paper")
    position = mock_add.call_args[0][0]
    assert position.entry_price == 102.5
    assert position.quantity == 7


def test_execute_signals_failed_buy_opens_no_position():
    sig = _entry_signal()
    with patch("execution.place_order") as mock_order, \
         patch("execution.add_position") as mock_add, \
         patch("execution.notify"):
        mock_order.return_value = execution.OrderResult(
            ticker="AAPL", action="buy", quantity=10, fill_price=100.0,
            timestamp="", mode="paper", success=False, error="rejected", intended_price=100.0)
        execute_signals([sig], [], mode="paper")
    mock_add.assert_not_called()


def test_execute_signals_buy_sends_notification():
    sig = _entry_signal()
    with patch("execution.place_order") as mock_order, \
         patch("execution.add_position"), \
         patch("execution.notify") as mock_notify:
        mock_order.return_value = execution.OrderResult(
            ticker="AAPL", action="buy", quantity=10, fill_price=100.0,
            timestamp="", mode="sim", success=True, error=None, intended_price=100.0)
        execute_signals([sig], [], mode="paper")
    mock_notify.assert_called_once()
    assert "AAPL" in mock_notify.call_args[0][0]


def test_execute_signals_skips_no_entry_signals():
    sig = _entry_signal(should_enter=False, entry_price=None, initial_stop=None)
    with patch("execution.place_order") as mock_order:
        execute_signals([sig], [], mode="paper")
    mock_order.assert_not_called()


def test_execute_signals_blocks_entries_when_risk_tripped():
    import risk
    sig = _entry_signal()
    status = risk.RiskStatus(entries_allowed=False, reasons=["drawdown 20.0% breached limit 15.0%"])
    with patch("execution.place_order") as mock_order, \
         patch("execution.notify") as mock_notify:
        execute_signals([sig], [], mode="paper", risk_status=status)
    mock_order.assert_not_called()
    assert "blocked" in mock_notify.call_args[0][0].lower()


def test_execute_signals_exits_run_even_when_risk_tripped():
    import risk
    act = PositionAction(ticker="AAPL", action="sell", new_stop=None, reason="stop_hit")
    status = risk.RiskStatus(entries_allowed=False, reasons=["daily loss"])
    with patch("execution.place_order") as mock_order, \
         patch("execution.remove_position") as mock_remove, \
         patch("execution.notify"), \
         patch("state.load_positions", return_value=[_position()]):
        mock_order.return_value = execution.OrderResult(
            ticker="AAPL", action="sell", quantity=10, fill_price=110.0,
            timestamp="", mode="sim", success=True, error=None, intended_price=110.0)
        execute_signals([], [act], current_prices={"AAPL": 110.0}, mode="paper", risk_status=status)
    mock_order.assert_called_once_with("AAPL", "sell", 10, fill_price=110.0, mode="paper")
    mock_remove.assert_called_once_with("AAPL")


def test_execute_signals_sell_routes_to_venue_of_entry():
    """A position opened in simulation is not sold into the broker."""
    act = PositionAction(ticker="AAPL", action="sell", new_stop=None, reason="stop_hit")
    with patch("execution.place_order") as mock_order, \
         patch("execution.remove_position"), \
         patch("execution.notify"), \
         patch("state.load_positions", return_value=[_position(mode="sim")]):
        mock_order.return_value = execution.OrderResult(
            ticker="AAPL", action="sell", quantity=10, fill_price=110.0,
            timestamp="", mode="sim", success=True, error=None, intended_price=110.0)
        execute_signals([], [act], current_prices={"AAPL": 110.0}, mode="paper")
    assert mock_order.call_args.kwargs["mode"] == "sim"


def test_execute_signals_buy_records_execution_venue():
    sig = _entry_signal()
    with patch("execution.place_order") as mock_order, \
         patch("execution.add_position") as mock_add, \
         patch("execution.notify"):
        mock_order.return_value = execution.OrderResult(
            ticker="AAPL", action="buy", quantity=10, fill_price=100.0,
            timestamp="", mode="paper", success=True, error=None, intended_price=100.0)
        execute_signals([sig], [], mode="paper")
    assert mock_add.call_args[0][0].mode == "paper"


def test_execute_signals_failed_sell_keeps_position():
    act = PositionAction(ticker="AAPL", action="sell", new_stop=None, reason="stop_hit")
    with patch("execution.place_order") as mock_order, \
         patch("execution.remove_position") as mock_remove, \
         patch("execution.notify"), \
         patch("state.load_positions", return_value=[_position()]):
        mock_order.return_value = execution.OrderResult(
            ticker="AAPL", action="sell", quantity=10, fill_price=110.0,
            timestamp="", mode="paper", success=False, error="rejected", intended_price=110.0)
        execute_signals([], [act], current_prices={"AAPL": 110.0}, mode="paper")
    mock_remove.assert_not_called()


def test_execute_signals_sell_sends_notification():
    act = PositionAction(ticker="AAPL", action="sell", new_stop=None, reason="max_days_reached")
    with patch("execution.place_order") as mock_order, \
         patch("execution.remove_position"), \
         patch("execution.notify") as mock_notify, \
         patch("state.load_positions", return_value=[_position()]):
        mock_order.return_value = execution.OrderResult(
            ticker="AAPL", action="sell", quantity=10, fill_price=110.0,
            timestamp="", mode="sim", success=True, error=None, intended_price=110.0)
        execute_signals([], [act], current_prices={"AAPL": 110.0}, mode="paper")
    mock_notify.assert_called_once()
    assert "AAPL" in mock_notify.call_args[0][0]


def test_execute_signals_update_stop():
    act = PositionAction(ticker="AAPL", action="update_stop", new_stop=98.0, reason="trailing_stop_updated")
    with patch("execution.update_stop") as mock_update, \
         patch("execution.notify"):
        execute_signals([], [act], current_prices={"AAPL": 110.0}, mode="paper")
    mock_update.assert_called_once_with("AAPL", 98.0)
