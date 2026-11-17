# Team 7 ACN project: frame-aware multipath QUIC

## What this project is
A controlled measurement-and-design study: how much of two weak uplinks can an
interactive live lecture use over Multipath QUIC, and does placing whole video
frames on paths (instead of single packets) recover what packet-level schedulers
waste? Full design, hypotheses, scenarios and metrics: `docs/proposal.md`
(revised 1 Nov 2026). There is no ML component.

Hypotheses H1-H5, their thresholds and the primary comparisons P1-P6
(proposal section 2) are fixed. Never change them after experiments start. If
you think one is wrong, tell the user before collecting data. Negative results
are reported, not hidden.

## Start of every session
1. Read `docs/PROGRESS.md` (create it if missing): the running log of what
   exists, what works, what failed and what is next.
2. Check `git status` and `git log --oneline -15`.
3. At the end of every work block, append a dated entry to `docs/PROGRESS.md`
   (done / evidence / next / open issues) and commit.

## Environment
- WSL2 Ubuntu 24.04 on a Windows laptop, kernel 6.18; netem verified (a 25 ms
  netem delay on lo gave 50 ms ping). Repo at `~/acn-mpquic` on the Linux
  filesystem; never work under `/mnt/c` (slow, breaks permissions). The user
  works in VS Code connected to WSL; its terminal (Ctrl+`) is an Ubuntu shell.
- Run `sudo modprobe -a sch_netem sch_tbf sch_htb` after every WSL restart
  (without `-a`, modprobe loads only the first module and takes the other
  names as its parameters; `testbed/up.sh` does this for you).
- Root is needed only for `ip`, `tc`, `modprobe`. Test with `sudo -n true`. If
  sudo needs a password, give the user one exact command to paste into the VS
  Code terminal; never edit sudoers yourself.
- WSL can restart and wipe namespaces: `testbed/up.sh` must be idempotent.
- Long batches: remind the user to keep the laptop plugged in and awake.
- The user prefers that you run commands and do hands-on steps yourself.

## Stack
- QUIC: TQUIC (Rust), https://github.com/Tencent/tquic, git submodule at
  `third_party/tquic`; our changes on branch `team7`. Fallback only if TQUIC
  multipath fails by the week-1 gate: picoquic (C).
- Emulation: network namespaces, veth pairs, `tc netem` (delay) + `tbf` (rate,
  buffer = 200 ms of the path's rate); trace replay changes rate every 100 ms.
- Workload: ffmpeg/x264 + ffprobe (frame sizes, types, timestamps).
- Analysis: Python (pandas, matplotlib, scipy) in a venv at `analysis/.venv`.

## TQUIC facts (checked in source on 1 Nov 2026; re-verify line numbers)
- Scheduler trait: `src/multipath_scheduler/multipath_scheduler.rs`,
  `on_select(paths, spaces, streams) -> Result<usize>`, `on_sent`,
  `on_path_updated`. Built in: MinRtt, Redundant, RoundRobin. New scheduler =
  new file + enum variant + `FromStr` + `build_multipath_scheduler` +
  `buffer_required`.
- Stats: `Path::stats() -> &PathStats` (`srtt`, `min_rtt`, `rttvar`,
  `pacing_rate`, cwnd fields), `recovery.bytes_in_flight`, `recovery.can_send()`.
  Public: `Connection::get_path_stats`, `paths_iter`, `add_path`,
  `stream_set_priority`.
- Known behaviours that affect results:
  1. `try_write_stream_frames` (connection.rs ~2549) takes streams from one
     global queue (`streams.peek_sendable()`) whatever the path.
  2. When `on_select` declines, `select_send_path` falls back to the active path
     (connection.rs ~3021) and `send_packet` writes stream data there if cwnd
     allows, silently overriding a scheduler's decision to wait.
  3. Stream default priority is urgency 127, incremental=true (stream.rs ~1830):
     concurrent streams are round-robined.
  4. CUBIC `pacing_rate` = cwnd/srtt (cubic.rs ~420); recovery passes
     app_limited=false to `on_ack` (recovery.rs ~368), so cwnd grows while the
     sender is application-limited (RFC 9002 section 7.8 says it should not).
  5. No reinjection on a dead path: loss detection needs a later ACK,
     `in_persistent_congestion` is a stub (recovery.rs ~417), PTO re-sends at
     most 2 packets with backoff.
  6. SendBuf reads from one `send_off`, so one stream cannot be sent from two
     offsets on two paths; split frames use two streams.
- The three hooks we add (week 2, each unit-tested, each small and commented):
  a. Stream-to-path filter: `Connection::stream_set_path_preference(stream_id,
     Option<path_id>)`, honoured in `try_write_stream_frames` via a path-aware
     peek that skips streams preferred for another active path.
  b. Declined-send flag: when the scheduler chooses to wait, the fallback path
     may send control frames/ACKs but not stream data (needed for ECF and F1).
  c. RFC 9002 7.8 app-limited rule for CUBIC (no cwnd growth when not
     cwnd-limited), applied to every scheduler, only if a test confirms (4).
- Every scheduler sets every frame stream to non-incremental priority (strict
  frame order).

## Schedulers and references (IDs used in code, results paths, plots)
| ID | What | Where |
| --- | --- | --- |
| S0 | best single path (higher mean capacity) | multipath off |
| S1 | MinRTT | TQUIC built-in |
| S2 | round-robin | built-in |
| S3 | redundant | built-in |
| S4 | ECF (Lim et al., CoNEXT 2017) | new TQUIC scheduler + hook b |
| F1 | whole frame on path with earliest predicted finish | streamer + hooks a, b |
| F2 | F1 + balanced split + rescue | streamer + hooks a, b |
| A1 | F2 logic over two separate single-path QUIC connections | streamer only |
| R1 | ideal link: one path, rate = sum, RTT = lower of the two | testbed only |
| R2 | F2 using the true trace rates instead of estimates | streamer |

Finish-time estimate for a frame part of S bytes on path p (as designed on 1 Nov;
as implemented since 17 Nov the queueing term is taken from bytes in flight, see
the note below and docs/report.md 3.4):
`T_p = (U_p + S)/R_p + (srtt_p - min_rtt_p) + min_rtt_p/2`
U_p = bytes assigned to p not yet sent. R_p = measured delivery rate: bytes
acknowledged on p per interval, max over the last 1 s (do NOT use TQUIC
pacing_rate). F2 split, used whenever it lowers predicted finish (never depends
on D), with d_p = (srtt_p - min_rtt_p) + min_rtt_p/2, clamped to [0, S]:
`S_a = ((U_b + S)/R_b + d_b - U_a/R_a - d_a) / (1/R_a + 1/R_b)`.
Implementation note (17 Nov): T_p = (W_p + S)/R_p + min_rtt_p/2 with W_p = U_p +
max(0, inflight_p - R_p * min_rtt_p); srtt lags behind the bottleneck queue
when CUBIC's window is several BDPs (streamer/src/lib.rs bottleneck_queue_bytes).
Rescue (F2, A1): if a path shows no ACK progress for one PTO, resend its
unfinished frame parts as new streams on the other path. Each stream starts with
a small header `{frame_id, offset, length, frame_size}`; the receiver completes a
frame when the union of received ranges covers [0, frame_size).
Unit-test the estimator, split, and receiver reassembly with hand-computed cases.

## Measurement definitions (implement exactly)
- Deadline: `capture_ts + D`; D swept 100..1000 ms offline; primary D = 250 ms,
  also report 150 and 400 ms.
- Display rule: frame i is on time iff max(completion time over i and every
  frame it depends on) <= its deadline. Dependencies: periodic keyframes = all
  frames since the last I-frame; intra-refresh = all earlier frames.
- Freeze: gap between consecutive displayed frames >= max(3 * frame_interval,
  frame_interval + 150 ms); freeze time = sum of such gaps. At 25 fps
  (40 ms interval) the threshold is 190 ms.
- Required playout delay: smallest D with >= 99% frames on time.
- Usable capacity: highest swept bitrate with freeze time <= 1% at D = 250 ms,
  divided by R1's usable capacity in the same scenario.

## Workload and scenarios
- Main clip: `stochastic` (60 s camera lecture at a blackboard, NPTEL, CC
  BY-SA; 25 fps). Controls: `handwriting`, `slides`, `linalg` (second
  blackboard lecture, no camera cut). Clips, windows, credits:
  `workload/README.md`. Videos stay out of git (`workload/download.sh`).
- Encodings (x264 zerolatency, CBR, vbv-maxrate = bitrate, vbv-bufsize = 0.5 s):
  main = intra-refresh; stress = keyframe every 2 s. Record exact ffmpeg commands.
- Main bitrate 2.6 Mbps; sweeps 0.5 Mbps to 1.1x combined capacity (6 points).
- Scenarios: control 4+4 Mbps @40/40 ms; symmetric 1.6+1.6 @40/40; rtt_asym
  1.6+1.6 @30/150; bw_asym 2.0+1.2 @40/60; outage 1.8+1.8 @40/60 with each path
  in turn at 0 for 2 s every 15 s; real_pairs = simultaneous two-operator traces
  from 3 sites (RTT values = RTT, split evenly per direction).

## Experiments
- One command per run: `scripts/run.sh <scenario> <config> <bitrate_kbps>
  <encoding> <clip> <run_id>`; batch drivers loop over it.
- Output: `results/<scenario>/<config>/<bitrate>_<encoding>_<clip>/<run_id>/`
  with `frames.csv`, `paths.csv`, qlog, `cpu.csv`, `meta.json` (git hashes of
  repo and TQUIC, seed, clip offset, trace offset, tool versions, start time).
- 8 runs per cell; clip start and trace start drawn from a fixed seed per run_id
  and identical for every config (paired design).
- Stats: P1-P6 paired Wilcoxon + Holm; equivalence claims TOST with 5% margin;
  everything else 95% bootstrap CIs.
- Budget ~1,240 runs (~25 h): main 10 configs x 8 scenarios x 8; stress
  (S1, S4, F1, F2, R1) x (rtt_asym, bw_asym, 1 real site) x 8; sweeps
  (S1, S4, F2, R1) x (rtt_asym, 1 real site) x 6 bitrates x 8; controls
  (S0, S1, F2, R1) x rtt_asym x 3 contents (handwriting, slides, linalg) x 8.
- Discard and rerun any run whose sender or receiver CPU exceeds 80%; log it.
- Never hand-edit or delete results; failed runs go in PROGRESS.md.

Per-frame log (`frames.csv`, one row per frame, one clock):
`frame_id, frame_type, size_bytes, capture_ts_us, send_start_us,
complete_ts_us, bytes_path0, bytes_path1, rescued, config, scenario,
bitrate_kbps, encoding, clip, run_id`.

## Repo layout
```
third_party/tquic/   submodule; our scheduler + hooks on branch team7
streamer/            Rust sender/receiver, frame parts as streams
testbed/             up.sh, down.sh, replay.py, calibrate.sh
scripts/             run.sh and batch drivers
traces/              csv: t_ms, mbps, rtt_ms; one file per path per site
workload/            download script, encode script, frame traces (csv)
analysis/            playout model, metrics, stats, plots
results/             raw runs, never edited by hand
docs/                proposal.md, PROGRESS.md, trace_collection.md
```

## Plan and gates (final demo 17 Nov 2026)
- Week 1 (to Nov 4), gate = calibrated two-path testbed: TQUIC builds, tests
  pass; `testbed/up.sh`/`down.sh`; calibration report; multipath transfer using
  both paths under MinRTT and round-robin; `docs/trace_collection.md` (cloud VM
  iperf3 server setup, phone steps with Termux iperf3 UDP offered above
  capacity, ping alongside, 10-15 min per site, ~1 GB per phone per site, UDP
  reachability check); encode the main clip in both modes.
- Week 2 (to Nov 11), decision point: streamer + analysis pipeline; hooks a-c
  with unit tests; S0-S4 on synthetic scenarios; RQ1 from traces (3 sites by
  Nov 9). Report H1 and H2 outcomes to the user before building F1/F2.
- Week 3 (Nov 12-14): F1, F2, A1, R2; full matrix; sweeps; stress case
  (overnight batches).
- Nov 15-17: statistics, plots, report, slides; final demo 17 Nov. Stretch
  items (BBR, 50 ms buffer, real-world spot check) only if time is left.

## Rules
- Change TQUIC only in `src/multipath_scheduler/` plus hooks a-c; keep changes
  small, commented, unit-tested.
- Small commits with clear messages.
- Do not invent numbers or results. If something doesn't work, say so plainly.
