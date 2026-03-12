# earnings-trader

A systematic trading system that exploits **Post-Earnings Announcement Drift (PEAD)** — the tendency for stocks to continue moving in the direction of an earnings surprise for days to weeks after the announcement.

## Docs

| Document | Description |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module interfaces, data flow, project structure |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phased plan, strategy investigation, open questions |
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
| 3 | After-hours move | ≥ 3% up |
| 4 | Prior run-up | ≤ 10% over prior 10 days |
| 5 | Sector ETF | > -1.5% on the day |
| 6 | Guidance | Not weak |

## Position Management

- **Stop loss:** Trailing stop at `entry_price - (2.5 × ATR)`, updated daily
- **Exit:** Stop hit or 10 trading days, whichever comes first
- **Max positions:** 5 concurrent

## Daily Schedule

| Time (ET) | Job |
|---|---|
| 10:00 AM | BMO scan (pre-market move) |
| 4:15 PM | AMC scan (after-hours move) |
| 4:30 PM | Position update (stops / exits) |
| 7:00 PM | Calendar preview (tomorrow's earnings) |
| Mon 9:00 AM | Weekly PnL summary |

---

## Setup

```bash
git clone https://github.com/jvalansi/earnings-trader.git
cd earnings-trader
pip install -r requirements.txt
cp .env.example .env        # add your FMP_API_KEY
PYTHONPATH=src python src/main.py
```

---

*This is a research project. Nothing here is financial advice.*
