# Notion Integration

Scan results, open positions, and earnings calendar are pushed to Notion after each scheduler cycle. Slack keeps buy/sell trade alerts only.

## Notion Databases

Three databases are created in a Notion page of your choice:

| Database | Populated at | Contents |
|---|---|---|
| Earnings Scans | 9 AM (BMO) + 4:15 PM (AMC) | One row per ticker: all 7 filters, signal, EPS beat %, move % |
| Open Positions | 4:30 PM | One row per open position, upserted each cycle, archived on close |
| Earnings Calendar | 7:00 PM | One row per ticker reporting tomorrow |

## Setup

### 1. Create a Notion integration

Go to https://www.notion.so/my-integrations → New integration → copy the token.

### 2. Create a Notion page and share it

Create a page in Notion (e.g. "Earnings Trader"), then share it with your integration via the `...` menu → Connections.

Get the page ID from the URL — it's the last part:
```
https://www.notion.so/My-Page-<page-id>
```

### 3. Install the dependency

```bash
/home/ubuntu/miniconda3/envs/earnings-trader/bin/pip install notion-client
```

### 4. Run the setup script (once)

```bash
cd /home/ubuntu/earnings-trader
NOTION_TOKEN=secret_xxx NOTION_PARENT_PAGE_ID=<page-id> python src/setup_notion.py
```

This creates the three databases inside your page and saves their IDs to `data/notion_config.json`.

### 5. Add NOTION_TOKEN to the service environment

Add to `/etc/systemd/system/earnings-trader.service`:
```ini
Environment=NOTION_TOKEN=secret_xxx
```

Then reload:
```bash
sudo systemctl daemon-reload && sudo systemctl restart earnings-trader
```

## Files Added / Modified

| File | Change |
|---|---|
| `src/notion_reporter.py` | New — Notion API wrapper (`write_scan`, `sync_positions`, `write_calendar`) |
| `src/setup_notion.py` | New — one-time setup script to create databases |
| `src/scheduler.py` | Modified — calls `notion_reporter` at end of each cycle |
| `requirements.txt` | Modified — added `notion-client>=2.2.1` |
| `data/notion_config.json` | Created at runtime by `setup_notion.py` — holds database IDs |

## How it works

- If `NOTION_TOKEN` is not set, all Notion calls are silently skipped (no crash)
- If `data/notion_config.json` is missing, a warning is logged and calls are skipped
- All Notion API calls are wrapped in try/except — a Notion failure never blocks Slack or the scheduler
- Positions are upserted by ticker name (updated if exists, created if new, archived if closed)
- Scan rows are appended each cycle (one row per ticker per run)
- Calendar rows are appended each preview cycle
