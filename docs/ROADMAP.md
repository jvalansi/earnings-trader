# Roadmap

## Roadmap to Profitability

**Current state:** Paper trading — live signals, real data, no capital deployed.

| Milestone | Description | Expected Monthly ROI |
|---|---|---|
| **Phase 2 complete** | Paper trading validated at the go/no-go checkpoint below | — |
| **Phase 3: go live** | Deploy $5k capital via Alpaca, real P&L | $200–500/mo |
| **Scale capital** | Raise to $20k+ as strategy proves out | $800–2,000/mo |
| **Multi-strategy** | Add BMO + sector rotation variants | 2–3× current returns |
| **Automation** | Zero-touch daily operation, Slack alerts only | — |

### Go/No-Go Checkpoint

Decided on the **post-fix trade set only** (entries on or after 2026-04-02, when commit
`2f38981` fixed the look-ahead entry bug). Earlier trades used a broken entry model
documented in `docs/BACKTEST.md`.

Run `python src/main.py track-record` to evaluate. Criteria, on the per-trade return series,
**decided at n >= 150**:

- **GO** (deploy $5k): `t-stat >= 2.0` **and** `mean return >= +1.0%/trade`
- **PILOT** (deploy $2.5k, re-evaluate at n = 300): `t-stat >= 1.0` **and** `mean >= +1.0%/trade`
- **NO-GO**: `mean return <= 0`, **or** the 95% CI upper bound is below +1.0%/trade
- **EXTEND** (keep paper trading): anything in between, including any sample below 150

Win rate is not a criterion — the backtest edge is payoff-asymmetric (1.46x win/loss ratio),
so a sub-50% win rate is consistent with a profitable strategy.

**Why n >= 150.** Per-trade returns have a ~12.9% standard deviation. At n = 60 a t-stat of
2 requires +3.3%/trade, and at n = 88 it requires +2.74% — both above the +2.46%/trade the
backtest itself produced. The earlier criteria (decide at n >= 60, kill at t <= 1.0) could
not have returned a GO even if the strategy performed exactly as backtested: at n = 88 the
backtest's own edge scores t = 1.79. That version tested the sample size, not the strategy.
The kill rule is now evidence-based — a negative mean, or a confidence interval that
excludes a tradeable edge — rather than a weak t-stat, which at small n means only
"not yet measurable".

| Evaluated | n | Win rate | Mean return | 95% CI | t-stat | Verdict |
|---|---|---|---|---|---|---|
| 2026-07-02 | 43 | 44.2% | +3.88% | — | 1.71 | EXTEND |
| 2026-08-28 | 88 | 50.0% | +0.90% | -1.79% to +3.59% | 0.65 | EXTEND (sample too small) |

The confidence interval contains both zero and the backtest's +2.46%, so the live record so
far distinguishes neither. The payoff profile does match the backtest — avg win +9.22% vs
+10.31%, avg loss -7.43% vs -7.04%, win rate 50.0% vs 54.8% (z = -0.90, not distinguishable).
At ~18 closed trades/month, n = 150 arrives around 2026-12.

**Next step (Notion task):** Add P&L performance dashboard + returns tracking — better visibility → better parameter tuning → ~$500/mo improvement in returns.

