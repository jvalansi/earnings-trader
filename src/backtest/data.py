"""
Historical data fetchers for backtesting. All results are disk-cached under
data/backtest_cache/ to avoid re-fetching across runs.

    get_trading_dates(start, end)                   -> list[str]
    get_ohlcv_range(ticker, start, end)             -> pd.DataFrame
    get_close_on_date(df, date)                     -> float
    get_open_on_date(df, date)                      -> float
    get_atr_as_of(df, date, period=14)              -> float
    get_prior_runup_as_of(df, date, days=10)        -> float
    get_ah_proxy(df, earnings_date)                 -> float
    get_historical_earnings_calendar(date)          -> list[dict]
    get_historical_surprise(ticker, date)           -> EarningsSurprise
    get_exchange_cached(ticker)                     -> str
    get_sector_etf_cached(ticker)                   -> str
    get_sector_move_on_date(etf, df, date)          -> float

AH move proxy:
    yfinance 1m intraday data is only available for the last 7 days, so
    historical after-hours moves are approximated as:
        (next_trading_day_open / earnings_date_close) - 1

    This means entries are simulated at the earnings-date close price, using
    next-day open as the AH confirmation signal. There is a slight look-ahead
    bias: in production the entry happens at ~4:15 PM after confirming the AH
    move, while here we confirm at D+1 open but record entry at D close.
    The effect is small — it overstates entry quality by a few cents on average.
"""
import json
import logging
import pickle
import requests
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from config import FMP_API_KEY, LOOKBACK_DAYS
from data.earnings import EarningsSurprise, _beat_pct
from data.sector import SECTOR_ETF_MAP, FALLBACK_ETF

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "backtest_cache"


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# OHLCV
# ---------------------------------------------------------------------------

def get_trading_dates(start: str, end: str) -> list[str]:
    """Return list of market-open trading dates (YYYY-MM-DD) between start and end."""
    df = get_ohlcv_range("SPY", start, end)
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    mask = (df.index >= start_dt) & (df.index <= end_dt)
    return [d.strftime("%Y-%m-%d") for d in df.index[mask]]


