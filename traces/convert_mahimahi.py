"""Convert a Mahimahi trace to our trace format (csv: t_ms, mbps).

A Mahimahi trace lists, one per line, the millisecond at which the link can
deliver one 1500-byte packet. We count delivery opportunities per bin.

  convert_mahimahi.py <in.trace> <out.csv> [--bin-ms 100] [--scale 1.0]
  convert_mahimahi.py --stats <in.trace> ...   print rate statistics only
"""

import argparse
import statistics

MTU_BITS = 1500 * 8


def bins(path, bin_ms):
    with open(path) as f:
        stamps = [int(line) for line in f if line.strip()]
    n = stamps[-1] // bin_ms + 1
    counts = [0] * n
    for t in stamps:
        counts[t // bin_ms] += 1
    # opportunities per bin -> Mbit/s
    return [c * MTU_BITS / (bin_ms / 1000) / 1e6 for c in counts]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--bin-ms", type=int, default=100)
    ap.add_argument("--scale", type=float, default=1.0, help="multiply every rate by this")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    if a.stats:
        print(f"{'trace':34s} {'dur_s':>6s} {'mean':>6s} {'p10':>6s} {'p50':>6s} {'p90':>6s} {'zero%':>6s}")
        for p in a.paths:
            r = bins(p, a.bin_ms)
            q = statistics.quantiles(r, n=10)
            zero = 100 * sum(x == 0 for x in r) / len(r)
            print(f"{p.rsplit('/', 1)[-1]:34s} {len(r) * a.bin_ms / 1000:6.0f} {statistics.mean(r):6.2f} "
                  f"{q[0]:6.2f} {q[4]:6.2f} {q[8]:6.2f} {zero:6.1f}")
        return

    src, dst = a.paths
    with open(dst, "w") as f:
        f.write("t_ms,mbps\n")
        for i, r in enumerate(bins(src, a.bin_ms)):
            f.write(f"{i * a.bin_ms},{r * a.scale:.3f}\n")


if __name__ == "__main__":
    main()
