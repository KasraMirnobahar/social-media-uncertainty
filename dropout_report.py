"""
dropout_report.py — count completers vs drop-outs across all participants.

Stdlib only. Scans data/ and classifies everyone who STARTED (i.e. has a
{pid}_schedule.csv) as:
    completer        - event log contains a 'finish' event
    dropout_explicit - no 'finish', but a 'task_exit' row (pressed Exit)
    dropout_silent   - no 'finish' and no 'task_exit' (closed the tab)

Usage:  python dropout_report.py
"""

import csv
import os
import glob
from collections import Counter, defaultdict

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def load(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main():
    scheds = sorted(glob.glob(os.path.join(DATA, "*_schedule.csv")))
    if not scheds:
        print(f"No participants found in {DATA}")
        return

    rows = []
    for sc in scheds:
        pid = os.path.basename(sc)[: -len("_schedule.csv")]
        sched = load(sc)
        group = sched[0]["group"] if sched else "?"
        n_design = len(sched)

        ev_path = os.path.join(DATA, f"{pid}.csv")
        events = load(ev_path) if os.path.exists(ev_path) else []
        finished = any(e["event"] == "finish" for e in events)
        exited = any(e["event"] == "task_exit" for e in events)
        rounds_done = sum(1 for e in events if e["event"] == "reward_revealed")

        if finished:
            status = "completer"
        elif exited:
            status = "dropout_explicit"
        else:
            status = "dropout_silent"

        rows.append({"pid": pid, "group": group, "status": status,
                     "rounds_done": rounds_done, "n_design": n_design})

    # ---- per-participant table ----
    print(f"{'pid':<26} {'group':<10} {'status':<17} rounds_done/total")
    print("-" * 72)
    for r in sorted(rows, key=lambda x: x["pid"]):
        print(f"{r['pid']:<26} {r['group']:<10} {r['status']:<17} "
              f"{r['rounds_done']}/{r['n_design']}")

    # ---- totals ----
    total = len(rows)
    by_status = Counter(r["status"] for r in rows)
    dropouts = by_status["dropout_explicit"] + by_status["dropout_silent"]

    print("\n=== Totals ===")
    print(f"started            : {total}")
    print(f"completers         : {by_status['completer']}")
    print(f"dropout (explicit) : {by_status['dropout_explicit']}")
    print(f"dropout (silent)   : {by_status['dropout_silent']}")
    print(f"dropout rate       : {dropouts}/{total} "
          f"({100*dropouts/total:.1f}%)" if total else "n/a")

    # ---- by group (relevant to the control vs treatment comparison) ----
    print("\n=== By group ===")
    g_started = Counter(r["group"] for r in rows)
    g_drop = defaultdict(int)
    for r in rows:
        if r["status"].startswith("dropout"):
            g_drop[r["group"]] += 1
    for g in sorted(g_started):
        s = g_started[g]
        d = g_drop[g]
        print(f"{g:<10} started={s:<4} dropouts={d:<4} "
              f"rate={100*d/s:.1f}%")


if __name__ == "__main__":
    main()
