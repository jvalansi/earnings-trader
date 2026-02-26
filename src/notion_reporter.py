"""
notion_reporter.py — writes earnings calendar, scan results, and positions to Notion.

All data flows into two databases:
  - Earnings Calendar: rows created at 7 PM with estimates; updated at scan time with
                       actuals (EPS Beat %, Move %) and filter pass/fail results
  - Open Positions:    upserted at 4:30 PM each day

Required env var:
    NOTION_TOKEN  — integration secret (secret_xxx)

Database IDs are stored in data/notion_config.json after running setup_notion.py.

Public API:
    write_calendar(date, entries)                                    -> None
    write_scan(scan_type, date, signals, move_pcts, eps_beat_pcts)  -> None
    sync_positions(positions)                                        -> None
"""
import json
import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"
_CONFIG_FILE = Path("data/notion_config.json")
_token = os.getenv("NOTION_TOKEN")
_headers = {
    "Authorization": f"Bearer {_token}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
} if _token else None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_config() -> dict | None:
    if not _token:
        return None
    if not _CONFIG_FILE.exists():
        logger.warning("data/notion_config.json not found — run setup_notion.py first")
        return None
    try:
        with _CONFIG_FILE.open() as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read notion_config.json: {e}")
        return None


def _create_page(db_id: str, props: dict) -> None:
    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers=_headers,
        json={"parent": {"database_id": db_id}, "properties": props},
    )
    r.raise_for_status()


def _update_page(page_id: str, props: dict) -> None:
    r = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=_headers,
        json={"properties": props},
    )
    r.raise_for_status()


def _query_db(db_id: str, filter_body: dict) -> list[dict]:
    r = requests.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers=_headers,
        json={"filter": filter_body},
    )
    r.raise_for_status()
    return r.json().get("results", [])


def _get_title(page: dict, prop: str) -> str | None:
    try:
        return page["properties"][prop]["title"][0]["text"]["content"]
    except (KeyError, IndexError):
        return None


def _title(value: str) -> dict:
    return {"title": [{"text": {"content": value}}]}


def _date(value: str) -> dict:
    return {"date": {"start": value}}


def _select(value: str) -> dict:
    return {"select": {"name": value}}


def _checkbox(value: bool) -> dict:
    return {"checkbox": bool(value)}


def _num(value: float | None) -> dict:
    return {"number": value}


def _pct(value: float | None) -> dict:
    """Convert decimal fraction to display percentage (0.08 -> 8.0)."""
    return {"number": round(value * 100, 2) if value is not None else None}


# ── Public API ────────────────────────────────────────────────────────────────

def clear_calendar() -> int:
    """Archive all rows in the Earnings Calendar database.

    Paginates through the full database and archives every page.
    Returns the count of archived rows.
    """
    config = _load_config()
    if not config:
        return 0
    db_id = config.get("calendar_db_id")
    if not db_id:
        logger.warning("calendar_db_id missing from notion_config.json")
        return 0

    cursor = None
    archived = 0
    while True:
        body: dict = {}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=_headers,
            json=body,
        )
        r.raise_for_status()
        data = r.json()
        for page in data.get("results", []):
            try:
                requests.patch(
                    f"https://api.notion.com/v1/pages/{page['id']}",
                    headers=_headers,
                    json={"archived": True},
                ).raise_for_status()
                archived += 1
            except Exception as e:
                logger.error(f"Notion: failed to archive page {page['id']}: {e}")
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    logger.info(f"Notion: archived {archived} calendar rows")
    return archived


def write_calendar(date: str, entries: list) -> tuple[int, int]:
    """Upsert one row per ticker in the Earnings Calendar database.

    entries: list[EarningsCalendarEntry]
    Populates: Ticker, Date, Timing, EPS Estimate, Rev Estimate.
    Archives stale rows (date matches but ticker no longer in filtered list).
    Scan columns are left blank and filled in later by write_scan().
    Returns (created, archived) counts.
    """
    config = _load_config()
    if not config:
        return 0, 0
    db_id = config.get("calendar_db_id")
    if not db_id:
        logger.warning("calendar_db_id missing from notion_config.json")
        return 0, 0

    expected_tickers = {e.ticker for e in entries}

    # Query existing rows for this date
    existing: dict[str, str] = {}  # ticker -> page_id
    try:
        pages = _query_db(db_id, {"property": "Date", "date": {"equals": date}})
        for page in pages:
            ticker = _get_title(page, "Ticker")
            if ticker:
                existing[ticker] = page["id"]
    except Exception as e:
        logger.error(f"Notion: failed to query calendar for {date}: {e}")

    # Archive stale rows (in Notion but no longer in the filtered list)
    archived = 0
    for ticker, page_id in existing.items():
        if ticker not in expected_tickers:
            try:
                requests.patch(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    headers=_headers,
                    json={"archived": True},
                ).raise_for_status()
                archived += 1
                logger.debug(f"Notion: archived stale calendar row for {ticker} on {date}")
            except Exception as e:
                logger.error(f"Notion: failed to archive stale row for {ticker} on {date}: {e}")

    # Create rows for tickers not yet in Notion
    created = 0
    for entry in entries:
        if entry.ticker in existing:
            logger.debug(f"Notion: skipping duplicate calendar row for {entry.ticker} on {date}")
            continue
        try:
            props = {
                "Ticker":       _title(entry.ticker),
                "Date":         _date(entry.date),
                "Timing":       _select(entry.timing),
                "EPS Estimate": _num(entry.eps_estimate),
                "Rev Estimate": _num(entry.rev_estimate),
            }
            _create_page(db_id, props)
            created += 1
            logger.debug(f"Notion: created calendar row for {entry.ticker} on {date}")
        except Exception as e:
            logger.error(f"Notion: failed to create calendar row for {entry.ticker}: {e}")

    return created, archived


