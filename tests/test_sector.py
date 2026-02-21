import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from data.sector import get_exchange, get_sector_etf, get_sector_move


def _mock_ticker(info=None, history_df=None):
    tk = MagicMock()
    tk.info = info or {}
    tk.history.return_value = history_df if history_df is not None else pd.DataFrame()
    return tk


# --- get_exchange ---

def test_get_exchange_returns_code():
    with patch("data.sector.yf.Ticker", return_value=_mock_ticker(info={"exchange": "NMS"})):
        assert get_exchange("AAPL") == "NMS"


def test_get_exchange_returns_empty_on_error():
    with patch("data.sector.yf.Ticker", side_effect=Exception("network error")):
        assert get_exchange("AAPL") == ""


# --- get_sector_etf ---

def test_get_sector_etf_known_sector():
    with patch("data.sector.yf.Ticker", return_value=_mock_ticker(info={"sector": "Technology"})):
        assert get_sector_etf("AAPL") == "XLK"


def test_get_sector_etf_unknown_sector_falls_back_to_spy():
    with patch("data.sector.yf.Ticker", return_value=_mock_ticker(info={"sector": "Crypto"})):
        assert get_sector_etf("COIN") == "SPY"


def test_get_sector_etf_error_falls_back_to_spy():
    with patch("data.sector.yf.Ticker", side_effect=Exception("network error")):
        assert get_sector_etf("AAPL") == "SPY"


# --- get_sector_move ---

def test_get_sector_move_calculates_correctly():
    dates = pd.date_range("2026-01-13", periods=3, freq="B")  # Tue, Wed, Thu
    df = pd.DataFrame({"Close": [100.0, 102.0, 101.0]}, index=dates)

    def mock_ticker(symbol):
        if symbol == "AAPL":
            return _mock_ticker(info={"sector": "Technology"})
        return _mock_ticker(history_df=df)  # XLK

    with patch("data.sector.yf.Ticker", side_effect=mock_ticker):
        move = get_sector_move("AAPL", "2026-01-15")

    assert move == pytest.approx((101.0 / 102.0) - 1.0)


def test_get_sector_move_insufficient_data_raises():
    df = pd.DataFrame({"Close": [100.0]}, index=pd.date_range("2026-01-15", periods=1))

    def mock_ticker(symbol):
        if symbol == "AAPL":
            return _mock_ticker(info={"sector": "Technology"})
        return _mock_ticker(history_df=df)

    with patch("data.sector.yf.Ticker", side_effect=mock_ticker):
        with pytest.raises(ValueError):
            get_sector_move("AAPL", "2026-01-15")