**Notion project page:** [earnings-trader — Strategy & Profitability](https://www.notion.so/earnings-trader-Strategy-Profitability-32505a1b5e0181d48a30ccf98437fa9f)

---

## Why Phase 2 First

Rather than building a backtester before seeing any live signals, we're starting with live paper trading so we can:
- Validate the data pipeline (FMP + yfinance) against real earnings events immediately
- Start accumulating a real trade log to eventually backtest against
- Catch integration issues (AH data availability, FMP rate limits, scheduling edge cases) in production before they matter

The backtester (Phase 1) will be built afterward using the same `data/` modules — the code is reusable.

---

## Phase 1 — Backtester

> Status: **Not started** (planned after Phase 2 is running)

- [ ] `data/earnings.py` — pull historical EPS/revenue surprises from FMP
- [ ] `data/prices.py` — ATR, AH move, run-up from yfinance
- [ ] `data/sector.py` — sector ETF filter
- [ ] `decision.py` — entry filter logic
- [ ] Backtest runner — scan historical earnings dates, simulate entries/exits, report P&L
- [ ] P&L report — win rate, avg return, max drawdown, Sharpe ratio

---

## Phase 2 — Live Paper Trading

> Status: **In progress** (current focus)

- [ ] `config.py` — all thresholds and parameters, loads `.env`
- [ ] `data/prices.py` — yfinance: OHLCV, ATR, AH move, prior run-up
- [ ] `data/earnings.py` — FMP: EPS/revenue surprise + daily earnings calendar
- [ ] `data/sector.py` — sector ETF mapping and daily % change
- [ ] `state.py` — JSON-backed position store (`data/positions.json`)
- [ ] `decision.py` — evaluate_entry() + evaluate_positions() (pure logic)
- [ ] `execution.py` — paper trade logger → `data/trades_log.jsonl`
- [ ] `scheduler.py` — APScheduler: scan @ 4:15 PM ET, update @ 4:30 PM ET
- [ ] `main.py` — entry point
- [ ] End-to-end dry run on next earnings season

---

## Strategy Investigation

> Observations from paper trading (Feb–Mar 2026) and open questions to explore.

### Findings so far
- All 6 losing trades were stopped out before day 10, most within 1–3 days
- RSKD was stopped out same day as entry — AH move reversed immediately at open
- 1W / 6L, -$532 total on $1,000/position sizing
- `ATR_STOP_MULTIPLIER = 1.5` appears too tight for post-earnings volatility; raised to 2.5

### Open questions

**Stop loss calibration**
- Is 2.5x ATR enough room, or should we go wider (3x)?
- Should the stop be wider on day 1 (absorb the post-earnings noise) and tighten after day 3?

**Entry timing — skip the AH move signal?**
- Current: enter after seeing a 3%+ AH move (4:15 PM for AMC, premarket for BMO)
- Hypothesis: with EPS beat + revenue beat + low prior runup + positive sector ETF, the AH move filter may be redundant and just gives a worse entry price
- Counter-argument: AH move still provides directional confirmation after guidance is known
- To investigate: compare win rate of trades that passed all filters vs those that also had AH move

**Minimum price filter**
- HRTX ($1.20) and RSKD ($4.75) are extremely noisy small-caps
- Consider adding `MIN_PRICE = 5.0` to avoid sub-$5 stocks

**Position sizing**
- Fixed $1,000/trade means 833 shares of a $1.20 stock — amplifies noise
- Consider a minimum price floor or vol-adjusted sizing

---

## Phase 3 — Live Trading

> Status: **Infrastructure ready, not deployed** — blocked on the go/no-go checkpoint above

- [x] `execution.py` — Alpaca integration with fill polling, real fill prices and slippage capture
- [x] Position sizing — `ACCOUNT_CAPITAL_USD / MAX_POSITIONS`, default $5,000 / 10 = $500 per slot
- [x] Alerting / monitoring — Slack + Discord notifications on entries, exits, failures and halts
- [x] Risk controls — daily loss limit, max drawdown circuit breaker, manual kill switch (`risk.py`)
- [x] Preflight — broker reachability and local/broker position reconciliation at startup (`broker.py`)
- [x] Live-mode guard — requires Alpaca keys **and** `LIVE_TRADING_CONFIRMED=yes`; never simulates a live order
- [ ] Measured slippage — no broker fill has been recorded yet; every fill in the log so far is assumed
- [ ] Statistical edge — checkpoint currently reads NO-GO

### Before deploying capital

1. Run against Alpaca paper (`ALPACA_*` keys set) long enough to record real fills, then
   compare `track-record` slippage against the assumed 9:30 open fill.
2. Flatten the simulated book — `data/positions.json` holds positions sized for an $80k
   book; the risk epoch excludes them, but they should not be inherited into live trading.
3. Reach n = 150 post-fix trades and re-run the checkpoint.

### Known execution gap vs the backtest

Measured over the post-fix trades (2026-04-02 to 2026-08-28):

| Gap | Cost |
|---|---|
| Entry lands ~2 minutes after the open, which the backtest assumes it fills at | **-0.46%/trade** (median -0.56%; 62% of entries above the open) |
| Exits were decided on the prior day's close (fixed 2026-08-28) and fill at the next open, while the backtest exits at the same-day close | **-0.18%/trade** on logged prices; the decision lag itself is not yet measured |

Together that is roughly a quarter of the backtest's +2.46%/trade expectancy, before any
broker slippage. Entering closer to the open, or modelling a 9:32 entry in the backtest,
would close most of the gap.
