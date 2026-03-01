import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Literal

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler

from config import TRADING_MODE, ALLOWED_EXCHANGES
from notifier import notify
from data.earnings import get_earnings_calendar_details, get_earnings_surprise
from data.prices import get_ohlcv, get_atr, get_ah_move, get_premarket_move, get_prior_runup
from data.sector import get_sector_move, get_sector_intraday_move
from decision import evaluate_entry, evaluate_positions
from execution import execute_signals
from state import load_positions, save_positions
import notion_reporter

logger = logging.getLogger(__name__)
EASTERN = pytz.timezone("US/Eastern")


def run_scan_cycle(mode: str = "paper") -> None:
    """4:15 PM ET — scan today's earnings for entry signals using AH move.

    1. Fetch today's full earnings calendar
    2. For each ticker: fetch surprise, AH move, prior run-up, sector move, ATR
    3. Evaluate entry signal against all 6 filters
    4. Execute BUY orders for passing signals
    """
    today = datetime.now(EASTERN).strftime("%Y-%m-%d")
    logger.info(f"=== Scan Cycle: {today} ===")

    try:
        all_entries = get_earnings_calendar_details(today)
        all_entries = [e for e in all_entries if e.eps_estimate is not None]
        tickers = _filter_us_exchange([e.ticker for e in all_entries])
    except Exception as e:
        logger.error(f"Failed to fetch earnings calendar: {e}", exc_info=True)
        return

    if not tickers:
        logger.info("No earnings today.")
        return

    open_positions = load_positions()
    signals = []
    move_pcts: dict[str, float] = {}
    eps_beat_pcts: dict[str, float] = {}

    for ticker in tickers:
        try:
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
            move_pcts[ticker] = ah_move
            eps_beat_pcts[ticker] = surprise.eps_beat_pct
            logger.info(f"{ticker}: should_enter={sig.should_enter}, filters={sig.filters_passed}")

        except Exception as e:
            logger.error(f"Error processing {ticker} in scan cycle: {e}", exc_info=True)
            continue

    execute_signals(signals, [], mode=mode)

    # Slack summary
    if signals:
        lines = [f"*Earnings Scan — {today}* ({len(tickers)} tickers)"]
        for sig in signals:
            checks = " ".join(
                ("✅" if v else "❌") + f" {k}" for k, v in sig.filters_passed.items()
            )
            if sig.should_enter:
                lines.append(f"📈 *{sig.ticker}* — BUY @ ${sig.entry_price:.2f} | stop loss ${sig.initial_stop:.2f}\n    {checks}")
            else:
                lines.append(f"➖ *{sig.ticker}* — no entry\n    {checks}")
        notify("\n".join(lines))
    else:
        notify(f"*Earnings Scan — {today}*: no tickers evaluated.")

    try:
        notion_reporter.write_scan("AMC", today, signals, move_pcts, eps_beat_pcts)
    except Exception as e:
        logger.error(f"Notion: failed to write AMC scan: {e}", exc_info=True)


def run_bmo_scan_cycle(mode: str = "paper") -> None:
    """10:00 AM ET — scan today's earnings for entry signals using pre-market move.

    Mirrors run_scan_cycle but uses pre-market move instead of AH move.
    1. Fetch today's full earnings calendar
    2. For each ticker: fetch surprise, pre-market move, prior run-up, sector move, ATR
    3. Evaluate entry signal against all 6 filters
    4. Execute BUY orders for passing signals
    """
    today = datetime.now(EASTERN).strftime("%Y-%m-%d")
    logger.info(f"=== BMO Scan Cycle: {today} ===")

    try:
        all_entries = get_earnings_calendar_details(today)
        all_entries = [e for e in all_entries if e.eps_estimate is not None]
        tickers = _filter_us_exchange([e.ticker for e in all_entries])
    except Exception as e:
        logger.error(f"Failed to fetch BMO earnings calendar: {e}", exc_info=True)
        return

    if not tickers:
        logger.info("No earnings today.")
        return

    open_positions = load_positions()
    signals = []
    move_pcts: dict[str, float] = {}
    eps_beat_pcts: dict[str, float] = {}

    for ticker in tickers:
        try:
            surprise = get_earnings_surprise(ticker, date=today)
            pm_move = get_premarket_move(ticker, today)
            prior_runup = get_prior_runup(ticker)
            sector_move = get_sector_intraday_move(ticker, today)
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
            move_pcts[ticker] = pm_move
            eps_beat_pcts[ticker] = surprise.eps_beat_pct
            logger.info(f"{ticker}: should_enter={sig.should_enter}, filters={sig.filters_passed}")

        except Exception as e:
            logger.error(f"Error processing {ticker} in BMO scan cycle: {e}", exc_info=True)
            continue

    execute_signals(signals, [], mode=mode)

    # Slack summary
    if signals:
        lines = [f"*BMO Earnings Scan — {today}* ({len(tickers)} tickers)"]
        for sig in signals:
            checks = " ".join(
                ("✅" if v else "❌") + f" {k}" for k, v in sig.filters_passed.items()
            )
            if sig.should_enter:
                lines.append(f"📈 *{sig.ticker}* — BUY @ ${sig.entry_price:.2f} | stop loss ${sig.initial_stop:.2f}\n    {checks}")
            else:
                lines.append(f"➖ *{sig.ticker}* — no entry\n    {checks}")
        notify("\n".join(lines))
    else:
        notify(f"*BMO Earnings Scan — {today}*: no tickers evaluated.")

    try:
        notion_reporter.write_scan("BMO", today, signals, move_pcts, eps_beat_pcts)
    except Exception as e:
        logger.error(f"Notion: failed to write BMO scan: {e}", exc_info=True)


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

    try:
        notion_reporter.sync_positions(load_positions())
    except Exception as e:
        logger.error(f"Notion: failed to sync positions: {e}", exc_info=True)

    # Slack summary
    today = datetime.now(EASTERN).strftime("%Y-%m-%d")
    lines = [f"*Position Update — {today}*"]
    action_map = {a.ticker: a for a in actions}
    for pos in positions:
        price = current_prices.get(pos.ticker)
        act = action_map.get(pos.ticker)
        price_str = f"${price:.2f}" if price is not None else "n/a"
        entry_str = f"${pos.entry_price:.2f}"
        if price is not None:
            pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
            pnl_sign = "+" if pnl_pct >= 0 else ""
            price_detail = f"entry {entry_str} → now {price_str} ({pnl_sign}{pnl_pct:.1f}%)"
        else:
            price_detail = f"entry {entry_str} → now n/a"
        if act and act.action == "sell":
            lines.append(f"📉 *{pos.ticker}* — SOLD @ {price_str} | {price_detail} | {act.reason} (day {pos.day_count}/{10})")
        elif act and act.action == "update_stop":
            lines.append(f"🔄 *{pos.ticker}* — holding | {price_detail} | stop loss → ${act.new_stop:.2f} (day {pos.day_count}/10)")
        else:
            lines.append(f"⏸ *{pos.ticker}* — holding | {price_detail} | stop loss ${pos.current_stop:.2f} (day {pos.day_count}/10)")
    notify("\n".join(lines))


