"""Report figures from testbed/run_stream.sh results.

  plots.py <results_dir> <out_dir>

Reads <results_dir>/<scenario>/<scheduler>/<run_id>/{summary,frames}.csv and
writes three PNGs, plus a csv with the numbers behind them (the table view):
  miss_vs_delay.png   deadline-miss rate vs playback delay, per scenario
  miss_by_type.png    I-frame and P-frame miss rate at the default delay
  fct_cdf.png         distribution of frame completion time, per scenario
  figures_data.csv
Each scheduler keeps one color and marker in every figure.
"""

import csv
import statistics as st
import sys
from pathlib import Path

import warnings

import matplotlib

warnings.filterwarnings("ignore", message="Unable to import Axes3D")
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SCENARIOS = ["symmetric", "rtt_asym", "low_bw", "trace", "outage"]
# Fixed order = fixed color slot (dataviz reference palette, light mode, all
# eight slots validated: adjacent CVD dE >= 9.1). Markers are the second,
# non-color cue.
SCHEDULERS = [
    ("learned", "learned (ours)", "#2a78d6", "o"),
    ("deadline", "hand-tuned deadline", "#eb6834", "s"),
    ("framepin", "frame-pin", "#1baf7a", "^"),
    ("minrtt", "MinRTT", "#eda100", "D"),
    ("ecf", "ECF", "#e87ba4", "v"),
    ("roundrobin", "round-robin", "#008300", "P"),
    ("redundant", "redundant", "#6250d6", "X"),
    ("single", "single path (A)", "#e34948", "*"),
]
SWEEP = [150, 300, 500, 1000]
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def style():
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "text.color": INK,
        "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID,
        "grid.linewidth": 0.6, "axes.axisbelow": True, "axes.spines.top": False, "axes.spines.right": False,
        "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold", "legend.frameon": False,
    })


def load(results):
    runs = {}  # (scenario, scheduler) -> list of summary rows
    for f in sorted(Path(results).glob("*/*/*/summary.csv")):
        with open(f) as fh:
            r = next(csv.DictReader(fh))
        if "fct_med_ms" in r:
            runs.setdefault((r["scenario"], r["scheduler"]), []).append((f.parent, r))
    return runs


def med(rows, col):
    v = [float(r[col]) for _, r in rows if r[col] not in ("", "nan")]
    return st.median(v) if v else float("nan")


def present(runs, scen):
    return [s for s in SCHEDULERS if (scen, s[0]) in runs]


