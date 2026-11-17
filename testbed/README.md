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
adds RTT/2 of delay, then an `htb` class limits the rate, with a plug queue as
the bottleneck buffer of
`QUEUE_MS` (default 200 ms) worth of bytes. Both directions of a path therefore
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

TQUIC is the submodule `third_party/tquic`, based on tag v1.6.0 (d87a9a0), on local
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
testbed/up.sh rtt_asym
```

```bash
testbed/down.sh
```

Scenarios (`docs/proposal.md` section 4; rate in kbit/s / RTT in ms; every
path has a 200 ms buffer, `QUEUE_MS`):

| scenario | path A | path B | changes over time |
| --- | --- | --- | --- |
| `control` | 4000 / 40 | 4000 / 40 | no |
| `symmetric` | 1600 / 40 | 1600 / 40 | no |
| `rtt_asym` | 1600 / 30 | 1600 / 150 | no |
| `bw_asym` | 2000 / 40 | 1200 / 60 | no |
| `outage` | 1800 / 40 | 1800 / 60 | `replay.py`: every 15 s one path, in turn (A at 15 s, B at 30 s, ...), drops to 8 kbit/s for 2 s, both directions |
| `real_pairs` | trace | trace | `SITE=<dir> testbed/up.sh real_pairs`; `replay.py` sets the data direction (client -> server) from the traces every 100 ms |

Override any value with env vars, e.g. `QUEUE_MS=50 testbed/up.sh symmetric`.
Data flows client -> server: the lecturer's device is the client. For
`real_pairs`, `up.sh` sets each path's delay to the site's unloaded RTT and its
rate (both directions) to the trace mean, which also sizes the buffer; the
reverse direction stays at the mean during replay. Site format:
`traces/<site>/site.json` + one csv per path (`docs/trace_collection.md`);
`python3 testbed/trace_site.py stats <site>` summarises one.

Replay (after `up.sh outage` or `up.sh real_pairs`; stops after
`--duration-s` or on SIGTERM; `--offset-ms` starts the schedule later):

```bash
python3 testbed/replay.py /tmp/replay.csv --duration-s 70
```

The log has one row per rate change (`mono_us,t_ms,dev,kbit`, CLOCK_MONOTONIC)
plus every device's starting rate.

Inspect by hand:

```bash
sudo /usr/bin/ip netns exec mpq-srv tc -s qdisc show
```

```bash
sudo /usr/bin/ip -n mpq-cli -s link show
```

## Calibration

```bash
testbed/calibrate.sh rtt_asym 1
```

```bash
SITE=public_mahimahi OFFSET_MS=300000 testbed/calibrate.sh real_pairs 1
```

```bash
analysis/.venv/bin/python analysis/calibration.py results/calibration/rtt_asym/1
```

Method, results and the shaper history (tbf -> htb -> htb + plug):
`docs/calibration.md`.

## Week 1, check 3: multipath transfer over both paths

```bash
testbed/run_transfer.sh symmetric
```

```bash
testbed/run_transfer.sh rtt_asym
```

Each call runs `RUNS` (default 5) downloads of a 10 MiB file (content from a
fixed seed) per scheduler (default `minrtt roundrobin`; pass names to choose).
Every run rebuilds the testbed, so queues and counters start empty. Env vars:
`RUN_START` (first run_id; existing runs are never overwritten), `CC`
(default `cubic`), `TIMEOUT_S` (default 300), `LOG_LEVEL` (`trace` logs per
path why the sender holds back; debug/trace logs are gzipped). Reprint the
table with:

```bash
python3 analysis/transfer_summary.py table results/week1_transfer/symmetric
```

Per run, `results/week1_transfer/<scenario>/<scheduler>-<cc>/<run_id>/` holds
`meta.env` (link parameters, TQUIC and repo commits, exit code, file check,
transfer time), `summary.csv`, before/after counter snapshots, logs, and qlogs
(git-ignored).

How "uses both paths" is measured: TQUIC's qlog events carry no path id, so
per-path bytes come from the kernel's veth counters (`down_bytes_a/b` = bytes
received on `cA`/`cB` during the transfer). `drops_a/b` are drops in the
server-side bottleneck queue. The file flows server to client, so the
scheduler that matters is the server's `--multipath-algor`; both ends get the
same value. `stalled = 1` marks a run whose file did not arrive complete;
such runs are reported, never averaged in.

## Week 1 results (1 Nov 2026)

TQUIC `team7` c51c237 (v1.6.0 + stall fix + frame_path log), CUBIC, 10 MiB
download, scenarios as above (1.6 + 1.6 Mbit/s, 200 ms buffers).

| scenario | scheduler | runs | file OK | stalled | transfer time | goodput | share A | share B |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| symmetric (40/40 ms) | minrtt | 5 | 5 | 0 | 28.6-29.2 s | 2.87-2.93 Mbit/s | 50.1% | 49.9% |
| symmetric | roundrobin | 5 | 5 | 0 | 28.6-28.7 s | 2.93-2.94 Mbit/s | 50.1% | 49.9% |
| rtt_asym (30/150 ms) | minrtt | 5 | 5 | 0 | 29.0-29.4 s | 2.85-2.90 Mbit/s | 50.6-50.7% | 49.3-49.4% |
| rtt_asym | roundrobin | 5 | 5 | 0 | 28.9-29.2 s | 2.87-2.90 Mbit/s | 50.5-50.7% | 49.3-49.5% |

Goodput is about 90% of the 3.2 Mbit/s the two paths carry together (headers,
slow start). In a bulk transfer both congestion windows stay full, so even
MinRTT on rtt_asym splits about evenly: it falls through to the 150 ms path
whenever the 30 ms path's window is full. That is packet-level splitting, the
behaviour the frame schedulers are meant to improve on for video.

The 28 Oct runs (old presets, BBR, one stall in 26 round-robin runs on
rtt_asym) are in git history (5fc8f5a) and on branch `archive/old-design`.
