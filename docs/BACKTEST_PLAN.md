# Backtesting Plan

## Goal

Run the PEAD strategy against 2–3 years of historical earnings data to answer:
- What is the realistic win rate and expectancy?
- Which parameter values (stop multiplier, min price, AH move threshold) optimize risk-adjusted returns?
- Does the strategy have positive edge, or is Phase 2 performance just noise?

---

## Approach: Event-Driven Simulation

The backtester replays historical earnings events one by one, applies the same entry/exit logic used in production, and tracks P&L using actual historical prices.

**Reuse existing modules as-is:**
- `decision.py` — `evaluate_entry()` and `evaluate_positions()` are pure functions; feed them historical data
- `data/earnings.py` — already supports historical FMP queries
- `data/prices.py` — yfinance supports any historical date range
- `config.py` — all thresholds imported directly; override per run for sensitivity analysis

**Do NOT reuse:**
- `execution.py` — skip broker calls; record simulated fills directly
- `state.py` — use in-memory position list instead of `positions.json`
- `scheduler.py` — replaced by a date-loop

---

## Implementation Plan

### Step 1 — Historical Data Layer

**File:** `src/backtest/data.py`

Thin wrappers that cache results to avoid re-fetching the same date repeatedly.

```python
def get_earnings_for_date(date: str) -> list[dict]:
    """Return FMP earnings calendar for a specific past date."""
    # FMP endpoint: /stable/earnings-calendar?from=DATE&to=DATE
    # Same as earnings.py but for historical dates

def get_historical_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """yfinance download for date range."""

def get_historical_atr(ticker: str, as_of_date: str, period: int = 14) -> float:
    """ATR computed from OHLCV ending on as_of_date."""

def get_historical_ah_move(ticker: str, date: str) -> float:
    """After-hours % move on a specific past date."""
    # yfinance: after_hours=True on daily bar or use intraday

def get_historical_prior_runup(ticker: str, date: str, days: int = 10) -> float:
    """% change over the N trading days before date."""

def get_historical_sector_move(ticker: str, date: str) -> float:
    """Sector ETF % change on date."""
```

**Caching strategy:** Pickle or parquet files in `data/backtest_cache/`. Key by `(ticker, date)`. This avoids hammering yfinance/FMP on repeated runs.

---

### Step 2 — Backtest Runner

**File:** `src/backtest/runner.py`

```python
def run_backtest(
    start_date: str,  # e.g. "2022-01-01"
    end_date: str,    # e.g. "2024-12-31"
    config_overrides: dict = {},  # e.g. {"ATR_STOP_MULTIPLIER": 3.0}
) -> BacktestResult:
```

**Algorithm:**

```
for each trading date D from start_date to end_date:
    1. Fetch earnings calendar for D (FMP)
    2. For each ticker in calendar:
         a. Fetch: surprise, AH move, prior runup, sector move, ATR, price
         b. Call evaluate_entry() with current in-memory positions
         c. If should_enter: open a simulated position
    3. For each open position:
         a. Fetch today's close price and ATR
         b. Call evaluate_positions()
         c. Apply action (close position or update stop)
         d. Record trade result if closed
```

**Position object (in-memory):**
```python
@dataclass
class SimPosition:
    ticker: str
    entry_date: str
    entry_price: float
    current_stop: float
    day_count: int
    quantity: int
    shares: int  # POSITION_SIZE_USD / entry_price
```

**Trade result object:**
```python
@dataclass
class SimTrade:
    ticker: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    exit_reason: str  # "stop", "max_days"
    pnl_usd: float
    pnl_pct: float
    days_held: int
```

---

### Step 3 — P&L Report

**File:** `src/backtest/report.py`

```python
def generate_report(trades: list[SimTrade]) -> dict:
```

Metrics to compute:

| Metric | Formula |
|--------|---------|
| **Win rate** | wins / total trades |
| **Avg return** | mean(pnl_pct) |
| **Avg win / avg loss** | mean(pnl_pct where win) / abs(mean(pnl_pct where loss)) |
| **Expectancy** | (win_rate × avg_win) - (loss_rate × avg_loss) |
| **Max drawdown** | Largest peak-to-trough in cumulative P&L curve |
| **Sharpe ratio** | mean(daily_returns) / std(daily_returns) × √252 |
| **Total return** | sum(pnl_usd) |
| **Trades per month** | total trades / months in range |

Print a text summary and optionally write to `data/backtest_results/YYYY-MM-DD_HH-MM.json`.

---

### Step 4 — Sensitivity Analysis

**File:** `src/backtest/sweep.py`

Grid search over key parameters to find the optimal config:

```python
SWEEP_GRID = {
    "ATR_STOP_MULTIPLIER": [1.5, 2.0, 2.5, 3.0, 3.5],
    "MIN_AH_MOVE_PCT":     [0.0, 0.02, 0.03, 0.05],
    "MIN_PRICE":           [0.0, 2.0, 5.0, 10.0],
    "HOLD_DAYS":           [5, 7, 10, 15],
}
```

For each combination: run full backtest, collect `expectancy` and `sharpe`. Output a ranked table sorted by Sharpe.

---

### Step 5 — Validate Against Paper Trades

