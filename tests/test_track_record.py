import pytest

from analysis.track_record import stats, verdict, slippage_stats, MIN_TRADES
from trade_history import ClosedTrade


def _trades(returns, entry_date="2026-05-01"):
    return [
        ClosedTrade(ticker=f"T{i}", entry_date=entry_date, exit_date="2026-05-10",
                    exit_ts=f"2026-05-10T13:30:{i:02d}+00:00", entry_price=100.0,
                    exit_price=100.0 * (1 + r), quantity=10, pnl=1000.0 * r, ret=r, mode="sim")
        for i, r in enumerate(returns)
    ]


def test_stats_empty():
    assert stats([]) == {}


def test_stats_basic():
    s = stats(_trades([0.10, -0.05, 0.20, -0.05]))
    assert s["n"] == 4
    assert s["win_rate"] == 0.5
    assert s["mean_ret"] == pytest.approx(0.05)
    assert s["avg_win"] == pytest.approx(0.15)
    assert s["avg_loss"] == pytest.approx(-0.05)
    assert s["expectancy"] == pytest.approx(50.0)


def test_stats_max_drawdown_follows_exit_order():
    s = stats(_trades([0.10, -0.20, 0.05]))
    assert s["max_drawdown"] == pytest.approx(-200.0)


def test_verdict_needs_minimum_sample():
    decision, why = verdict(stats(_trades([0.05] * 10)))
    assert decision == "EXTEND"
    assert str(MIN_TRADES) in why


def test_verdict_go_on_strong_edge():
    decision, _ = verdict(stats(_trades([0.03, 0.02] * 40)))
    assert decision == "GO"


def test_verdict_no_go_on_weak_t_stat():
    returns = [0.30, -0.25] * 40          # high mean noise, t-stat near zero
    decision, why = verdict(stats(returns and _trades(returns)))
    assert decision == "NO-GO"
    assert "t-stat" in why


def test_verdict_no_go_on_negative_mean():
    decision, why = verdict(stats(_trades([-0.01] * 80)))
    assert decision == "NO-GO"


def test_slippage_stats_groups_by_mode():
    orders = [
        {"mode": "paper", "slippage_pct": 0.01},
        {"mode": "paper", "slippage_pct": 0.03},
        {"mode": "sim", "slippage_pct": 0.0},
        {"mode": "paper"},                      # no slippage recorded — ignored
    ]
    s = slippage_stats(orders)
    assert s["paper"]["n"] == 2
    assert s["paper"]["mean"] == pytest.approx(0.02)
    assert s["paper"]["worst"] == pytest.approx(0.03)
