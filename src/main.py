"""
Entry point.

    python src/main.py                 start the scheduler (default: `run`)
    python src/main.py run             same, explicit
    python src/main.py preflight       check broker connectivity, risk state, position drift
    python src/main.py track-record    performance stats + go/no-go verdict
    python src/main.py halt "reason"   block new entries (exits keep running)
    python src/main.py resume          clear the kill switch and any latched risk breach
"""
import argparse
import logging
import sys

import broker
import risk
from config import TRADING_MODE, ACCOUNT_CAPITAL_USD, POSITION_SIZE_USD, MAX_POSITIONS
from state import load_positions


def _preflight(mode: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    info = broker.preflight(mode)
    print(broker.summary_line(info))
    print(
        f"   capital ${ACCOUNT_CAPITAL_USD:,.0f} | {MAX_POSITIONS} slots x "
        f"${POSITION_SIZE_USD:,.0f} per position"
    )

    status = risk.evaluate_risk(persist=False)
    print(risk.status_line(status))

    positions = load_positions()
    drifted = False
    if info.mode == "sim":
        print(f"   {len(positions)} local position(s); no broker to reconcile against")
    else:
        drift = broker.reconcile(positions, mode)
        drifted = any(drift.values())
        broker_held = [p for p in positions if p.mode != "sim"]
        if drifted:
            print(f"🛑 Position mismatch — local vs broker: {drift}")
        else:
            print(
                f"🟢 Positions reconciled — {len(broker_held)} broker position(s) match; "
                f"{len(positions) - len(broker_held)} simulated position(s) ignored"
            )

    return 1 if (info.blocked or drifted or not status.entries_allowed) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PEAD earnings trader")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start the scheduler")
    run.add_argument("--mode", default=TRADING_MODE, choices=["paper", "live"])

    pre = sub.add_parser("preflight", help="check broker, risk state and position drift")
    pre.add_argument("--mode", default=TRADING_MODE, choices=["paper", "live"])

    tr = sub.add_parser("track-record", help="performance stats and go/no-go verdict")
    tr.add_argument("--since", default=None, help="only trades entered on/after this date")

    h = sub.add_parser("halt", help="block new entries")
    h.add_argument("reason", nargs="?", default="")

    sub.add_parser("resume", help="clear the kill switch and latched risk breach")

    args = parser.parse_args()
    command = args.command or "run"

    if command == "run":
        from scheduler import start
        start(mode=getattr(args, "mode", TRADING_MODE))
        return 0
    if command == "preflight":
        return _preflight(args.mode)
    if command == "track-record":
        from analysis.track_record import report
        return report(since=args.since)
    if command == "halt":
        risk.halt(args.reason)
        print(f"🛑 Entries halted{f': {args.reason}' if args.reason else ''}. Exits still run.")
        return 0
    if command == "resume":
        risk.resume()
        print("🟢 Entries re-enabled.")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
