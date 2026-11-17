# Frame-aware multipath QUIC for live lectures (CS6048 ACN, Team 7)

**Kabir Nawal Bajaj · Prasanna Bhavan · Divyanshu** (equal contribution)

How much of two weak uplinks can an interactive live lecture use over Multipath
QUIC, and does placing whole video frames on paths (instead of single packets)
recover what packet-level schedulers waste?

| Deliverable | Where |
| --- | --- |
| Final report (problem, related work, method, results, analysis) | [`docs/Team7-final-report.pdf`](docs/Team7-final-report.pdf) · [`docs/report.md`](docs/report.md) |
| Final slides (13) | [`docs/slides/Team7-final.pdf`](docs/slides/Team7-final.pdf) (source: `docs/slides/deck/`) |
| Design, hypotheses H1-H5, metrics | [`docs/proposal.md`](docs/proposal.md) |
| Lab log: what was built, what failed, evidence | [`docs/PROGRESS.md`](docs/PROGRESS.md) |
| Testbed calibration | [`docs/calibration.md`](docs/calibration.md) |
| Per-run summary of all 758 runs | [`analysis/summary_all.csv`](analysis/summary_all.csv) |
| Raw per-run data (all runs, calibration) | `results/` (`frames.csv`, `paths.csv`, `meta.json` per run; batch logs in `results/batches/`) |

![Cross-scenario results](analysis/plots/fig1_cross_scenario_ontime.png)

## Results in one minute (758 paired runs, 60 s NPTEL lecture, D = 250 ms)

- **One weak path is not enough:** the better single 1.6 Mbit/s path shows
  <1 % of frames on time at 2.6 Mbit/s.
- **Packet-level MPQUIC wastes capacity (H2 supported):** on paths with
  30 ms vs 150 ms RTT, MinRTT and ECF sustain only 1.0 Mbit/s with <= 1 %
  freeze, vs 2.6 Mbit/s on an ideal link of the same total capacity (38 %).
  They miss 11-17 % of frame deadlines even with 23 % spare capacity; plain
  round-robin misses only 1-2 %.
- **Frame-aware splitting (F2) helps at the main bitrate:** ~10 pp less freeze
  than MinRTT/ECF at 2.6 Mbit/s on symmetric, bandwidth- and RTT-asymmetric
  paths (rtt_asym 4.5 % vs 14.9 %, Holm p = 0.043), matching the ideal link on
  bw_asym. Pinning whole frames to one path (F1) fails.
- **Honest negative results:** F2's delivery-rate estimate is too low when the
  sender is application-limited (1.0-2.2 Mbit/s) and its one-PTO rescue
  misfires under 2 s outages, so H3 is only partly supported; two separate QUIC
  connections (A1) stall, so one MPQUIC connection wins (H4 rejected).

What we built: a calibrated two-path netem/htb testbed (`testbed/`), a frame
streamer on TQUIC that sends each frame part as a QUIC stream (`streamer/`),
three unit-tested TQUIC hooks plus an ECF scheduler (fork `team7` branch), a
paired experiment harness (`scripts/`), and a deadline- and decode-aware
playout model with paired statistics (`analysis/`, 15 hand-computed tests).

**Code status (18 Nov):** every configuration (S0-S4, F1, F2, A1, R1, R2) runs
from this repository with `scripts/run.sh`. F1/F2/A1/R2 are placement policies
in the streamer (`streamer/src/lib.rs`, `streamer/src/bin/mpq_sender.rs`). Raw
per-run data for all runs is in `results/`. Tests: TQUIC 564 + 1, streamer 9,
analysis 15, all passing.

## Team

Kabir Nawal Bajaj, Prasanna Bhavan and Divyanshu contributed equally. We worked
together on one shared laptop testbed, so the commit author names do not reflect
who did what (see report section 9).

## Get the code

```bash
git clone --recurse-submodules https://github.com/knb2345/acn-mpquic.git
cd acn-mpquic
```

`third_party/tquic` is our TQUIC fork (https://github.com/knb2345/tquic, branch
`team7`: hooks a-c, ECF scheduler, stall fix, frame_path log, on top of
v1.6.0). If you already cloned without submodules: `git submodule update --init`.

## Requirements

Linux (we use WSL2 Ubuntu 24.04; keep the repo on the Linux filesystem, not
`/mnt/c`), with:

- Rust (cargo) and a C toolchain (for TQUIC's BoringSSL build: `cmake`,
  `clang` or `gcc`, `golang` may also be needed)
- `iproute2` with `tc`, kernel modules `sch_netem sch_tbf sch_htb`
- ffmpeg/ffprobe with libx264, iperf3
- Python 3.12 venv: `python3 -m venv analysis/.venv &&
  analysis/.venv/bin/pip install pandas matplotlib scipy`
- passwordless sudo for `ip`, `tc`, `modprobe` only (see `testbed/README.md`)

## Build and test

```bash
(cd third_party/tquic && cargo build --release --all && cargo test --release)
(cd streamer && cargo build --release && cargo test --release)
(cd analysis && .venv/bin/python -m unittest test_playout test_stats)
```

## Run

```bash
workload/download.sh stochastic             # videos are not in git (needs yt-dlp)
workload/encode.sh stochastic intra          # frame traces (.csv) are in git
testbed/up.sh rtt_asym                       # two emulated paths
scripts/run.sh rtt_asym S1 2600 intra stochastic 0
```

One run writes `results/<scenario>/<config>/<bitrate>_<encoding>_<clip>/<run_id>/`.
Batches: `scripts/batch.sh`. Raw qlogs and dumps are git-ignored; per-run
`frames.csv`, `paths.csv`, `meta.json` and summaries are tracked. Never edit or
delete results by hand; log failed runs in `docs/PROGRESS.md`.

## Contributing

Work on a branch, small commits, open a PR against `main`. TQUIC changes go to
the `team7` branch of the fork, only in `src/multipath_scheduler/` plus hooks
a-c, each with a unit test; then bump the submodule pointer here.
