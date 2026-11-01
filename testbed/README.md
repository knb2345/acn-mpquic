# Testbed

Two network namespaces joined by two veth pairs, one per emulated path. Every
command below is run from the repo root (`~/acn-mpquic`) in WSL.

```
 ns mpq-cli                               ns mpq-srv
  cA 10.10.1.1/24 ---- path A (veth) ---- sA 10.10.1.2/24
  cB 10.10.2.1/24 ---- path B (veth) ---- sB 10.10.2.2/24
                                          lo 10.10.0.1/32  (server address)
```

The client reaches the server address over A or B depending on which local
address a socket is bound to (policy routing tables 101 and 102), so each QUIC
path is pinned to one veth pair. Every veth end shapes its egress: `netem`
adds RTT/2 of delay, then `tbf` limits the rate with a bottleneck queue of
`QUEUE_MS` (default 100 ms) worth of bytes. Both directions of a path therefore
see the same link.

## One-time setup

Only `ip`, `tc` and `modprobe` need root. Give your user passwordless sudo for
exactly those (note: `ip netns exec` can run any command as root, so this is
effectively passwordless root for this user):

```bash
echo "$USER ALL=(root) NOPASSWD: /usr/bin/ip, /usr/sbin/tc, /usr/sbin/modprobe" > /tmp/acn-mpquic.sudoers
```

```bash
sudo visudo -cf /tmp/acn-mpquic.sudoers
```

```bash
sudo install -m 0440 -o root -g root /tmp/acn-mpquic.sudoers /etc/sudoers.d/acn-mpquic
```

The scripts call `/usr/bin/ip` by full path: plain `sudo ip` resolves to the
`/usr/sbin/ip` symlink, which sudo does not match against the rule.

## Week 1, check 1: build TQUIC and run its tests

TQUIC is the submodule `third_party/tquic`, pinned to tag v1.6.0, on local
branch `team7` (our scheduler commits go there). BoringSSL is its own nested
submodule and builds through cmake; Go is not needed.

```bash
git submodule update --init --recursive
```

```bash
cd third_party/tquic && cargo build --release --all && cargo test && cargo test --release; cd -
```

TQUIC's own multipath test (loopback, 4 paths, no namespaces, no root). Keep
the default debug log level: the test counts paths from debug log lines.

```bash
cd third_party/tquic && bash tools/tests/tquic_tools_test.sh -b "$PWD/target/release" -w /tmp/tquic-tools-test -t multipath_minrtt,multipath_roundrobin,multipath_redundant; cd -
```

## Week 1, check 2: bring the testbed up and down

`up.sh` loads the qdisc modules (needed after every WSL restart), deletes any
old testbed, and builds a fresh one, so it is always safe to rerun.

```bash
testbed/up.sh symmetric
```

```bash
testbed/check.sh
```

```bash
testbed/down.sh
```

Presets (from `docs/proposal.md` section 4): `symmetric` (4/4 Mbit, 40/40 ms),
`rtt_asym` (4/4 Mbit, 30/150 ms), `low_bw` (1.5/1 Mbit, 60/120 ms). Override any
value with env vars, e.g. `QUEUE_MS=50 B_RTT_MS=80 testbed/up.sh symmetric`.
`check.sh` pings each path (RTT) and runs a 5 s iperf3 TCP download over it
(rate); expect TCP goodput a little under the configured rate (headers).

Inspect by hand:

```bash
sudo /usr/bin/ip netns exec mpq-srv tc -s qdisc show
```

```bash
sudo /usr/bin/ip -n mpq-cli -s link show
```

## Week 1, check 3: multipath transfer over both paths

```bash
testbed/run_transfer.sh symmetric
```

```bash
testbed/run_transfer.sh rtt_asym
```

Each call runs `RUNS` (default 5) downloads of a 10 MiB file (content from a
fixed seed) per scheduler (default `minrtt roundrobin`; pass names to choose,
e.g. `testbed/run_transfer.sh low_bw minrtt roundrobin redundant`). Every run
rebuilds the testbed, so queues and counters start empty. Other knobs (env
vars): `RUN_START` (first run_id, to add runs without overwriting), `CC`
(`bbr` default, `cubic`, `bbr3`, `copa`; non-default results go to
`<scheduler>-<cc>/`), `TIMEOUT_S` (default 300), `LOG_LEVEL` (`trace` logs per
path why the sender holds back; debug/trace logs are gzipped). The table at the end
is reprinted any time with:

```bash
python3 analysis/transfer_summary.py table results/week1_transfer/symmetric
```

