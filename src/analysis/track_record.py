"""
Track record report and go/no-go verdict for the paper-to-live decision.

    stats(trades)             -> dict   n, win_rate, mean_ret, t_stat, expectancy, max_drawdown, ...
    verdict(s)                -> tuple  (decision, explanation) against the checkpoint criteria
    slippage_stats(orders)    -> dict   realized slippage by execution mode
    report(since=None)        -> int    prints the full report; exit code for the CLI

Checkpoint criteria (docs/ROADMAP.md, "Go/No-Go Checkpoint"). Only trades entered on or
after POST_FIX_START count: earlier ones used the look-ahead entry model fixed in 2f38981.
Win rate is deliberately not a criterion — the edge is payoff-asymmetric, so a sub-50%
win rate is consistent with a profitable strategy.

The sample size is set by power, not by impatience. Per-trade returns have a ~13%
standard deviation, so at n=60 a t-stat of 2 needs +3.3%/trade — more than the backtest
itself produced. Deciding that early tests the sample size, not the strategy. At n=150 a
2%/trade edge is detectable, which is the region worth deploying capital into.

A kill is declared on evidence rather than on a weak t-stat: either the mean is negative,
or the 95% confidence interval excludes an edge large enough to be worth trading.
"""
import math
import statistics

from trade_history import ClosedTrade, closed_trades, load_orders

POST_FIX_START = "2026-04-02"

MIN_TRADES = 150        # sample needed before the test can distinguish a real edge from noise
GO_T_STAT = 2.0         # full deployment
PILOT_T_STAT = 1.0      # half capital, re-evaluate at 2x the sample
MIN_MEAN_RET = 0.01     # an edge below +1%/trade is not worth the operational risk


def stats(trades: list[ClosedTrade]) -> dict:
    """Per-trade return statistics. Returns {} for an empty trade list."""
    if not trades:
        return {}

    rets = [t.ret for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    mean = statistics.mean(rets)
    sd = statistics.stdev(rets) if len(rets) > 1 else 0.0

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x.exit_ts):
        equity += t.pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    stderr = sd / math.sqrt(len(rets)) if sd else 0.0
    return {
        "n": len(trades),
        "win_rate": len(wins) / len(rets),
        "ci95": (mean - 1.96 * stderr, mean + 1.96 * stderr),
        "mean_ret": mean,
        "median_ret": statistics.median(rets),
        "stdev_ret": sd,
        "t_stat": mean / (sd / math.sqrt(len(rets))) if sd else float("nan"),
        "avg_win": statistics.mean(wins) if wins else 0.0,
        "avg_loss": statistics.mean(losses) if losses else 0.0,
        "total_pnl": sum(t.pnl for t in trades),
        "expectancy": sum(t.pnl for t in trades) / len(trades),
        "max_drawdown": max_dd,
    }


def verdict(s: dict) -> tuple[str, str]:
    """Apply the checkpoint criteria to a stats dict.

    Returns one of GO (deploy in full), PILOT (deploy half), NO-GO (stop) or EXTEND
    (keep paper trading), with the reasoning.
    """
    if not s or s["n"] < MIN_TRADES:
        n = s.get("n", 0)
        needed = MIN_TRADES - n
        return "EXTEND", (
            f"{n} post-fix trades, {needed} short of the {MIN_TRADES} needed for the test "
            f"to distinguish a real edge from noise"
        )

    t, mean, ci_high = s["t_stat"], s["mean_ret"], s["ci95"][1]

    if t >= GO_T_STAT and mean >= MIN_MEAN_RET:
        return "GO", f"t-stat {t:.2f} >= {GO_T_STAT} and mean {mean:+.2%} >= {MIN_MEAN_RET:.1%}"
    if mean <= 0:
        return "NO-GO", f"mean return {mean:+.2%} is not positive over {s['n']} trades"
    if ci_high < MIN_MEAN_RET:
        return "NO-GO", (
            f"95% CI upper bound {ci_high:+.2%} rules out an edge of {MIN_MEAN_RET:.1%}/trade"
        )
    if t >= PILOT_T_STAT and mean >= MIN_MEAN_RET:
        return "PILOT", (
            f"t-stat {t:.2f} >= {PILOT_T_STAT} and mean {mean:+.2%} >= {MIN_MEAN_RET:.1%} — "
            f"deploy half capital and re-evaluate at {2 * MIN_TRADES} trades"
        )
    return "EXTEND", (
        f"t-stat {t:.2f} with mean {mean:+.2%}: too weak to deploy, too early to kill "
        f"(95% CI {s['ci95'][0]:+.2%} to {ci_high:+.2%})"
    )


