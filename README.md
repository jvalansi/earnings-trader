# earnings-trader

A systematic trading system that exploits **Post-Earnings Announcement Drift (PEAD)** — the empirically documented tendency for stocks to continue moving in the direction of an earnings surprise for days to weeks after the announcement.

---

## Core Thesis

Markets systematically underreact to earnings surprises. A stock that beats on EPS *and* revenue, confirmed by a positive after-hours price move, tends to continue drifting upward over the following 10 trading days.

The three-factor signal:

```
1. Strong beat (EPS + revenue)    ← fundamentals confirm the move
2. Market confirming it (AH move) ← price action validates the beat
3. Not already priced in          ← timing ensures drift hasn't happened yet
```

---

## Architecture

```
prices.py   ┐
earnings.py ├──→  decision.py  ──→  execution.py
sector.py   ┤         ↑                  │
state.py ───┘                            │
    ↑                                    │
    └────────────────────────────────────┘
```

| Module | Role |
|---|---|
| `prices.py` | Fetches OHLCV, calculates ATR, checks prior run-up, gets AH move |
| `earnings.py` | Fetches EPS/revenue actuals vs estimates and guidance flag (FMP API) |
| `sector.py` | Gets sector ETF % change on earnings day (yfinance) |
| `state.py` | Reads/writes open positions (entry price, current stop, day count) |
| `decision.py` | Applies entry and position management logic |
| `execution.py` | Places orders (paper log or live broker) and writes back state |
| `scheduler.py` | Runs the two daily cycles at the right times |
| `config.py` | All tunable parameters in one place |

---

## Two Daily Cycles

### Scan Cycle — 4:15 PM ET (after AH data available)
```
prices + earnings + sector + state  →  decision.evaluate_entry()  →  BUY orders
```

### Update Cycle — 4:30 PM ET
```
prices + state  →  decision.evaluate_positions()  →  SELL orders or updated stops
```

---

## Entry Filters

A BUY signal requires **all** of the following:

| # | Filter | Threshold | Data Source |
|---|---|---|---|
| 1 | EPS beat | ≥ 5% above consensus | FMP |
| 2 | Revenue beat | Any positive surprise | FMP |
| 3 | After-hours move | ≥ 3% up | yfinance |
| 4 | Prior run-up | ≤ 10% over prior 10 days | yfinance |
| 5 | Sector ETF | > -1.5% on the day | yfinance |
| 6 | Guidance | Not weak (best effort — skipped if data unavailable) | FMP |

---

## Position Management

- **Stop loss:** Trailing stop set at `entry_price - (1.5 × ATR)`, updated daily
- **Exit:** When price hits stop OR after 10 trading days, whichever comes first
- **Max positions:** Configurable (default: 5 concurrent)

---

## Project Structure

```
earnings-trader/
├── data/
│   ├── prices.py       # yfinance: OHLCV, ATR, AH move, run-up
│   ├── earnings.py     # FMP: EPS/rev surprise, guidance
│   └── sector.py       # yfinance: sector ETF % change
├── state.py            # JSON-backed position store
├── decision.py         # evaluate_entry() + evaluate_positions()
├── execution.py        # place_order() + update_state()
├── scheduler.py        # APScheduler: scan + update cycles
├── config.py           # all thresholds and parameters
├── requirements.txt
└── README.md
```

---

## Module Interfaces

### `config.py`

All tunable constants imported by other modules. No functions — import directly.

```python
MIN_EPS_BEAT_PCT: float      # minimum EPS beat fraction (e.g. 0.05 = 5%)
MIN_AH_MOVE_PCT: float       # minimum after-hours % move (e.g. 0.03 = 3%)
MAX_PRIOR_RUNUP_PCT: float   # max allowed prior 10-day run-up (e.g. 0.10)
SECTOR_ETF_MIN: float        # minimum sector ETF daily % change (e.g. -0.015)
ATR_STOP_MULTIPLIER: float   # trailing stop distance in ATR multiples (e.g. 1.5)
HOLD_DAYS: int               # maximum holding period in trading days (e.g. 10)
MAX_POSITIONS: int           # maximum concurrent open positions (e.g. 5)
LOOKBACK_DAYS: int           # days used for prior run-up calculation (e.g. 10)
```

