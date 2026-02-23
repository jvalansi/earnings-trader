import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Literal

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler

from config import TRADING_MODE, ALLOWED_EXCHANGES
from notifier import notify
from data.earnings import get_earnings_calendar, get_earnings_surprise
from data.prices import get_ohlcv, get_atr, get_ah_move, get_premarket_move, get_prior_runup
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

    # Slack summary
    if signals:
        lines = [f"*Earnings Scan — {today}* ({len(tickers)} AMC tickers)"]
        for sig in signals:
            checks = " ".join(
                ("✅" if v else "❌") + f" {k}" for k, v in sig.filters_passed.items()
            )
            if sig.should_enter:
                lines.append(f"📈 *{sig.ticker}* — BUY @ ${sig.entry_price:.2f} | stop ${sig.initial_stop:.2f}\n    {checks}")
            else:
                lines.append(f"➖ *{sig.ticker}* — no entry\n    {checks}")
        notify("\n".join(lines))
    else:
        notify(f"*Earnings Scan — {today}*: no tickers evaluated.")


def run_bmo_scan_cycle(mode: str = "paper") -> None:
    """9:00 AM ET — scan today's BMO earnings for entry signals.

    Mirrors run_scan_cycle but uses pre-market move instead of AH move.
    1. Fetch today's earnings calendar (BMO tickers only)
    2. For each ticker: fetch surprise, pre-market move, prior run-up, sector move, ATR
    3. Evaluate entry signal against all 6 filters
    4. Execute BUY orders for passing signals
    """
    today = date.today().strftime("%Y-%m-%d")
    logger.info(f"=== BMO Scan Cycle: {today} ===")

    try:
        tickers = get_earnings_calendar(today, timing="bmo")
    except Exception as e:
        logger.error(f"Failed to fetch BMO earnings calendar: {e}", exc_info=True)
        return

    if not tickers:
        logger.info("No BMO earnings today.")
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
            pm_move = get_premarket_move(ticker, today)
            prior_runup = get_prior_runup(ticker)
            sector_move = get_sector_move(ticker, today)
            atr = get_atr(ticker)
            df = get_ohlcv(ticker, days=1)
            current_price = float(df["Close"].iloc[-1])

            sig = evaluate_entry(
                ticker=ticker,
                surprise=surprise,
                ah_move=pm_move,
                prior_runup=prior_runup,
                sector_move=sector_move,
                atr=atr,
                current_price=current_price,
                open_positions=open_positions,
            )
            signals.append(sig)
            logger.info(f"{ticker}: should_enter={sig.should_enter}, filters={sig.filters_passed}")

        except Exception as e:
            logger.error(f"Error processing {ticker} in BMO scan cycle: {e}", exc_info=True)
            continue

    execute_signals(signals, [], mode=mode)

    # Slack summary
    if signals:
        lines = [f"*BMO Earnings Scan — {today}* ({len(tickers)} BMO tickers)"]
        for sig in signals:
            checks = " ".join(
                ("✅" if v else "❌") + f" {k}" for k, v in sig.filters_passed.items()
            )
            if sig.should_enter:
                lines.append(f"📈 *{sig.ticker}* — BUY @ ${sig.entry_price:.2f} | stop ${sig.initial_stop:.2f}\n    {checks}")
            else:
                lines.append(f"➖ *{sig.ticker}* — no entry\n    {checks}")
        notify("\n".join(lines))
    else:
        notify(f"*BMO Earnings Scan — {today}*: no tickers evaluated.")


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

    # Slack summary
    today = date.today().strftime("%Y-%m-%d")
    lines = [f"*Position Update — {today}*"]
    action_map = {a.ticker: a for a in actions}
    for pos in positions:
        price = current_prices.get(pos.ticker)
        act = action_map.get(pos.ticker)
        price_str = f"${price:.2f}" if price is not None else "n/a"
        if act and act.action == "sell":
            lines.append(f"📉 *{pos.ticker}* — SOLD @ {price_str} | {act.reason} (day {pos.day_count}/{10})")
        elif act and act.action == "update_stop":
            lines.append(f"🔄 *{pos.ticker}* — holding @ {price_str} | stop → ${act.new_stop:.2f} (day {pos.day_count}/10)")
        else:
            lines.append(f"⏸ *{pos.ticker}* — holding @ {price_str} | stop ${pos.current_stop:.2f} (day {pos.day_count}/10)")
    notify("\n".join(lines))


_US_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


def _filter_us_exchange(tickers: list[str]) -> list[str]:
    """Filter tickers to major US exchanges.

    First pass: drop anything that doesn't look like a US ticker (1-5 uppercase letters).
    Second pass: confirm remaining tickers are on an allowed exchange via yfinance lookup,
    using a thread pool to parallelize the calls.
    """
    candidates = [t for t in tickers if _US_TICKER_RE.match(t)]

    def check(ticker: str) -> str | None:
        exchange = get_exchange(ticker)
        return ticker if exchange in ALLOWED_EXCHANGES else None

    results = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(check, t): t for t in candidates}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    return sorted(results)


def run_calendar_preview() -> None:
    """7:00 PM ET — post tomorrow's earnings calendar to Slack."""
    tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    logger.info(f"=== Calendar Preview: {tomorrow} ===")

    try:
        bmo_tickers = _filter_us_exchange(get_earnings_calendar(tomorrow, timing="bmo"))
    except Exception as e:
        logger.error(f"Failed to fetch BMO calendar for {tomorrow}: {e}", exc_info=True)
        bmo_tickers = []

    try:
        amc_tickers = _filter_us_exchange(get_earnings_calendar(tomorrow, timing="amc"))
    except Exception as e:
        logger.error(f"Failed to fetch AMC calendar for {tomorrow}: {e}", exc_info=True)
        amc_tickers = []

    lines = [f"*Earnings Calendar — {tomorrow}*"]
    if bmo_tickers:
        lines.append(f"🌅 *BMO ({len(bmo_tickers)}):* {', '.join(bmo_tickers)}")
    else:
        lines.append("🌅 *BMO:* none")
    if amc_tickers:
        lines.append(f"🌆 *AMC ({len(amc_tickers)}):* {', '.join(amc_tickers)}")
    else:
        lines.append("🌆 *AMC:* none")
    notify("\n".join(lines))


def start(mode: Literal["paper", "live"] = "paper") -> None:
    """Start the APScheduler blocking event loop. Registers both daily cycles."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    scheduler = BlockingScheduler(timezone=EASTERN)
    scheduler.add_job(
        run_bmo_scan_cycle,
        trigger="cron",
        hour=9,
        minute=0,
        kwargs={"mode": mode},
        id="bmo_scan_cycle",
        name="BMO Earnings Scan @ 9:00 AM ET",
    )
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
    scheduler.add_job(
        run_calendar_preview,
        trigger="cron",
        hour=19,
        minute=0,
        id="calendar_preview",
        name="Calendar Preview @ 7:00 PM ET",
    )

    logger.info(f"Scheduler starting in {mode!r} mode.")
    logger.info("  BMO scan:         9:00 AM ET (pre-market move + evaluate entries)")
    logger.info("  Scan cycle:       4:15 PM ET (fetch earnings + evaluate entries)")
    logger.info("  Update cycle:     4:30 PM ET (manage open positions)")
    logger.info("  Calendar preview: 7:00 PM ET (tomorrow's earnings calendar)")
    scheduler.start()
