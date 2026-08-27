import json

import pytest

import trade_history
from trade_history import closed_trades, load_orders, realized_pnl


def _order(ticker, action, price, ts, qty=10, success=True, mode="sim", **extra):
    return {"ticker": ticker, "action": action, "quantity": qty, "fill_price": price,
            "timestamp": ts, "mode": mode, "success": success, "error": None, **extra}


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "trades.jsonl"
    monkeypatch.setattr(trade_history, "TRADES_LOG_FILE", str(path))

    def write(orders):
        path.write_text("".join(json.dumps(o) + "\n" for o in orders))
    return write


def test_missing_log_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_history, "TRADES_LOG_FILE", str(tmp_path / "nope.jsonl"))
    assert closed_trades() == []


def test_pairs_buy_and_sell(log):
    log([_order("AAPL", "buy", 100.0, "2026-05-01T13:30:00+00:00"),
         _order("AAPL", "sell", 110.0, "2026-05-10T13:30:00+00:00")])
    (t,) = closed_trades()
    assert t.pnl == pytest.approx(100.0)
    assert t.ret == pytest.approx(0.10)
    assert t.entry_date == "2026-05-01" and t.exit_date == "2026-05-10"


def test_open_position_is_not_a_closed_trade(log):
    log([_order("AAPL", "buy", 100.0, "2026-05-01T13:30:00+00:00")])
    assert closed_trades() == []


def test_failed_orders_are_ignored(log):
    log([_order("AAPL", "buy", 100.0, "2026-05-01T13:30:00+00:00", success=False),
         _order("AAPL", "sell", 110.0, "2026-05-10T13:30:00+00:00")])
    assert closed_trades() == []


def test_fifo_matching_across_repeat_tickers(log):
    log([_order("AAPL", "buy", 100.0, "2026-05-01T13:30:00+00:00"),
         _order("AAPL", "sell", 110.0, "2026-05-10T13:30:00+00:00"),
         _order("AAPL", "buy", 200.0, "2026-06-01T13:30:00+00:00"),
         _order("AAPL", "sell", 180.0, "2026-06-10T13:30:00+00:00")])
    first, second = closed_trades()
    assert first.entry_price == 100.0 and second.entry_price == 200.0
    assert second.ret == pytest.approx(-0.10)


def test_malformed_lines_are_skipped(log, tmp_path):
    log([_order("AAPL", "buy", 100.0, "2026-05-01T13:30:00+00:00")])
    path = tmp_path / "trades.jsonl"
    path.write_text(path.read_text() + "not json\n" +
                    json.dumps(_order("AAPL", "sell", 110.0, "2026-05-10T13:30:00+00:00")) + "\n")
    assert len(closed_trades()) == 1


def test_realized_pnl_window(log):
    log([_order("AAPL", "buy", 100.0, "2026-05-01T13:30:00+00:00"),
         _order("AAPL", "sell", 110.0, "2026-05-10T13:30:00+00:00"),
         _order("MSFT", "buy", 100.0, "2026-06-01T13:30:00+00:00"),
         _order("MSFT", "sell", 90.0, "2026-06-10T13:30:00+00:00")])
    trades = closed_trades()
    assert realized_pnl(trades) == pytest.approx(0.0)
    assert realized_pnl(trades, since="2026-06-01") == pytest.approx(-100.0)
    assert realized_pnl(trades, until="2026-05-31") == pytest.approx(100.0)


def test_load_orders_keeps_slippage_fields(log):
    log([_order("AAPL", "buy", 101.0, "2026-05-01T13:30:00+00:00", intended_price=100.0, slippage_pct=0.01)])
    assert load_orders()[0]["slippage_pct"] == 0.01