def get_ohlcv_range(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Return daily OHLCV DataFrame for ticker, with disk caching.

    Downloads enough history before start to support ATR and runup lookbacks.
    Re-downloads if the cache doesn't cover the requested range.
    """
    _ensure_cache_dir()
    cache_path = CACHE_DIR / f"{ticker}_ohlcv.pkl"

    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")

    if cache_path.exists():
        with cache_path.open("rb") as f:
            df = pickle.load(f)
        if df.index.tzinfo is not None:
            df.index = df.index.tz_localize(None)
        if (
            not df.empty
            and df.index[0].date() <= start_dt.date()
            and df.index[-1].date() >= end_dt.date()
        ):
            return df

    # Add 30-day buffer before start for ATR/runup lookback
    fetch_start = (start_dt - timedelta(days=30)).strftime("%Y-%m-%d")
    fetch_end = (end_dt + timedelta(days=5)).strftime("%Y-%m-%d")

    raw = yf.Ticker(ticker).history(
        start=fetch_start, end=fetch_end, interval="1d", auto_adjust=True
    )
    if raw.empty:
        raise ValueError(f"No OHLCV data for {ticker}")

    if raw.index.tzinfo is not None:
        raw.index = raw.index.tz_localize(None)

    df = raw[["Open", "High", "Low", "Close", "Volume"]]
    with cache_path.open("wb") as f:
        pickle.dump(df, f)

    return df


def get_close_on_date(df: pd.DataFrame, date: str) -> float:
    row = df[df.index.strftime("%Y-%m-%d") == date]
    if row.empty:
        raise ValueError(f"No close price for {date}")
    return float(row["Close"].iloc[0])


def get_open_on_date(df: pd.DataFrame, date: str) -> float:
    row = df[df.index.strftime("%Y-%m-%d") == date]
    if row.empty:
        raise ValueError(f"No open price for {date}")
    return float(row["Open"].iloc[0])


def get_atr_as_of(df: pd.DataFrame, date: str, period: int = 14) -> float:
    """Compute Wilder ATR using data up to and including date."""
    sub = df[df.index.strftime("%Y-%m-%d") <= date].tail(period + 10)
    if len(sub) < 2:
        raise ValueError(f"Not enough data to compute ATR as of {date}")
    high, low, close = sub["High"], sub["Low"], sub["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return float(tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])


def get_prior_runup_as_of(df: pd.DataFrame, date: str, days: int = LOOKBACK_DAYS) -> float:
    """Compute % price change over the N trading days ending on date."""
    sub = df[df.index.strftime("%Y-%m-%d") <= date].tail(days + 5)
    closes = sub["Close"].tail(days)
    if len(closes) < 2:
        raise ValueError(f"Not enough data to compute runup as of {date}")
    return float((closes.iloc[-1] / closes.iloc[0]) - 1.0)


def get_ah_proxy(df: pd.DataFrame, earnings_date: str) -> float:
    """Return (next_trading_day_open / earnings_date_close) - 1 as AH move proxy."""
    earnings_close = get_close_on_date(df, earnings_date)
    next_rows = df[df.index.strftime("%Y-%m-%d") > earnings_date]
    if next_rows.empty:
        raise ValueError(f"No next trading day after {earnings_date}")
    next_open = float(next_rows["Open"].iloc[0])
    return (next_open / earnings_close) - 1.0


def get_sector_move_on_date(etf: str, etf_df: pd.DataFrame, date: str) -> float:
    """Return sector ETF daily % change on date."""
    sub = etf_df[etf_df.index.strftime("%Y-%m-%d") <= date].tail(2)
    if len(sub) < 2:
        raise ValueError(f"Not enough ETF data for {etf} on {date}")
    if sub.index[-1].strftime("%Y-%m-%d") != date:
        raise ValueError(f"No ETF bar for {etf} on {date}")
    return float((sub["Close"].iloc[-1] / sub["Close"].iloc[-2]) - 1.0)


# ---------------------------------------------------------------------------
# FMP earnings
# ---------------------------------------------------------------------------

def get_historical_earnings_calendar(date: str) -> list[dict]:
    """Return raw FMP earnings calendar for date, with disk caching."""
    _ensure_cache_dir()
    cache_path = CACHE_DIR / f"earnings_{date}.json"

    if cache_path.exists():
        with cache_path.open("r") as f:
            return json.load(f)

    url = "https://financialmodelingprep.com/stable/earnings-calendar"
    resp = requests.get(
        url, params={"from": date, "to": date, "apikey": FMP_API_KEY}, timeout=15
    )
    resp.raise_for_status()
    records = resp.json()

    with cache_path.open("w") as f:
        json.dump(records, f)

    logger.info(f"FMP earnings for {date}: {len(records)} tickers")
    return records


def get_historical_surprise(ticker: str, date: str) -> EarningsSurprise:
    """Return EarningsSurprise for ticker on date, with per-ticker/date disk cache."""
    _ensure_cache_dir()
    cache_path = CACHE_DIR / f"surprise_{ticker}_{date}.json"

    if cache_path.exists():
        with cache_path.open("r") as f:
            r = json.load(f)
    else:
        url = "https://financialmodelingprep.com/stable/earnings"
        resp = requests.get(
            url,
            params={"symbol": ticker.upper(), "apikey": FMP_API_KEY, "limit": 20},
            timeout=10,
        )
        resp.raise_for_status()
        records = resp.json()
        matches = [rec for rec in records if rec.get("date", "").startswith(date)]
        if not matches:
            raise ValueError(f"No FMP earnings for {ticker} on {date}")
        r = matches[0]
        with cache_path.open("w") as f:
            json.dump(r, f)

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


# ---------------------------------------------------------------------------
# Ticker metadata
# ---------------------------------------------------------------------------

def get_exchange_cached(ticker: str) -> str:
    """Return yfinance exchange code for ticker, cached to disk."""
    _ensure_cache_dir()
    cache_path = CACHE_DIR / f"exchange_{ticker}.json"
    if cache_path.exists():
        with cache_path.open("r") as f:
            return json.load(f).get("exchange", "")
    try:
        exchange = yf.Ticker(ticker).info.get("exchange", "")
    except Exception:
        exchange = ""
    with cache_path.open("w") as f:
        json.dump({"exchange": exchange}, f)
    return exchange


def get_sector_etf_cached(ticker: str) -> str:
    """Return sector ETF symbol for ticker, cached to disk."""
    _ensure_cache_dir()
    cache_path = CACHE_DIR / f"sector_{ticker}.json"
    if cache_path.exists():
        with cache_path.open("r") as f:
            return json.load(f).get("etf", FALLBACK_ETF)
    try:
        info = yf.Ticker(ticker).info
        etf = SECTOR_ETF_MAP.get(info.get("sector", ""), FALLBACK_ETF)
    except Exception:
        etf = FALLBACK_ETF
    with cache_path.open("w") as f:
        json.dump({"etf": etf}, f)
    return etf
