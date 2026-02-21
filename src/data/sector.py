"""
Sector ETF data via yfinance. Falls back to SPY for unknown sectors.

    get_exchange(ticker)            -> str     yfinance exchange code (e.g. 'NMS', 'NYQ')
    get_sector_etf(ticker)         -> str     sector ETF symbol (e.g. 'XLK', 'XLF')
    get_sector_move(ticker, date)  -> float   sector ETF daily % change (fractional)
"""
import logging
from datetime import datetime, timedelta

import yfinance as yf

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


def get_exchange(ticker: str) -> str:
    """Return the yfinance exchange code for a ticker (e.g. 'NYQ', 'NMS').

    Returns empty string if exchange cannot be determined.
    """
    try:
        return yf.Ticker(ticker).info.get("exchange", "")
    except Exception as e:
        logger.warning(f"Could not get exchange for {ticker}: {e}")
        return ""


def get_sector_etf(ticker: str) -> str:
    """Return the sector ETF symbol for a given stock (e.g. 'XLK', 'XLF').

    Falls back to 'SPY' if sector cannot be determined.
    """
    try:
        info = yf.Ticker(ticker).info
        sector = info.get("sector", "")
        etf = SECTOR_ETF_MAP.get(sector, FALLBACK_ETF)
        if etf == FALLBACK_ETF and sector:
            logger.warning(f"Unknown sector '{sector}' for {ticker}, using SPY")
        return etf
    except Exception as e:
        logger.warning(f"Could not get sector for {ticker}: {e}. Using SPY.")
        return FALLBACK_ETF


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
