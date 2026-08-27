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
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10"))   # max concurrent open positions
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

# --- Capital and position sizing ---
# Capital actually allocated to the strategy. Position size is derived so that
# MAX_POSITIONS concurrent slots fully allocate it (backtest used the same
# structure at a larger scale: 10 x $8k = $80k).
ACCOUNT_CAPITAL_USD = float(os.getenv("ACCOUNT_CAPITAL_USD", "5000"))
POSITION_SIZE_USD = float(os.getenv("POSITION_SIZE_USD", ACCOUNT_CAPITAL_USD / MAX_POSITIONS))

# --- Risk controls (see risk.py) ---
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.04"))   # halt entries after -4% in a day
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "0.15"))           # halt entries after -15% from peak equity
RISK_STATE_FILE = "data/risk_state.json"
HALT_FILE = "data/HALT"          # touch this file to block new entries; exits are never blocked

# --- File paths ---
POSITIONS_FILE = "data/positions.json"
TRADES_LOG_FILE = "data/trades_log.jsonl"

# --- API keys ---
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# --- Execution ---
# 'paper' -> Alpaca paper endpoint (or local simulation when no keys are set)
# 'live'  -> real money; additionally requires LIVE_TRADING_CONFIRMED=yes and Alpaca keys
TRADING_MODE = os.getenv("TRADING_MODE", "paper")
LIVE_TRADING_CONFIRMED = os.getenv("LIVE_TRADING_CONFIRMED", "").lower() in ("yes", "true", "1")
ORDER_FILL_TIMEOUT_SEC = float(os.getenv("ORDER_FILL_TIMEOUT_SEC", "30"))  # how long to poll for a fill
