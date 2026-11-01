"""Evaluate the bandwidth predictor against simpler estimates, offline.

  eval_predictor.py <model.npz> <dataset.npz> [--by scenario|sched] [--json out.json]
  eval_predictor.py --parity <model.npz> <run_dir>

The dataset (features.py) holds, per path and 100 ms tick, the model input and
the true mean capacity over the next 0.5 s. For each estimator we report:
  over%     how often it overestimates the capacity (what makes a deadline
            scheduler put too much on a path and miss)
  under     mean shortfall below capacity, as a share of capacity, when it
            underestimates (the price of caution: needless splitting/avoidance)
  med err%  median |estimate - capacity| / capacity
Quantile outputs also get their pinball loss and coverage (share of samples
at or below the prediction; a well-calibrated q20 covers about 20%).

--parity checks that the deployed Rust model gives the same predictions as
this Python code: it rebuilds features from a learned run's ml_features.csv
and compares with the predictions the scheduler logged (ml_pred.csv).
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import features as F  # noqa: E402


def load_model(path):
    m = np.load(path)
    return {k: m[k] for k in m.files}


def predict(m, x):
    z = (x - m["mean"]) / m["std"]
    h1 = np.maximum(z @ m["W1"].T + m["b1"], 0)
    h2 = np.maximum(h1 @ m["W2"].T + m["b2"], 0)
    y = np.sort(h2 @ m["W3"].T + m["b3"], axis=1)
    return np.expm1(y) * 1000.0  # bytes/s


def scores(est, cap):
    est, cap = np.maximum(est, 1.0), np.maximum(cap, 1.0)
    over = est > cap
    short = np.where(~over, (cap - est) / cap, 0.0)
    return {"over_pct": round(100 * over.mean(), 1),
            "under_mean": round(float(short[~over].mean()) if (~over).any() else 0.0, 3),
            "med_err_pct": round(100 * float(np.median(np.abs(est - cap) / cap)), 1)}


def evaluate(m, d, mask):
    x, cap = d["x"][mask], d["cap"][mask]
    q = predict(m, x)
    out = {}
    for i, tau in enumerate(m["quantiles"]):
        s = scores(q[:, i], cap)
        y = np.log1p(cap / 1000.0)
        e = y - np.log1p(q[:, i] / 1000.0)
        s["coverage"] = round(float((cap <= q[:, i]).mean()), 3)
        s["pinball"] = round(float(np.mean(np.maximum(tau * e, (tau - 1) * e))), 4)
        out[f"model_q{int(round(tau * 100))}"] = s
    for b in ("bbr", "peak3s", "mean1s", "last"):
        out[b] = scores(d[f"base_{b}"][mask], cap)
    out["n"] = int(mask.sum())
    return out


def group_of(run_path, by):
    parts = Path(run_path).parts
    # results/<workload>/<scenario>/<scheduler>/<run_id>
    return parts[-3] if by == "scenario" else parts[-2]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model")
    ap.add_argument("data")
    ap.add_argument("--parity", action="store_true")
    ap.add_argument("--by", choices=["scenario", "sched"])
    ap.add_argument("--json")
    a = ap.parse_args()
    m = load_model(a.model)

    if a.parity:
        import csv
        run = Path(a.data)
        logged = {}
        for r in csv.DictReader(open(run / "ml_pred.csv")):
            logged.setdefault(int(r["path_id"]), []).append([float(r[k]) for k in ("q10", "q20", "q30", "q50")])
        worst = 0.0
        for p, arr in F.load_paths(run).items():
            if p not in logged or len(arr) <= F.WINDOW:
                continue
            py = predict(m, np.array([F.features_at(arr, j) for j in range(F.WINDOW, len(arr))]))
            rust = np.array(logged[p])
            n = min(len(py), len(rust))
            rel = np.abs(py[:n] - rust[:n]) / np.maximum(rust[:n], 1.0)
            worst = max(worst, rel.max())
            print(f"path {p}: {n} predictions compared, max relative difference {rel.max():.2e}")
        sys.exit(0 if worst < 1e-3 else 1)

    d = dict(np.load(a.data))
    groups = {"all": np.ones(len(d["cap"]), bool)}
    if a.by:
        names = np.array([group_of(d["runs"][i], a.by) for i in d["run"]])
        for g in sorted(set(names)):
            groups[g] = names == g
    report = {g: evaluate(m, d, mask) for g, mask in groups.items()}
    for g, r in report.items():
        print(f"\n{g} ({r['n']} samples)")
        print(f"  {'estimator':10s} {'over%':>7s} {'under':>7s} {'med err%':>9s} {'coverage':>9s} {'pinball':>8s}")
        for k, s in r.items():
            if k == "n":
                continue
            print(f"  {k:10s} {s['over_pct']:7.1f} {s['under_mean']:7.3f} {s['med_err_pct']:9.1f} "
                  f"{s.get('coverage', ''):>9} {s.get('pinball', ''):>8}")
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