def fig_miss_vs_delay(runs, scens, out, table):
    fig, axes = plt.subplots(1, len(scens), figsize=(2.6 * len(scens), 2.9), sharey=True, squeeze=False)
    for ax, scen in zip(axes[0], scens):
        for key, label, color, marker in present(runs, scen):
            rows = runs[(scen, key)]
            ys = [med(rows, f"miss{d}_pct") for d in SWEEP]
            ax.plot(range(len(SWEEP)), ys, color=color, marker=marker, lw=1.6, ms=5,
                    markeredgecolor=SURFACE, markeredgewidth=0.8, label=label)
            for d, y in zip(SWEEP, ys):
                table.append({"figure": "miss_vs_delay", "scenario": scen, "scheduler": key,
                              "x": d, "value": round(y, 2), "runs": len(rows)})
        ax.set_title(scen, loc="left")
        ax.set_xticks(range(len(SWEEP)), [str(d) for d in SWEEP])
        ax.set_xlabel("playback delay (ms)")
        ax.set_ylim(-3, 103)
    axes[0][0].set_ylabel("frames missing deadline (%)")
    handles, labels = axes[0][-1].get_legend_handles_labels()
    for ax in axes[0]:
        h, l = ax.get_legend_handles_labels()
        if len(h) > len(handles):
            handles, labels = h, l
    fig.legend(handles, labels, loc="upper center", ncol=len(labels), bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(out / "miss_vs_delay.png", dpi=200)
    plt.close(fig)


def fig_miss_by_type(runs, scens, out, table):
    fig, axes = plt.subplots(2, len(scens), figsize=(2.6 * len(scens), 4.6), sharey=True, squeeze=False)
    for col, scen in enumerate(scens):
        sch = present(runs, scen)
        for row, (ftype, colname) in enumerate([("I-frames", "miss_I_pct"), ("P-frames", "miss_P_pct")]):
            ax = axes[row][col]
            ys = [med(runs[(scen, s[0])], colname) for s in sch]
            ax.bar(range(len(sch)), ys, width=0.72, color=[s[2] for s in sch],
                   edgecolor=SURFACE, linewidth=1.5)
            for i, y in enumerate(ys):
                ax.text(i, y + 2, f"{y:.0f}", ha="center", va="bottom", fontsize=7, color=INK2)
                table.append({"figure": f"miss_{ftype}", "scenario": scen, "scheduler": sch[i][0],
                              "x": "", "value": round(y, 2), "runs": len(runs[(scen, sch[i][0])])})
            ax.set_xticks(range(len(sch)), [s[1].split(" ")[0] for s in sch], rotation=55,
                          ha="right", fontsize=7)
            ax.grid(axis="x", visible=False)
            ax.set_ylim(0, 112)
            if row == 0:
                ax.set_title(scen, loc="left")
            if col == 0:
                ax.set_ylabel(f"{ftype} missing (%)")
    fig.tight_layout()
    fig.savefig(out / "miss_by_type.png", dpi=200)
    plt.close(fig)


def fig_fct_cdf(runs, scens, out, deadline_ms=300):
    fig, axes = plt.subplots(1, len(scens), figsize=(2.6 * len(scens), 2.9), sharey=True, squeeze=False)
    for ax, scen in zip(axes[0], scens):
        for key, label, color, marker in present(runs, scen):
            fct = []
            for run_dir, _ in runs[(scen, key)]:
                with open(run_dir / "frames.csv") as fh:
                    for r in csv.DictReader(fh):
                        if r["complete_ts_us"]:
                            sched = int(r["deadline_us"]) - deadline_ms * 1000
                            fct.append(max((int(r["complete_ts_us"]) - sched) / 1000, 1.0))
            fct.sort()
            n = len(fct)
            ax.plot(fct, [100 * (i + 1) / n for i in range(n)], color=color, lw=1.6, label=label)
            # Marker at the 95th percentile ties the line to the legend shape.
            p95 = fct[int(0.95 * (n - 1))]
            ax.plot([p95], [95], marker=marker, color=color, ms=5, markeredgecolor=SURFACE)
        ax.axvline(deadline_ms, color=INK2, lw=0.8, ls=(0, (3, 3)))
        ax.text(deadline_ms * 1.08, 4, f"{deadline_ms} ms", color=INK2, fontsize=7,
                bbox=dict(facecolor=SURFACE, edgecolor="none", pad=1))
        ax.set_xscale("log")
        ax.set_title(scen, loc="left")
        ax.set_xlabel("frame completion time (ms)")
    axes[0][0].set_ylabel("frames completed (%)")
    handles, labels = [], []
    for ax in axes[0]:
        h, l = ax.get_legend_handles_labels()
        if len(h) > len(handles):
            handles, labels = h, l
    fig.legend(handles, labels, loc="upper center", ncol=len(labels), bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(out / "fct_cdf.png", dpi=200)
    plt.close(fig)


def main():
    results, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    style()
    runs = load(results)
    scens = [s for s in SCENARIOS if any(k[0] == s for k in runs)]
    table = []
    fig_miss_vs_delay(runs, scens, out, table)
    fig_miss_by_type(runs, scens, out, table)
    fig_fct_cdf(runs, scens, out)
    with open(out / "figures_data.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["figure", "scenario", "scheduler", "x", "value", "runs"])
        w.writeheader()
        w.writerows(table)
    print(f"wrote 3 figures and figures_data.csv to {out} ({len(runs)} scenario/scheduler groups)")


if __name__ == "__main__":
    main()
