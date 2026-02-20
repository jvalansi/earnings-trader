import logging
from datetime import date
from typing import Literal

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler

from config import TRADING_MODE, ALLOWED_EXCHANGES
from data.earnings import get_earnings_calendar, get_earnings_surprise
from data.prices import get_ohlcv, get_atr, get_ah_move, get_prior_runup
from data.sector import get_sector_move, get_exchange
from decision import evaluate_entry, evaluate_positions
from execution import execute_signals
from state import load_positions, save_positions

logger = logging.getLogger(__name__)
EASTERN = pytz.timezone("US/Eastern")


def run_scan_cycle(mode: str = "paper") -> None:
    """4:15 PM ET — scan today's AMC earnings for entry signals.

    1. Fetch today's earnings calendar (AMC tickers only)
    2. For each ticker: fetch surprise, AH move, prior run-up, sector move, ATR
    3. Evaluate entry signal against all 6 filters
    4. Execute BUY orders for passing signals
    """
    today = date.today().strftime("%Y-%m-%d")
    logger.info(f"=== Scan Cycle: {today} ===")

    try:
        tickers = get_earnings_calendar(today)
    except Exception as e:
        logger.error(f"Failed to fetch earnings calendar: {e}", exc_info=True)
        return

    if not tickers:
        logger.info("No AMC earnings today.")
        return

    open_positions = load_positions()
    signals = []

    for ticker in tickers:
        try:
            exchange = get_exchange(ticker)
            if exchange not in ALLOWED_EXCHANGES:
                logger.debug(f"Skipping {ticker}: exchange '{exchange}' not in allowed list")
                continue

            surprise = get_earnings_surprise(ticker, date=today)
            ah_move = get_ah_move(ticker, today)
            prior_runup = get_prior_runup(ticker)
            sector_move = get_sector_move(ticker, today)
            atr = get_atr(ticker)
            df = get_ohlcv(ticker, days=1)
            current_price = float(df["Close"].iloc[-1])

            sig = evaluate_entry(
                ticker=ticker,
                surprise=surprise,
                ah_move=ah_move,
                prior_runup=prior_runup,
                sector_move=sector_move,
                atr=atr,
                current_price=current_price,
                open_positions=open_positions,
            )
            signals.append(sig)
            logger.info(f"{ticker}: should_enter={sig.should_enter}, filters={sig.filters_passed}")

        except Exception as e:
            logger.error(f"Error processing {ticker} in scan cycle: {e}", exc_info=True)
            continue

    execute_signals(signals, [], mode=mode)


def run_update_cycle(mode: str = "paper") -> None:
    """4:30 PM ET — evaluate open positions, update stops or exit.

    1. Load open positions
    2. Increment day_count for each position
    3. Fetch current prices and ATRs
    4. Evaluate position actions (hold / sell / update_stop)
    5. Execute actions
    """
    logger.info("=== Update Cycle ===")
    positions = load_positions()

    if not positions:
        logger.info("No open positions to manage.")
        return

    current_prices: dict[str, float] = {}
    current_atrs: dict[str, float] = {}

    for pos in positions:
        pos.day_count += 1
        try:
            df = get_ohlcv(pos.ticker, days=1)
            current_prices[pos.ticker] = float(df["Close"].iloc[-1])
            current_atrs[pos.ticker] = get_atr(pos.ticker)
        except Exception as e:
            logger.error(f"Error fetching data for {pos.ticker}: {e}", exc_info=True)

    # Save updated day counts before evaluating
    save_positions(positions)

    actions = evaluate_positions(positions, current_prices, current_atrs)
    execute_signals([], actions, current_prices=current_prices, mode=mode)


def start(mode: Literal["paper", "live"] = "paper") -> None:
    """Start the APScheduler blocking event loop. Registers both daily cycles."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    scheduler = BlockingScheduler(timezone=EASTERN)
    scheduler.add_job(
        run_scan_cycle,
        trigger="cron",
        hour=16,
        minute=15,
        kwargs={"mode": mode},
        id="scan_cycle",
        name="Earnings Scan @ 4:15 PM ET",
    )
    scheduler.add_job(
        run_update_cycle,
        trigger="cron",
        hour=16,
        minute=30,
        kwargs={"mode": mode},
        id="update_cycle",
        name="Position Update @ 4:30 PM ET",
    )

    logger.info(f"Scheduler starting in {mode!r} mode.")
    logger.info("  Scan cycle:   4:15 PM ET (fetch earnings + evaluate entries)")
    logger.info("  Update cycle: 4:30 PM ET (manage open positions)")
    scheduler.start()
