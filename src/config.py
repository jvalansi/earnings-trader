import os
from dotenv import load_dotenv

load_dotenv()

# --- Entry filter thresholds ---
MIN_EPS_BEAT_PCT = 0.05       # 5% EPS beat required
MIN_AH_MOVE_PCT = 0.03        # 3% after-hours move required
MAX_PRIOR_RUNUP_PCT = 0.10    # max 10% run-up over prior LOOKBACK_DAYS
SECTOR_ETF_MIN = -0.015       # sector ETF must be > -1.5% on the day
ATR_STOP_MULTIPLIER = 2.5     # trailing stop = entry_price - (2.5 * ATR)
HOLD_DAYS = 10                # max trading days to hold a position
MAX_POSITIONS = 10            # max concurrent open positions
LOOKBACK_DAYS = 10            # days used for prior run-up calculation

# --- Exchange filter (yfinance exchange codes for target US exchanges) ---
ALLOWED_EXCHANGES: frozenset[str] = frozenset({
    "NYQ",  # NYSE (XNYS)
    "ASE",  # NYSE American (XASE)
    "PCX",  # NYSE ARCA (ARCX)
    "NMS",  # Nasdaq Global Select Market (XNGS)
    "NGM",  # Nasdaq Global Market (XNMS)
    "NCM",  # Nasdaq Capital Market (XNCM)
    "BTS",  # Cboe BZX (BATS)
})

# --- Position sizing ---
POSITION_SIZE_USD = 8000.0    # fixed dollar amount per trade

# --- File paths ---
POSITIONS_FILE = "data/positions.json"
TRADES_LOG_FILE = "data/trades_log.jsonl"

# --- API keys ---
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# --- Mode ---
TRADING_MODE = os.getenv("TRADING_MODE", "paper")
