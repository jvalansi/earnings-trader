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
from notifier import notify, notify_thread
from data.earnings import get_earnings_calendar_details, get_earnings_surprise
from data.prices import get_ohlcv, get_atr, get_prior_runup
from data.sector import get_sector_intraday_move
from decision import evaluate_entry, evaluate_positions
from execution import execute_signals
from state import load_positions, save_positions

logger = logging.getLogger(__name__)
EASTERN = pytz.timezone("US/Eastern")


def run_scan_cycle(mode: str = "paper") -> None:
    """9:30 AM ET — exit/update open positions, then scan for new entries.

    1. Increment day_count, evaluate exits and trailing stop updates for open positions
    2. Execute SELLs for positions that hit stop or max hold days
    3. Fetch yesterday's (AMC) and today's (BMO) earnings calendar
    4. For each ticker: fetch surprise, overnight gap, prior run-up, sector move, ATR
    5. Evaluate entry signal against all filters
    6. Execute BUY orders for passing signals
    7. Post a single combined Slack notification
    """
    eastern_now = datetime.now(EASTERN)
    today = eastern_now.strftime("%Y-%m-%d")
    yesterday = (eastern_now - timedelta(days=1)).strftime("%Y-%m-%d")
    logger.info(f"=== Scan Cycle: {today} ===")

    # --- Exit / update open positions ---
    positions = load_positions()
    actions = []
    current_prices: dict[str, float] = {}
    current_atrs: dict[str, float] = {}

    prev_prices: dict[str, float] = {}
    for pos in positions:
        pos.day_count += 1
        try:
            df = get_ohlcv(pos.ticker, days=2)
            current_prices[pos.ticker] = float(df["Close"].iloc[-1])
            if len(df) >= 2:
                prev_prices[pos.ticker] = float(df["Close"].iloc[-2])
            current_atrs[pos.ticker] = get_atr(pos.ticker)
        except Exception as e:
            logger.error(f"Error fetching data for {pos.ticker}: {e}", exc_info=True)

    if positions:
        save_positions(positions)
        actions = evaluate_positions(positions, current_prices, current_atrs)
        execute_signals([], actions, current_prices=current_prices, mode=mode)

    # Reload after exits so entry evaluation sees accurate open position count
    open_positions = load_positions()

    # --- Scan for new entries ---
    try:
        entries_amc = get_earnings_calendar_details(yesterday)
        entries_bmo = get_earnings_calendar_details(today)
        all_entries = [e for e in entries_amc + entries_bmo if e.eps_estimate is not None]
        entry_by_ticker = {e.ticker: e for e in all_entries}
        tickers = _filter_us_exchange([e.ticker for e in all_entries])
    except Exception as e:
        logger.error(f"Failed to fetch earnings calendar: {e}", exc_info=True)
        tickers = []
        entry_by_ticker = {}

    signals = []
    for ticker in tickers:
        try:
            entry_date = entry_by_ticker[ticker].date if ticker in entry_by_ticker else today
            surprise = get_earnings_surprise(ticker, date=entry_date)
            prior_runup = get_prior_runup(ticker)
            sector_move = get_sector_intraday_move(ticker, today)
            atr = get_atr(ticker)
            df = get_ohlcv(ticker, days=2)
            current_price = float(df["Close"].iloc[-1])
            prior_close = float(df["Close"].iloc[-2]) if len(df) >= 2 else current_price
            overnight_gap = (current_price / prior_close) - 1.0

            sig = evaluate_entry(
                ticker=ticker,
                surprise=surprise,
                ah_move=overnight_gap,
                prior_runup=prior_runup,
                sector_move=sector_move,
                atr=atr,
                current_price=current_price,
                open_positions=open_positions,
            )
            signals.append(sig)
            logger.info(f"{ticker}: should_enter={sig.should_enter}, gap={overnight_gap:.1%}, filters={sig.filters_passed}")

        except Exception as e:
            logger.error(f"Error processing {ticker}: {e}", exc_info=True)
            continue

    if signals:
        execute_signals(signals, [], mode=mode)

    # --- Combined notification ---
    lines = [f"*Morning Update — {today}*"]

    action_map = {a.ticker: a for a in actions}
    if positions:
        daily_pnl_total = sum(
            (current_prices[p.ticker] - prev_prices[p.ticker]) * p.quantity
            for p in positions
            if p.ticker in current_prices and p.ticker in prev_prices and p.quantity
        )
        daily_sign = "+" if daily_pnl_total >= 0 else ""
        lines.append(f"\n*Daily P&L: {daily_sign}${daily_pnl_total:.2f}*")
        lines.append("\n*Positions*")
        max_ticker_len = max(len(pos.ticker) for pos in positions)
        for pos in positions:
            price = current_prices.get(pos.ticker)
            act = action_map.get(pos.ticker)
            price_str = f"${price:.2f}" if price is not None else "n/a"
            if price is not None:
                pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                pnl_sign = "+" if pnl_pct >= 0 else ""
                detail = f"entry ${pos.entry_price:.2f} → {price_str} ({pnl_sign}{pnl_pct:.1f}%)"
            else:
                detail = f"entry ${pos.entry_price:.2f} → n/a"
            ticker_col = pos.ticker.ljust(max_ticker_len)
            if act and act.action == "sell":
                lines.append(f"📉 `{ticker_col}` — SELL | {detail} | {act.reason} (day {pos.day_count}/10)")
            elif act and act.action == "update_stop":
                lines.append(f"🔄 `{ticker_col}` — hold | {detail} | stop → ${act.new_stop:.2f} (day {pos.day_count}/10)")
            else:
                lines.append(f"⏸ `{ticker_col}` — hold | {detail} | stop ${pos.current_stop:.2f} (day {pos.day_count}/10)")

    # Count BUY signals for the summary line
    buys = [s for s in signals if s.should_enter]
    if buys:
        lines.append(f"\n*Earnings Scan* ({len(tickers)} tickers): {len(buys)} BUY signal(s) — see thread")
    elif not tickers:
        lines.append("\n*Earnings Scan*: no tickers evaluated.")
    else:
        lines.append(f"\n*Earnings Scan* ({len(tickers)} tickers): no entries — see thread")

    ts = notify("\n".join(lines))

    # Post full scan detail as a thread reply
    if signals and ts:
        scan_lines = [f"*Earnings Scan Detail — {today}* ({len(tickers)} tickers)"]
        keys = list(signals[0].filters_passed.keys())
        scan_lines.append("  " + " | ".join(keys))
        max_ticker_len = max(len(sig.ticker) for sig in signals)
        for sig in signals:
            checks = " ".join("✅" if v else "❌" for v in sig.filters_passed.values())
            ticker_col = sig.ticker.ljust(max_ticker_len)
            if sig.should_enter:
                scan_lines.append(f"📈 `{ticker_col}` — BUY @ ${sig.entry_price:.2f} | stop ${sig.initial_stop:.2f}  {checks}")
            else:
                scan_lines.append(f"➖ `{ticker_col}`  {checks}")
        notify_thread(ts, "\n".join(scan_lines))



