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
