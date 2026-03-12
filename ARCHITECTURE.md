# Architecture

## Data Flow

```
prices.py   ┐
earnings.py ├──→  decision.py  ──→  execution.py
sector.py   ┤         ↑                  │
state.py ───┘                            │
    ↑                                    │
    └────────────────────────────────────┘
```

## Project Structure

```
earnings-trader/
├── src/
│   ├── data/
│   │   ├── prices.py       # yfinance: OHLCV, ATR, AH move, run-up
│   │   ├── earnings.py     # FMP: EPS/rev surprise, guidance
│   │   └── sector.py       # yfinance: sector ETF % change
│   ├── config.py           # all thresholds and parameters
│   ├── state.py            # JSON-backed position store
│   ├── decision.py         # evaluate_entry() + evaluate_positions()
│   ├── execution.py        # place_order() + update_state()
│   ├── scheduler.py        # APScheduler: daily cycles
│   └── main.py             # entry point
├── data/                   # runtime data (gitignored)
│   ├── positions.json
│   └── trades_log.jsonl
├── ROADMAP.md
├── ARCHITECTURE.md
├── CLAUDE.md
└── README.md
```

## Module Interfaces

### `config.py`

All tunable constants imported by other modules. No functions — import directly.

```python
MIN_EPS_BEAT_PCT: float      # minimum EPS beat fraction (e.g. 0.05 = 5%)
MIN_AH_MOVE_PCT: float       # minimum after-hours % move (e.g. 0.03 = 3%)
MAX_PRIOR_RUNUP_PCT: float   # max allowed prior 10-day run-up (e.g. 0.10)
SECTOR_ETF_MIN: float        # minimum sector ETF daily % change (e.g. -0.015)
ATR_STOP_MULTIPLIER: float   # trailing stop distance in ATR multiples (e.g. 2.5)
HOLD_DAYS: int               # maximum holding period in trading days (e.g. 10)
MAX_POSITIONS: int           # maximum concurrent open positions (e.g. 5)
LOOKBACK_DAYS: int           # days used for prior run-up calculation (e.g. 10)
POSITION_SIZE_USD: float     # fixed dollar amount per trade
```

Current values: see [`src/config.py`](src/config.py).

---

### `data/prices.py`

```python
def get_ohlcv(ticker: str, days: int) -> pd.DataFrame: ...
def get_atr(ticker: str, period: int = 14) -> float: ...
def get_ah_move(ticker: str, date: str) -> float: ...        # post-close vs close, e.g. 0.05 = +5%
def get_premarket_move(ticker: str, date: str) -> float: ... # pre-market vs prior close
def get_prior_runup(ticker: str, days: int = LOOKBACK_DAYS) -> float: ...
```

---

### `data/earnings.py`

```python
@dataclass
class EarningsSurprise:
    ticker: str
    eps_actual: float
    eps_estimate: float
    eps_beat_pct: float        # (actual - estimate) / abs(estimate)
    rev_actual: float
    rev_estimate: float
    rev_beat_pct: float
    guidance_weak: bool | None # None if unavailable

def get_earnings_surprise(ticker: str, date: str | None = None) -> EarningsSurprise: ...
def get_earnings_calendar_details(date: str) -> list[EarningsCalendarEntry]: ...
```

Requires env var: `FMP_API_KEY`

---

### `data/sector.py`

```python
def get_sector_etf(ticker: str) -> str: ...          # e.g. 'XLK', falls back to 'SPY'
def get_sector_move(ticker: str, date: str) -> float: ...
def get_sector_intraday_move(ticker: str, date: str) -> float: ...
```

---

### `state.py`

```python
@dataclass
class Position:
    ticker: str
    entry_price: float
    current_stop: float
    entry_date: str     # 'YYYY-MM-DD'
    day_count: int
    quantity: int

def load_positions() -> list[Position]: ...
def save_positions(positions: list[Position]) -> None: ...
def add_position(position: Position) -> None: ...
def remove_position(ticker: str) -> None: ...
def update_stop(ticker: str, new_stop: float) -> None: ...
```

---

### `decision.py`

Pure functions — no side effects.

```python
@dataclass
class EntrySignal:
    ticker: str
    should_enter: bool
    filters_passed: dict[str, bool]
    entry_price: float | None
    initial_stop: float | None    # entry_price - (ATR_STOP_MULTIPLIER * ATR)

@dataclass
class PositionAction:
    ticker: str
    action: Literal["hold", "sell", "update_stop"]
    new_stop: float | None
    reason: str                   # "stop_hit" | "max_days_reached" | "trailing_stop_updated"

def evaluate_entry(ticker, surprise, ah_move, prior_runup, sector_move, atr, current_price, open_positions) -> EntrySignal: ...
def evaluate_positions(positions, current_prices, current_atrs) -> list[PositionAction]: ...
```

---

### `execution.py`

```python
def execute_signals(
    signals: list[EntrySignal],
    actions: list[PositionAction],
    current_prices: dict[str, float] | None = None,
    mode: Literal["paper", "live"] = "paper",
) -> None: ...
```

Logs all trades to `data/trades_log.jsonl`. Live mode raises `NotImplementedError`.

---

### `scheduler.py`

```python
def run_bmo_scan_cycle(mode: str = "paper") -> None: ...   # 10:00 AM ET
def run_scan_cycle(mode: str = "paper") -> None: ...       # 4:15 PM ET
def run_update_cycle(mode: str = "paper") -> None: ...     # 4:30 PM ET
def run_calendar_preview() -> None: ...                    # 7:00 PM ET
def run_weekly_pnl_summary() -> None: ...                  # Mon 9:00 AM ET
def start(mode: Literal["paper", "live"] = "paper") -> None: ...
```

## Data Sources

| Data | Source | Notes |
|---|---|---|
| EPS / revenue actuals + estimates | [Financial Modeling Prep (FMP)](https://financialmodelingprep.com) | Free tier: 250 req/day |
| OHLCV prices, ATR, AH/pre-market move | [yfinance](https://github.com/ranaroussi/yfinance) | Free, no key required |
| Sector ETF prices | yfinance | SPY, XLK, XLF, etc. |
