# Flask Web Dashboard — Implementation Plan

## Overview

Replace verbose Slack messages for earnings calendar and scan results with a Flask web dashboard that accumulates data throughout the day. Slack notifications for **buy/sell signals only** are kept. The dashboard auto-refreshes every 60 seconds and can be left open in a browser tab.

---

## Architecture

```
earnings-trader/
├── src/
│   ├── daily_state.py          (NEW)
│   ├── scheduler.py            (MODIFIED)
│   └── ...
├── dashboard/
│   ├── app.py                  (NEW)
│   └── templates/
│       └── index.html          (NEW)
├── data/
│   ├── positions.json          (existing)
│   ├── trades_log.jsonl        (existing)
│   └── daily_log.json          (NEW — created at runtime)
└── requirements.txt            (MODIFIED — add flask)
```

The scheduler process and Flask process share data via JSON files (matching the existing pattern). No database needed.

---

## Dashboard Sections

The page fills in throughout the day as each cycle runs. Pending sections show a greyed-out placeholder.

| Section | Populated at | Source |
|---|---|---|
| Open Positions | Always | `data/positions.json` + live yfinance prices |
| BMO Scan Results | 9:00 AM ET | `data/daily_log.json` → `bmo_scan` |
| AMC Scan Results | 4:15 PM ET | `data/daily_log.json` → `amc_scan` |
| Position Updates | 4:30 PM ET | `data/daily_log.json` → `position_update` |
| Tomorrow's Calendar | 7:00 PM ET | `data/daily_log.json` → `calendar_preview` |
| Recent Trades | Always | `data/trades_log.jsonl` (last 20) |

---

## Files to Create / Modify

### 1. NEW — `src/daily_state.py`

Accumulates today's cycle results into `data/daily_log.json`. Resets automatically when a new day starts (date rollover detected on read).

**Public API:**
```python
write_bmo_scan(signals, move_pcts, eps_beat_pcts)         -> None
write_amc_scan(signals, move_pcts, eps_beat_pcts)         -> None
write_position_update(actions, current_prices, positions) -> None
write_calendar_preview(date, tickers)                     -> None
load_daily_log()                                          -> dict
```

**`data/daily_log.json` schema:**
```json
{
  "date": "2026-02-24",
  "bmo_scan": {
    "run_at": "2026-02-24T09:00:00-05:00",
    "results": [
      {
        "ticker": "AAPL",
        "should_enter": true,
        "filters_passed": {
          "eps_beat": true, "rev_beat": true, "ah_move": true,
          "prior_runup": true, "sector_etf": true, "guidance": true, "capacity": true
        },
        "entry_price": 150.25,
        "initial_stop": 145.00,
        "eps_beat_pct": 0.08,
        "move_pct": 0.042
      }
    ]
  },
  "amc_scan": { "run_at": "...", "results": [] },
  "position_update": {
    "run_at": "...",
    "actions": [
      {
        "ticker": "MSFT", "action": "hold",
        "price": 400.0, "stop": 390.0, "day_count": 3, "reason": null
      }
    ]
  },
  "calendar_preview": {
    "run_at": "...",
    "date": "2026-02-25",
    "tickers": ["NVDA", "AMZN"]
  }
}
```

Writes are atomic (`tempfile.mkstemp` + `os.replace`) to prevent Flask from reading a torn file.

---

### 2. MODIFIED — `src/scheduler.py`

Four small additions — one per cycle. Pattern: accumulate intermediate values in the existing loop, then call `daily_state.write_*()` after the Slack `notify()` call so a crash in `daily_state` never blocks Slack.

**Add import at top:**
```python
import daily_state
```

**`run_scan_cycle` (line 45 — before `for ticker in tickers:`):**
```python
    move_pcts: dict[str, float] = {}
    eps_beat_pcts: dict[str, float] = {}
```

**Inside the loop, after `signals.append(sig)` (line 72):**
```python
            move_pcts[ticker] = ah_move
            eps_beat_pcts[ticker] = surprise.eps_beat_pct
```

