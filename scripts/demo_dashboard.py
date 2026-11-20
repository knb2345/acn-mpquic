"""Live terminal dashboard and comparison figure for the class demo.

  demo_dashboard.py live   --run-dir DIR --config C --scenario S --pid PID
      Follows a run that scripts/run.sh is writing (send.csv, recv.csv,
      frame_path.csv) and redraws every 0.5 s until the run process exits.
  demo_dashboard.py replay --run-dir DIR --config C --scenario S [--speed X]
      Replays a recorded run's frames.csv on a virtual clock (no network
      needed); the fallback if the live testbed misbehaves on stage.
  demo_dashboard.py figure --out PNG DIR [DIR ...]
      Side-by-side figure and a summary table for finished runs.

Frames are scored with analysis/playout.py exactly as in the analysis: a
frame is on time if it and every frame it depends on (intra refresh: all
earlier frames) completed within D = 250 ms of capture. While a run is in
progress, only frames whose deadline has passed are scored.
"""
import argparse
import csv
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
import playout  # noqa: E402

D_US = 250_000
CLI_A_IP, CLI_B_IP = "10.10.1.1", "10.10.2.1"
NAMES = {"S0": "Single best path (S0)", "S1": "MinRTT (S1)", "S2": "Round-robin (S2)",
         "S3": "Redundant (S3)", "S4": "ECF (S4)", "F1": "Whole-frame placement (F1)",
         "F2": "Frame split + rescue (F2, ours)", "A1": "F2 over two connections (A1)",
         "R1": "Ideal single link (R1)", "R2": "F2 with true link rates (R2)"}
SCEN = {"rtt_asym": "path A 1.6 Mbit/s, 30 ms RTT  |  path B 1.6 Mbit/s, 150 ms RTT",
        "symmetric": "path A 1.6 Mbit/s, 40 ms RTT  |  path B 1.6 Mbit/s, 40 ms RTT",
        "bw_asym": "path A 2.0 Mbit/s, 40 ms RTT  |  path B 1.2 Mbit/s, 60 ms RTT",
        "outage": "2 x 1.8 Mbit/s, 40/60 ms RTT, each path off 2 s every 15 s",
        "control": "path A 4 Mbit/s, 40 ms RTT  |  path B 4 Mbit/s, 40 ms RTT"}

G, R, Y, B, DIM, RST = "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[2m", "\033[0m"
SPARK = "▁▂▃▄▅▆▇█"


