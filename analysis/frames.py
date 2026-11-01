"""Per-frame results for testbed/run_stream.sh.

  frames.py run <run_dir>         write frames.csv and summary.csv, print a line
  frames.py table <scenario_dir>  median over runs, per scheduler

Inputs in <run_dir>: sent.csv (sender), recv.csv (receiver), frame_path.csv
(TQUIC team7 hook: bytes of every STREAM frame sent, per path), meta.env.

Definitions (times relative to each frame's scheduled send time s_i, i.e.
when the live encoder would have produced it):
  completion time  c_i - s_i, for frames that completed
  deadline         s_i + D (D = DEADLINE_MS, the playback delay budget); a
                   frame misses if it completes later or never
  playback         the player starts at s_0 + D and plays frame i at
                   p_i = max(p_{i-1} + (pts_i - pts_{i-1}), c_i); a stall is
                   any p_i > p_{i-1} + frame interval, its length the excess.
                   Frames that never complete are skipped by the player.
  lag              p_i - s_i (D plus all stalls so far)
  reorder buffer   bytes of completed frames held because an earlier frame
                   is still incomplete (in-order playout), peak over the run
"""

import csv
import statistics as st
import sys
from pathlib import Path

SWEEP_MS = (150, 300, 500, 1000)
FIELDS = ["scenario", "scheduler", "run_id", "frames", "completed", "stalled_run",
          "fct_med_ms", "fct_p95_ms", "fct_p99_ms", "miss_pct", "miss_I_pct", "miss_P_pct",
          *[f"miss{d}_pct" for d in SWEEP_MS],
          "stalls", "stall_ms", "lag_end_ms", "lag_max_ms", "reorder_peak_kb",
          "share_path0_pct", "share_path1_pct", "split_frames_pct", "veth_down_a", "veth_down_b",
          "handshake_ms"]


def read_csv(p):
    with open(p) as f:
        return list(csv.DictReader(f))


def meta(run_dir):
    return dict(l.split("=", 1) for l in (run_dir / "meta.env").read_text().splitlines() if "=" in l)


def q(xs, p):
    if not xs:
        return float("nan")
    if len(xs) == 1:
        return xs[0]
    return st.quantiles(xs, n=1000, method="inclusive")[round(p * 1000) - 1]


def veth_down(run_dir, dev):
    import json
    def get(tag):
        for l in json.loads((run_dir / f"{tag}.mpq-cli.link.json").read_text()):
            if l["ifname"] == dev:
                return l["stats64"]["rx"]["bytes"]
    return get("after") - get("before")


def handshake_ms(run_dir):
    """From the receiver's "connecting" log line to "established"."""
    t = {}
    for line in (run_dir / "receiver.log").read_text().splitlines():
        ts, msg = line.split(" ", 1)
        for key in ("connecting", "established"):
            if key in msg and key not in t:
                t[key] = int(ts)
    return round((t["established"] - t["connecting"]) / 1000) if len(t) == 2 else ""


