"""Change path rates over time for the "trace" and "outage" scenarios.

  replay.py <scenario> <log.csv> [--offset-ms N] [--duration-s S]

Every 100 ms, sets the tbf rate on each veth end from its schedule, through one
long-running `tc -batch` per namespace (one sudo per namespace, not per
change). Writes every applied change to log.csv (mono_us, t_ms, dev, kbit;
mono_us is CLOCK_MONOTONIC, the streamer's clock). Runs until
--duration-s or SIGTERM. Run after testbed/up.sh <scenario>.

trace:  path A = carrier 1 (AT&T LTE), path B = carrier 2 (T-Mobile UMTS);
        down traces on the server ends, up traces on the client ends. Both
        traces start at --offset-ms and loop.
outage: path B drops to (nearly) 0 for 2 s every 30 s, first at t = 15 s;
        path A stays at its up.sh rate.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TICK_MS = 100
# tbf cannot be set to 0; at 8 kbit a full-size packet waits 1.5 s, so
# almost nothing gets through, and arriving packets queue or drop as in a
# coverage hole.
MIN_KBIT = 8
DEVS = {"cA": "mpq-cli", "cB": "mpq-cli", "sA": "mpq-srv", "sB": "mpq-srv"}


def load_trace(name):
    rows = (REPO / "traces" / name).read_text().splitlines()[1:]
    return [round(float(r.split(",")[1]) * 1000) for r in rows]  # kbit/s per bin


def schedules(scenario, state):
    """dev -> function(tick) -> kbit/s"""
    if scenario == "trace":
        files = {"sA": "att_lte_driving.down.csv", "cA": "att_lte_driving.up.csv",
                 "sB": "tmobile_umts_driving.down.csv", "cB": "tmobile_umts_driving.up.csv"}
        out = {}
        for dev, f in files.items():
            tr = load_trace(f)
            out[dev] = lambda k, tr=tr: tr[k % len(tr)]
        return out
    if scenario == "outage":
        base = int(state["B_RATE_KBIT"])
        def b(k):
            t = k * TICK_MS
            return 0 if t >= 15000 and (t - 15000) % 30000 < 2000 else base
        return {"sB": b, "cB": b}
    sys.exit(f"replay.py: scenario {scenario} has no rate schedule")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("log")
    ap.add_argument("--offset-ms", type=int, default=0)
    ap.add_argument("--duration-s", type=float, default=float("inf"))
    a = ap.parse_args()

    state_file = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "mpq-testbed.env"
    state = dict(l.split("=", 1) for l in state_file.read_text().split())
    if state["SCENARIO"] != a.scenario:
        sys.exit(f"replay.py: testbed is up for {state['SCENARIO']}, not {a.scenario}")
    queue_ms = int(state["QUEUE_MS"])
    base = {"A": int(state["A_RATE_KBIT"]), "B": int(state["B_RATE_KBIT"])}

    sched = schedules(a.scenario, state)
    tc = {ns: subprocess.Popen(["sudo", "/usr/sbin/tc", "-n", ns, "-batch", "-"],
                               stdin=subprocess.PIPE, text=True)
          for ns in sorted({DEVS[d] for d in sched})}
    stop = False

    def on_term(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    last = {}
    first_tick = a.offset_ms // TICK_MS
    t0 = time.monotonic()
    with open(a.log, "w") as log:
        log.write("mono_us,t_ms,dev,kbit\n")
        k = 0
        while not stop and k * TICK_MS < a.duration_s * 1000:
            for dev, f in sched.items():
                kbit = max(f(first_tick + k), MIN_KBIT)
                if last.get(dev) == kbit:
                    continue
                # Same burst rule as up.sh; the queue limit stays fixed at
                # QUEUE_MS of the path's starting rate.
                burst = max(kbit * 1000 // 8 // 250, 3028)
                limit = max(base[dev[1]] * queue_ms // 8, 15140)
                tc[DEVS[dev]].stdin.write(
                    f"qdisc change dev {dev} parent 1:1 handle 10: tbf rate {kbit}kbit burst {burst} limit {limit}\n")
                last[dev] = kbit
                now = time.monotonic()
                log.write(f"{round(now * 1e6)},{round((now - t0) * 1000)},{dev},{kbit}\n")
            for p in tc.values():
                p.stdin.flush()
            k += 1
            time.sleep(max(0.0, t0 + k * TICK_MS / 1000 - time.monotonic()))
    for p in tc.values():
        p.stdin.close()
        p.wait()


if __name__ == "__main__":
    main()
