"""
Price data from yfinance. All % changes are fractional (0.05 = 5%).

    get_ohlcv(ticker, days)          -> pd.DataFrame   columns: Open High Low Close Volume
    get_latest_price(ticker)         -> float          last traded price right now
    get_prior_close(ticker)          -> float          close of the last completed session
    get_today_open(ticker)           -> float | None   today's opening print, None before it exists
    get_atr(ticker, period=14)       -> float          Average True Range (Wilder smoothing)
    get_ah_move(ticker, date)        -> float          after-hours % move vs regular close
    get_premarket_move(ticker, date) -> float          pre-market % move vs prior regular close
    get_prior_runup(ticker, days=10) -> float          % change over prior N trading days

Note: yfinance 1m data (used by get_ah_move / get_premarket_move) is only available
for the past 7 days.
"""
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


def get_latest_price(ticker: str) -> float:
    """Last traded price as of now.

    The daily bar is useless for this at 9:30 — yfinance has not formed today's bar yet,
    so its last row is still yesterday's close. Position management ran on that stale
    close for months. Prefer Alpaca's last trade, fall back to yfinance intraday, and
    only then to the daily bar.
    """
    from config import ALPACA_API_KEY, ALPACA_SECRET_KEY

    if ALPACA_API_KEY and ALPACA_SECRET_KEY:
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestTradeRequest

            client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
            trade = client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=ticker))
            price = float(trade[ticker].price)
            if price > 0:
                return price
        except Exception as e:
            logger.warning(f"Alpaca last trade failed for {ticker}: {e}")

    try:
        df = yf.Ticker(ticker).history(period="1d", interval="1m")
        if not df.empty:
            return float(df["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"Intraday price failed for {ticker}: {e}")

    logger.warning(f"Falling back to the daily bar for {ticker} — price may be stale")
    return float(get_ohlcv(ticker, days=1)["Close"].iloc[-1])


def _today_et() -> str:
    return datetime.now(EASTERN).strftime("%Y-%m-%d")


def get_prior_close(ticker: str) -> float:
    """Close of the last completed session, never today's partial bar."""
    df = get_ohlcv(ticker, days=5)
    dates = [str(i)[:10] for i in df.index]
    today = _today_et()
    closes = [c for d, c in zip(dates, df["Close"]) if d < today]
    if not closes:
        raise ValueError(f"No completed session for {ticker}")
    return float(closes[-1])


def get_today_open(ticker: str) -> float | None:
    """Today's opening print, or None if the session has not opened yet."""
    df = get_ohlcv(ticker, days=5)
    dates = [str(i)[:10] for i in df.index]
    today = _today_et()
    for d, o in zip(dates, df["Open"]):
        if d == today:
            return float(o)
    return None


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