def slippage_stats(orders: list[dict]) -> dict:
    """Realized slippage by execution mode, for orders that recorded it."""
    by_mode: dict[str, list[float]] = {}
    for o in orders:
        slip = o.get("slippage_pct")
        if slip is None:
            continue
        by_mode.setdefault(o.get("mode", "sim"), []).append(slip)
    return {
        mode: {
            "n": len(v),
            "mean": statistics.mean(v),
            "median": statistics.median(v),
            "worst": max(v),
        }
        for mode, v in by_mode.items()
    }


def _print_stats(label: str, s: dict) -> None:
    if not s:
        print(f"{label}: no trades")
        return
    print(f"{label}")
    print(f"  Trades:        {s['n']}")
    print(f"  Win rate:      {s['win_rate']:.1%}")
    print(f"  Mean return:   {s['mean_ret']:+.2%}   (median {s['median_ret']:+.2%}, sd {s['stdev_ret']:.2%})")
    print(f"  95% CI:        {s['ci95'][0]:+.2%} to {s['ci95'][1]:+.2%}")
    print(f"  t-stat:        {s['t_stat']:.2f}")
    print(f"  Avg win/loss:  {s['avg_win']:+.2%} / {s['avg_loss']:+.2%}")
    print(f"  Expectancy:    ${s['expectancy']:,.0f}/trade   (total ${s['total_pnl']:,.0f})")
    print(f"  Max drawdown:  ${s['max_drawdown']:,.0f}")


def report(since: str | None = None) -> int:
    """Print the full track record and checkpoint verdict."""
    trades = closed_trades()
    if not trades:
        print("No closed trades in the log.")
        return 1

    cutoff = since or POST_FIX_START
    post = [t for t in trades if t.entry_date >= cutoff]

    print("=" * 66)
    print(f"TRACK RECORD — {len(trades)} closed trades, {trades[0].entry_date} to {trades[-1].exit_date}")
    print("=" * 66)
    _print_stats("\nAll trades", stats(trades))
    _print_stats(f"\nDecision set (entered >= {cutoff})", stats(post))

    by_mode: dict[str, list[ClosedTrade]] = {}
    for t in post:
        by_mode.setdefault(t.mode, []).append(t)
    if len(by_mode) > 1:
        for mode, group in sorted(by_mode.items()):
            _print_stats(f"\nBy execution mode — {mode}", stats(group))

    slip = slippage_stats(load_orders())
    print("\nSlippage vs intended price (positive == worse)")
    real = {m: v for m, v in slip.items() if m in ("paper", "live")}
    if not real:
        print("  No broker fills recorded yet — every fill so far is assumed, not measured.")
    else:
        for mode, v in sorted(real.items()):
            print(f"  {mode}: n={v['n']} mean {v['mean']:+.3%} median {v['median']:+.3%} worst {v['worst']:+.3%}")

    s = stats(post)
    decision, why = verdict(s)
    print("\n" + "=" * 66)
    print(f"CHECKPOINT VERDICT: {decision} — {why}")
    print(f"  criteria at n>={MIN_TRADES}: GO at t>={GO_T_STAT}, PILOT (half capital) at "
          f"t>={PILOT_T_STAT}, both with mean>={MIN_MEAN_RET:.0%};")
    print(f"            NO-GO if mean<=0 or the 95% CI rules out a {MIN_MEAN_RET:.0%} edge")
    if s and s["n"] < MIN_TRADES:
        need = MIN_MEAN_RET
        print(f"  at the current spread, deciding at n={s['n']} would demand "
              f"{GO_T_STAT * s['stdev_ret'] / math.sqrt(s['n']):+.2%}/trade to pass — "
              f"more than the backtest's +2.46%")
    print("=" * 66)
    return 0
