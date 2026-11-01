"""Network conditions for one training run of the bandwidth predictor.

  train_spec.py <run_id> <out.json>

Training never uses the test traces (traces/*.csv, the trace scenario). Each
run draws, from a generator seeded by run_id, one of:
  trace   (60%): one of the six training traces (traces/train/) per path,
          scaled so its mean is 1-6 Mbit/s, starting at a random offset;
  static  (20%): constant rates of 0.8-6 Mbit/s per path;
  outage  (20%): static, plus 1-3 outages of 1-3 s on path B.
RTTs: path A 20-80 ms, path B 30-160 ms. The json also gives the starting
rates and RTTs for testbed/up.sh and the schedule for testbed/replay_spec.py.
"""

import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRAIN = ["verizon_lte_driving", "verizon_lte_short", "tmobile_lte_driving", "tmobile_lte_short",
         "att_lte_driving_2016", "verizon_evdo_driving"]
CLIP_S = 75  # streaming run plus warm-up and slack


def trace_mean_kbit(name, direction):
    rows = (REPO / "traces/train" / f"{name}.{direction}.csv").read_text().split()[1:]
    v = [float(r.split(",")[1]) for r in rows]
    return 1000 * sum(v) / len(v), len(v) * 100


def main():
    run_id, out = int(sys.argv[1]), sys.argv[2]
    rng = random.Random(1000 + run_id)
    u = rng.random()
    mode = "trace" if u < 0.6 else "static" if u < 0.8 else "outage"
    spec = {"run_id": run_id, "mode": mode, "paths": {}, "outages": []}
    names = rng.sample(TRAIN, 2)
    for p, name, rtt in (("A", names[0], rng.randint(20, 80)), ("B", names[1], rng.randint(30, 160))):
        if mode == "trace":
            mean_down, dur_ms = trace_mean_kbit(name, "down")
            scale = rng.uniform(1000, 6000) / mean_down
            offset = rng.randrange(0, max(1, dur_ms - CLIP_S * 1000)) // 100 * 100
            spec["paths"][p] = {"trace": name, "scale": round(scale, 4), "offset_ms": offset,
                                "rtt_ms": rtt, "base_kbit": round(mean_down * scale)}
        else:
            spec["paths"][p] = {"const_kbit": rng.randint(800, 6000), "rtt_ms": rtt}
            spec["paths"][p]["base_kbit"] = spec["paths"][p]["const_kbit"]
    if mode == "outage":
        t = rng.uniform(5, 15)
        for _ in range(rng.randint(1, 3)):
            d = rng.uniform(1, 3)
            spec["outages"].append([round(t * 1000), round(d * 1000)])
            t += d + rng.uniform(8, 20)
    Path(out).write_text(json.dumps(spec, indent=1))


if __name__ == "__main__":
    main()
