# Roadmap

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

## Phase 3 — Live Trading

> Status: **Not started** (after Phase 2 is validated)

- [ ] `execution.py` — broker integration (Alpaca or IBKR)
- [ ] Position sizing — fixed dollar, Kelly, or vol-adjusted
- [ ] Alerting / monitoring — Slack or email notifications on entries/exits
- [ ] Risk controls — daily loss limit, max drawdown circuit breaker