def write_scan(
    scan_type: str,
    date: str,
    signals: list,
    move_pcts: dict,
    eps_beat_pcts: dict,
) -> None:
    """Update existing calendar rows with scan results (actuals, filters, signal).

    Looks up each ticker's row by date, then patches it.
    If no row exists (e.g. calendar wasn't run), creates a new one.
    scan_type: "BMO" or "AMC"
    """
    config = _load_config()
    if not config:
        return
    db_id = config.get("calendar_db_id")
    if not db_id:
        logger.warning("calendar_db_id missing from notion_config.json")
        return

    # Fetch all rows for this date once, build a ticker -> page_id map
    existing: dict[str, str] = {}
    try:
        pages = _query_db(db_id, {"property": "Date", "date": {"equals": date}})
        for page in pages:
            ticker = _get_title(page, "Ticker")
            if ticker:
                existing[ticker] = page["id"]
    except Exception as e:
        logger.error(f"Notion: failed to query calendar for {date}: {e}")
        return

    for sig in signals:
        try:
            f = sig.filters_passed or {}
            props = {
                "Signal":       _select("BUY" if sig.should_enter else "skip"),
                "EPS Beat %":   _pct(eps_beat_pcts.get(sig.ticker)),
                "Move %":       _pct(move_pcts.get(sig.ticker)),
                "eps_beat":     _checkbox(f.get("eps_beat", False)),
                "rev_beat":     _checkbox(f.get("rev_beat", False)),
                "ah_move":      _checkbox(f.get("ah_move", False)),
                "prior_runup":  _checkbox(f.get("prior_runup", False)),
                "sector_etf":   _checkbox(f.get("sector_etf", False)),
                "guidance":     _checkbox(f.get("guidance", False)),
                "capacity":     _checkbox(f.get("capacity", False)),
            }
            if sig.entry_price is not None:
                props["Entry Price"] = _num(sig.entry_price)
            if sig.initial_stop is not None:
                props["Stop Price"] = _num(sig.initial_stop)

            if sig.ticker in existing:
                _update_page(existing[sig.ticker], props)
                logger.debug(f"Notion: updated {scan_type} scan row for {sig.ticker}")
            else:
                # Calendar wasn't pre-populated — create a full row
                props["Ticker"] = _title(sig.ticker)
                props["Date"] = _date(date)
                props["Timing"] = _select(scan_type.lower())
                _create_page(db_id, props)
                logger.debug(f"Notion: created {scan_type} scan row for {sig.ticker}")
        except Exception as e:
            logger.error(f"Notion: failed to write {scan_type} row for {sig.ticker}: {e}")


def sync_positions(positions: list) -> None:
    """Upsert open positions into the Open Positions database.

    Creates a row for new positions, updates existing ones, archives closed ones.
    """
    config = _load_config()
    if not config:
        return
    db_id = config.get("positions_db_id")
    if not db_id:
        logger.warning("positions_db_id missing from notion_config.json")
        return

    # Fetch existing pages keyed by ticker
    existing: dict[str, str] = {}
    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=_headers,
            json={},
        )
        r.raise_for_status()
        for page in r.json().get("results", []):
            ticker = _get_title(page, "Ticker")
            if ticker:
                existing[ticker] = page["id"]
    except Exception as e:
        logger.error(f"Notion: failed to query positions database: {e}")
        return

    active_tickers = {p.ticker for p in positions}

    # Archive rows for positions that have been closed
    for ticker, page_id in existing.items():
        if ticker not in active_tickers:
            try:
                requests.patch(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    headers=_headers,
                    json={"archived": True},
                ).raise_for_status()
                logger.debug(f"Notion: archived closed position {ticker}")
            except Exception as e:
                logger.error(f"Notion: failed to archive position {ticker}: {e}")

    # Upsert current open positions
    for pos in positions:
        try:
            props = {
                "Ticker":       _title(pos.ticker),
                "Entry Price":  _num(pos.entry_price),
                "Entry Date":   _date(pos.entry_date),
                "Stop":         _num(pos.current_stop),
                "Day":          _num(pos.day_count),
                "Qty":          _num(pos.quantity),
            }
            if pos.ticker in existing:
                _update_page(existing[pos.ticker], props)
                logger.debug(f"Notion: updated position {pos.ticker}")
            else:
                _create_page(db_id, props)
                logger.debug(f"Notion: created position row for {pos.ticker}")
        except Exception as e:
            logger.error(f"Notion: failed to upsert position {pos.ticker}: {e}")
