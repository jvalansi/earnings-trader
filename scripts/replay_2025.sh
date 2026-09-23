#!/usr/bin/env bash
# Backtest replays used to date the strategy's edge, all run with the CURRENT backtester
# so the periods are comparable. The published 2022-24 figures predate the BMO/AMC entry
# fix (7662a3c), so they cannot be compared against 2025/2026 runs directly.
#
# FMP's free tier caps at 250 requests/day, so one pass cannot cover every range. The disk
# cache persists, so each daily run advances coverage. Runs right after the 9:30 ET scan so
# the live cycle gets first claim on the day's quota.
#
# This job is deliberately quiet: it posts to Discord only when there is news — a range
# newly reaches full coverage, every range does (time to delete the job), or a range
# starts failing. On an ordinary day it says nothing, so the 9:30 ET scan report is the
# only message you get. Coverage state lives in data/replay_coverage.json.
set -uo pipefail
cd /home/ubuntu/earnings-trader

PYTHON=/home/ubuntu/miniconda3/envs/earnings-trader/bin/python
STATE=data/replay_coverage.json
RESULTS=$(mktemp)
trap 'rm -f "$RESULTS"' EXIT

# A range that already reports full coverage is not re-run: it costs no API quota but it
# does cost ~8 min of replay, which is what pushed this job past its cron timeout.
# Its metrics are read back from the state file instead.
is_complete() {
  "$PYTHON" - "$STATE" "$1" <<'PY'
import json, sys
try:
    state = json.load(open(sys.argv[1]))
except Exception:
    state = {}
r = state.get("ranges", {}).get(sys.argv[2], {})
sys.exit(0 if r.get("complete") else 1)
PY
}

run_range() {
  local label="$1" start="$2" end="$3"
  local log="/tmp/bt_${label}.log"
  PYTHONPATH=src "$PYTHON" -m backtest.runner --start "$start" --end "$end" > "$log" 2>&1
  local rc=$?
  printf '%s\t%s\n' "$label" "$rc" >> "$RESULTS"
  echo "[$(date -u '+%H:%M:%S')] $label exit=$rc"
}

echo "=== replay: $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="

for spec in "2022_2023 2022-01-01 2023-12-31" "2024 2024-01-01 2024-12-31"; do
  set -- $spec
  if is_complete "$1"; then
    echo "SKIP $1 — already fully covered, reusing recorded metrics"
  else
    run_range "$1" "$2" "$3"
  fi
done

# 2025 only starts once 2024 is fully cached. Before that the earlier ranges have already
# spent the 250-request budget, so every 2025 run dies on a 429 partway through and burns
# the tail of the quota for nothing.
if is_complete 2024; then
  if is_complete 2025; then
    echo "SKIP 2025 — already fully covered, reusing recorded metrics"
  else
    run_range 2025 2025-01-01 2025-12-31
  fi
else
  echo "HOLD 2025 — waiting for 2024 to reach full coverage (quota)"
fi

"$PYTHON" - "$STATE" "$RESULTS" <<'PY'
"""Diff this run's coverage against the last one and post only if something changed."""
import json, os, re, sys

sys.path.insert(0, "src")
import config  # noqa: F401  — load_dotenv() puts DISCORD_BOT_TOKEN in the environment
from notifier import notify

state_path, results_path = sys.argv[1], sys.argv[2]
RANGES = ["2022_2023", "2024", "2025"]
METRIC = re.compile(r"Trades \(closed\)|Win rate|Avg return|Sharpe")

try:
    with open(state_path) as f:
        state = json.load(f)
except (OSError, ValueError):
    state = {}
ranges = state.setdefault("ranges", {})

ran = {}
with open(results_path) as f:
    for line in f:
        label, rc = line.split("\t")
        ran[label] = int(rc)

news, lines = [], []
for label in RANGES:
    prev = ranges.get(label, {})
    if label not in ran:
        if prev.get("complete"):
            lines.append(f"--- {label}: complete (cached) ---")
            lines += prev.get("metrics", [])
        continue

    log = f"/tmp/bt_{label}.log"
    try:
        text = open(log).read()
    except OSError:
        text = ""
    fetched = text.count("FMP earnings for")
    skipped = text.count("Could not fetch earnings calendar")
    metrics = [l.rstrip() for l in text.splitlines() if METRIC.search(l)]
    # Full coverage only counts if the run also produced results — a crash before the
    # first fetch also leaves 0 skipped, and must not be recorded as done.
    complete = skipped == 0 and bool(metrics)
    # A 429 crash is the daily quota wall, not a fault — it is the normal way an
    # uncovered range ends once the 250-request budget is gone. Only report other faults.
    failed = (ran[label] != 0 or "Traceback" in text) and "429" not in text

    if complete and not prev.get("complete"):
        news.append(f"{label} reached full coverage")
    if failed and not prev.get("failed"):
        news.append(f"{label} started failing (exit {ran[label]})")

    ranges[label] = {
        "fetched": fetched,
        "skipped": skipped,
        "complete": complete,
        "failed": failed,
        "metrics": metrics,
    }
    if failed:
        status = "run failed"
    elif complete:
        status = "complete"
    else:
        status = f"{skipped} days still uncovered"
    lines.append(f"--- {label}: {fetched} days fetched, {status} ---")
    lines += metrics
    if failed:
        lines.append(f"    FAILED (exit {ran[label]}) — see {log}")

all_done = all(ranges.get(l, {}).get("complete") for l in RANGES)
if all_done and not state.get("announced_done"):
    news.append("every range is fully covered — delete this job with `cc-connect cron del 3de3708e`")
    state["announced_done"] = True

with open(state_path, "w") as f:
    json.dump(state, f, indent=2)

if news:
    notify("**Backtest replay coverage**\n" + "; ".join(news) + "\n```\n" + "\n".join(lines) + "\n```")
    print("POSTED: " + "; ".join(news))
else:
    print("quiet — no coverage change")
PY
