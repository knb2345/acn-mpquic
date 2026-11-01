"""Replay a training spec (testbed/train_spec.py) onto the running testbed.

  replay_spec.py <spec.json> <log.csv> [--duration-s S]

Like replay.py: every 100 ms sets each veth end's tbf rate through one
`tc -batch` per namespace and logs every change (mono_us,t_ms,dev,kbit; mono_us
is CLOCK_MONOTONIC, the streamer's clock). Unlike replay.py it also logs the
starting rate of every device, so the log alone gives each path's capacity at
any time (the labels for training). Trace paths use the down trace on the
server end and the up trace on the client end, both scaled; static paths keep
their rate except during outages (path B only), when both ends drop to 8 kbit.
"""

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TICK_MS = 100
MIN_KBIT = 8
DEVS = {"cA": ("mpq-cli", "A", "up"), "sA": ("mpq-srv", "A", "down"),
        "cB": ("mpq-cli", "B", "up"), "sB": ("mpq-srv", "B", "down")}


def load(name, direction):
    rows = (REPO / "traces/train" / f"{name}.{direction}.csv").read_text().split()[1:]
    return [float(r.split(",")[1]) * 1000 for r in rows]  # kbit/s per bin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("log")
    ap.add_argument("--duration-s", type=float, default=float("inf"))
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text())
    state = dict(l.split("=", 1) for l in
                 (Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "mpq-testbed.env").read_text().split())
    queue_ms = int(state["QUEUE_MS"])

    sched = {}
    for dev, (_, p, direction) in DEVS.items():
        ps = spec["paths"][p]
        if "trace" in ps:
            tr = [v * ps["scale"] for v in load(ps["trace"], direction)]
            first = ps["offset_ms"] // TICK_MS
            sched[dev] = lambda k, tr=tr, first=first: tr[(first + k) % len(tr)]
        else:
            rate = ps["const_kbit"]
            outs = spec["outages"] if p == "B" else []
            sched[dev] = lambda k, rate=rate, outs=outs: (
                0 if any(s <= k * TICK_MS < s + d for s, d in outs) else rate)

    tc = {ns: subprocess.Popen(["sudo", "/usr/sbin/tc", "-n", ns, "-batch", "-"], stdin=subprocess.PIPE, text=True)
          for ns in ("mpq-cli", "mpq-srv")}
    stop = False

    def on_term(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    last = {}
    t0 = time.monotonic()
    with open(a.log, "w") as log:
        log.write("mono_us,t_ms,dev,kbit\n")
        k = 0
        while not stop and k * TICK_MS < a.duration_s * 1000:
            for dev, f in sched.items():
                kbit = max(round(f(k)), MIN_KBIT)
                if last.get(dev) == kbit:
                    continue
                ns, p, _ = DEVS[dev]
                burst = max(kbit * 1000 // 8 // 250, 3028)
                limit = max(spec["paths"][p]["base_kbit"] * queue_ms // 8, 15140)
                tc[ns].stdin.write(f"qdisc change dev {dev} parent 1:1 handle 10: tbf rate {kbit}kbit "
                                   f"burst {burst} limit {limit}\n")
                last[dev] = kbit
                now = time.monotonic()
                log.write(f"{round(now * 1e6)},{round((now - t0) * 1000)},{dev},{kbit}\n")
            for proc in tc.values():
                proc.stdin.flush()
            k += 1
            time.sleep(max(0.0, t0 + k * TICK_MS / 1000 - time.monotonic()))
    for proc in tc.values():
        proc.stdin.close()
        proc.wait()


if __name__ == "__main__":
    main()
