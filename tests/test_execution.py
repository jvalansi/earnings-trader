import pytest
from unittest.mock import patch, MagicMock, call
from execution import place_order, execute_signals
from decision import EntrySignal, PositionAction
from state import Position


def _entry_signal(ticker="AAPL", should_enter=True, entry_price=100.0, initial_stop=97.0):
    return EntrySignal(ticker=ticker, should_enter=should_enter,
                       filters_passed={}, entry_price=entry_price, initial_stop=initial_stop)


def _position(ticker="AAPL", quantity=10):
    return Position(ticker=ticker, entry_price=100.0, current_stop=95.0,
                    entry_date="2026-01-01", day_count=3, quantity=quantity)


# --- place_order ---

def test_place_order_paper_returns_success():
    with patch("execution._append_trade_log"):
        result = place_order("AAPL", "buy", 10, fill_price=150.0)
    assert result.ticker == "AAPL"
    assert result.action == "buy"
    assert result.quantity == 10
    assert result.fill_price == 150.0
    assert result.mode == "paper"
    assert result.success is True
    assert result.error is None


def test_place_order_appends_to_trade_log():
    with patch("execution._append_trade_log") as mock_log:
        place_order("AAPL", "sell", 5, fill_price=200.0)
    mock_log.assert_called_once()


def test_place_order_live_raises():
    with pytest.raises(NotImplementedError):
        place_order("AAPL", "buy", 10, fill_price=150.0, mode="live")


# --- execute_signals ---

def test_execute_signals_buy_places_order_and_adds_position():
    sig = _entry_signal(entry_price=100.0)  # qty = max(1, int(1000/100)) = 10
    with patch("execution.place_order") as mock_order, \
         patch("execution.add_position") as mock_add, \
         patch("execution.notify"):
        execute_signals([sig], [], mode="paper")
    mock_order.assert_called_once_with("AAPL", "buy", 10, fill_price=100.0, mode="paper")
    mock_add.assert_called_once()


def test_execute_signals_buy_sends_notification():
    sig = _entry_signal()
    with patch("execution.place_order"), \
         patch("execution.add_position"), \
         patch("execution.notify") as mock_notify:
        execute_signals([sig], [], mode="paper")
    mock_notify.assert_called_once()
    assert "AAPL" in mock_notify.call_args[0][0]


def test_execute_signals_skips_no_entry_signals():
    sig = _entry_signal(should_enter=False, entry_price=None, initial_stop=None)
    with patch("execution.place_order") as mock_order:
        execute_signals([sig], [], mode="paper")
    mock_order.assert_not_called()


def test_execute_signals_sell_places_order_and_removes_position():
    act = PositionAction(ticker="AAPL", action="sell", new_stop=None, reason="stop_hit")
    with patch("execution.place_order") as mock_order, \
         patch("execution.remove_position") as mock_remove, \
         patch("execution.notify"), \
         patch("state.load_positions", return_value=[_position()]):
        execute_signals([], [act], current_prices={"AAPL": 110.0}, mode="paper")
    mock_order.assert_called_once_with("AAPL", "sell", 10, fill_price=110.0, mode="paper")
    mock_remove.assert_called_once_with("AAPL")


def test_execute_signals_sell_sends_notification():
    act = PositionAction(ticker="AAPL", action="sell", new_stop=None, reason="max_days_reached")
    with patch("execution.place_order"), \
         patch("execution.remove_position"), \
         patch("execution.notify") as mock_notify, \
         patch("state.load_positions", return_value=[_position()]):
        execute_signals([], [act], current_prices={"AAPL": 110.0}, mode="paper")
    mock_notify.assert_called_once()
    assert "AAPL" in mock_notify.call_args[0][0]


def test_execute_signals_update_stop():
    act = PositionAction(ticker="AAPL", action="update_stop", new_stop=98.0, reason="trailing_stop_updated")
    with patch("execution.update_stop") as mock_update, \
         patch("execution.notify"):
        execute_signals([], [act], current_prices={"AAPL": 110.0}, mode="paper")
    mock_update.assert_called_once_with("AAPL", 98.0)
