"""
setup_notion.py — one-time setup: creates Notion databases and saves their IDs.

Usage:
    NOTION_TOKEN=secret_xxx NOTION_PARENT_PAGE_ID=<page-id> python src/setup_notion.py

The page ID is the last part of the Notion page URL:
    https://www.notion.so/My-Page-<page-id>

Creates three databases inside that page:
    - Earnings Scans   (BMO + AMC scan results)
    - Open Positions   (current open positions, kept in sync)
    - Earnings Calendar (upcoming earnings)

Saves database IDs to data/notion_config.json.
Running this script again is safe — it will create duplicate databases,
so only run it once (or delete notion_config.json to reset).
"""
import json
import os
import sys
from pathlib import Path

from notion_client import Client


def main() -> None:
    token = os.environ.get("NOTION_TOKEN")
    parent_page_id = os.environ.get("NOTION_PARENT_PAGE_ID")

    if not token:
        print("Error: NOTION_TOKEN env var not set", file=sys.stderr)
        sys.exit(1)
    if not parent_page_id:
        print("Error: NOTION_PARENT_PAGE_ID env var not set", file=sys.stderr)
        sys.exit(1)

    client = Client(auth=token)
    parent = {"type": "page_id", "page_id": parent_page_id}

    print("Creating Notion databases...")

    # ── Earnings Scans ────────────────────────────────────────────────────────
    scans_db = client.databases.create(
        parent=parent,
        title=[{"text": {"content": "Earnings Scans"}}],
        properties={
            "Ticker":       {"title": {}},
            "Date":         {"date": {}},
            "Type":         {"select": {"options": [
                                {"name": "BMO", "color": "blue"},
                                {"name": "AMC", "color": "orange"},
                            ]}},
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
        },
    )
    print(f"  Earnings Scans:    {scans_db['id']}")

    # ── Open Positions ────────────────────────────────────────────────────────
    positions_db = client.databases.create(
        parent=parent,
        title=[{"text": {"content": "Open Positions"}}],
        properties={
            "Ticker":       {"title": {}},
            "Entry Price":  {"number": {"format": "dollar"}},
            "Entry Date":   {"date": {}},
            "Stop":         {"number": {"format": "dollar"}},
            "Day":          {"number": {"format": "number"}},
            "Qty":          {"number": {"format": "number"}},
        },
    )
    print(f"  Open Positions:    {positions_db['id']}")

    # ── Earnings Calendar ─────────────────────────────────────────────────────
    calendar_db = client.databases.create(
        parent=parent,
        title=[{"text": {"content": "Earnings Calendar"}}],
        properties={
            "Ticker": {"title": {}},
            "Date":   {"date": {}},
        },
    )
    print(f"  Earnings Calendar: {calendar_db['id']}")

    # ── Save config ───────────────────────────────────────────────────────────
    config = {
        "scans_db_id":     scans_db["id"],
        "positions_db_id": positions_db["id"],
        "calendar_db_id":  calendar_db["id"],
    }

    Path("data").mkdir(exist_ok=True)
    config_path = Path("data/notion_config.json")
    with config_path.open("w") as f:
        json.dump(config, f, indent=2)

    print(f"\nSaved to {config_path}")
    print("Setup complete. Add NOTION_TOKEN to your environment and restart the bot.")


if __name__ == "__main__":
    main()
