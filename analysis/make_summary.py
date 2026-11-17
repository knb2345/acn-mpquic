"""Build analysis/summary_final.csv: one row per analysed run.

  make_summary.py [--results results] [--out analysis/summary_final.csv]

Runs are the directories results/<scenario>/<config>/<bitrate>_<enc>_<clip>/<run_id>/
of the five synthetic scenarios (one row per directory, so a run can never be
counted twice). Only run ids 1-8 are analysed (the paired design); run ids 0,
91 and >= 900 are development and demo runs. results/_superseded/ is never read.
A run that is still being written (no meta.json, or meta.json without the
exit codes run.sh adds at the end) is skipped.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from summarize import summarize  # noqa: E402

SCENARIOS = ["control", "symmetric", "rtt_asym", "bw_asym", "outage"]


def runs(results):
    for scen in SCENARIOS:
        for f in sorted((results / scen).glob("*/*/*/frames.csv")):
            run = f.parent
            if not run.name.isdigit() or not 1 <= int(run.name) <= 8:
                continue
            try:
                meta = json.loads((run / "meta.json").read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            if "sender_exit" not in meta:
                continue
            yield run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--out", type=Path, default=Path("analysis/summary_final.csv"))
    a = ap.parse_args()
    rows = []
    for run in runs(a.results):
        r = summarize(run)
        m = json.loads((run / "meta.json").read_text())
        r["repo_git"] = m.get("repo_git", "")
        rows.append(r)
    if not rows:
        sys.exit("no runs found")
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} runs -> {a.out}")


if __name__ == "__main__":
    main()
