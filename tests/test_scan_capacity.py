from types import SimpleNamespace
from unittest.mock import patch

import scheduler
from config import MAX_POSITIONS
from decision import EntrySignal


def test_scan_counts_accepted_entries_against_max_positions():
    # MAX_POSITIONS - 2 held, five tickers that pass every other filter → only two accepted
    held = [SimpleNamespace(ticker=f"H{i}", quantity=1, mode="paper", day_count=1, entry_price=9.0, current_stop=5.0) for i in range(MAX_POSITIONS - 2)]
    tickers = ["A", "B", "C", "D", "E"]

    def fake_entry(ticker, open_positions, current_price, **_):
        ok = len(open_positions) < MAX_POSITIONS
        return EntrySignal(ticker=ticker, should_enter=ok, filters_passed={"capacity": ok},
                           entry_price=current_price, initial_stop=current_price - 1 if ok else None)

    captured = {}
    with patch.object(scheduler, "load_positions", return_value=held), \
         patch.object(scheduler, "save_positions"), \
         patch.object(scheduler, "get_latest_price", return_value=10.0), \
         patch.object(scheduler, "get_prior_close", return_value=9.0), \
         patch.object(scheduler, "get_atr", return_value=0.5), \
         patch.object(scheduler, "evaluate_positions", return_value=[]), \
         patch.object(scheduler, "get_prior_session_date", return_value="2026-10-21"), \
         patch.object(scheduler, "get_earnings_calendar_details", return_value=[]), \
         patch.object(scheduler, "reacting_today",
                      return_value=[SimpleNamespace(ticker=t, date="2026-10-21", eps_estimate=1.0) for t in tickers]), \
         patch.object(scheduler, "_filter_us_exchange", side_effect=lambda ts: ts), \
         patch.object(scheduler, "get_earnings_surprise"), \
         patch.object(scheduler, "get_prior_runup", return_value=0.0), \
         patch.object(scheduler, "get_sector_intraday_move", return_value=0.0), \
         patch.object(scheduler, "get_today_open", return_value=10.0), \
         patch.object(scheduler, "evaluate_entry", side_effect=fake_entry), \
         patch.object(scheduler, "execute_signals",
                      side_effect=lambda sigs, *a, **k: captured.setdefault("sigs", sigs) if sigs else None), \
         patch.object(scheduler, "notify"), patch.object(scheduler, "notify_thread"), \
         patch.object(scheduler, "_get_spy_return_daily", return_value=None):
        scheduler.run_scan_cycle(mode="paper")
    assert [s.ticker for s in captured.get("sigs", []) if s.should_enter] == ["A", "B"]
