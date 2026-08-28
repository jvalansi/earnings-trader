# earnings-trader

A systematic trading system that exploits **Post-Earnings Announcement Drift (PEAD)** — the tendency for stocks to continue moving in the direction of an earnings surprise for days to weeks after the announcement.

## Docs

| Document | Description |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module interfaces, data flow, project structure |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phased plan, strategy investigation, open questions |
| [docs/BACKTEST.md](docs/BACKTEST.md) | Backtest results, entry model investigation, ROI analysis |
| [CLAUDE.md](CLAUDE.md) | Dev workflow (running, pushing, restarting) |

---

## Core Thesis

Markets underreact to earnings surprises. A stock that beats on EPS *and* revenue, confirmed by a positive after-hours move, tends to drift upward over the following 10 trading days.

---

## Entry Filters

A BUY signal requires **all** of the following:

| # | Filter | Threshold |
|---|---|---|
| 1 | EPS beat | ≥ 5% above consensus |
| 2 | Revenue beat | Any positive surprise |
| 3 | Overnight gap | ≥ 3% (next-day open vs prior close) |
| 4 | Prior run-up | ≤ 10% over prior 10 days |
| 5 | Sector ETF | > -1.5% on the day |
| 6 | Guidance | Not weak |

## Position Management

- **Stop loss:** Trailing stop at `entry_price - (2.5 × ATR)`, updated daily
- **Exit:** Stop hit or 10 trading days, whichever comes first
- **Max positions:** 10 concurrent
- **Position size:** `ACCOUNT_CAPITAL_USD / MAX_POSITIONS` (default $5,000 / 10 = $500), sent
  as a notional order where the broker supports fractional shares, so an $1,800 stock still
  takes a $500 slot rather than one oversized share

## Risk Controls

New entries are blocked — exits always continue — when any of these trips:

| Control | Default | Behavior |
|---|---|---|
| Daily loss limit | -4% of equity | Latches; clear with `main.py resume` |
| Drawdown breaker | -15% from peak equity | Latches; clear with `main.py resume` |
| Kill switch | `data/HALT` or `TRADING_HALTED=1` | Active while present |

Equity is measured from the risk epoch recorded in `data/risk_state.json`, so P&L from an
earlier capital base neither masks nor fakes a drawdown.

## Execution Modes

| Mode | Behavior |
|---|---|
| `sim` | No broker. Fills are assumed at the signal price — slippage is not measured. |
| `paper` | Alpaca paper endpoint. Used automatically when `ALPACA_*` keys are set. |
| `live` | Real money. Requires Alpaca keys **and** `LIVE_TRADING_CONFIRMED=yes`. A live order that cannot reach the broker fails loudly rather than being simulated. |

Startup runs a preflight: broker reachability, account status, and a reconciliation of
`data/positions.json` against the broker's positions.

## Daily Schedule

| Time (ET) | Job |
|---|---|
| 9:30 AM | Earnings scan (overnight gap — covers both AMC and BMO) |
| 4:30 PM | Position update (stops / exits) |
| 7:00 PM | Calendar preview (tomorrow's earnings) |
| Mon 9:00 AM | Weekly PnL summary |

---

## Setup

```bash
git clone https://github.com/jvalansi/earnings-trader.git
cd earnings-trader
pip install -r requirements.txt
cp .env.example .env        # add your FMP_API_KEY (and ALPACA_* keys to trade)
PYTHONPATH=src python src/main.py
```

## CLI

```bash
PYTHONPATH=src python src/main.py                  # start the scheduler
PYTHONPATH=src python src/main.py preflight        # broker, risk state, position drift
PYTHONPATH=src python src/main.py track-record     # performance stats + go/no-go verdict
PYTHONPATH=src python src/main.py halt "reason"    # block new entries (exits keep running)
PYTHONPATH=src python src/main.py resume           # clear halt / latched risk breach
```

---

*This is a research project. Nothing here is financial advice.*
