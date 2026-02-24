"""
setup_notion.py — one-time setup: creates Notion databases and saves their IDs.

Usage:
    NOTION_TOKEN=secret_xxx NOTION_PARENT_PAGE_ID=<page-id> python src/setup_notion.py

The page ID is the last part of the Notion page URL:
    https://www.notion.so/My-Page-<page-id>

Creates two databases inside that page:
    - Earnings Calendar (upcoming earnings pre-populated with estimates; updated at scan time with actuals and filter results)
    - Open Positions    (current open positions, kept in sync)

Saves database IDs to data/notion_config.json.
Running this script again is safe — it will create duplicate databases,
so only run it once (or delete notion_config.json to reset).
"""
import json
import os
import sys
from pathlib import Path

import requests

NOTION_VERSION = "2022-06-28"


def main() -> None:
    token = os.environ.get("NOTION_TOKEN")
    parent_page_id = os.environ.get("NOTION_PARENT_PAGE_ID")

    if not token:
        print("Error: NOTION_TOKEN env var not set", file=sys.stderr)
        sys.exit(1)
    if not parent_page_id:
        print("Error: NOTION_PARENT_PAGE_ID env var not set", file=sys.stderr)
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    parent = {"type": "page_id", "page_id": parent_page_id}

    def create_db(title: str, properties: dict) -> str:
        r = requests.post(
            "https://api.notion.com/v1/databases",
            headers=headers,
            json={
                "parent": parent,
                "title": [{"text": {"content": title}}],
                "properties": properties,
            },
        )
        r.raise_for_status()
        return r.json()["id"]

    print("Creating Notion databases...")

    # ── Earnings Calendar ─────────────────────────────────────────────────────
    # Populated at 7 PM with estimates; updated at scan time with actuals + filter results.
    calendar_id = create_db("Earnings Calendar", {
        "Ticker":       {"title": {}},
        "Date":         {"date": {}},
        "Timing":       {"select": {"options": [
                            {"name": "bmo",     "color": "blue"},
                            {"name": "amc",     "color": "orange"},
                            {"name": "unknown", "color": "gray"},
                        ]}},
        "EPS Estimate": {"number": {"format": "number"}},
        "Rev Estimate": {"number": {"format": "number"}},
        "Signal":       {"select": {"options": [
                            {"name": "BUY",  "color": "green"},
                            {"name": "skip", "color": "gray"},
                        ]}},
        "EPS Beat %":   {"number": {"format": "number"}},
        "Move %":       {"number": {"format": "number"}},
        "Entry Price":  {"number": {"format": "dollar"}},
        "Stop Price":   {"number": {"format": "dollar"}},
        "eps_beat":     {"checkbox": {}},
        "rev_beat":     {"checkbox": {}},
        "ah_move":      {"checkbox": {}},
        "prior_runup":  {"checkbox": {}},
        "sector_etf":   {"checkbox": {}},
        "guidance":     {"checkbox": {}},
        "capacity":     {"checkbox": {}},
    })
    print(f"  Earnings Calendar: {calendar_id}")

    # ── Open Positions ────────────────────────────────────────────────────────
    positions_id = create_db("Open Positions", {
        "Ticker":       {"title": {}},
        "Entry Price":  {"number": {"format": "dollar"}},
        "Entry Date":   {"date": {}},
        "Stop":         {"number": {"format": "dollar"}},
        "Day":          {"number": {"format": "number"}},
        "Qty":          {"number": {"format": "number"}},
    })
    print(f"  Open Positions:    {positions_id}")

    # ── Save config ───────────────────────────────────────────────────────────
    config = {
        "calendar_db_id":  calendar_id,
        "positions_db_id": positions_id,
    }

    Path("data").mkdir(exist_ok=True)
    config_path = Path("data/notion_config.json")
    with config_path.open("w") as f:
        json.dump(config, f, indent=2)

    print(f"\nSaved to {config_path}")
    print("Setup complete. Add NOTION_TOKEN to your environment and restart the bot.")


if __name__ == "__main__":
    main()