Per run, `results/week1_transfer/<scenario>/<scheduler>/<run_id>/` holds `meta.env` (link
parameters, TQUIC commit, exit code, file check, transfer time), `summary.csv`,
before/after counter snapshots, client and server logs, and qlogs (git-ignored,
about 4 MB per run).

How "uses both paths" is measured: TQUIC's qlog events carry no path id, so
per-path bytes come from the kernel's veth counters (`down_bytes_a/b` = bytes
received on `cA`/`cB` during the transfer). `drops_a/b` are tbf drops on the
server side. The file flows server to client, so the scheduler that matters is
the server's `--multipath-algor`; both ends get the same value.

## Rate replay (trace and outage scenarios)

`replay.py` changes the tbf rates every 100 ms while a run is going; the run
scripts start it themselves. By hand:

```bash
testbed/up.sh trace && python3 testbed/replay.py trace /tmp/replay.csv --offset-ms 200000 --duration-s 30
```

`trace`: path A follows carrier 1 (AT&T LTE), path B carrier 2 (T-Mobile
UMTS), down traces on the server side and up traces on the client side (see
`traces/README.md`). `outage`: path B drops to 8 kbit/s (tbf cannot do 0)
for 2 s every 30 s, first at 15 s. Checked with iperf3: during an outage path B
carries 0.00 Mbit/s and recovers within a second; on `trace`, per-second TCP
throughput follows the trace within about 1 Mbit/s.

## Frame streaming (Week 2 on)

Build the streamer once (and after any TQUIC change):

```bash
cargo build --release --manifest-path streamer/Cargo.toml
```

Run one scenario, 5 runs per scheduler:

```bash
testbed/run_stream.sh rtt_asym deadline framepin minrtt ecf roundrobin redundant single
```

Schedulers: `deadline` (ours) and `framepin` (its ablation) are in
`third_party/tquic/src/multipath_scheduler/scheduler_deadline.rs`, `ecf` in
`scheduler_ecf.rs`; `minrtt`, `roundrobin`, `redundant` are TQUIC's. The
sender gives every frame a deadline (scheduled send time + `DEADLINE_MS`)
through `Connection::stream_set_deadline`; only `deadline` uses it. For
`deadline`/`framepin` runs, `plan.log` has the scheduler's per-frame estimates
and decision (rate, queue and predicted finish time per path).

For a long batch, freeze the binaries first so later builds cannot change
it mid-way, and do not edit the testbed scripts while it runs:

```bash
testbed/freeze_bins.sh final
```

```bash
BIN_DIR=.cache/bin/final testbed/run_stream.sh low_bw deadline framepin minrtt ecf roundrobin redundant single
```

The sender (QUIC server, `mpq-srv`) writes frame i of
`workload/synthetic_lecture.csv` at its scheduled time to a new unidirectional
stream; the receiver (QUIC client, `mpq-cli`, one path per address) logs when
each stream completes. `single` disables multipath and uses only path A, the
better path in every scenario. Per run, `results/<scenario>/<scheduler>/<run_id>/`
holds `frames.csv` (one row per frame, CLAUDE.md columns), `summary.csv`
(metrics), the raw `sent.csv`, `recv.csv`, `frame_path.csv` (bytes of every
STREAM frame per path, from the TQUIC hook), logs and `meta.env`. Metric
definitions are at the top of `analysis/frames.py`; reprint a scenario's
table with:

```bash
python3 analysis/frames.py table results/rtt_asym
```

Env knobs: `RUNS`, `RUN_START`, `CC`, `WORKLOAD`, `DEADLINE_MS` (playback
delay budget, default 300), `LOG_LEVEL`, `QLOG=1`, `BIN_DIR`.

Figures for the report (PNG plus the numbers behind them in
`figures_data.csv`):

```bash
python3 analysis/plots.py results analysis/figures
```

### Results layout

- `results/<workload>/<scenario>/<scheduler>/<run_id>/`: the final matrices,
  `copyrightx_lecture` (real lecture, main) and `synthetic_lecture`. Each run's
  `meta.env` names the frozen build it used (`BUILD_TAG`, `TQUIC_COMMIT`).
- `results/synthetic_lecture/deadline_v1/`: `deadline` from the first final
  build, before frames past their deadline were released to any path (see
  CLAUDE.md).
- `results/week3_dev/`: development runs of `deadline`/`framepin` while tuning.
- `results/week2_baseline/`: baselines from the Week 2 build (before the
  path-selection fix below).
