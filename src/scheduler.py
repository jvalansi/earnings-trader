import json
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
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
        keys = list(signals[0].filters_passed.keys())
        lines.append("  " + " | ".join(keys))
        max_ticker_len = max(len(sig.ticker) for sig in signals)
        for sig in signals:
            checks = " ".join("✅" if v else "❌" for v in sig.filters_passed.values())
            ticker_col = sig.ticker.ljust(max_ticker_len)
            if sig.should_enter:
                lines.append(f"📈 `{ticker_col}` — BUY @ ${sig.entry_price:.2f} | stop ${sig.initial_stop:.2f}  {checks}")
            else:
                lines.append(f"➖ `{ticker_col}`  {checks}")
        notify("\n".join(lines))
    else:
        notify(f"*Earnings Scan — {today}*: no tickers evaluated.")



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
        keys = list(signals[0].filters_passed.keys())
        lines.append("  " + " | ".join(keys))
        max_ticker_len = max(len(sig.ticker) for sig in signals)
        for sig in signals:
            checks = " ".join("✅" if v else "❌" for v in sig.filters_passed.values())
            ticker_col = sig.ticker.ljust(max_ticker_len)
            if sig.should_enter:
                lines.append(f"📈 `{ticker_col}` — BUY @ ${sig.entry_price:.2f} | stop ${sig.initial_stop:.2f}  {checks}")
            else:
                lines.append(f"➖ `{ticker_col}`  {checks}")
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
    today = datetime.now(EASTERN).strftime("%Y-%m-%d")
    lines = [f"*Position Update — {today}*"]
    action_map = {a.ticker: a for a in actions}
    max_ticker_len = max(len(pos.ticker) for pos in positions)
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
        ticker_col = pos.ticker.ljust(max_ticker_len)
        if act and act.action == "sell":
            lines.append(f"📉 `{ticker_col}` — SOLD @ {price_str} | {price_detail} | {act.reason} (day {pos.day_count}/{10})")
        elif act and act.action == "update_stop":
            lines.append(f"🔄 `{ticker_col}` — holding | {price_detail} | stop loss → ${act.new_stop:.2f} (day {pos.day_count}/10)")
        else:
            lines.append(f"⏸ `{ticker_col}` — holding | {price_detail} | stop loss ${pos.current_stop:.2f} (day {pos.day_count}/10)")
    notify("\n".join(lines))


