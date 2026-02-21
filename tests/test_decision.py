import pytest
from decision import evaluate_entry, evaluate_positions
from data.earnings import EarningsSurprise
from state import Position


def _surprise(eps_beat=0.10, rev_beat=0.05, guidance_weak=False):
    return EarningsSurprise(
        ticker="AAPL",
        eps_actual=1.10, eps_estimate=1.00, eps_beat_pct=eps_beat,
        rev_actual=105,  rev_estimate=100,  rev_beat_pct=rev_beat,
        guidance_weak=guidance_weak,
    )


def _position(ticker="AAPL", entry_price=100.0, current_stop=95.0, day_count=0):
    return Position(ticker=ticker, entry_price=entry_price, current_stop=current_stop,
                    entry_date="2026-01-01", day_count=day_count, quantity=10)


# --- evaluate_entry ---

def test_evaluate_entry_all_filters_pass():
    sig = evaluate_entry(
        ticker="AAPL", surprise=_surprise(), ah_move=0.05, prior_runup=0.03,
        sector_move=0.01, atr=2.0, current_price=100.0, open_positions=[],
    )
    assert sig.should_enter is True
    assert sig.entry_price == 100.0
    assert sig.initial_stop == pytest.approx(100.0 - 1.5 * 2.0)
    assert all(sig.filters_passed.values())


def test_evaluate_entry_fails_eps_beat():
    sig = evaluate_entry(
        ticker="AAPL", surprise=_surprise(eps_beat=0.01), ah_move=0.05,
        prior_runup=0.03, sector_move=0.01, atr=2.0, current_price=100.0, open_positions=[],
    )
    assert sig.should_enter is False
    assert sig.filters_passed["eps_beat"] is False


def test_evaluate_entry_fails_ah_move():
    sig = evaluate_entry(
        ticker="AAPL", surprise=_surprise(), ah_move=0.01,  # below 3% threshold
        prior_runup=0.03, sector_move=0.01, atr=2.0, current_price=100.0, open_positions=[],
    )
    assert sig.should_enter is False
    assert sig.filters_passed["ah_move"] is False


def test_evaluate_entry_fails_prior_runup():
    sig = evaluate_entry(
        ticker="AAPL", surprise=_surprise(), ah_move=0.05,
        prior_runup=0.15,  # above 10% threshold
        sector_move=0.01, atr=2.0, current_price=100.0, open_positions=[],
    )
    assert sig.should_enter is False
    assert sig.filters_passed["prior_runup"] is False


def test_evaluate_entry_fails_capacity():
    full = [_position(ticker=f"T{i}") for i in range(5)]  # MAX_POSITIONS = 5
    sig = evaluate_entry(
        ticker="AAPL", surprise=_surprise(), ah_move=0.05, prior_runup=0.03,
        sector_move=0.01, atr=2.0, current_price=100.0, open_positions=full,
    )
    assert sig.should_enter is False
    assert sig.filters_passed["capacity"] is False


def test_evaluate_entry_guidance_none_treated_as_pass():
    sig = evaluate_entry(
        ticker="AAPL", surprise=_surprise(guidance_weak=None), ah_move=0.05,
        prior_runup=0.03, sector_move=0.01, atr=2.0, current_price=100.0, open_positions=[],
    )
    assert sig.filters_passed["guidance"] is True


# --- evaluate_positions ---

def test_evaluate_positions_stop_hit():
    pos = _position(current_stop=98.0, day_count=3)
    actions = evaluate_positions([pos], current_prices={"AAPL": 97.0}, current_atrs={"AAPL": 2.0})
    assert actions[0].action == "sell"
    assert actions[0].reason == "stop_hit"


def test_evaluate_positions_max_days_reached():
    pos = _position(current_stop=95.0, day_count=10)
    actions = evaluate_positions([pos], current_prices={"AAPL": 110.0}, current_atrs={"AAPL": 2.0})
    assert actions[0].action == "sell"
    assert actions[0].reason == "max_days_reached"


def test_evaluate_positions_update_stop():
    pos = _position(current_stop=95.0, day_count=3)
    # new_stop = 110 - 1.5*2 = 107.0 > 95.0 → raise stop
    actions = evaluate_positions([pos], current_prices={"AAPL": 110.0}, current_atrs={"AAPL": 2.0})
    assert actions[0].action == "update_stop"
    assert actions[0].new_stop == pytest.approx(107.0)


def test_evaluate_positions_hold_when_stop_would_lower():
    pos = _position(current_stop=95.0, day_count=3)
    # new_stop = 96 - 1.5*2 = 93.0 < 95.0 → don't lower, hold
    actions = evaluate_positions([pos], current_prices={"AAPL": 96.0}, current_atrs={"AAPL": 2.0})
    assert actions[0].action == "hold"


def test_evaluate_positions_hold_when_price_unavailable():
    pos = _position()
    actions = evaluate_positions([pos], current_prices={}, current_atrs={})
    assert actions[0].action == "hold"
    assert actions[0].reason == "price_unavailable"
