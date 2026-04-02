"""
Backtest runner. Replays the PEAD strategy over historical earnings events.

Usage:
    PYTHONPATH=src python src/backtest/runner.py --start 2022-01-01 --end 2024-12-31

Output:
    list[SimTrade] — one record per completed trade.
    Calls report.generate_report() and prints a summary to stdout.

Config overrides:
    Pass a dict of config key overrides to run_backtest() for sensitivity analysis.
    E.g. {"ATR_STOP_MULTIPLIER": 3.0, "MIN_AH_MOVE_PCT": 0.05}
    These are passed directly to evaluate_entry() / evaluate_positions().

Design notes:
    - Uses evaluate_entry() and evaluate_positions() from decision.py directly,
      so backtest and production share identical logic with no duplication.
    - Entry price and AH move signal both use next-trading-day open vs prior close,
      matching the production approach of entering at market open after confirming
      the overnight gap. No AMC/BMO distinction needed.
    - ETF OHLCV for all 12 sector ETFs + SPY is preloaded for the full date
      range to avoid repeated yfinance calls in the inner loop.
    - All FMP and yfinance calls are disk-cached via backtest/data.py.
"""
import argparse
import logging
from dataclasses import dataclass

import config as _cfg
from backtest.data import (
    get_trading_dates,
    get_ohlcv_range,
    get_close_on_date,
    get_open_on_date,
    get_atr_as_of,
    get_prior_runup_as_of,
    get_historical_earnings_calendar,
    get_historical_surprise,
    get_exchange_cached,
    get_sector_etf_cached,
    get_sector_move_on_date,
)
from data.sector import SECTOR_ETF_MAP, FALLBACK_ETF
from decision import evaluate_entry, evaluate_positions
from state import Position

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

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
# Main runner
# ---------------------------------------------------------------------------