---

### `data/prices.py`

Fetches price data from yfinance.

```python
def get_ohlcv(ticker: str, days: int) -> pd.DataFrame:
    """Return OHLCV DataFrame with columns: Open, High, Low, Close, Volume."""

def get_atr(ticker: str, period: int = 14) -> float:
    """Return the most recent Average True Range value."""

def get_ah_move(ticker: str, date: str) -> float:
    """Return after-hours % move on the given date (post-close vs close).
    date format: 'YYYY-MM-DD'
    Returns fractional change, e.g. 0.05 = +5%."""

def get_prior_runup(ticker: str, days: int = LOOKBACK_DAYS) -> float:
    """Return the % price change over the prior N trading days.
    Returns fractional change, e.g. 0.08 = +8%."""
```

---

### `data/earnings.py`

Fetches earnings data from the Financial Modeling Prep (FMP) API.

```python
@dataclass
class EarningsSurprise:
    ticker: str
    eps_actual: float
    eps_estimate: float
    eps_beat_pct: float        # (actual - estimate) / abs(estimate)
    rev_actual: float
    rev_estimate: float
    rev_beat_pct: float        # (actual - estimate) / abs(estimate)
    guidance_weak: bool | None # None if guidance data unavailable

def get_earnings_surprise(ticker: str, date: str | None = None) -> EarningsSurprise:
    """Return the most recent (or date-specific) earnings surprise for a ticker.
    Raises ValueError if no earnings data is available.
    date format: 'YYYY-MM-DD' (defaults to most recent report)."""
```

Requires environment variable: `FMP_API_KEY`

---

### `data/sector.py`

Maps stocks to their sector ETF and fetches ETF performance via yfinance.

```python
def get_sector_etf(ticker: str) -> str:
    """Return the sector ETF symbol for a given stock (e.g. 'XLK', 'XLF').
    Falls back to 'SPY' if sector cannot be determined."""

def get_sector_move(ticker: str, date: str) -> float:
    """Return the sector ETF's daily % change on the given date.
    date format: 'YYYY-MM-DD'
    Returns fractional change, e.g. -0.01 = -1%."""
```

---

### `state.py`

JSON-backed position store. Reads/writes `data/positions.json`.

```python
@dataclass
class Position:
    ticker: str
    entry_price: float
    current_stop: float
    entry_date: str     # 'YYYY-MM-DD'
    day_count: int      # trading days held so far

def load_positions() -> list[Position]:
    """Load all open positions from the JSON store."""

def save_positions(positions: list[Position]) -> None:
    """Overwrite the JSON store with the given list of positions."""

def add_position(position: Position) -> None:
    """Append a new position to the store."""

def remove_position(ticker: str) -> None:
    """Remove the position for the given ticker from the store."""

def update_stop(ticker: str, new_stop: float) -> None:
    """Update the trailing stop for an existing position."""
```

---

### `decision.py`

Applies entry and position management logic. Pure functions — no side effects.

```python
@dataclass
class EntrySignal:
    ticker: str
    should_enter: bool
    filters_passed: dict[str, bool]  # per-filter pass/fail breakdown
    entry_price: float | None
    initial_stop: float | None       # entry_price - (ATR_STOP_MULTIPLIER * ATR)

@dataclass
class PositionAction:
    ticker: str
    action: Literal["hold", "sell", "update_stop"]
    new_stop: float | None           # set when action == "update_stop"
    reason: str                      # e.g. "stop_hit", "max_days_reached", "trailing_stop_updated"

def evaluate_entry(
    ticker: str,
    surprise: EarningsSurprise,
    ah_move: float,
    prior_runup: float,
    sector_move: float,
    atr: float,
    current_price: float,
    open_positions: list[Position],
) -> EntrySignal:
    """Evaluate all six entry filters and return a signal.
    Returns EntrySignal with should_enter=False if MAX_POSITIONS is already reached."""

def evaluate_positions(
    positions: list[Position],
    current_prices: dict[str, float],
    current_atrs: dict[str, float],
) -> list[PositionAction]:
    """Evaluate each open position and return the appropriate action for each."""
```

