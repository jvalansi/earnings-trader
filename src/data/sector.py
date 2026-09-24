"""
Sector ETF data. Sector comes from FMP's bulk stock screener (one call, cached for
SECTOR_MAP_MAX_AGE_DAYS), with yfinance .info as the fallback for tickers it lacks.
yfinance .info alone is rate-limited hard enough that it silently mapped ~1 in 6
tickers to SPY. Falls back to SPY for unknown sectors.

    get_exchange(ticker)            -> str     yfinance exchange code (e.g. 'NMS', 'NYQ')
    lookup_sector(ticker)          -> str | None  sector name; None if no source answered
    get_sector_etf(ticker)         -> str     sector ETF symbol (e.g. 'XLK', 'XLF')
    get_sector_move(ticker, date)  -> float   sector ETF daily % change (fractional)
"""
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import yfinance as yf

from config import FMP_API_KEY

logger = logging.getLogger(__name__)

SECTOR_ETF_MAP: dict[str, str] = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
    "Communication Services": "XLC",
}
FALLBACK_ETF = "SPY"

SECTOR_MAP_FILE = Path(__file__).resolve().parents[2] / "data" / "sector_map.json"
SECTOR_MAP_MAX_AGE_DAYS = 30
_SCREENER_URL = "https://financialmodelingprep.com/api/v3/stock-screener"
_sector_map: dict[str, str] | None = None


def _fmp_sectors() -> dict[str, str]:
    """Ticker -> sector for US-listed stocks, from FMP's screener (disk-cached)."""
    global _sector_map
    if _sector_map is not None:
        return _sector_map
    fresh = SECTOR_MAP_FILE.exists() and (
        time.time() - SECTOR_MAP_FILE.stat().st_mtime < SECTOR_MAP_MAX_AGE_DAYS * 86400
    )
    if not fresh and FMP_API_KEY:
        try:
            resp = requests.get(_SCREENER_URL, params={
                "exchange": "NYSE,NASDAQ,AMEX", "isEtf": "false", "isFund": "false",
                "limit": 20000, "apikey": FMP_API_KEY,
            }, timeout=60)
            resp.raise_for_status()
            mapping = {r["symbol"]: r["sector"] for r in resp.json() if r.get("symbol") and r.get("sector")}
            if mapping:
                SECTOR_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
                SECTOR_MAP_FILE.write_text(json.dumps(mapping))
        except Exception as e:
            logger.warning(f"FMP sector map refresh failed, using cached copy if any: {e}")
    _sector_map = json.loads(SECTOR_MAP_FILE.read_text()) if SECTOR_MAP_FILE.exists() else {}
    return _sector_map


def get_exchange(ticker: str) -> str:
    """Return the yfinance exchange code for a ticker (e.g. 'NYQ', 'NMS').

    Returns empty string if exchange cannot be determined.
    """
    try:
        return yf.Ticker(ticker).info.get("exchange", "")
    except Exception as e:
        logger.warning(f"Could not get exchange for {ticker}: {e}")
        return ""


def lookup_sector(ticker: str) -> str | None:
    """Return the sector name for a stock, or None if neither FMP nor yfinance answered."""
    sector = _fmp_sectors().get(ticker)
    if sector:
        return sector
    try:
        return yf.Ticker(ticker).info.get("sector") or None
    except Exception as e:
        logger.warning(f"Could not get sector for {ticker}: {e}")
        return None


def get_sector_etf(ticker: str) -> str:
    """Return the sector ETF symbol for a given stock (e.g. 'XLK', 'XLF').

    Falls back to 'SPY' if sector cannot be determined.
    """
    sector = lookup_sector(ticker)
    etf = SECTOR_ETF_MAP.get(sector or "", FALLBACK_ETF)
    if etf == FALLBACK_ETF:
        logger.warning(f"No sector ETF for {ticker} (sector={sector!r}), using SPY")
    return etf


def get_sector_intraday_move(ticker: str, date: str) -> float:
    """Return the sector ETF's % move on the given date using intraday data.

    Uses 1-minute prepost data so it works during pre-market and early session
    (before the daily bar is available). Compares the latest available price
    for `date` against the prior regular-session close.

    date format: 'YYYY-MM-DD'
    Returns fractional change, e.g. -0.01 = -1%.
    """
    etf = get_sector_etf(ticker)
    date_dt = datetime.strptime(date, "%Y-%m-%d")
    start = (date_dt - timedelta(days=5)).strftime("%Y-%m-%d")
    end = (date_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    import pytz
    eastern = pytz.timezone("US/Eastern")

    df = yf.Ticker(etf).history(start=start, end=end, interval="1m", prepost=True)
    if df.empty:
        raise ValueError(f"No intraday ETF data for {etf} on {date}")

    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC").tz_convert(eastern)
    else:
        df.index = df.index.tz_convert(eastern)

    date_naive = date_dt.date()
    prior_regular = df[df.index.date < date_naive].between_time("09:30", "15:59")
    if prior_regular.empty:
        raise ValueError(f"No prior regular session data for {etf}")
    prior_close = float(prior_regular["Close"].iloc[-1])

    today_data = df[df.index.date == date_naive]
    if today_data.empty:
        raise ValueError(f"No intraday ETF data for {etf} on {date}")
    latest_price = float(today_data["Close"].iloc[-1])

    return (latest_price / prior_close) - 1.0


def get_sector_move(ticker: str, date: str) -> float:
    """Return the sector ETF's daily % change on the given date.

    date format: 'YYYY-MM-DD'
    Returns fractional change, e.g. -0.01 = -1%.
    """
    etf = get_sector_etf(ticker)
    date_dt = datetime.strptime(date, "%Y-%m-%d")
    start = (date_dt - timedelta(days=7)).strftime("%Y-%m-%d")
    end = (date_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    df = yf.Ticker(etf).history(start=start, end=end, interval="1d", auto_adjust=True)
    if df.empty or len(df) < 2:
        raise ValueError(f"Not enough ETF data for {etf} around {date}")

    # Strip timezone for date comparison
    df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index
    target = df[df.index.strftime("%Y-%m-%d") == date]
    if target.empty:
        raise ValueError(f"No ETF data for {etf} on {date}")

    target_idx = df.index.get_loc(target.index[0])
    if target_idx == 0:
        raise ValueError(f"No prior day available for {etf} on {date}")

    today_close = float(df["Close"].iloc[target_idx])
    prev_close = float(df["Close"].iloc[target_idx - 1])
    return (today_close / prev_close) - 1.0
