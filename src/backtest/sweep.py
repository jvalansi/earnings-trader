"""
Parameter sensitivity sweep. Runs the full backtest for every combination in
a parameter grid and prints a ranked results table.

Usage:
    PYTHONPATH=src python src/backtest/sweep.py --start 2022-01-01 --end 2023-12-31

Default grid (override by editing SWEEP_GRID below or passing --grid as JSON):
    ATR_STOP_MULTIPLIER: [1.5, 2.0, 2.5, 3.0, 3.5]
    MIN_AH_MOVE_PCT:     [0.0, 0.02, 0.03, 0.05]
    MIN_PRICE:           not a config var — applied as pre-filter in sweep
    HOLD_DAYS:           [5, 7, 10, 15]

Results are sorted by Sharpe (descending) and also written to
data/backtest_results/sweep_<timestamp>.json.

Note: MIN_PRICE is handled as a special case — it's not in production config
but is a candidate addition. Tickers with entry_price < MIN_PRICE are excluded
from trades when computing metrics for that grid cell.
"""
import argparse
import itertools
import json
import logging
from datetime import datetime
from pathlib import Path

from backtest.runner import run_backtest, SimTrade
from backtest.report import generate_report

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SWEEP_GRID: dict[str, list] = {
    "ATR_STOP_MULTIPLIER": [1.5, 2.0, 2.5, 3.0, 3.5],
    "MIN_AH_MOVE_PCT":     [0.0, 0.02, 0.03, 0.05],
    "HOLD_DAYS":           [5, 7, 10, 15],
}

# MIN_PRICE is a post-hoc filter applied to already-simulated trades.
# This avoids re-running the full backtest per price threshold.
MIN_PRICE_VALUES = [0.0, 2.0, 5.0, 10.0]


def _filter_by_min_price(trades: list[SimTrade], min_price: float) -> list[SimTrade]:
    if min_price <= 0:
        return trades
    return [t for t in trades if t.entry_price >= min_price]


def run_sweep(
    start_date: str,
    end_date: str,
    grid: dict[str, list] | None = None,
) -> list[dict]:
    """Run full backtest for every combination in the grid.

    Because MIN_PRICE is a post-hoc filter, we first run one backtest per
    combination of (ATR_STOP_MULTIPLIER, MIN_AH_MOVE_PCT, HOLD_DAYS), then
    apply each MIN_PRICE threshold to the resulting trade list without
    re-running the simulation.

    Returns list of result dicts sorted by Sharpe descending.
    """
    if grid is None:
        grid = SWEEP_GRID

    # Separate MIN_PRICE from runner-level params
    runner_grid = {k: v for k, v in grid.items() if k != "MIN_PRICE"}
    min_prices = grid.get("MIN_PRICE", MIN_PRICE_VALUES)

    keys = list(runner_grid.keys())
    combos = list(itertools.product(*[runner_grid[k] for k in keys]))
    total_runner_runs = len(combos)
    total_runs = total_runner_runs * len(min_prices)

    logger.setLevel(logging.WARNING)  # suppress INFO during sweep
    print(f"\nSweep: {total_runner_runs} param combos × {len(min_prices)} price filters = {total_runs} result rows\n")

    all_results = []
    cache: dict[tuple, list[SimTrade]] = {}  # combo → trades

    for i, values in enumerate(combos, 1):
        overrides = dict(zip(keys, values))
        combo_key = tuple(sorted(overrides.items()))

        print(f"  [{i}/{total_runner_runs}] {overrides} ...", end=" ", flush=True)
        try:
            trades = run_backtest(start_date, end_date, config_overrides=overrides)
            cache[combo_key] = trades
            print(f"{len(trades)} trades")
        except Exception as e:
            print(f"ERROR: {e}")
            cache[combo_key] = []

    # Apply MIN_PRICE filters and collect metrics
    for combo_key, trades in cache.items():
        overrides = dict(combo_key)
        for min_price in min_prices:
            filtered = _filter_by_min_price(trades, min_price)
            metrics = generate_report(filtered, print_output=False)
            row = {**overrides, "MIN_PRICE": min_price, **metrics}
            all_results.append(row)

    # Sort by Sharpe descending, then expectancy_usd
    all_results.sort(key=lambda r: (r.get("sharpe", -999), r.get("expectancy_usd", -999)), reverse=True)

    return all_results


def _print_sweep_table(results: list[dict], top_n: int = 20) -> None:
    print(f"\n{'SWEEP RESULTS':=<80}")
    print(f"{'ATR_MULT':>8} {'AH_MIN':>7} {'HOLD':>5} {'MINPRC':>7} "
          f"{'Trades':>7} {'WinRate':>8} {'Expect%':>8} {'Expect$':>8} "
          f"{'Sharpe':>7} {'MaxDD':>8}")
    print("-" * 80)
    for r in results[:top_n]:
        if r.get("error"):
            continue
        print(
            f"{r.get('ATR_STOP_MULTIPLIER', '-'):>8.1f} "
            f"{r.get('MIN_AH_MOVE_PCT', '-'):>7.2f} "
            f"{r.get('HOLD_DAYS', '-'):>5} "
            f"{r.get('MIN_PRICE', '-'):>7.1f} "
            f"{r.get('total_trades', 0):>7} "
            f"{r.get('win_rate', 0)*100:>7.1f}% "
            f"{r.get('expectancy_pct', 0):>+7.2f}% "
            f"${r.get('expectancy_usd', 0):>+7.2f} "
            f"{r.get('sharpe', 0):>7.2f} "
            f"${r.get('max_drawdown_usd', 0):>7.0f}"
        )
    print()


def _save_results(results: list[dict], start_date: str, end_date: str) -> Path:
    out_dir = Path("data/backtest_results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"sweep_{ts}.json"
    payload = {
        "start_date": start_date,
        "end_date": end_date,
        "run_at": datetime.now().isoformat(),
        "results": results,
    }
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2)
    print(f"Results saved to {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Sweep PEAD backtest parameters")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--top",   type=int, default=20, help="Top N rows to print")
    args = parser.parse_args()

    results = run_sweep(args.start, args.end)
    _print_sweep_table(results, top_n=args.top)
    _save_results(results, args.start, args.end)

    if results:
        best = results[0]
        print("Best config by Sharpe:")
        for k in ("ATR_STOP_MULTIPLIER", "MIN_AH_MOVE_PCT", "HOLD_DAYS", "MIN_PRICE"):
            print(f"  {k}: {best.get(k)}")
        print(f"  → Sharpe {best.get('sharpe'):.2f}, "
              f"expectancy ${best.get('expectancy_usd'):+.2f}/trade, "
              f"win rate {best.get('win_rate', 0)*100:.1f}%\n")


if __name__ == "__main__":
    main()
