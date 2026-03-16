# Roadmap

## Roadmap to Profitability

**Current state:** Paper trading — live signals, real data, no capital deployed.

| Milestone | Description | Expected Monthly ROI |
|---|---|---|
| **Phase 2 complete** | Paper trading validated, win rate > 50%, positive expectancy | — |
| **Phase 3: go live** | Deploy $5k capital via Alpaca/IBKR, real P&L | $200–500/mo |
| **Scale capital** | Raise to $20k+ as strategy proves out | $800–2,000/mo |
| **Multi-strategy** | Add BMO + sector rotation variants | 2–3× current returns |
| **Automation** | Zero-touch daily operation, Slack alerts only | — |

**Next step (Notion task):** Add P&L performance dashboard + returns tracking — better visibility → better parameter tuning → ~$500/mo improvement in returns.

**Notion project page:** [earnings-trader — Strategy & Profitability](https://www.notion.so/earnings-trader-Strategy-Profitability-32505a1b5e0181e6bc42c00fe95589f1)

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

> Status: **Not started** (after Phase 2 is validated)

- [ ] `execution.py` — broker integration (Alpaca or IBKR)
- [ ] Position sizing — fixed dollar, Kelly, or vol-adjusted
- [ ] Alerting / monitoring — Slack or email notifications on entries/exits
- [ ] Risk controls — daily loss limit, max drawdown circuit breaker
