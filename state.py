import json
import logging
from dataclasses import dataclass, asdict, field
from pathlib import Path

from config import POSITIONS_FILE

logger = logging.getLogger(__name__)

_path = Path(POSITIONS_FILE)


@dataclass
class Position:
    ticker: str
    entry_price: float
    current_stop: float
    entry_date: str   # 'YYYY-MM-DD'
    day_count: int    # trading days held so far
    quantity: int = 0 # shares held (set at buy time)


def _ensure_data_dir() -> None:
    _path.parent.mkdir(parents=True, exist_ok=True)


def load_positions() -> list[Position]:
    """Load all open positions from the JSON store."""
    _ensure_data_dir()
    if not _path.exists():
        return []
    with _path.open("r") as f:
        raw = json.load(f)
    return [Position(**r) for r in raw]


def save_positions(positions: list[Position]) -> None:
    """Overwrite the JSON store with the given list of positions."""
    _ensure_data_dir()
    with _path.open("w") as f:
        json.dump([asdict(p) for p in positions], f, indent=2)


def add_position(position: Position) -> None:
    """Append a new position to the store."""
    positions = load_positions()
    if any(p.ticker == position.ticker for p in positions):
        logger.warning(f"Position for {position.ticker} already exists, skipping")
        return
    positions.append(position)
    save_positions(positions)


def remove_position(ticker: str) -> None:
    """Remove the position for the given ticker from the store."""
    positions = load_positions()
    positions = [p for p in positions if p.ticker != ticker]
    save_positions(positions)


def update_stop(ticker: str, new_stop: float) -> None:
    """Update the trailing stop for an existing position."""
    positions = load_positions()
    for p in positions:
        if p.ticker == ticker:
            p.current_stop = new_stop
    save_positions(positions)