def summarize_run(run_dir):
    m = meta(run_dir)
    D = int(m["DEADLINE_MS"]) * 1000
    sent = {int(r["frame_id"]): r for r in read_csv(run_dir / "sent.csv")}
    recv = {int(r["frame_id"]): r for r in read_csv(run_dir / "recv.csv")}
    sid2frame = {int(r["stream_id"]): fid for fid, r in sent.items()}
    path_bytes = {}  # frame_id -> [path0, path1]
    total = [0, 0]
    for r in read_csv(run_dir / "frame_path.csv"):
        fid = sid2frame.get(int(r["stream_id"]))
        p, n = int(r["path_id"]), int(r["len"])
        if p > 1:
            raise ValueError(f"unexpected path id {p}")
        total[p] += n
        if fid is not None:
            path_bytes.setdefault(fid, [0, 0])[p] += n

    ids = sorted(sent)
    rows, fct, miss = [], [], {d: 0 for d in SWEEP_MS}
    miss_main, miss_type, n_type = 0, {"I": 0, "P": 0}, {"I": 0, "P": 0}
    for fid in ids:
        s = sent[fid]
        sched = int(s["sched_ts_us"])
        c = int(recv[fid]["complete_ts_us"]) if fid in recv else None
        late = c is None or c > sched + D
        miss_main += late
        miss_type[s["frame_type"]] += late
        n_type[s["frame_type"]] += 1
        for d in SWEEP_MS:
            miss[d] += c is None or c > sched + d * 1000
        if c is not None:
            fct.append((c - sched) / 1000)
        b = path_bytes.get(fid, [0, 0])
        rows.append({"frame_id": fid, "frame_type": s["frame_type"], "size_bytes": s["size_bytes"],
                     "send_ts_us": s["send_ts_us"], "complete_ts_us": "" if c is None else c,
                     "deadline_us": sched + D, "bytes_path0": b[0], "bytes_path1": b[1],
                     "scheduler": m["SCHEDULER"], "scenario": m["SCENARIO"], "run_id": m["RUN_ID"]})
    with open(run_dir / "frames.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # Playback with rebuffering, frames in id order (= pts order).
    stalls, stall_us, lag, prev_p, prev_pts = 0, 0, [], None, None
    for fid in ids:
        if fid not in recv:
            continue
        s, c = int(sent[fid]["sched_ts_us"]), int(recv[fid]["complete_ts_us"])
        pts = s - int(sent[ids[0]]["sched_ts_us"])
        due = s + D if prev_p is None else prev_p + (pts - prev_pts)
        p = max(due, c)
        if prev_p is not None and p > due:
            stalls += 1
            stall_us += p - due
        lag.append((p - s) / 1000)
        prev_p, prev_pts = p, pts

    # In-order playout buffer.
    buf, peak, nxt, done = 0, 0, ids[0], set()
    for fid in sorted(recv, key=lambda f: int(recv[f]["complete_ts_us"])):
        done.add(fid)
        if fid == nxt:
            while nxt in done:
                if nxt != fid:
                    buf -= int(sent[nxt]["size_bytes"])
                done.discard(nxt)
                nxt += 1
        else:
            buf += int(sent[fid]["size_bytes"])
            peak = max(peak, buf)

    n = len(ids)
    pct = lambda x, d=n: round(100 * x / d, 2) if d else 0.0
    split = sum(1 for b in path_bytes.values() if b[0] > 0 and b[1] > 0)
    row = {"scenario": m["SCENARIO"], "scheduler": m["SCHEDULER"], "run_id": m["RUN_ID"],
           "frames": n, "completed": len(recv), "stalled_run": int(len(recv) < n),
           "fct_med_ms": round(q(fct, 0.5), 1), "fct_p95_ms": round(q(fct, 0.95), 1),
           "fct_p99_ms": round(q(fct, 0.99), 1), "miss_pct": pct(miss_main),
           "miss_I_pct": pct(miss_type["I"], n_type["I"]), "miss_P_pct": pct(miss_type["P"], n_type["P"]),
           **{f"miss{d}_pct": pct(miss[d]) for d in SWEEP_MS},
           "stalls": stalls, "stall_ms": round(stall_us / 1000), "lag_end_ms": round(lag[-1]) if lag else "",
           "lag_max_ms": round(max(lag)) if lag else "", "reorder_peak_kb": round(peak / 1000, 1),
           "share_path0_pct": pct(total[0], sum(total)), "share_path1_pct": pct(total[1], sum(total)),
           "split_frames_pct": pct(split), "veth_down_a": veth_down(run_dir, "cA"),
           "veth_down_b": veth_down(run_dir, "cB"), "handshake_ms": handshake_ms(run_dir)}
    with open(run_dir / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerow(row)
    return row


COLS = ["scheduler", "runs", "stalled_run", "fct_med_ms", "fct_p95_ms", "fct_p99_ms", "miss_pct",
        "miss_I_pct", "miss_P_pct", "stalls", "stall_ms", "lag_end_ms", "reorder_peak_kb",
        "share_path1_pct", "split_frames_pct"]


def table(scen_dir):
    rows = [r for f in sorted(Path(scen_dir).glob("*/*/summary.csv")) for r in read_csv(f)
            if "fct_med_ms" in r]
    print(f"{scen_dir}: median over runs (stalled_run = runs that never finished)")
    print("  ".join(f"{c:>15}" for c in COLS))
    for s in sorted({r["scheduler"] for r in rows}):
        rs = [r for r in rows if r["scheduler"] == s]
        def med(c):
            v = [float(r[c]) for r in rs if r[c] not in ("", "nan")]
            return round(st.median(v), 1) if v else ""
        out = {"scheduler": s, "runs": len(rs), "stalled_run": sum(int(r["stalled_run"]) for r in rs)}
        out.update({c: med(c) for c in COLS[3:]})
        print("  ".join(f"{str(out[c]):>15}" for c in COLS))


def main():
    mode, path = sys.argv[1], Path(sys.argv[2])
    if mode == "run":
        r = summarize_run(path)
        print(f"{r['scheduler']} run {r['run_id']}: {r['completed']}/{r['frames']} frames, "
              f"fct med/p95 {r['fct_med_ms']}/{r['fct_p95_ms']} ms, miss {r['miss_pct']}% "
              f"(I {r['miss_I_pct']}%), stalls {r['stalls']} ({r['stall_ms']} ms), "
              f"path1 {r['share_path1_pct']}%, split {r['split_frames_pct']}%"
              + (f"  SLOW HANDSHAKE {r['handshake_ms']} ms" if (r["handshake_ms"] or 0) > 2000 else ""))
    elif mode == "table":
        table(path)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
