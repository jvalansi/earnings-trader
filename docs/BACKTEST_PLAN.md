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

## Success Criteria

Before using backtest results to tune parameters:
- Simulated Feb–Mar 2026 trades match paper trades on entry dates (>80% match rate)
- At least 200 total trades in the backtest window (enough for statistical significance)
- Backtest completes in under 10 minutes with caching enabled
