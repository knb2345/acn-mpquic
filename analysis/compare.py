"""Paired comparison of one scheduler against the others.

  compare.py <results_dir> [scheduler, default learned] [metric, default miss_pct]

Runs with the same scenario and run_id see the same network (same trace
offset), so the scheduler is compared run by run: for each baseline, the
mean difference (scheduler - baseline) over the paired runs, and in how many
runs the scheduler was better, equal or worse. Lower is better for every
metric this is meant for (miss rates, completion times, stall time).
"""

import csv
import statistics as st
import sys
from pathlib import Path

ORDER = ["learned", "deadline", "framepin", "minrtt", "ecf", "roundrobin", "redundant", "single"]
SCENARIOS = ["symmetric", "rtt_asym", "low_bw", "trace", "outage"]


def main():
    results = Path(sys.argv[1])
    ours = sys.argv[2] if len(sys.argv) > 2 else "learned"
    metric = sys.argv[3] if len(sys.argv) > 3 else "miss_pct"
    val = {}  # (scenario, scheduler, run_id) -> value
    for f in results.glob("*/*/*/summary.csv"):
        with open(f) as fh:
            r = next(csv.DictReader(fh))
        if metric in r and r[metric] not in ("", "nan"):
            val[(r["scenario"], r["scheduler"], int(r["run_id"]))] = float(r[metric])

    print(f"{metric}: {ours} minus baseline, paired by run (negative = {ours} better)")
    print(f"{'scenario':10s} {'baseline':11s} {'pairs':>5s} {'mean diff':>10s} {'better/equal/worse':>19s}")
    for scen in SCENARIOS:
        runs = sorted(k[2] for k in val if k[0] == scen and k[1] == ours)
        for base in ORDER:
            if base == ours:
                continue
            d = [val[(scen, ours, r)] - val[(scen, base, r)] for r in runs if (scen, base, r) in val]
            if not d:
                continue
            b = sum(x < 0 for x in d)
            e = sum(x == 0 for x in d)
            print(f"{scen:10s} {base:11s} {len(d):5d} {st.mean(d):+10.2f} {f'{b}/{e}/{len(d) - b - e}':>19s}")


if __name__ == "__main__":
    main()
