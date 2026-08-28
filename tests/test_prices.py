import pytest
import pandas as pd
import pytz
from unittest.mock import patch, MagicMock
from data.prices import get_ohlcv, get_atr, get_ah_move, get_premarket_move, get_prior_runup

EASTERN = pytz.timezone("US/Eastern")


def _daily_df(closes):
    """Simple daily OHLCV DataFrame."""
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "Open": closes, "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes], "Close": closes,
        "Volume": [1_000_000] * n,
    }, index=idx)


def _intraday_df(entries):
    """1-minute DataFrame with Eastern-timezone index. entries: [(datetime_str, close), ...]"""
    idx = pd.DatetimeIndex([EASTERN.localize(pd.Timestamp(t)) for t, _ in entries])
    closes = [c for _, c in entries]
    return pd.DataFrame({
        "Open": closes, "High": closes, "Low": closes, "Close": closes,
        "Volume": [1000] * len(closes),
    }, index=idx)


def _mock_ticker(daily=None, intraday=None):
    tk = MagicMock()
    def history(**kwargs):
        if kwargs.get("interval") == "1m":
            return intraday if intraday is not None else pd.DataFrame()
        return daily if daily is not None else pd.DataFrame()
    tk.history.side_effect = history
    return tk


# --- get_ohlcv ---

def test_get_ohlcv_returns_requested_rows():
    df = _daily_df([100.0, 101.0, 102.0, 103.0, 104.0])
    with patch("data.prices.yf.Ticker", return_value=_mock_ticker(daily=df)):
        result = get_ohlcv("AAPL", days=3)
    assert len(result) == 3
    assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_get_ohlcv_empty_raises():
    with patch("data.prices.yf.Ticker", return_value=_mock_ticker(daily=pd.DataFrame())):
        with pytest.raises(ValueError, match="No OHLCV data"):
            get_ohlcv("AAPL", days=1)


# --- get_atr ---

def test_get_atr_returns_positive_float():
    df = _daily_df([100 + i for i in range(30)])
    with patch("data.prices.yf.Ticker", return_value=_mock_ticker(daily=df)):
        atr = get_atr("AAPL")
    assert isinstance(atr, float)
    assert atr > 0


# --- get_ah_move ---

def test_get_ah_move_calculates_correctly():
    entries = [
        ("2026-01-15 15:59:00", 100.0),  # regular session close
        ("2026-01-15 17:00:00", 105.0),  # after-hours
    ]
    df = _intraday_df(entries)
    with patch("data.prices.yf.Ticker", return_value=_mock_ticker(intraday=df)):
        move = get_ah_move("AAPL", "2026-01-15")
    assert move == pytest.approx(0.05)


def test_get_ah_move_empty_raises():
    with patch("data.prices.yf.Ticker", return_value=_mock_ticker(intraday=pd.DataFrame())):
        with pytest.raises(ValueError, match="No intraday data"):
            get_ah_move("AAPL", "2026-01-15")


# --- get_premarket_move ---

def test_get_premarket_move_calculates_correctly():
    entries = [
        ("2026-01-14 15:59:00", 100.0),  # prior regular session close
        ("2026-01-15 08:00:00", 106.0),  # pre-market on target date
    ]
    df = _intraday_df(entries)
    with patch("data.prices.yf.Ticker", return_value=_mock_ticker(intraday=df)):
        move = get_premarket_move("AAPL", "2026-01-15")
    assert move == pytest.approx(0.06)


def test_get_premarket_move_no_premarket_data_raises():
    # Only prior-day data, nothing on the target date
    entries = [("2026-01-14 15:59:00", 100.0)]
    df = _intraday_df(entries)
    with patch("data.prices.yf.Ticker", return_value=_mock_ticker(intraday=df)):
        with pytest.raises(ValueError, match="No pre-market data"):
            get_premarket_move("AAPL", "2026-01-15")


# --- get_prior_runup ---

def test_get_prior_runup_calculates_correctly():
    # get_prior_runup(days=10) calls get_ohlcv(days=15) then .tail(10)
    # First 5 rows are ignored; row 6 (tail start) = 100, last row = 110 → +10%
    closes = [90.0] * 5 + [100.0] + [110.0] * 9
    df = _daily_df(closes)
    with patch("data.prices.yf.Ticker", return_value=_mock_ticker(daily=df)):
        runup = get_prior_runup("AAPL", days=10)
    assert runup == pytest.approx(0.10)


# --- get_prior_close / get_today_open / get_latest_price ---

def _dated_df(rows):
    """Daily bars from [(date, open, close), ...]."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d, _, _ in rows])
    return pd.DataFrame({
        "Open": [o for _, o, _ in rows], "High": [max(o, c) for _, o, c in rows],
        "Low": [min(o, c) for _, o, c in rows], "Close": [c for _, _, c in rows],
        "Volume": [1_000_000] * len(rows),
    }, index=idx)


def test_get_prior_close_ignores_todays_partial_bar():
    """The bug this guards: at 9:30 the last daily row can be today, or yesterday."""
    from data import prices
    bars = _dated_df([("2026-05-11", 90.0, 95.0), ("2026-05-12", 96.0, 99.0)])
    with patch("data.prices.get_ohlcv", return_value=bars), \
         patch("data.prices._today_et", return_value="2026-05-12"):
        assert prices.get_prior_close("AAPL") == 95.0


def test_get_prior_close_when_todays_bar_is_absent():
    from data import prices
    bars = _dated_df([("2026-05-11", 90.0, 95.0)])
    with patch("data.prices.get_ohlcv", return_value=bars), \
         patch("data.prices._today_et", return_value="2026-05-12"):
        assert prices.get_prior_close("AAPL") == 95.0


def test_get_today_open_returns_none_before_the_open():
    from data import prices
    bars = _dated_df([("2026-05-11", 90.0, 95.0)])
    with patch("data.prices.get_ohlcv", return_value=bars), \
         patch("data.prices._today_et", return_value="2026-05-12"):
        assert prices.get_today_open("AAPL") is None


def test_get_today_open_returns_the_opening_print():
    from data import prices
    bars = _dated_df([("2026-05-11", 90.0, 95.0), ("2026-05-12", 96.0, 99.0)])
    with patch("data.prices.get_ohlcv", return_value=bars), \
         patch("data.prices._today_et", return_value="2026-05-12"):
        assert prices.get_today_open("AAPL") == 96.0


def test_get_latest_price_prefers_intraday_over_the_daily_bar():
    from data import prices
    intraday = _daily_df([100.0, 101.5])
    ticker = MagicMock()
    ticker.history.return_value = intraday
    with patch("data.prices.yf.Ticker", return_value=ticker), \
         patch("data.prices.get_ohlcv") as daily:
        assert prices.get_latest_price("AAPL") == 101.5
    daily.assert_not_called()


def test_get_latest_price_falls_back_to_daily_bar():
    from data import prices
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame()
    with patch("data.prices.yf.Ticker", return_value=ticker), \
         patch("data.prices.get_ohlcv", return_value=_daily_df([100.0, 102.0])):
        assert prices.get_latest_price("AAPL") == 102.0
