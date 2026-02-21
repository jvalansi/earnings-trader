import pytest
from unittest.mock import patch, MagicMock
from data.earnings import get_earnings_surprise, get_earnings_calendar


SAMPLE_RECORD = {
    "date": "2026-01-15",
    "symbol": "AAPL",
    "epsActual": 1.50,
    "epsEstimated": 1.20,
    "revenueActual": 120_000_000,
    "revenueEstimated": 100_000_000,
    "guidanceEps": None,
}


def _mock_response(records):
    resp = MagicMock()
    resp.json.return_value = records
    resp.raise_for_status.return_value = None
    return resp


# --- get_earnings_surprise ---

def test_get_earnings_surprise_parses_fields():
    with patch("data.earnings.requests.get", return_value=_mock_response([SAMPLE_RECORD])):
        s = get_earnings_surprise("AAPL")
    assert s.ticker == "AAPL"
    assert s.eps_beat_pct == pytest.approx((1.50 - 1.20) / 1.20)
    assert s.rev_beat_pct == pytest.approx((120e6 - 100e6) / 100e6)
    assert s.guidance_weak is None


def test_get_earnings_surprise_no_records_raises():
    with patch("data.earnings.requests.get", return_value=_mock_response([])):
        with pytest.raises(ValueError, match="No earnings data"):
            get_earnings_surprise("AAPL")


def test_get_earnings_surprise_date_mismatch_raises():
    with patch("data.earnings.requests.get", return_value=_mock_response([SAMPLE_RECORD])):
        with pytest.raises(ValueError, match="No FMP earnings data"):
            get_earnings_surprise("AAPL", date="2025-01-01")


def test_get_earnings_surprise_guidance_weak():
    record = {**SAMPLE_RECORD, "guidanceEps": 1.00}  # below eps_estimate of 1.20 → weak
    with patch("data.earnings.requests.get", return_value=_mock_response([record])):
        s = get_earnings_surprise("AAPL")
    assert s.guidance_weak is True


# --- get_earnings_calendar ---

CALENDAR_RECORDS = [
    {"symbol": "AAPL", "time": "amc"},
    {"symbol": "MSFT", "time": "bmo"},
    {"symbol": "GOOG", "time": ""},    # unknown timing → treated as amc
]


def test_get_earnings_calendar_amc_default():
    with patch("data.earnings.requests.get", return_value=_mock_response(CALENDAR_RECORDS)):
        tickers = get_earnings_calendar("2026-01-15")
    assert "AAPL" in tickers
    assert "GOOG" in tickers
    assert "MSFT" not in tickers


def test_get_earnings_calendar_bmo():
    with patch("data.earnings.requests.get", return_value=_mock_response(CALENDAR_RECORDS)):
        tickers = get_earnings_calendar("2026-01-15", timing="bmo")
    assert tickers == ["MSFT"]


def test_get_earnings_calendar_empty():
    with patch("data.earnings.requests.get", return_value=_mock_response([])):
        assert get_earnings_calendar("2026-01-15") == []
