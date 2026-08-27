import json

import pytest

import risk
from state import Position
from trade_history import ClosedTrade


def _trade(pnl, exit_ts="2026-05-02T13:30:00+00:00", ticker="AAPL"):
    return ClosedTrade(ticker=ticker, entry_date=exit_ts[:10], exit_date=exit_ts[:10], exit_ts=exit_ts,
                       entry_price=100.0, exit_price=100.0 + pnl, quantity=1,
                       pnl=pnl, ret=pnl / 100.0, mode="sim")


@pytest.fixture
def trades(monkeypatch):
    """Control the closed-trade set risk.py reads."""
    holder = []
    monkeypatch.setattr(risk, "closed_trades", lambda: holder)
    return holder


@pytest.fixture
def capital(monkeypatch):
    monkeypatch.setattr(risk, "ACCOUNT_CAPITAL_USD", 5000.0)
    monkeypatch.setattr(risk, "DAILY_LOSS_LIMIT_PCT", 0.04)
    monkeypatch.setattr(risk, "MAX_DRAWDOWN_PCT", 0.15)


def test_clean_slate_allows_entries(trades, capital):
    status = risk.evaluate_risk(today="2026-05-01")
    assert status.entries_allowed is True
    assert status.equity == 5000.0
    assert status.drawdown_pct == 0.0


def test_pre_epoch_trades_are_ignored(trades, capital):
    risk.evaluate_risk(today="2026-05-01")
    trades.append(_trade(-4000.0, exit_ts="2026-01-01T13:30:00+00:00"))
    status = risk.evaluate_risk(today="2026-05-01")
    assert status.equity == 5000.0
    assert status.entries_allowed is True


def test_daily_loss_limit_halts_entries(trades, capital):
    risk.evaluate_risk(today="2026-05-01")               # establishes the day's opening equity
    trades.append(_trade(-300.0, exit_ts="2026-05-02T13:30:00+00:00"))
    status = risk.evaluate_risk(today="2026-05-02")      # -6% on the day
    assert status.entries_allowed is False
    assert any("daily loss" in r for r in status.reasons)


def test_small_daily_loss_does_not_halt(trades, capital):
    risk.evaluate_risk(today="2026-05-01")
    trades.append(_trade(-100.0, exit_ts="2026-05-02T13:30:00+00:00"))
    status = risk.evaluate_risk(today="2026-05-02")      # -2% on the day
    assert status.entries_allowed is True


def test_drawdown_breaker_halts_entries(trades, capital):
    risk.evaluate_risk(today="2026-05-01")
    trades.append(_trade(1000.0, exit_ts="2026-05-02T13:30:00+00:00"))
    risk.evaluate_risk(today="2026-05-02")               # peak equity 6000
    trades.append(_trade(-1000.0, exit_ts="2026-05-20T13:30:00+00:00"))
    status = risk.evaluate_risk(today="2026-05-20")      # back to 5000 == -16.7% from peak
    assert status.entries_allowed is False
    assert any("drawdown" in r for r in status.reasons)


def test_halt_latches_until_resume(trades, capital):
    risk.evaluate_risk(today="2026-05-01")
    trades.append(_trade(-300.0, exit_ts="2026-05-02T13:30:00+00:00"))
    risk.evaluate_risk(today="2026-05-02")
    trades.append(_trade(50.0, exit_ts="2026-05-03T13:30:00+00:00"))
    assert risk.evaluate_risk(today="2026-05-03").entries_allowed is False   # calm day, still halted
    assert risk.entries_allowed() is False
    risk.resume()
    assert risk.evaluate_risk(today="2026-05-03").entries_allowed is True


def test_manual_kill_switch(trades, capital):
    risk.halt("earnings season blackout")
    assert risk.entries_allowed() is False
    status = risk.evaluate_risk(today="2026-05-01")
    assert status.entries_allowed is False
    assert any("earnings season blackout" in r for r in status.reasons)
    risk.resume()
    assert risk.entries_allowed() is True


def test_kill_switch_via_environment(trades, capital, monkeypatch):
    monkeypatch.setenv("TRADING_HALTED", "1")
    assert risk.entries_allowed() is False


def test_mark_to_market_excludes_pre_epoch_positions(trades, capital):
    risk.evaluate_risk(today="2026-05-10")
    old = Position("OLD", entry_price=100.0, current_stop=90.0, entry_date="2026-04-01", day_count=5, quantity=10)
    new = Position("NEW", entry_price=50.0, current_stop=45.0, entry_date="2026-05-11", day_count=1, quantity=10)
    prices = {"OLD": 200.0, "NEW": 55.0}
    assert risk.mark_to_market([old, new], prices) == pytest.approx(50.0)


def test_unrealized_loss_counts_toward_drawdown(trades, capital):
    risk.evaluate_risk(today="2026-05-01")
    status = risk.evaluate_risk(today="2026-05-02", unrealized_pnl=-900.0)
    assert status.entries_allowed is False
    assert status.equity == pytest.approx(4100.0)


def test_state_is_persisted(trades, capital):
    risk.evaluate_risk(today="2026-05-01")
    state = json.loads(risk._state_path.read_text())
    assert "epoch_ts" in state and state["peak_equity"] == 5000.0


def test_status_line_reports_halt(trades, capital):
    risk.halt("manual")
    assert "HALTED" in risk.status_line(risk.evaluate_risk(today="2026-05-01"))