def read_csv(path):
    """Rows of a CSV that may still be being written (drops a partial last line)."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return []
    if not text.endswith("\n"):
        text = text[: text.rfind("\n") + 1]
    return list(csv.DictReader(text.splitlines()))


def live_state(run):
    """Frames in send order + bytes per path, from the logs of a running run."""
    frames, seen = [], set()
    for r in read_csv(run / "send.csv"):
        if r.get("frame_id") and r["frame_id"] not in seen:
            seen.add(r["frame_id"])
            frames.append({"id": r["frame_id"], "frame_type": r["frame_type"],
                           "capture_ts_us": int(r["capture_ts_us"]), "complete_ts_us": None})
    # live.csv is written by the receiver as each frame completes (demo runs);
    # recv.csv only at the end of the run.
    src = run / "live.csv" if (run / "live.csv").exists() else run / "recv.csv"
    done = {r["frame_id"]: int(r["complete_ts_us"]) for r in read_csv(src)
            if r.get("complete_ts_us", "").isdigit()}
    for f in frames:
        f["complete_ts_us"] = done.get(f["id"])
    a = b = 0
    for r in read_csv(run / "frame_path.csv"):
        n = int(r["len"]) if r.get("len", "").isdigit() else 0
        ip = (r.get("local") or "").rsplit(":", 1)[0]
        a += n if ip == CLI_A_IP else 0
        b += n if ip == CLI_B_IP else 0
    return frames, a, b


def recorded(run):
    rows = list(csv.DictReader(open(run / "frames.csv")))
    return [{"id": r["frame_id"], "frame_type": r["frame_type"],
             "capture_ts_us": int(r["capture_ts_us"]),
             "complete_ts_us": int(r["complete_ts_us"]) if r["complete_ts_us"] else None,
             "a": int(r["bytes_path0"] or 0), "b": int(r["bytes_path1"] or 0)} for r in rows]


def score(frames, now_us):
    """Metrics over frames whose deadline has passed by now_us."""
    seen = [dict(f, complete_ts_us=f["complete_ts_us"] if f["complete_ts_us"] is not None
                 and f["complete_ts_us"] <= now_us else None) for f in frames]
    decided = [f for f in seen if f["capture_ts_us"] + D_US <= now_us]
    if len(decided) < 3:
        return None
    lags = playout.lags_us(decided, "intra")
    n, t = playout.freezes(decided, lags, D_US)
    dur = len(decided) * playout.interval_us(decided)
    return {"n": len(decided), "on_time": playout.on_time_ratio(lags, D_US),
            "freeze_frac": t / dur, "freezes": n, "lags": lags}


def bar(frac, width=30, color=G):
    k = max(0, min(width, round(frac * width)))
    return color + "█" * k + RST + DIM + "░" * (width - k) + RST


def spark(lags, n=48, top_ms=1000):
    out = []
    for l in lags[-n:]:
        ms = l / 1000 if math.isfinite(l) else top_ms
        i = min(len(SPARK) - 1, int(min(ms, top_ms) / top_ms * (len(SPARK) - 1)))
        out.append((G if ms <= 250 else R) + SPARK[i] + RST)
    return "".join(out)


def render(config, scenario, video_s, total_s, m, a, b, note=""):
    lines = [f"{B}{NAMES.get(config, config)}{RST}   {DIM}{scenario}: {SCEN.get(scenario, '')}{RST}",
             f"{DIM}60-s NPTEL lecture, 2.6 Mbit/s, 25 frames/s; deadline 250 ms after capture{RST}", ""]
    lines.append(f"  video streamed     {video_s:5.1f} s / {total_s:.0f} s  {bar(video_s / total_s, color=Y)}")
    if m is None:
        lines += ["", "  waiting for the first frames ..."]
    else:
        col = G if m["on_time"] >= 0.95 else Y if m["on_time"] >= 0.85 else R
        last = m["lags"][-1]
        last_s = f"{last / 1000:6.0f} ms" if math.isfinite(last) else "  never"
        late = f"{R}late{RST}" if not math.isfinite(last) or last > D_US else f"{G}on time{RST}"
        lines += [f"  frames on time     {col}{m['on_time'] * 100:5.1f} %{RST}  {bar(m['on_time'], color=col)}",
                  f"  time frozen        {(R if m['freeze_frac'] > 0.02 else G)}{m['freeze_frac'] * 100:5.1f} %{RST}"
                  f"   ({m['freezes']} freezes)",
                  f"  latest frame       {last_s}  {late}",
                  f"  frame delay        {spark(m['lags'])}  {DIM}(green <= 250 ms, red late){RST}"]
    tot = a + b
    if tot:
        lines.append(f"  bytes on A | B     {a / tot * 100:5.1f} % | {b / tot * 100:5.1f} %"
                     f"   {G}{'█' * round(20 * a / tot)}{RST}{Y}{'█' * (20 - round(20 * a / tot))}{RST}")
    if note:
        lines += ["", f"  {DIM}{note}{RST}"]
    sys.stdout.write("\033[H\033[J" + "\n".join(lines) + "\n")
    sys.stdout.flush()


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cmd_live(a):
    run = Path(a.run_dir)
    while True:
        frames, ba, bb = live_state(run)
        now = time.monotonic_ns() // 1000
        if frames:
            t0 = frames[0]["capture_ts_us"]
            video_s = min(60.0, max(0.0, (now - t0) / 1e6))
            render(a.config, a.scenario, video_s, 60, score(frames, now), ba, bb, "live run")
        else:
            render(a.config, a.scenario, 0, 60, None, 0, 0, "setting up the two links and the QUIC connection ...")
        if not alive(a.pid):
            break
        time.sleep(0.5)


def cmd_replay(a):
    frames = recorded(Path(a.run_dir))
    t0 = frames[0]["capture_ts_us"]
    end = max([f["complete_ts_us"] or 0 for f in frames] + [frames[-1]["capture_ts_us"] + D_US])
    start = time.monotonic()
    while True:
        now = t0 + int((time.monotonic() - start) * a.speed * 1e6)
        vis = [f for f in frames if f["complete_ts_us"] is not None and f["complete_ts_us"] <= now]
        render(a.config, a.scenario, min(60.0, (now - t0) / 1e6), 60, score(frames, now),
               sum(f["a"] for f in vis), sum(f["b"] for f in vis),
               f"replay of recorded run {Path(a.run_dir).name} ({a.speed:g}x speed)")
        if now >= end:
            break
        time.sleep(0.5)


def cmd_figure(a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    runs = [Path(d) for d in a.dirs]
    fig, (ax, axb) = plt.subplots(1, 2, figsize=(13, 4.8), gridspec_kw={"width_ratios": [3, 1.1]})
    colors = ["#C8691F", "#2F6FB0", "#5A8F3C", "#8A5BB0"]
    labels, ont, frz = [], [], []
    print(f"\n{B}{'scheduler':34s} {'on time':>8s} {'frozen':>8s} {'path A':>7s} {'path B':>7s}{RST}")
    for run, c in zip(runs, colors):
        rows = list(csv.DictReader(open(run / "frames.csv")))
        cfg = rows[0]["config"]
        fr = [{"frame_type": r["frame_type"], "capture_ts_us": int(r["capture_ts_us"]),
               "complete_ts_us": int(r["complete_ts_us"]) if r["complete_ts_us"] else None} for r in rows]
        m, lags = playout.run_metrics(fr, "intra", deadlines_ms=(250,))
        t0 = fr[0]["capture_ts_us"]
        xs = [(f["capture_ts_us"] - t0) / 1e6 for f in fr]
        ys = [min(l / 1000, 3000) if math.isfinite(l) else 3000 for l in lags]
        name = NAMES.get(cfg, cfg)
        ax.plot(xs, ys, color=c, lw=1.2, label=f"{name}: {m['on_time_250'] * 100:.1f}% on time")
        ba = sum(int(r["bytes_path0"] or 0) for r in rows)
        bb = sum(int(r["bytes_path1"] or 0) for r in rows)
        tot = max(1, ba + bb)
        print(f"{name:34s} {m['on_time_250'] * 100:7.1f}% {m['freeze_frac_250'] * 100:7.1f}% "
              f"{ba / tot * 100:6.1f}% {bb / tot * 100:6.1f}%")
        labels.append(cfg)
        ont.append(m["on_time_250"] * 100)
        frz.append(m["freeze_frac_250"] * 100)
    ax.axhline(250, color="#9A4D12", ls="--", lw=1.2)
    ax.text(60, 270, "250 ms deadline", color="#9A4D12", fontsize=9, ha="right")
    ax.set_yscale("log")
    ax.set_ylim(50, 3500)
    ax.set_xlabel("video time (s)")
    ax.set_ylabel("frame display delay (ms, log)")
    ax.set_title("Same lecture, same two links: delay of every frame")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)
    x = range(len(labels))
    axb.bar([i - 0.2 for i in x], ont, 0.4, label="on time %", color="#2F6FB0")
    axb.bar([i + 0.2 for i in x], frz, 0.4, label="frozen %", color="#C8691F")
    for i in x:
        axb.text(i - 0.2, ont[i] + 1, f"{ont[i]:.0f}", ha="center", fontsize=9)
        axb.text(i + 0.2, frz[i] + 1, f"{frz[i]:.0f}", ha="center", fontsize=9)
    axb.set_xticks(list(x), labels)
    axb.set_ylim(0, 112)
    axb.set_title("At D = 250 ms")
    axb.legend(fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=2)
    fig.tight_layout()
    fig.savefig(a.out, dpi=150)
    print(f"\nfigure: {a.out}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("live", "replay"):
        s = sub.add_parser(name)
        s.add_argument("--run-dir", required=True)
        s.add_argument("--config", required=True)
        s.add_argument("--scenario", default="rtt_asym")
        if name == "live":
            s.add_argument("--pid", type=int, required=True)
        else:
            s.add_argument("--speed", type=float, default=1.0)
    s = sub.add_parser("figure")
    s.add_argument("--out", required=True)
    s.add_argument("dirs", nargs="+")
    a = p.parse_args()
    {"live": cmd_live, "replay": cmd_replay, "figure": cmd_figure}[a.cmd](a)


if __name__ == "__main__":
    main()