_US_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


def _filter_us_exchange(tickers: list[str]) -> list[str]:
    """Filter tickers to major US exchange equities.

    First pass: drop anything that doesn't look like a US ticker (1-5 uppercase letters).
    Second pass: confirm remaining tickers are on an allowed exchange and are equities
    (not ETFs, funds, etc.) via yfinance lookup, using a thread pool to parallelize the calls.
    """
    import yfinance as yf

    candidates = [t for t in tickers if _US_TICKER_RE.match(t)]

    def check(ticker: str) -> str | None:
        try:
            info = yf.Ticker(ticker).info
            exchange = info.get("exchange", "")
            quote_type = info.get("quoteType", "")
            if exchange in ALLOWED_EXCHANGES and quote_type == "EQUITY":
                return ticker
        except Exception as e:
            logger.warning(f"Could not get info for {ticker}: {e}")
        return None

    results = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(check, t): t for t in candidates}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    return sorted(results)


def run_calendar_preview() -> None:
    """7:00 PM ET — post tomorrow's earnings calendar to Slack and Notion."""
    tomorrow = (datetime.now(EASTERN) + timedelta(days=1)).strftime("%Y-%m-%d")
    logger.info(f"=== Calendar Preview: {tomorrow} ===")

    try:
        all_entries = get_earnings_calendar_details(tomorrow)
        # Only keep entries that have analyst EPS estimates — required for beat evaluation
        all_entries = [e for e in all_entries if e.eps_estimate is not None]
        valid_tickers = set(_filter_us_exchange([e.ticker for e in all_entries]))
        entries = [e for e in all_entries if e.ticker in valid_tickers]
        tickers = sorted(valid_tickers)
    except Exception as e:
        logger.error(f"Failed to fetch calendar for {tomorrow}: {e}", exc_info=True)
        entries = []
        tickers = []

    lines = [f"*Earnings Calendar — {tomorrow}*"]
    if tickers:
        lines.append(f"{len(tickers)} reporting: {', '.join(tickers)}")
    else:
        lines.append("No earnings reporting.")
    notify("\n".join(lines))

    try:
        created, archived = notion_reporter.write_calendar(tomorrow, entries)
        logger.info(
            f"Notion calendar for {tomorrow}: {len(entries)} expected, "
            f"{created} created, {archived} stale archived"
        )
        if archived:
            logger.warning(
                f"Notion: archived {archived} stale rows for {tomorrow} — "
                "these were in Notion but not in the filtered ticker list"
            )
    except Exception as e:
        logger.error(f"Notion: failed to write calendar: {e}", exc_info=True)


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
        hour=10,
        minute=0,
        kwargs={"mode": mode},
        id="bmo_scan_cycle",
        name="BMO Earnings Scan @ 10:00 AM ET",
        misfire_grace_time=1,
    )
    scheduler.add_job(
        run_scan_cycle,
        trigger="cron",
        hour=16,
        minute=15,
        kwargs={"mode": mode},
        id="scan_cycle",
        name="Earnings Scan @ 4:15 PM ET",
        misfire_grace_time=1,
    )
    scheduler.add_job(
        run_update_cycle,
        trigger="cron",
        hour=16,
        minute=30,
        kwargs={"mode": mode},
        id="update_cycle",
        name="Position Update @ 4:30 PM ET",
        misfire_grace_time=1,
    )
    scheduler.add_job(
        run_calendar_preview,
        trigger="cron",
        hour=19,
        minute=0,
        id="calendar_preview",
        name="Calendar Preview @ 7:00 PM ET",
        misfire_grace_time=1,
    )

    logger.info(f"Scheduler starting in {mode!r} mode.")
    logger.info("  BMO scan:        10:00 AM ET (pre-market move + evaluate entries)")
    logger.info("  Scan cycle:       4:15 PM ET (fetch earnings + evaluate entries)")
    logger.info("  Update cycle:     4:30 PM ET (manage open positions)")
    logger.info("  Calendar preview: 7:00 PM ET (tomorrow's earnings calendar)")
    scheduler.start()