- `results/week1_transfer/`: Week 1 file transfers.

### TQUIC bug found on the trace scenario

With multipath on, whole connections stalled when path B was nearly dead
while it was being validated (15 of 1800 frames in 90 s; `single` on the
same trace was fine). `select_send_path` kept choosing the path that needed
PATH_CHALLENGE/RESPONSE frames although its congestion window was full;
`send_packet` writes those frames only after the congestion check, so no
packet left on any path, ACKs for path A included, and the server backed
off (sending at 1, 2, 4, ... 32 s). Fixed on `team7` (commit "fix connection
stall when a path being validated cannot send"); runs before the fix are in
`results/week2_baseline/trace/`.

## Week 1 results (28 Oct 2026)

TQUIC v1.6.0 (`d87a9a0`), BBR (TQUIC's default), 10 MiB download, testbed as
above. Commands: `testbed/run_transfer.sh symmetric` and
`testbed/run_transfer.sh rtt_asym`, then extra rtt_asym runs with
`RUN_START=6`/`7 RUNS=20 TIMEOUT_S=60 LOG_LEVEL=trace`.

1. **Build and tests:** release build of the library and tools in 1.5 min;
   `cargo test` 552/552 passed in debug and in release. TQUIC's loopback tool
   test passed for minrtt, roundrobin and redundant (all 4 paths received
   packets, file identical).
2. **Testbed:** `check.sh` on symmetric measured 40.5 ms RTT on both paths
   (40 configured) and 3.56 Mbit/s of iperf3 TCP goodput (4 Mbit configured).
   On rtt_asym, ping from each client address gives 30 ms and 150 ms minimum
   RTT, so the policy routing keeps each path on its own veth pair.
3. **Multipath transfer:** both paths carry about half the bytes under both
   schedulers:

| scenario | scheduler | runs | file OK | median time | goodput | share A | share B |
| --- | --- | --- | --- | --- | --- | --- | --- |
| symmetric | minrtt | 6 | 6 | 11.85 s | 7.08 Mbit/s | 50.3% | 49.7% |
| symmetric | roundrobin | 5 | 5 | 11.85 s | 7.08 Mbit/s | 50.3% | 49.7% |
| rtt_asym | minrtt | 25 | 25 | 12.24 s | 6.85 Mbit/s | 51.7-52.4% | 47.6-48.3% |
| rtt_asym | roundrobin | 26 | **25** | 12.20 s | 6.88 Mbit/s | 51.5-52.7% | 47.3-48.5% |

Caveat on the times: these runs timed the transfer with the wall clock, which
WSL2 steps forward by about 0.5 s every 30-40 s (found in Week 2; see
CLAUDE.md). A run that spans a step reads about 0.5 s long (e.g.
symmetric/minrtt/1, 12.36 s against 11.8 s for the others); medians and byte
shares are unaffected. `run_transfer.sh` now uses the monotonic clock.

The veth counters agree with TQUIC's own count: in symmetric/minrtt/1 the
client reports 10,747,543 bytes in 7,410 packets; adding 42 bytes of
Ethernet/IP/UDP header per packet gives 11,058,763, against 11,059,173 on
`cA`+`cB` (the 410-byte difference is ARP).

Even on rtt_asym, MinRTT puts only 52% on the 30 ms path: in a bulk transfer
both congestion windows stay full, so MinRTT falls through to the 150 ms path
most of the time. That is the byte-level splitting the frame scheduler is meant
to avoid.

### Known issue: one stalled transfer (rtt_asym, roundrobin, run 5)

The transfer stopped making progress after about 0.9 MB and the client timed
out at 300 s (evidence in `results/week1_transfer/rtt_asym/roundrobin/5/`, qlogs kept
locally). From the qlogs: 21 packets were lost on path A in the first second
(tbf drops); after that the server's congestion window stayed at 4.8-9.5 KB on
both paths and it sent about one data packet every 1-2 s for 5 minutes, with no
further loss. The client had every byte sent so far, in order, and flow
control allowed 5 MB, so the sender alone was holding back; with BBR this
points at a collapsed bandwidth estimate (pacing), but it is not confirmed.
Unusually, this run had zero drops on path B (healthy runs: 140-180).

It did not reproduce in 20 more roundrobin and 20 more minrtt runs on rtt_asym
with trace logging (`LOG_LEVEL=trace` logs, per path, whether the pacer or the
congestion window blocks sending). Rate so far: 1 in 26 roundrobin runs, 0 in
25 minrtt runs. Week 2 must detect stalled runs rather than average them in.