Cross-check: run the backtester over Feb–Mar 2026 and compare simulated trades against `data/trades_log.jsonl`. They should match on entry/exit dates (within data availability constraints). Any divergence reveals bugs or data gaps.

---

## Data Challenges & Mitigations

| Challenge | Mitigation |
|-----------|-----------|
| **FMP rate limits (250/day free)** | Cache all fetched earnings data; run backtest in batches across days |
| **yfinance AH data availability** | AH data for older dates is unreliable pre-2020; cap backtest start at 2022 |
| **Look-ahead bias** | Only use data available as of market close on the earnings date |
| **Survivorship bias** | FMP historical calendar includes delisted companies — no special handling needed |
| **Post-earnings price gaps** | Use actual open price on day after earnings (not close) if AH move data is unavailable |

---

## File Structure

```
src/backtest/
├── __init__.py
├── data.py      # Historical data fetchers + cache
├── runner.py    # Main event loop
├── report.py    # P&L metrics and output
└── sweep.py     # Parameter grid search
```

CLI entry point:
```
PYTHONPATH=src python src/backtest/runner.py --start 2022-01-01 --end 2024-12-31
PYTHONPATH=src python src/backtest/sweep.py --start 2022-01-01 --end 2024-12-31
```

---

## Build vs. Reuse

Evaluated: **Backtrader**, **VectorBT**, **Backtesting.py**, **Zipline-Reloaded**.

All assume price-bar-driven strategies. This strategy is *earnings-event-driven* — the trigger is FMP earnings data on specific dates, not price indicators. Wiring that into any framework requires a custom data feed regardless, which costs as much as writing the runner itself.

**Decision: build it.** Reasons:
- `decision.py` is already pure functions — the runner is just a date loop calling them
- The data layer (FMP + yfinance fetching + caching) is the same work either way
- Any library saves the loop but adds 100–200 lines of adapter boilerplate to compensate for non-standard data
- No library dependency = no version conflicts, easier debugging

**One exception:** once `SimTrade` results exist, **VectorBT** is worth using for the parameter sweep (Step 4). It's fast at scanning large grids and producing ranked output tables — that part of the analysis fits its vectorized model well.

---

## Evaluation Loop & Exit Strategy

### Checkpoints

**Checkpoint 1 — Fidelity validation** *(stop and report)*
Before running on historical data: show how well the backtester replays Feb–Mar 2026 paper trades.
- If match rate ≥80% → continue to historical run
- If match rate <80% → stop; backtester has a bug or data issue; decide whether to debug or fix data before continuing

**Checkpoint 2 — In-sample results (2022–2023)** *(stop and report)*
Present raw metrics. Three outcomes:
- **Looks good** → continue to parameter sweep automatically
- **Looks bad but diagnosable** → present one targeted hypothesis (e.g. "stop too tight") and ask whether to test it
- **Looks bad and unclear** → stop; present the breakdown; discuss whether the strategy has fundamental problems before continuing

**Checkpoint 3 — Final results** *(stop and report)*
After out-of-sample (2024) holdout check: sweep results, optimal params vs. current config, and a concrete go/no-go recommendation for Phase 3. Go/no-go decision stays with you.

**Unplanned interruption:** if a hard data blocker is hit mid-build (e.g. FMP doesn't serve historical AH data far enough back), flag it immediately rather than silently working around it.

Implementation details within each phase are handled autonomously — no check-ins for those.

---

### Success Criteria

**Stage 1 — Backtester fidelity** (gate before trusting any results)
- ≥80% of simulated Feb–Mar 2026 trades match paper trades on entry date
- No look-ahead bias (only data available by 4:15 PM ET on earnings day used)
- Full 3-year run completes in <10 min with caching

**Stage 2 — Strategy has edge (2022–2023 in-sample)**

| Metric | Minimum | Good |
|--------|---------|------|
| Win rate | >45% | >55% |
| Expectancy (avg $ per trade) | >$0 | >$50 |
| Sharpe (annualized) | >0.5 | >1.0 |
| Max drawdown | <30% | <15% |
| Trades in window | >150 | >300 |

**Expectancy** is the gate metric — win rate alone is misleading. A 40% win rate with 2:1 win/loss beats a 55% win rate with 0.8:1.

**Stage 3 — Out-of-sample holds (2024 holdout)**
- Expectancy doesn't degrade >30% vs in-sample
- Anti-overfitting check: parameters that only work on 2022–2023 will fail here

**Stage 4 — Go/no-go for live capital**
- All Stage 2 + 3 criteria met
- Optimal params from sweep differ from current config by no more than one threshold (large changes = overfit signal)
- Paper trading win rate trending toward backtest expectation

---

### What Failure Tells You

| Outcome | Diagnosis |
|---------|-----------|
| Win rate <40%, losses within 1–2 days | Stop too tight — test wider multiplier |
| Win rate ok, avg win << avg loss | Holding too long into reversals — test shorter `HOLD_DAYS` |
| Too few trades (<100 in 3 years) | Filters too restrictive — AH move threshold likely culprit |
| Out-of-sample degrades badly | Overfit — revert to default config, don't tune |
| Backtest looks great, paper trades don't match | Data issue (AH prices, timing) — fix backtester first |