def run_backtest(
    start_date: str,
    end_date: str,
    config_overrides: dict = {},
) -> list[SimTrade]:
    """Run the PEAD backtest over the given date range.

    Args:
        start_date: 'YYYY-MM-DD' inclusive
        end_date:   'YYYY-MM-DD' inclusive
        config_overrides: dict of config keys to override (for sweep).
            Supported keys: MIN_EPS_BEAT_PCT, MIN_AH_MOVE_PCT, MAX_PRIOR_RUNUP_PCT,
            SECTOR_ETF_MIN, ATR_STOP_MULTIPLIER, HOLD_DAYS, MAX_POSITIONS,
            POSITION_SIZE_USD, ALLOWED_EXCHANGES.

    Returns:
        list[SimTrade] — one per completed trade (open positions at end_date
        are closed at last available price with reason='backtest_end').
    """
    cfg = {
        "MIN_EPS_BEAT_PCT":    config_overrides.get("MIN_EPS_BEAT_PCT",    _cfg.MIN_EPS_BEAT_PCT),
        "MIN_AH_MOVE_PCT":     config_overrides.get("MIN_AH_MOVE_PCT",     _cfg.MIN_AH_MOVE_PCT),
        "MAX_PRIOR_RUNUP_PCT": config_overrides.get("MAX_PRIOR_RUNUP_PCT", _cfg.MAX_PRIOR_RUNUP_PCT),
        "SECTOR_ETF_MIN":      config_overrides.get("SECTOR_ETF_MIN",      _cfg.SECTOR_ETF_MIN),
        "ATR_STOP_MULTIPLIER": config_overrides.get("ATR_STOP_MULTIPLIER", _cfg.ATR_STOP_MULTIPLIER),
        "HOLD_DAYS":           config_overrides.get("HOLD_DAYS",           _cfg.HOLD_DAYS),
        "MAX_POSITIONS":       config_overrides.get("MAX_POSITIONS",       _cfg.MAX_POSITIONS),
        "POSITION_SIZE_USD":   config_overrides.get("POSITION_SIZE_USD",   _cfg.POSITION_SIZE_USD),
        "ALLOWED_EXCHANGES":   config_overrides.get("ALLOWED_EXCHANGES",   _cfg.ALLOWED_EXCHANGES),
    }

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

    positions: list[Position] = []
    trades: list[SimTrade] = []

    for date in trading_dates:
        # --- Update existing positions ---
        current_prices = {}
        current_atrs = {}
        for pos in positions:
            pos.day_count += 1
            try:
                df = get_ohlcv_range(pos.ticker, start_date, end_date)
                current_prices[pos.ticker] = get_close_on_date(df, date)
                current_atrs[pos.ticker] = get_atr_as_of(df, date)
            except Exception:
                pass

        actions = evaluate_positions(
            positions, current_prices, current_atrs,
            atr_stop_multiplier=cfg["ATR_STOP_MULTIPLIER"],
            hold_days=cfg["HOLD_DAYS"],
        )

        still_open = []
        for pos, action in zip(positions, actions):
            if action.action == "sell":
                price = current_prices.get(pos.ticker, pos.entry_price)
                pnl_usd = round((price - pos.entry_price) * pos.quantity, 2)
                pnl_pct = round((price / pos.entry_price) - 1.0, 4)
                trades.append(SimTrade(
                    ticker=pos.ticker,
                    entry_date=pos.entry_date,
                    exit_date=date,
                    entry_price=pos.entry_price,
                    exit_price=price,
                    exit_reason=action.reason,
                    pnl_usd=pnl_usd,
                    pnl_pct=pnl_pct,
                    days_held=pos.day_count,
                ))
            elif action.action == "update_stop":
                pos.current_stop = action.new_stop
                still_open.append(pos)
            else:
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

            ticker = record.get("symbol", "")
            if not ticker:
                continue

            try:
                exchange = get_exchange_cached(ticker)
                if exchange not in cfg["ALLOWED_EXCHANGES"]:
                    continue

                df = get_ohlcv_range(ticker, start_date, end_date)
                atr = get_atr_as_of(df, date)
                prior_runup = get_prior_runup_as_of(df, date)

                # Entry price and AH signal: next-day open vs earnings-date close.
                # Matches production (market order at 9:30 AM after overnight gap confirmed).
                reg_close = get_close_on_date(df, date)
                next_rows = df[df.index.strftime("%Y-%m-%d") > date]
                if next_rows.empty:
                    continue
                entry_price = float(next_rows["Open"].iloc[0])
                ah_move = (entry_price / reg_close) - 1.0

                etf = get_sector_etf_cached(ticker)
                etf_df = etf_dfs.get(etf, etf_dfs.get(FALLBACK_ETF))
                if etf_df is None:
                    continue
                sector_move = get_sector_move_on_date(etf, etf_df, date)

                surprise = get_historical_surprise(ticker, date)

                # Use evaluate_entry() from decision.py directly
                signal = evaluate_entry(
                    ticker=ticker,
                    surprise=surprise,
                    ah_move=ah_move,
                    prior_runup=prior_runup,
                    sector_move=sector_move,
                    atr=atr,
                    current_price=entry_price,
                    open_positions=positions,
                    min_eps_beat_pct=cfg["MIN_EPS_BEAT_PCT"],
                    min_ah_move_pct=cfg["MIN_AH_MOVE_PCT"],
                    max_prior_runup_pct=cfg["MAX_PRIOR_RUNUP_PCT"],
                    sector_etf_min=cfg["SECTOR_ETF_MIN"],
                    atr_stop_multiplier=cfg["ATR_STOP_MULTIPLIER"],
                    max_positions=cfg["MAX_POSITIONS"],
                )
                if not signal.should_enter:
                    continue

                quantity = max(1, int(cfg["POSITION_SIZE_USD"] / entry_price))
                positions.append(Position(
                    ticker=ticker,
                    entry_date=date,
                    entry_price=entry_price,
                    current_stop=signal.initial_stop,
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
            pnl_usd = round((close - pos.entry_price) * pos.quantity, 2)
            pnl_pct = round((close / pos.entry_price) - 1.0, 4)
            trades.append(SimTrade(
                ticker=pos.ticker,
                entry_date=pos.entry_date,
                exit_date=trading_dates[-1],
                entry_price=pos.entry_price,
                exit_price=close,
                exit_reason="backtest_end",
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                days_held=pos.day_count,
            ))
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
