# Collecting our own dual-SIM traces

Out of scope since 28 Oct 2026 (the project runs in emulation only, on public
traces). Kept in case someone records traces later.

Needs: two Android phones on two different carriers, and a public server
running `iperf3 -s` (not set up yet; an Azure VM in Central India was the
suggestion). Replace `SERVER` below with its address.

On each phone, install Termux, then in Termux:

```bash
pkg install iperf3
```

Record 5 minutes of downlink, 100 ms intervals, at the same time on both
phones, standing or moving the way a student would during a class:

```bash
iperf3 -c SERVER -R -u -b 10M -t 300 -i 0.1 -J > carrier1_$(date +%Y%m%d_%H%M).json
```

`-b 10M` is the offered rate; set it above the best rate you expect from that
carrier, but note each run uses about `rate x 300 s` of mobile data (10M: about
375 MB). Copy the JSON files to `traces/raw/` and convert:

```bash
python3 traces/collect/iperf3_to_csv.py traces/raw/carrier1_X.json traces/chennai_carrier1.down.csv
```

Write down per session: date, time, place, carriers, signal bars, and whether
the phone was moving.
