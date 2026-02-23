"""
Earnings data from the Financial Modeling Prep (FMP) API.

    EarningsSurprise                          dataclass with eps/rev beat pcts and guidance flag
    get_earnings_surprise(ticker, date=None)  -> EarningsSurprise
    get_earnings_calendar(date, timing='amc') -> list[str]   timing: 'amc' | 'bmo'

Requires env var: FMP_API_KEY
"""
import logging
from dataclasses import dataclass

import requests

from config import FMP_API_KEY

logger = logging.getLogger(__name__)

BASE_STABLE = "https://financialmodelingprep.com/stable"


@dataclass
class EarningsSurprise:
    ticker: str
    eps_actual: float
    eps_estimate: float
    eps_beat_pct: float         # (actual - estimate) / abs(estimate)
    rev_actual: float
    rev_estimate: float
    rev_beat_pct: float         # (actual - estimate) / abs(estimate)
    guidance_weak: bool | None  # None if guidance data unavailable


def _beat_pct(actual: float, estimate: float) -> float:
    if estimate == 0:
        return 0.0
    return (actual - estimate) / abs(estimate)


def get_earnings_surprise(ticker: str, date: str | None = None) -> EarningsSurprise:
    """Return the most recent (or date-specific) earnings surprise for a ticker.

    Raises ValueError if no earnings data is available.
    date format: 'YYYY-MM-DD' (defaults to most recent report).
    Requires FMP_API_KEY environment variable.
    """
    url = f"{BASE_STABLE}/earnings"
    params = {"symbol": ticker.upper(), "apikey": FMP_API_KEY, "limit": 10}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    records = resp.json()

    if not records:
        raise ValueError(f"No earnings data from FMP for {ticker}")

    if date:
        records = [r for r in records if r.get("date", "").startswith(date)]
        if not records:
            raise ValueError(f"No FMP earnings data for {ticker} on {date}")

    r = records[0]

    eps_actual = float(r.get("epsActual") or 0.0)
    eps_estimate = float(r.get("epsEstimated") or 0.0)
    rev_actual = float(r.get("revenueActual") or 0.0)
    rev_estimate = float(r.get("revenueEstimated") or 0.0)

    guidance_weak: bool | None = None
    if "guidanceEps" in r and r["guidanceEps"] is not None:
        guidance_weak = float(r["guidanceEps"]) < eps_estimate

    return EarningsSurprise(
        ticker=ticker.upper(),
        eps_actual=eps_actual,
        eps_estimate=eps_estimate,
        eps_beat_pct=_beat_pct(eps_actual, eps_estimate),
        rev_actual=rev_actual,
        rev_estimate=rev_estimate,
        rev_beat_pct=_beat_pct(rev_actual, rev_estimate),
        guidance_weak=guidance_weak,
    )


def get_earnings_calendar(date: str, timing: str = "amc") -> list[str]:
    """Return list of ticker symbols reporting on the given date.

    date format: 'YYYY-MM-DD'.
    timing: 'amc' (after market close, default), 'bmo' (before market open), or 'all'.
    """
    url = f"{BASE_STABLE}/earnings-calendar"
    params = {"from": date, "to": date, "apikey": FMP_API_KEY}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    records = resp.json()

    tickers = []
    for r in records:
        time_val = r.get("time", "").lower()
        if timing == "all":
            match = True
        elif timing == "amc":
            match = time_val in ("amc", "")
        else:
            match = time_val == timing
        if match:
            symbol = r.get("symbol", "")
            if symbol:
                tickers.append(symbol)

    logger.info(f"Earnings calendar for {date} ({timing}): {len(tickers)} tickers")
    return tickers