def run_monthly_pnl_summary() -> None:
    """1st of each month, 9:30 AM ET — post last month's realized PnL summary to Slack."""
    now = datetime.now(EASTERN)
    # Last month's date range
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = first_of_this_month
    last_month_start = (first_of_this_month - timedelta(days=1)).replace(day=1)
    month_label = last_month_start.strftime("%B %Y")
    logger.info(f"=== Monthly PnL Summary: {last_month_start.date()} to {last_month_end.date()} ===")

    trades_path = Path(__file__).parent.parent / "data" / "trades_log.jsonl"
    if not trades_path.exists():
        notify(f"*Monthly PnL Summary — {month_label}*: no trades log found.")
        return

    buys: dict[str, list[dict]] = defaultdict(list)
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
            elif t["action"] == "sell" and last_month_start <= ts < last_month_end:
                ticker = t["ticker"]
                buy = buys[ticker].pop(0) if buys[ticker] else None
                closed_trades.append({
                    "ticker": ticker,
                    "buy_price": buy["price"] if buy else None,
                    "sell_price": t["fill_price"],
                    "qty": t["quantity"],
                    "sell_ts": ts,
                })

    lines = [f"*Monthly PnL Summary — {month_label}*"]

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
        lines.append("No closed trades last month.")

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
        import time
        time.sleep(0.3)
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
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(check, t): t for t in candidates}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    return sorted(results)


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
        day_of_week="mon-fri",
        hour=9,
        minute=30,
        kwargs={"mode": mode},
        id="scan_cycle",
        name="Earnings Scan @ 9:30 AM ET",
        misfire_grace_time=60,
    )
    scheduler.add_job(
        run_weekly_pnl_summary,
        trigger="cron",
        day_of_week="sun",
        hour=9,
        minute=30,
        id="weekly_pnl_summary",
        name="Weekly PnL Summary @ 9:30 AM ET Sunday",
        misfire_grace_time=300,
    )
    scheduler.add_job(
        run_monthly_pnl_summary,
        trigger="cron",
        day=1,
        hour=9,
        minute=30,
        id="monthly_pnl_summary",
        name="Monthly PnL Summary @ 9:30 AM ET on 1st",
        misfire_grace_time=3600,
    )

    logger.info(f"Scheduler starting in {mode!r} mode.")
    logger.info("  Scan cycle:          9:30 AM ET Mon-Fri (exit positions + scan entries)")
    logger.info("  Weekly PnL summary:  9:30 AM ET Sunday (last week's realized PnL)")
    logger.info("  Monthly PnL summary: 9:30 AM ET 1st of month (last month's realized PnL)")
    scheduler.start()