**After `notify(...)` (line 92):**
```python
    try:
        daily_state.write_amc_scan(signals, move_pcts, eps_beat_pcts)
    except Exception as e:
        logger.error(f"Failed to write AMC scan to daily log: {e}", exc_info=True)
```

**`run_bmo_scan_cycle` — same pattern, with `pm_move` instead of `ah_move`, calling `write_bmo_scan`.**

**`run_update_cycle` — after `notify(...)` (line 220):**
```python
    try:
        daily_state.write_position_update(actions, current_prices, positions)
    except Exception as e:
        logger.error(f"Failed to write position update to daily log: {e}", exc_info=True)
```

**`run_calendar_preview` — after `notify(...)` (line 275):**
```python
    try:
        daily_state.write_calendar_preview(tomorrow, tickers)
    except Exception as e:
        logger.error(f"Failed to write calendar preview to daily log: {e}", exc_info=True)
```

---

### 3. NEW — `dashboard/app.py`

Single-route Flask app. On each `GET /`:
1. Loads `daily_log.json`, `positions.json`, `trades_log.jsonl`
2. Fetches live prices for open positions via `yf.Ticker().fast_info` (parallelized with `ThreadPoolExecutor`)
3. Computes P&L % per position
4. Renders `index.html`

Resolves data paths relative to repo root via `Path(__file__).parent.parent / "data"` — no `PYTHONPATH` dependency.

Run:
```bash
python dashboard/app.py   # listens on 0.0.0.0:5001
```

---

### 4. NEW — `dashboard/templates/index.html`

Bootstrap 5.3 (CDN). Auto-refresh via `<meta http-equiv="refresh" content="60">`.

- **Scan tables:** one row per ticker, filter columns use green/red cell shading
- **P&L column:** green for positive, red for negative
- **Action column:** color-coded by action type (buy/sell/hold/update_stop)
- **Pending sections:** grey italic placeholder text with scheduled time
- **Recent trades:** newest first, badge for paper/live mode

---

### 5. MODIFIED — `requirements.txt`

Add:
```
flask>=3.0.0
```

Install into the existing conda env:
```bash
/home/ubuntu/miniconda3/envs/earnings-trader/bin/pip install flask
```

---

## Running the Dashboard

### Manual
```bash
cd /home/ubuntu/earnings-trader
python dashboard/app.py
# Open http://<server-ip>:5001
```

### Systemd service (production)

Create `/etc/systemd/system/earnings-trader-dashboard.service`:
```ini
[Unit]
Description=Earnings Trader Web Dashboard
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/earnings-trader
ExecStart=/home/ubuntu/miniconda3/envs/earnings-trader/bin/python dashboard/app.py
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now earnings-trader-dashboard
```

---

## Edge Cases

| Scenario | Handling |
|---|---|
| `daily_log.json` missing | Returns empty skeleton; all sections show as pending |
| File is from yesterday | Date check discards stale data; file only overwritten when next cycle runs |
| Torn JSON write | Atomic `os.replace` — Flask always sees a complete file |
| `positions.json` missing | Returns `[]`; Open Positions shows "No open positions" |
| `trades_log.jsonl` missing | Returns `[]`; Recent Trades shows empty |
| Live price fetch fails | Per-ticker exception caught; renders `—` for price and P&L |
| Filter key missing from old log | `filters_passed.get(key)` returns `None` → renders as fail cell |
| No tickers in calendar | Cycle exits early; section stays pending |
| `daily_state` write fails | Exception caught and logged; Slack notify already sent — no blocking |

---

## Implementation Order

1. `src/daily_state.py` — standalone, no new dependencies
2. `requirements.txt` — add flask, install
3. `src/scheduler.py` — add `import daily_state` and four write calls
4. `dashboard/app.py` + `dashboard/templates/index.html`
5. Test manually: start Flask, verify all sections render as pending
6. Create systemd service and start it
7. Commit and push