---

### `execution.py`

Places orders and updates state. Supports paper and live modes.

```python
@dataclass
class OrderResult:
    ticker: str
    action: Literal["buy", "sell"]
    quantity: int
    fill_price: float
    timestamp: str
    mode: Literal["paper", "live"]
    success: bool
    error: str | None

def place_order(
    ticker: str,
    action: Literal["buy", "sell"],
    quantity: int,
    mode: Literal["paper", "live"] = "paper",
) -> OrderResult:
    """Place a buy or sell order. In paper mode, logs to stdout/file and returns a
    simulated fill. In live mode, submits to the configured broker (Alpaca or IBKR)."""

def execute_signals(
    signals: list[EntrySignal],
    actions: list[PositionAction],
    mode: Literal["paper", "live"] = "paper",
) -> None:
    """Process a batch of entry signals and position actions, placing orders and
    updating state for each."""
```

---

### `scheduler.py`

Runs the two daily cycles using APScheduler.

```python
def run_scan_cycle() -> None:
    """4:15 PM ET — fetch earnings/prices/sector, evaluate entries, place BUY orders."""

def run_update_cycle() -> None:
    """4:30 PM ET — fetch current prices, evaluate open positions, place SELL orders
    or update trailing stops."""

def start(mode: Literal["paper", "live"] = "paper") -> None:
    """Start the APScheduler blocking event loop. Registers both daily cycles."""
```

---

## Configuration (`config.py`)

```python
MIN_EPS_BEAT_PCT     = 0.05   # 5% beat required
MIN_AH_MOVE_PCT      = 0.03   # 3% after-hours confirmation
MAX_PRIOR_RUNUP_PCT  = 0.10   # 10% max run-up in prior 10 days
SECTOR_ETF_MIN       = -0.015 # sector must be > -1.5%
ATR_STOP_MULTIPLIER  = 1.5    # trailing stop = 1.5x ATR
HOLD_DAYS            = 10     # max hold period
MAX_POSITIONS        = 5      # max concurrent positions
LOOKBACK_DAYS        = 10     # days to check prior run-up
```

---

## Data Sources

| Data | Source | Notes |
|---|---|---|
| EPS / revenue actuals + estimates | [Financial Modeling Prep (FMP)](https://financialmodelingprep.com) | Free tier: 250 req/day |
| OHLCV prices, ATR, AH move | [yfinance](https://github.com/ranaroussi/yfinance) | Free, no key required |
| Sector ETF prices | yfinance | SPY, XLK, XLF, etc. |

---

## Roadmap

### Phase 1 — Backtester
- [ ] `earnings.py` — pull historical EPS/revenue surprises from FMP
- [ ] `prices.py` — ATR, AH move, run-up from yfinance
- [ ] `sector.py` — sector ETF filter
- [ ] `decision.py` — entry filter logic
- [ ] Backtest runner — scan historical earnings, simulate entries/exits, report P&L

### Phase 2 — Live Paper Trading
- [ ] `state.py` — JSON position store
- [ ] `execution.py` — paper trade logger
- [ ] `scheduler.py` — two-cycle daily scheduler
- [ ] End-to-end dry run on next earnings season

### Phase 3 — Live Trading
- [ ] `execution.py` — broker integration (Alpaca or IBKR)
- [ ] Position sizing (fixed dollar, Kelly, or vol-adjusted)
- [ ] Alerting / monitoring

---

## Setup

```bash
git clone https://github.com/jvalansi/earnings-trader.git
cd earnings-trader
pip install -r requirements.txt
cp .env.example .env   # add your FMP_API_KEY
```

---

## Disclaimer

This is a research and educational project. Nothing here constitutes financial advice. Use at your own risk.
