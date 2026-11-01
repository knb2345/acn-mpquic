"""Every number the report quotes, computed from the results tree.

  report_numbers.py <results_dir> [out.json]

Per scenario and scheduler: medians over runs of the summary metrics. Per
scenario: paired comparison of OURS (`learned`) with each other scheduler (same
run_id = same network) on miss rate, I-frame miss rate and stall time.
Prints markdown tables; writes the same data as json if a path is given.
"""

import csv
import json
import statistics as st
import sys
from pathlib import Path

SCENARIOS = ["symmetric", "rtt_asym", "low_bw", "trace", "outage"]
SCHEDULERS = ["learned", "deadline", "framepin", "minrtt", "ecf", "roundrobin", "redundant", "single"]
OURS = "learned"
METRICS = ["miss_pct", "miss_I_pct", "miss_P_pct", "miss150_pct", "miss500_pct", "miss1000_pct",
           "fct_med_ms", "fct_p95_ms", "stalls", "stall_ms", "share_path1_pct", "split_frames_pct"]
PAIRED = ["miss_pct", "miss_I_pct", "stall_ms"]


def main():
    results = Path(sys.argv[1])
    rows = {}
    for f in results.glob("*/*/*/summary.csv"):
        if f.parts[-4] not in SCENARIOS:  # skip archived trees (week*_, deadline_v1)
            continue
        with open(f) as fh:
            r = next(csv.DictReader(fh))
        rows[(r["scenario"], r["scheduler"], int(r["run_id"]))] = r

    out = {"medians": {}, "paired": {}}
    for scen in SCENARIOS:
        present = [s for s in SCHEDULERS if any(k[:2] == (scen, s) for k in rows)]
        if not present:
            continue
        print(f"\n## {scen}\n")
        print("| scheduler | runs | incomplete | " + " | ".join(METRICS) + " |")
        print("|" + "---|" * (len(METRICS) + 3))
        out["medians"][scen] = {}
        for s in present:
            rs = [r for k, r in rows.items() if k[:2] == (scen, s)]
            m = {"runs": len(rs), "incomplete": sum(int(r["stalled_run"]) for r in rs)}
            for c in METRICS:
                v = [float(r[c]) for r in rs if r[c] not in ("", "nan")]
                m[c] = round(st.median(v), 2) if v else None
            out["medians"][scen][s] = m
            print(f"| {s} | {m['runs']} | {m['incomplete']} | " + " | ".join(str(m[c]) for c in METRICS) + " |")
        if OURS not in present:
            continue
        out["paired"][scen] = {}
        print(f"\npaired, {OURS} minus baseline (better/equal/worse, mean difference):\n")
        print("| baseline | " + " | ".join(PAIRED) + " |")
        print("|---|" + "---|" * len(PAIRED))
        runs = sorted(k[2] for k in rows if k[:2] == (scen, OURS))
        for base in present:
            if base == OURS:
                continue
            cells, rec = [], {}
            for c in PAIRED:
                d = [float(rows[(scen, OURS, r)][c]) - float(rows[(scen, base, r)][c])
                     for r in runs if (scen, base, r) in rows]
                b, e = sum(x < 0 for x in d), sum(x == 0 for x in d)
                rec[c] = {"pairs": len(d), "better": b, "equal": e, "worse": len(d) - b - e,
                          "mean_diff": round(st.mean(d), 2) if d else None}
                cells.append(f"{b}/{e}/{len(d) - b - e}, {rec[c]['mean_diff']:+}" if d else "-")
            out["paired"][scen][base] = rec
            print(f"| {base} | " + " | ".join(cells) + " |")
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
