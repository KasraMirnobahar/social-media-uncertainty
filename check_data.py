"""
check_data.py — verify a participant's logged data is internally consistent and
analysis-ready. Stdlib only (no pandas needed).

Usage:
    python check_data.py            # checks the most recent participant in data/
    python check_data.py P20260616T...-abc123   # checks a specific pid

It loads the three files written by app.py for one participant:
    data/{pid}.csv            event log   (one row per event)
    data/{pid}_schedule.csv   design      (30 rows, the counterbalanced plan)
    data/{pid}_rounds.csv     round summary(one row per completed round)
and asserts they agree with each other and with the counterbalancing design.
Exits non-zero if any check fails.
"""

import csv
import os
import sys
import glob
from collections import Counter
from datetime import datetime

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

_fail = 0
_pass = 0


def check(name, ok, detail=""):
    global _fail, _pass
    mark = "PASS" if ok else "FAIL"
    if ok:
        _pass += 1
    else:
        _fail += 1
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""))


def load(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def pick_pid(pid=None):
    if pid:
        return pid
    scheds = sorted(glob.glob(os.path.join(DATA, "*_schedule.csv")))
    if not scheds:
        sys.exit(f"No participants found in {DATA}")
    return os.path.basename(scheds[-1])[: -len("_schedule.csv")]


def main():
    pid = pick_pid(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"Checking participant: {pid}\n")

    ev_path = os.path.join(DATA, f"{pid}.csv")
    sc_path = os.path.join(DATA, f"{pid}_schedule.csv")
    rd_path = os.path.join(DATA, f"{pid}_rounds.csv")
    for p in (ev_path, sc_path, rd_path):
        if not os.path.exists(p):
            sys.exit(f"Missing file: {p}")

    events = load(ev_path)
    sched = load(sc_path)
    rounds = load(rd_path)
    n = len(sched)

    # --- identity columns present on every event row ---------------------
    bad_id = [e for e in events if e["pid"] != pid or not e["group"]]
    check("group + pid present on every event row", not bad_id,
          f"{len(bad_id)} rows missing pid/group" if bad_id else f"{len(events)} rows ok")

    # --- timestamps are timezone-aware -----------------------------------
    tz_ok = True
    for e in events:
        try:
            if datetime.fromisoformat(e["timestamp"]).tzinfo is None:
                tz_ok = False
                break
        except ValueError:
            tz_ok = False
            break
    check("timestamps are timezone-aware ISO-8601", tz_ok,
          f"e.g. {events[0]['timestamp']}")

    # --- schedule integrity / counterbalancing ---------------------------
    sc_rounds = sorted(int(r["round"]) for r in sched)
    check("schedule covers rounds 1..N exactly once", sc_rounds == list(range(1, n + 1)),
          f"n={n}")

    level_counts = Counter(r["reward_level"] for r in sched)
    levels = sorted(level_counts)
    balanced_levels = len(set(level_counts.values())) == 1 and all(
        v == n // len(level_counts) for v in level_counts.values())
    check("reward levels balanced across schedule", balanced_levels, dict(level_counts))

    # delay x level independence: within each delay, levels equally represented
    by_delay = {}
    for r in sched:
        by_delay.setdefault(r["delay_s"], Counter())[r["reward_level"]] += 1
    indep = all(len(set(c.values())) == 1 for c in by_delay.values())
    check("delay x reward-level independent (counterbalanced)", indep,
          {d: dict(c) for d, c in sorted(by_delay.items())})

    # --- session completion ----------------------------------------------
    finished = any(e["event"] == "finish" for e in events)
    rd_rounds = sorted(int(r["round"]) for r in rounds)
    if finished:
        check("completed session has all N rounds in summary",
              rd_rounds == list(range(1, n + 1)),
              f"{len(rd_rounds)}/{n} rounds")
        revealed_rounds = sorted(int(e["round"]) for e in events
                                 if e["event"] == "reward_revealed")
        check("completed session has a reward_revealed for every round",
              revealed_rounds == list(range(1, n + 1)),
              f"{len(revealed_rounds)}/{n}")
    else:
        print(f"  [INFO] session not finished (partial) — {len(rd_rounds)} rounds completed")

    # --- per-round cross-file consistency --------------------------------
    checks_in_log = Counter(int(e["round"]) for e in events if e["event"] == "check")
    sched_reward = {int(r["round"]): int(r["reward_value"]) for r in sched}
    sched_delay = {int(r["round"]): int(r["delay_s"]) for r in sched}
    reveal_likes = {int(e["round"]): int(e["extra"].split("=")[1])
                    for e in events if e["event"] == "reward_revealed"}

    counts_ok = rewards_ok = delays_ok = lat_ok = reveal_ok = True
    for r in rounds:
        rnd = int(r["round"])
        total = int(r["total_checks"])
        # 1) total checks in summary == count of check rows in event log
        if total != checks_in_log.get(rnd, 0):
            counts_ok = False
        # 2) reward value agrees across schedule / summary / reveal event
        if not (int(r["reward_value"]) == sched_reward[rnd] == reveal_likes.get(rnd)):
            rewards_ok = False
        # 3) delay agrees between summary and schedule
        if int(r["delay_s"]) != sched_delay[rnd]:
            delays_ok = False
        # 4) latencies: count matches, first matches, non-decreasing
        lats = [float(x) for x in r["all_check_latencies_s"].split(";") if x]
        if len(lats) != total:
            lat_ok = False
        elif lats:
            if abs(lats[0] - float(r["first_check_latency_s"])) > 1e-6:
                lat_ok = False
            if any(lats[i] < lats[i - 1] for i in range(1, len(lats))):
                lat_ok = False
        # 5) exactly one is_reveal==1 check row in this round
        n_reveal = sum(1 for e in events if e["event"] == "check"
                       and int(e["round"]) == rnd and e["is_reveal"] == "1")
        if n_reveal != 1:
            reveal_ok = False

    check("summary total_checks == #check rows in event log (per round)", counts_ok)
    check("reward_value matches across schedule / summary / reveal event", rewards_ok)
    check("delay_s matches between summary and schedule", delays_ok)
    check("check latencies consistent (count, first, monotonic)", lat_ok)
    check("exactly one revealing check (is_reveal=1) per completed round", reveal_ok)

    # --- event-log delay_s matches schedule (every round-tagged row) -----
    ev_delay_ok = True
    for e in events:
        if e["round"] and e["delay_s"]:
            if int(e["delay_s"]) != sched_delay.get(int(e["round"]), -1):
                ev_delay_ok = False
                break
    check("event-log delay_s matches schedule on every round row", ev_delay_ok)

    print(f"\n{_pass} passed, {_fail} failed.")
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