def run_weekly_pnl_summary() -> None:
    """Monday 9:00 AM ET — post last week's realized PnL summary to Slack."""
    now = datetime.now(EASTERN)
    week_end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = week_end - timedelta(days=7)
    logger.info(f"=== Weekly PnL Summary: {week_start.date()} to {week_end.date()} ===")

    trades_path = Path(__file__).parent.parent / "data" / "trades_log.jsonl"
    if not trades_path.exists():
        notify("*Weekly PnL Summary*: no trades log found.")
        return

    # Load all successful trades in the past week
    buys: dict[str, list[dict]] = defaultdict(list)   # ticker → stack of buy entries
    closed_trades: list[dict] = []

    with trades_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not t.get("success"):
                continue
            ts = datetime.fromisoformat(t["timestamp"]).astimezone(EASTERN)
            if t["action"] == "buy":
                buys[t["ticker"]].append({"price": t["fill_price"], "qty": t["quantity"], "ts": ts})
            elif t["action"] == "sell" and week_start <= ts < week_end:
                # Match against earliest unmatched buy for this ticker
                ticker = t["ticker"]
                buy = buys[ticker].pop(0) if buys[ticker] else None
                closed_trades.append({
                    "ticker": ticker,
                    "buy_price": buy["price"] if buy else None,
                    "sell_price": t["fill_price"],
                    "qty": t["quantity"],
                    "sell_ts": ts,
                })

    lines = [f"*Weekly PnL Summary — {week_start.strftime('%b %d')} to {week_end.strftime('%b %d, %Y')}*"]

    if closed_trades:
        total_pnl = 0.0
        wins, losses = 0, 0
        for ct in closed_trades:
            if ct["buy_price"] is not None:
                pnl = (ct["sell_price"] - ct["buy_price"]) * ct["qty"]
                pnl_pct = (ct["sell_price"] - ct["buy_price"]) / ct["buy_price"] * 100
                total_pnl += pnl
                sign = "+" if pnl >= 0 else ""
                icon = "✅" if pnl >= 0 else "❌"
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1
                lines.append(
                    f"{icon} *{ct['ticker']}* — {ct['qty']} shares | "
                    f"${ct['buy_price']:.2f} → ${ct['sell_price']:.2f} "
                    f"({sign}{pnl_pct:.1f}%) | {sign}${pnl:.2f}"
                )
            else:
                lines.append(f"❓ *{ct['ticker']}* — sold @ ${ct['sell_price']:.2f} (no matching buy)")

        total_sign = "+" if total_pnl >= 0 else ""
        win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
        lines.append(
            f"\n*Total: {total_sign}${total_pnl:.2f}* | "
            f"{wins}W / {losses}L ({win_rate:.0f}% win rate)"
        )
    else:
        lines.append("No closed trades this week.")

    # Open positions (unrealized)
    positions = load_positions()
    if positions:
        lines.append("\n*Open Positions (unrealized)*")
        for pos in positions:
            try:
                df = get_ohlcv(pos.ticker, days=1)
                price = float(df["Close"].iloc[-1])
                pnl = (price - pos.entry_price) * pos.quantity
                pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                sign = "+" if pnl >= 0 else ""
                lines.append(
                    f"  • *{pos.ticker}* — {pos.quantity} sh | "
                    f"${pos.entry_price:.2f} → ${price:.2f} "
                    f"({sign}{pnl_pct:.1f}%) | {sign}${pnl:.2f} | day {pos.day_count}/10"
                )
            except Exception as e:
                logger.warning(f"Could not fetch price for {pos.ticker}: {e}")
                lines.append(f"  • *{pos.ticker}* — price unavailable | day {pos.day_count}/10")

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

    # Current holdings with buy price and ROI
    positions = load_positions()
    if positions:
        lines.append("\n*Current Holdings*")
        for pos in positions:
            try:
                df = get_ohlcv(pos.ticker, days=1)
                current_price = float(df["Close"].iloc[-1])
                roi_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                roi_sign = "+" if roi_pct >= 0 else ""
                lines.append(
                    f"  • *{pos.ticker}* — {pos.quantity} shares | "
                    f"buy ${pos.entry_price:.2f} → ${current_price:.2f} "
                    f"({roi_sign}{roi_pct:.1f}%) | day {pos.day_count}/10"
                )
            except Exception as e:
                logger.warning(f"Could not fetch price for {pos.ticker}: {e}")
                lines.append(
                    f"  • *{pos.ticker}* — {pos.quantity} shares | "
                    f"buy ${pos.entry_price:.2f} | price unavailable | day {pos.day_count}/10"
                )

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
    scheduler.add_job(
        run_weekly_pnl_summary,
        trigger="cron",
        day_of_week="mon",
        hour=9,
        minute=0,
        id="weekly_pnl_summary",
        name="Weekly PnL Summary @ 9:00 AM ET Monday",
        misfire_grace_time=300,
    )

    logger.info(f"Scheduler starting in {mode!r} mode.")
    logger.info("  BMO scan:          10:00 AM ET (pre-market move + evaluate entries)")
    logger.info("  Scan cycle:         4:15 PM ET (fetch earnings + evaluate entries)")
    logger.info("  Update cycle:       4:30 PM ET (manage open positions)")
    logger.info("  Calendar preview:   7:00 PM ET (tomorrow's earnings calendar)")
    logger.info("  Weekly PnL summary: 9:00 AM ET Monday (last week's realized PnL)")
    scheduler.start()
