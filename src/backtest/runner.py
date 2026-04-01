"""
Backtest runner. Replays the PEAD strategy over historical earnings events.

Usage:
    PYTHONPATH=src python src/backtest/runner.py --start 2022-01-01 --end 2024-12-31
    PYTHONPATH=src python src/backtest/runner.py --start 2026-02-01 --end 2026-03-31  # fidelity check

Output:
    list[SimTrade] — one record per completed trade.
    Calls report.generate_report() and prints a summary to stdout.

Config overrides:
    Pass a dict of config key overrides to run_backtest() for sensitivity analysis.
    E.g. {"ATR_STOP_MULTIPLIER": 3.0, "MIN_AH_MOVE_PCT": 0.05}
    These override the production config values for the duration of the run.

Design notes:
    - Entry/exit filter logic is inlined (mirrors decision.py) so that config
      overrides apply cleanly without patching the production module.
    - ETF OHLCV for all 12 sector ETFs + SPY is preloaded for the full date
      range to avoid repeated yfinance calls in the inner loop.
    - All FMP and yfinance calls are disk-cached via backtest/data.py.
    - BMO earnings are included: AH proxy for BMO = same-day open vs prior close.
"""
import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime

import config as _cfg
from backtest.data import (
    get_trading_dates,
    get_ohlcv_range,
    get_close_on_date,
    get_atr_as_of,
    get_prior_runup_as_of,
    get_ah_proxy,
    get_historical_earnings_calendar,
    get_historical_surprise,
    get_exchange_cached,
    get_sector_etf_cached,
    get_sector_move_on_date,
)
from data.sector import SECTOR_ETF_MAP, FALLBACK_ETF

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SimPosition:
    ticker: str
    entry_date: str
    entry_price: float
    current_stop: float
    day_count: int
    quantity: int


@dataclass
class SimTrade:
    ticker: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    exit_reason: str   # "stop_hit" | "max_days_reached" | "backtest_end"
    pnl_usd: float
    pnl_pct: float
    days_held: int


# ---------------------------------------------------------------------------
# Inline entry/exit logic (mirrors decision.py; inlined so config overrides work)
# ---------------------------------------------------------------------------

def _should_enter(surprise, ah_move, prior_runup, sector_move, n_positions, cfg):
    """Return (should_enter: bool, filters: dict)."""
    filters = {
        "eps_beat":    surprise.eps_beat_pct >= cfg["MIN_EPS_BEAT_PCT"],
        "rev_beat":    surprise.rev_beat_pct > 0,
        "ah_move":     ah_move >= cfg["MIN_AH_MOVE_PCT"],
        "prior_runup": prior_runup <= cfg["MAX_PRIOR_RUNUP_PCT"],
        "sector_etf":  sector_move > cfg["SECTOR_ETF_MIN"],
        "guidance":    True if surprise.guidance_weak is None else not surprise.guidance_weak,
        "capacity":    n_positions < cfg["MAX_POSITIONS"],
    }
    return all(filters.values()), filters


