"""Convert an iperf3 JSON log (iperf3 -J -i 0.1) to our trace format.

  iperf3_to_csv.py <in.json> <out.csv>

Uses each interval's received rate: for a reverse (-R) UDP test that is the
downlink rate the phone actually got.
"""

import json
import sys


def main():
    src, dst = sys.argv[1:3]
    with open(src) as f:
        log = json.load(f)
    with open(dst, "w") as f:
        f.write("t_ms,mbps\n")
        for iv in log["intervals"]:
            s = iv["sum"]
            f.write(f"{round(s['start'] * 1000)},{s['bits_per_second'] / 1e6:.3f}\n")


if __name__ == "__main__":
    main()
