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
