"""
notion_reporter.py — writes scan results, positions, and calendar to Notion databases.

Required env vars:
    NOTION_TOKEN            — integration secret (secret_xxx)

Database IDs are stored in data/notion_config.json after running setup_notion.py.

Public API:
    write_scan(scan_type, date, signals, move_pcts, eps_beat_pcts)  -> None
    sync_positions(positions)                                        -> None
    write_calendar(date, tickers)                                    -> None
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_FILE = Path("data/notion_config.json")
_token = os.getenv("NOTION_TOKEN")

# Lazily import notion_client so the rest of the bot works even if it's not installed
try:
    from notion_client import Client as _NotionClient
    _client = _NotionClient(auth=_token) if _token else None
except ImportError:
    _NotionClient = None
    _client = None
    logger.warning("notion-client not installed — Notion reporting disabled")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_config() -> dict | None:
    if not _client:
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


def _title(value: str) -> dict:
    return {"title": [{"text": {"content": value}}]}


def _date(value: str) -> dict:
    return {"date": {"start": value}}


def _select(value: str) -> dict:
    return {"select": {"name": value}}


def _checkbox(value: bool) -> dict:
    return {"checkbox": bool(value)}


def _pct(value: float | None) -> dict:
    """Store percentage as a plain number (0.08 -> 8.0) for readability in Notion."""
    return {"number": round(value * 100, 2) if value is not None else None}


# ── Public API ────────────────────────────────────────────────────────────────

def write_scan(
    scan_type: str,
    date: str,
    signals: list,
    move_pcts: dict,
    eps_beat_pcts: dict,
) -> None:
    """Append one row per ticker to the Earnings Scans database.

    scan_type: "BMO" or "AMC"
    """
    config = _load_config()
    if not config:
        return
    db_id = config.get("scans_db_id")
    if not db_id:
        logger.warning("scans_db_id missing from notion_config.json")
        return

    for sig in signals:
        try:
            f = sig.filters_passed or {}
            props = {
                "Ticker":       _title(sig.ticker),
                "Date":         _date(date),
                "Type":         _select(scan_type),
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
                props["Entry Price"] = {"number": sig.entry_price}
            if sig.initial_stop is not None:
                props["Stop Price"] = {"number": sig.initial_stop}

            _client.pages.create(parent={"database_id": db_id}, properties=props)
            logger.debug(f"Notion: wrote {scan_type} scan row for {sig.ticker}")
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
    existing: dict[str, str] = {}  # ticker -> page_id
    try:
        resp = _client.databases.query(database_id=db_id)
        for page in resp.get("results", []):
            try:
                title_parts = page["properties"]["Ticker"]["title"]
                if title_parts:
                    ticker = title_parts[0]["text"]["content"]
                    existing[ticker] = page["id"]
            except (KeyError, IndexError):
                pass
    except Exception as e:
        logger.error(f"Notion: failed to query positions database: {e}")
        return

    active_tickers = {p.ticker for p in positions}

    # Archive rows for positions that have been closed
    for ticker, page_id in existing.items():
        if ticker not in active_tickers:
            try:
                _client.pages.update(page_id=page_id, archived=True)
                logger.debug(f"Notion: archived closed position {ticker}")
            except Exception as e:
                logger.error(f"Notion: failed to archive position {ticker}: {e}")

    # Upsert current open positions
    for pos in positions:
        try:
            props = {
                "Ticker":       _title(pos.ticker),
                "Entry Price":  {"number": pos.entry_price},
                "Entry Date":   _date(pos.entry_date),
                "Stop":         {"number": pos.current_stop},
                "Day":          {"number": pos.day_count},
                "Qty":          {"number": pos.quantity},
            }
            if pos.ticker in existing:
                _client.pages.update(page_id=existing[pos.ticker], properties=props)
                logger.debug(f"Notion: updated position {pos.ticker}")
            else:
                _client.pages.create(parent={"database_id": db_id}, properties=props)
                logger.debug(f"Notion: created position row for {pos.ticker}")
        except Exception as e:
            logger.error(f"Notion: failed to upsert position {pos.ticker}: {e}")


def write_calendar(date: str, tickers: list) -> None:
    """Append one row per ticker to the Earnings Calendar database."""
    config = _load_config()
    if not config:
        return
    db_id = config.get("calendar_db_id")
    if not db_id:
        logger.warning("calendar_db_id missing from notion_config.json")
        return

    for ticker in tickers:
        try:
            _client.pages.create(
                parent={"database_id": db_id},
                properties={
                    "Ticker": _title(ticker),
                    "Date":   _date(date),
                },
            )
            logger.debug(f"Notion: wrote calendar row for {ticker} on {date}")
        except Exception as e:
            logger.error(f"Notion: failed to write calendar row for {ticker}: {e}")
