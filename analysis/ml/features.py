"""Build the bandwidth predictor's dataset from streaming runs.

  features.py <out.npz> <run_dir> [<run_dir> ...]

Each run dir needs ml_features.csv (per-path samples every 100 ms, logged by
the sender) and meta.env; capacity comes from replay.csv when the scenario
replays rates, else from the static rates in meta.env. Path 0 is path A (veth
sA), path 1 is path B (sB); the label is the server-side (data direction) rate.

Per sample with a full second of history:
  x          the 44 model inputs, computed exactly as ml_features.rs does
  y          ln(1 + kB/s) of the mean capacity over the next HORIZON_MS
  cap        that capacity in bytes/s
  cond       the run's network condition (run_id): runs with the same id saw
             the same network, so train/validation splits group by it
  baselines  other estimates of it, in bytes/s, for comparison:
             bbr (the controller's estimate), last (last tick's delivery
             rate), mean1s (mean delivery rate over the last second),
             peak3s (best bbr value of the last 3 s, the Deadline heuristic)
"""

import csv
import sys
from pathlib import Path

import numpy as np

WINDOW = 10
HORIZON_MS = 500
DEV = {0: "sA", 1: "sB"}
RATE_KEY = {0: "A_RATE_KBIT", 1: "B_RATE_KBIT"}


def ln1p_k(bytes_per_s):
    return np.log1p(np.asarray(bytes_per_s, dtype=np.float64) / 1000.0)


def capacity_fn(run_dir, meta):
    """path -> (times_us, kbit) step function, or a constant."""
    steps = {0: [], 1: []}
    rp = run_dir / "replay.csv"
    if rp.exists():
        with open(rp) as f:
            for r in csv.DictReader(f):
                for p, dev in DEV.items():
                    if r["dev"] == dev:
                        steps[p].append((int(r["mono_us"]), float(r["kbit"])))
    out = {}
    for p in (0, 1):
        base = float(meta[RATE_KEY[p]])
        if steps[p]:
            t = np.array([s[0] for s in steps[p]], dtype=np.int64)
            v = np.array([s[1] for s in steps[p]])
            out[p] = (t, v, base)
        else:
            out[p] = (None, None, base)
    return out


def mean_capacity(fn, t0, t1):
    """Time-weighted mean kbit over [t0, t1] (us) of a step function."""
    t, v, base = fn
    if t is None or t1 <= t[0]:
        return base if t is None else base
    edges = np.concatenate(([t0], t[(t > t0) & (t < t1)], [t1]))
    total = 0.0
    for a, b in zip(edges[:-1], edges[1:]):
        i = np.searchsorted(t, a, side="right") - 1
        val = v[i] if i >= 0 else base
        total += val * (b - a)
    return total / (t1 - t0)


COLS = ("ts_us", "sent", "acked", "lost", "bw", "srtt_us", "min_rtt_us", "inflight", "cwnd")


def features_at(a, j):
    """Model input at row j of one path's samples (columns COLS), as in
    ml_features.rs: per tick delivery and send rate (log kB/s), queueing delay
    (100 ms) and loss ratio; then bandwidth estimate, cwnd/sRTT, inflight/cwnd
    and min RTT."""
    ts, sent, acked, lost, bw, srtt, minrtt, infl, cwnd = a.T
    x = np.zeros(4 * WINDOW + 4)
    for i in range(WINDOW):
        lo, hi = j - WINDOW + i, j - WINDOW + i + 1
        dt = max((ts[hi] - ts[lo]) / 1e6, 1e-3)
        ds = max(sent[hi] - sent[lo], 0)
        x[4 * i] = ln1p_k(max(acked[hi] - acked[lo], 0) / dt)
        x[4 * i + 1] = ln1p_k(ds / dt)
        x[4 * i + 2] = max(srtt[hi] - minrtt[hi], 0) / 1e5
        x[4 * i + 3] = min(max(lost[hi] - lost[lo], 0) / max(ds, 1.0), 1.0)
    s = max(srtt[j] / 1e6, 1e-3)
    x[4 * WINDOW] = ln1p_k(bw[j])
    x[4 * WINDOW + 1] = ln1p_k(cwnd[j] / s)
    x[4 * WINDOW + 2] = min(infl[j] / max(cwnd[j], 1), 2.0)
    x[4 * WINDOW + 3] = minrtt[j] / 1e5
    return x


def load_paths(run_dir):
    """path id -> array of samples (columns COLS), in log order."""
    rows = {0: [], 1: []}
    with open(run_dir / "ml_features.csv") as f:
        for r in csv.DictReader(f):
            # A run still being written can end in a partial line.
            if any(not r.get(k) for k in COLS + ("path_id",)):
                continue
            p = int(r["path_id"])
            if p in rows:
                rows[p].append([int(r[k]) for k in COLS])
    return {p: np.array(v, dtype=np.float64) for p, v in rows.items()}


def run_samples(run_dir):
    meta = dict(l.split("=", 1) for l in (run_dir / "meta.env").read_text().splitlines() if "=" in l)
    # Capacity is a step function: after the last logged change it keeps that
    # value until the replay stops, which is after the connection closed.
    cap = capacity_fn(run_dir, meta)
    out = []
    for p, a in load_paths(run_dir).items():
        if len(a) <= WINDOW:
            continue
        ts, sent, acked, lost, bw, srtt, minrtt, infl, cwnd = a.T
        for j in range(WINDOW, len(a)):
            t_end = ts[j] + HORIZON_MS * 1000
            x = features_at(a, j)
            c = mean_capacity(cap[p], ts[j], t_end) * 1000 / 8
            dt_last = max((ts[j] - ts[j - 1]) / 1e6, 1e-3)
            k3 = max(0, j - 30)
            out.append((x, c, p, bw[j], max(acked[j] - acked[j - 1], 0) / dt_last,
                        max(acked[j] - acked[j - WINDOW], 0) / max((ts[j] - ts[j - WINDOW]) / 1e6, 1e-3),
                        bw[k3:j + 1].max()))
    return out, meta


def main():
    out_path, dirs = sys.argv[1], [Path(d) for d in sys.argv[2:]]
    xs, cap, path, run, sched, cond = [], [], [], [], [], []
    base = {k: [] for k in ("bbr", "last", "mean1s", "peak3s")}
    names = []
    for i, d in enumerate(dirs):
        if not (d / "ml_features.csv").exists():
            continue
        samples, meta = run_samples(d)
        names.append(str(d))
        for x, c, p, b, last, m1, pk in samples:
            xs.append(x); cap.append(c); path.append(p); run.append(len(names) - 1); sched.append(meta["SCHEDULER"])
            cond.append(int(meta["RUN_ID"]))
            base["bbr"].append(b); base["last"].append(last); base["mean1s"].append(m1); base["peak3s"].append(pk)
    cap = np.array(cap)
    np.savez_compressed(out_path, x=np.array(xs, dtype=np.float32), y=ln1p_k(cap).astype(np.float32),
                        cap=cap, path=np.array(path), run=np.array(run), sched=np.array(sched), cond=np.array(cond),
                        runs=np.array(names), **{f"base_{k}": np.array(v) for k, v in base.items()})
    print(f"{out_path}: {len(cap)} samples from {len(names)} runs")


if __name__ == "__main__":
    main()
