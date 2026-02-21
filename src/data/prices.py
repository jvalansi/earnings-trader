import logging
from datetime import datetime, timedelta

import pandas as pd
import pytz
import yfinance as yf

from config import LOOKBACK_DAYS

logger = logging.getLogger(__name__)

EASTERN = pytz.timezone("US/Eastern")


def get_ohlcv(ticker: str, days: int) -> pd.DataFrame:
    """Return OHLCV DataFrame with columns: Open, High, Low, Close, Volume."""
    tk = yf.Ticker(ticker)
    df = tk.history(period=f"{days + 10}d", interval="1d", auto_adjust=True)
    if df.empty:
        raise ValueError(f"No OHLCV data for {ticker}")
    df = df[["Open", "High", "Low", "Close", "Volume"]].tail(days)
    return df


def get_atr(ticker: str, period: int = 14) -> float:
    """Return the most recent Average True Range value (Wilder smoothing)."""
    df = get_ohlcv(ticker, days=period + 10)
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    return float(atr.iloc[-1])


def get_ah_move(ticker: str, date: str) -> float:
    """Return after-hours % move on the given date (post-close vs regular close).

    date format: 'YYYY-MM-DD'
    Returns fractional change, e.g. 0.05 = +5%.
    Note: yfinance 1m data is only available for the past 7 days.
    """
    tk = yf.Ticker(ticker)
    date_dt = datetime.strptime(date, "%Y-%m-%d")
    next_day = (date_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    df = tk.history(start=date, end=next_day, interval="1m", prepost=True)
    if df.empty:
        raise ValueError(f"No intraday data for {ticker} on {date}")

    # Normalize index to Eastern time
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC").tz_convert(EASTERN)
    else:
        df.index = df.index.tz_convert(EASTERN)

    regular = df.between_time("09:30", "15:59")
    after_hours = df.between_time("16:01", "20:00")

    if regular.empty or after_hours.empty:
        raise ValueError(f"Insufficient session data for {ticker} on {date}")

    reg_close = float(regular["Close"].iloc[-1])
    ah_close = float(after_hours["Close"].iloc[-1])
    return (ah_close / reg_close) - 1.0


def get_premarket_move(ticker: str, date: str) -> float:
    """Return pre-market % move on the given date (last pre-market price vs prior regular close).

    date format: 'YYYY-MM-DD'
    Returns fractional change, e.g. 0.05 = +5%.
    Note: yfinance 1m data is only available for the past 7 days.
    """
    tk = yf.Ticker(ticker)
    date_dt = datetime.strptime(date, "%Y-%m-%d")
    # Go back 5 days to capture prior close across weekends
    start = (date_dt - timedelta(days=5)).strftime("%Y-%m-%d")
    next_day = (date_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    df = tk.history(start=start, end=next_day, interval="1m", prepost=True)
    if df.empty:
        raise ValueError(f"No intraday data for {ticker}")

    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC").tz_convert(EASTERN)
    else:
        df.index = df.index.tz_convert(EASTERN)

    date_naive = date_dt.date()
    prior_regular = df[df.index.date < date_naive].between_time("09:30", "15:59")
    if prior_regular.empty:
        raise ValueError(f"No prior regular session data for {ticker}")
    prior_close = float(prior_regular["Close"].iloc[-1])

    date_data = df[df.index.date == date_naive]
    premarket = date_data.between_time("04:00", "09:29")
    if premarket.empty:
        raise ValueError(f"No pre-market data for {ticker} on {date}")
    pm_last = float(premarket["Close"].iloc[-1])

    return (pm_last / prior_close) - 1.0


def get_prior_runup(ticker: str, days: int = LOOKBACK_DAYS) -> float:
    """Return the % price change over the prior N trading days.

    Returns fractional change, e.g. 0.08 = +8%.
    """
    df = get_ohlcv(ticker, days=days + 5)
    closes = df["Close"].tail(days)
    if len(closes) < 2:
        raise ValueError(f"Not enough price history for {ticker}")
    return float((closes.iloc[-1] / closes.iloc[0]) - 1.0)
