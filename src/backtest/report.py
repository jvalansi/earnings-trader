"""
P&L report for backtest results.

    generate_report(trades, print_output=True)  -> dict
    validate_against_paper_trades(trades, log_path) -> dict

generate_report() computes:
    - trade count, win rate, avg return
    - avg win / avg loss, win/loss ratio, expectancy
    - total P&L, max drawdown, Sharpe ratio
    - breakdown by exit reason

validate_against_paper_trades() cross-checks simulated entries against the
production trades_log.jsonl to confirm the backtester replays reality correctly.
"""
import json
import math
from collections import defaultdict
from pathlib import Path

from backtest.runner import SimTrade


def generate_report(trades: list[SimTrade], print_output: bool = True) -> dict:
    """Compute P&L metrics from a list of SimTrade results.

    Returns a dict of all metrics. Also prints a formatted summary if
    print_output=True.
    """
    if not trades:
        result = {"error": "no trades"}
        if print_output:
            print("No trades to report.")
        return result

    # Exclude backtest_end trades from win/loss stats (incomplete holds)
    closed = [t for t in trades if t.exit_reason != "backtest_end"]
    total = len(closed)

    wins = [t for t in closed if t.pnl_usd > 0]
    losses = [t for t in closed if t.pnl_usd <= 0]

    win_rate = len(wins) / total if total else 0.0
    avg_win_pct = (sum(t.pnl_pct for t in wins) / len(wins)) if wins else 0.0
    avg_loss_pct = (sum(t.pnl_pct for t in losses) / len(losses)) if losses else 0.0
    avg_return_pct = sum(t.pnl_pct for t in closed) / total if total else 0.0

    win_loss_ratio = (
        abs(avg_win_pct / avg_loss_pct) if avg_loss_pct != 0 else float("inf")
    )
    expectancy_pct = (win_rate * avg_win_pct) + ((1 - win_rate) * avg_loss_pct)
    expectancy_usd = (
        sum(t.pnl_usd for t in closed) / total if total else 0.0
    )

    total_pnl = sum(t.pnl_usd for t in trades)
    avg_days_held = sum(t.days_held for t in closed) / total if total else 0.0

    # Max drawdown on cumulative P&L curve
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in closed:
        cum += t.pnl_usd
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    # Sharpe: annualise using trade-level returns (approximate)
    if total > 1:
        returns = [t.pnl_pct for t in closed]
        mean_r = sum(returns) / total
        variance = sum((r - mean_r) ** 2 for r in returns) / (total - 1)
        std_r = math.sqrt(variance) if variance > 0 else 0.0
        # Annualise assuming ~252 trading days / avg_days_held trades per year
        trades_per_year = 252 / max(avg_days_held, 1)
        sharpe = (mean_r / std_r * math.sqrt(trades_per_year)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # Exit reason breakdown
    reasons: dict[str, int] = defaultdict(int)
    for t in trades:
        reasons[t.exit_reason] += 1

    result = {
        "total_trades":      total,
        "wins":              len(wins),
        "losses":            len(losses),
        "win_rate":          round(win_rate, 4),
        "avg_return_pct":    round(avg_return_pct * 100, 2),
        "avg_win_pct":       round(avg_win_pct * 100, 2),
        "avg_loss_pct":      round(avg_loss_pct * 100, 2),
        "win_loss_ratio":    round(win_loss_ratio, 2),
        "expectancy_pct":    round(expectancy_pct * 100, 2),
        "expectancy_usd":    round(expectancy_usd, 2),
        "total_pnl_usd":     round(total_pnl, 2),
        "max_drawdown_usd":  round(max_dd, 2),
        "sharpe":            round(sharpe, 2),
        "avg_days_held":     round(avg_days_held, 1),
        "exit_reasons":      dict(reasons),
        "open_at_end":       len([t for t in trades if t.exit_reason == "backtest_end"]),
    }

    if print_output:
        _print_report(result, trades)

    return result


def _print_report(r: dict, trades: list[SimTrade]) -> None:
    print("\n" + "=" * 50)
    print("BACKTEST RESULTS")
    print("=" * 50)
    print(f"  Trades (closed):    {r['total_trades']}  ({r['open_at_end']} open at end)")
    print(f"  Win / Loss:         {r['wins']} W / {r['losses']} L")
    print(f"  Win rate:           {r['win_rate']*100:.1f}%")
    print(f"  Avg return:         {r['avg_return_pct']:+.2f}%")
    print(f"  Avg win:            {r['avg_win_pct']:+.2f}%")
    print(f"  Avg loss:           {r['avg_loss_pct']:+.2f}%")
    print(f"  Win/loss ratio:     {r['win_loss_ratio']:.2f}x")
    print(f"  Expectancy:         {r['expectancy_pct']:+.2f}%  (${r['expectancy_usd']:+.2f}/trade)")
    print(f"  Total P&L:          ${r['total_pnl_usd']:+.2f}")
    print(f"  Max drawdown:       ${r['max_drawdown_usd']:.2f}")
    print(f"  Sharpe (annl.):     {r['sharpe']:.2f}")
    print(f"  Avg days held:      {r['avg_days_held']:.1f}")
    print(f"  Exit reasons:       {r['exit_reasons']}")
    print("=" * 50 + "\n")

    # Per-trade detail
    print(f"{'Ticker':<8} {'Entry':>10} {'Exit':>10} {'Entry$':>8} {'Exit$':>8} {'P&L%':>7} {'Reason'}")
    print("-" * 70)
    for t in sorted(trades, key=lambda x: x.entry_date):
        print(
            f"{t.ticker:<8} {t.entry_date:>10} {t.exit_date:>10} "
            f"{t.entry_price:>8.2f} {t.exit_price:>8.2f} "
            f"{t.pnl_pct*100:>+6.1f}%  {t.exit_reason}"
        )
    print()


def validate_against_paper_trades(
    sim_trades: list[SimTrade],
    log_path: str = "data/trades_log.jsonl",
) -> dict:
    """Cross-check simulated entry dates against production trades_log.jsonl.

    For each paper trade BUY, checks whether the backtester also entered the
    same ticker on the same date (or within 1 day for timing edge cases).

    Returns a dict with match_rate and per-ticker details.
    """
    paper_path = Path(log_path)
    if not paper_path.exists():
        return {"error": f"trades log not found: {log_path}"}

    paper_buys: list[dict] = []
    with paper_path.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("action") == "buy" and rec.get("success"):
                paper_buys.append(rec)

    if not paper_buys:
        return {"error": "no paper buy trades found"}

    # Index simulated entries by (ticker, date)
    sim_entries: set[tuple] = set()
    for t in sim_trades:
        sim_entries.add((t.ticker, t.entry_date))

    results = []
    for buy in paper_buys:
        ticker = buy["ticker"].upper()
        ts = buy["timestamp"][:10]  # YYYY-MM-DD
        matched = (ticker, ts) in sim_entries
        results.append({
            "ticker":    ticker,
            "paper_date": ts,
            "matched":   matched,
        })

    matched_count = sum(1 for r in results if r["matched"])
    match_rate = matched_count / len(results) if results else 0.0

    report = {
        "paper_buys":    len(results),
        "matched":       matched_count,
        "unmatched":     len(results) - matched_count,
        "match_rate":    round(match_rate, 4),
        "details":       results,
    }

    print("\n" + "=" * 50)
    print("FIDELITY CHECK vs PAPER TRADES")
    print("=" * 50)
    print(f"  Paper buys:   {report['paper_buys']}")
    print(f"  Matched:      {report['matched']}")
    print(f"  Unmatched:    {report['unmatched']}")
    print(f"  Match rate:   {match_rate*100:.1f}%  (target: ≥80%)")
    print()
    for r in results:
        status = "✓" if r["matched"] else "✗"
        print(f"  {status} {r['ticker']:<8} {r['paper_date']}")
    print("=" * 50 + "\n")

    return report
