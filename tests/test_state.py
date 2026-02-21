import pytest
import state as _state_module
from state import Position, load_positions, save_positions, add_position, remove_position, update_stop


def _pos(ticker="AAPL", entry_price=100.0, current_stop=95.0, day_count=0):
    return Position(ticker=ticker, entry_price=entry_price, current_stop=current_stop,
                    entry_date="2026-01-01", day_count=day_count, quantity=10)


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(_state_module, "_path", tmp_path / "positions.json")


def test_load_returns_empty_when_no_file():
    assert load_positions() == []


def test_save_and_load_roundtrip():
    positions = [_pos("AAPL"), _pos("MSFT", entry_price=200.0, current_stop=195.0)]
    save_positions(positions)
    loaded = load_positions()
    assert len(loaded) == 2
    assert loaded[0].ticker == "AAPL"
    assert loaded[1].entry_price == 200.0


def test_add_position():
    add_position(_pos("AAPL"))
    assert len(load_positions()) == 1


def test_add_position_duplicate_is_skipped():
    add_position(_pos("AAPL"))
    add_position(_pos("AAPL"))
    assert len(load_positions()) == 1


def test_remove_position():
    save_positions([_pos("AAPL"), _pos("MSFT")])
    remove_position("AAPL")
    tickers = [p.ticker for p in load_positions()]
    assert tickers == ["MSFT"]


def test_update_stop():
    save_positions([_pos("AAPL", current_stop=95.0)])
    update_stop("AAPL", 98.0)
    assert load_positions()[0].current_stop == 98.0


def test_update_stop_does_not_affect_other_positions():
    save_positions([_pos("AAPL", current_stop=95.0), _pos("MSFT", current_stop=190.0)])
    update_stop("AAPL", 98.0)
    positions = {p.ticker: p for p in load_positions()}
    assert positions["AAPL"].current_stop == 98.0
    assert positions["MSFT"].current_stop == 190.0
