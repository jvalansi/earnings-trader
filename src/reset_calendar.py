"""
reset_calendar.py — clear all rows from the Earnings Calendar Notion database.

Usage:
    NOTION_TOKEN=secret_xxx PYTHONPATH=src python src/reset_calendar.py

Archives every row (Notion doesn't support hard-delete via API).
After running, the calendar will be empty and write_calendar() will
repopulate it fresh on the next 7 PM run.
"""
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if not os.getenv("NOTION_TOKEN"):
    print("Error: NOTION_TOKEN env var not set", file=sys.stderr)
    sys.exit(1)

import notion_reporter

print("Clearing Earnings Calendar...")
count = notion_reporter.clear_calendar()
print(f"Done — archived {count} rows.")