def _close_position(pos: SimPosition, date: str, price: float, reason: str) -> SimTrade:
    pnl_usd = (price - pos.entry_price) * pos.quantity
    pnl_pct = (price / pos.entry_price) - 1.0
    return SimTrade(
        ticker=pos.ticker,
        entry_date=pos.entry_date,
        exit_date=date,
        entry_price=pos.entry_price,
        exit_price=price,
        exit_reason=reason,
        pnl_usd=round(pnl_usd, 2),
        pnl_pct=round(pnl_pct, 4),
        days_held=pos.day_count,
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def _build_cfg(overrides: dict) -> dict:
    """Return a config dict from production config, with optional overrides."""
    return {
        "MIN_EPS_BEAT_PCT":    overrides.get("MIN_EPS_BEAT_PCT",    _cfg.MIN_EPS_BEAT_PCT),
        "MIN_AH_MOVE_PCT":     overrides.get("MIN_AH_MOVE_PCT",     _cfg.MIN_AH_MOVE_PCT),
        "MAX_PRIOR_RUNUP_PCT": overrides.get("MAX_PRIOR_RUNUP_PCT", _cfg.MAX_PRIOR_RUNUP_PCT),
        "SECTOR_ETF_MIN":      overrides.get("SECTOR_ETF_MIN",      _cfg.SECTOR_ETF_MIN),
        "ATR_STOP_MULTIPLIER": overrides.get("ATR_STOP_MULTIPLIER", _cfg.ATR_STOP_MULTIPLIER),
        "HOLD_DAYS":           overrides.get("HOLD_DAYS",           _cfg.HOLD_DAYS),
        "MAX_POSITIONS":       overrides.get("MAX_POSITIONS",       _cfg.MAX_POSITIONS),
        "POSITION_SIZE_USD":   overrides.get("POSITION_SIZE_USD",   _cfg.POSITION_SIZE_USD),
        "ALLOWED_EXCHANGES":   overrides.get("ALLOWED_EXCHANGES",   _cfg.ALLOWED_EXCHANGES),
    }


def run_backtest(
    start_date: str,
    end_date: str,
    config_overrides: dict = {},
) -> list[SimTrade]:
    """Run the PEAD backtest over the given date range.

    Args:
        start_date: 'YYYY-MM-DD' inclusive
        end_date:   'YYYY-MM-DD' inclusive
        config_overrides: dict of config keys to override (for sweep)

    Returns:
        list[SimTrade] — one per completed trade (open positions at end_date
        are closed at last available price with reason='backtest_end').
    """
    cfg = _build_cfg(config_overrides)

    # Preload ETF OHLCV for all sector ETFs (avoids per-ticker inner-loop fetches)
    all_etfs = set(SECTOR_ETF_MAP.values()) | {FALLBACK_ETF}
    logger.info(f"Preloading {len(all_etfs)} ETF OHLCV series...")
    etf_dfs: dict = {}
    for etf in all_etfs:
        try:
            etf_dfs[etf] = get_ohlcv_range(etf, start_date, end_date)
        except Exception as e:
            logger.warning(f"Could not load ETF {etf}: {e}")

    trading_dates = get_trading_dates(start_date, end_date)
    logger.info(f"Backtesting {len(trading_dates)} trading days ({start_date} → {end_date})")

    positions: list[SimPosition] = []
    trades: list[SimTrade] = []

    for date in trading_dates:
        # --- Update existing positions ---
        still_open = []
        for pos in positions:
            pos.day_count += 1
            try:
                df = get_ohlcv_range(pos.ticker, start_date, end_date)
                close = get_close_on_date(df, date)
                atr = get_atr_as_of(df, date)
            except Exception:
                still_open.append(pos)
                continue

            if close <= pos.current_stop:
                trades.append(_close_position(pos, date, close, "stop_hit"))
                continue

            if pos.day_count >= cfg["HOLD_DAYS"]:
                trades.append(_close_position(pos, date, close, "max_days_reached"))
                continue

            new_stop = close - (cfg["ATR_STOP_MULTIPLIER"] * atr)
            if new_stop > pos.current_stop:
                pos.current_stop = new_stop

            still_open.append(pos)

        positions = still_open

        # --- Scan for entries ---
        if len(positions) >= cfg["MAX_POSITIONS"]:
            continue

        try:
            calendar = get_historical_earnings_calendar(date)
        except Exception as e:
            logger.warning(f"Could not fetch earnings calendar for {date}: {e}")
            continue

        for record in calendar:
            if len(positions) >= cfg["MAX_POSITIONS"]:
                break

            timing = record.get("time", "").lower()
            if timing not in ("amc", "bmo"):
                continue

            ticker = record.get("symbol", "")
            if not ticker:
                continue

            try:
                # Exchange filter
                exchange = get_exchange_cached(ticker)
                if exchange not in cfg["ALLOWED_EXCHANGES"]:
                    continue

                df = get_ohlcv_range(ticker, start_date, end_date)
                entry_price = get_close_on_date(df, date)
                atr = get_atr_as_of(df, date)
                prior_runup = get_prior_runup_as_of(df, date)

                # AH/premarket proxy: next-day open vs earnings-date close
                # (same formula for both AMC and BMO since we only have daily bars)
                ah_move = get_ah_proxy(df, date)

                # Sector move
                etf = get_sector_etf_cached(ticker)
                etf_df = etf_dfs.get(etf, etf_dfs.get(FALLBACK_ETF))
                if etf_df is None:
                    continue
                sector_move = get_sector_move_on_date(etf, etf_df, date)

                # Earnings surprise
                surprise = get_historical_surprise(ticker, date)

                # Entry decision
                enter, _ = _should_enter(
                    surprise, ah_move, prior_runup, sector_move,
                    len(positions), cfg,
                )
                if not enter:
                    continue

                quantity = max(1, int(cfg["POSITION_SIZE_USD"] / entry_price))
                positions.append(SimPosition(
                    ticker=ticker,
                    entry_date=date,
                    entry_price=entry_price,
                    current_stop=entry_price - (cfg["ATR_STOP_MULTIPLIER"] * atr),
                    day_count=0,
                    quantity=quantity,
                ))
                logger.info(f"ENTER {ticker} on {date} @ {entry_price:.2f}")

            except Exception as e:
                logger.debug(f"Skip {ticker} on {date}: {e}")
                continue

    # Close any positions still open at end of backtest
    for pos in positions:
        try:
            df = get_ohlcv_range(pos.ticker, start_date, end_date)
            close = get_close_on_date(df, trading_dates[-1])
            trades.append(_close_position(pos, trading_dates[-1], close, "backtest_end"))
        except Exception:
            pass

    logger.info(f"Backtest complete: {len(trades)} trades")
    return trades


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run PEAD backtest")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   required=True, help="End date YYYY-MM-DD")
    args = parser.parse_args()

    trades = run_backtest(args.start, args.end)

    from backtest.report import generate_report
    generate_report(trades)


if __name__ == "__main__":
    main()
