# Progress log

Running log of what exists, what works, what failed and what is next. Newest
entry last. Design and hypotheses: `docs/proposal.md` (revised 1 Nov 2026).

## 2026-11-01: audit after the 1 Nov redesign

Context: the previous Claude Code session was lost. `CLAUDE.md` and
`docs/proposal.md` were replaced with the 1 Nov versions from the user's
Downloads folder (the previous working-tree copies are not in git; the
committed versions are in 5fc8f5a). Everything below was checked today, not
copied from the old `CLAUDE.md`.

### Git state
- One commit on branch `week1` (5fc8f5a, 28 Oct): TQUIC submodule at stock
  v1.6.0 (d87a9a0), testbed scripts, Week 1 transfer runs.
- Not committed (left as is, nothing deleted): work from the old design
  (28-31 Oct, deadline scheduler and ML predictor):
  - 787 staged renames `results/<scenario>/...` ->
    `results/week1_transfer/<scenario>/...`, plus edits to `testbed/up.sh`,
    `testbed/run_transfer.sh`, `testbed/README.md`, `.gitignore`;
  - untracked: `streamer/` (mpq_sender/mpq_receiver, ~690 lines),
    `analysis/{frames,compare,plots,report_numbers}.py`, `analysis/ml/`,
    `testbed/{replay,replay_spec,train_spec}.py`, `testbed/run_stream.sh`,
    `docs/report/`, `docs/slides/`, three status PDFs, 1.1 GB in `results/`
    (old-design matrices, ML training data);
  - TQUIC submodule checked out at branch `team7` (10b826e), 9 commits above
    v1.6.0: frame_path log hook, ECF, deadline/framepin/learned schedulers,
    ML model, and a fix for a `select_send_path` stall when a path being
    validated has a full cwnd (5a82889). The parent repo still records d87a9a0.
- None of the old results answer the new hypotheses (different scenarios,
  workload, encodings, metrics); they are not evidence for H1-H5.

### Environment (checked 1 Nov)
| Item | State |
| --- | --- |
| Kernel | 6.18.54.1-microsoft-standard-WSL2, Ubuntu 24.04.4 |
| sch_netem, sch_tbf, sch_htb | modules (`=m`); load with `modprobe -a ...`. Plain `modprobe sch_netem sch_tbf sch_htb` loads only netem (the rest are taken as module parameters); `testbed/up.sh` already uses `-a` |
| sudo | passwordless for `/usr/bin/ip`, `/usr/sbin/tc`, `/usr/sbin/modprobe` (works); `sudo -n true` needs a password (not needed) |
| Rust | cargo/rustc 1.99.1 |
| ffmpeg/ffprobe | 6.1.1 (libx264) |
| iperf3 | 3.16 |
| Python | 3.12.3; venv created at `analysis/.venv` with pandas 3.0.1, matplotlib 3.10.8, scipy 1.17.1 |

### Week 1 checklist (CLAUDE.md "Plan and gates")
| Item | Status | Evidence / gap |
| --- | --- | --- |
| TQUIC builds, tests pass | done for `team7` (10b826e) | `cargo build --release --all` ok; `cargo test --release`: 566 + 1 passed, 0 failed (1 Nov). Stock v1.6.0 passed 552/552 on 28 Oct (commit 5fc8f5a message) |
| `testbed/up.sh` / `down.sh` | works, old presets | up, rerun and down all clean; ping 40.1 ms min on both paths for a 40 ms preset. Presets are the old ones (4 Mbit/s, 100 ms queue); new scenarios (control, symmetric 1.6+1.6, rtt_asym 1.6+1.6 @30/150, bw_asym, outage) and the 200 ms buffer are missing |
| Calibration report | missing | `testbed/check.sh` reports mean rate (5 s TCP iperf3) and mean ping only; no 100 ms bins vs trace, no report |
| Multipath transfer, both paths, MinRTT and round-robin | done under old presets | `results/week1_transfer/`: symmetric and rtt_asym (4 Mbit/s paths), both paths 48-52% of bytes, 1 of 62 transfers stalled (rtt_asym/roundrobin). Not repeated on the new scenarios |
| `docs/trace_collection.md` | missing | `traces/collect/README.md` + `iperf3_to_csv.py` exist (old, downlink `-R`, 5 min, no ping, no VM setup) |
| Encode main clip, both modes | missing | No NPTEL clip chosen or downloaded; `workload/make_frame_trace.sh` does CRF + 2 s GOP only (no CBR, no intra-refresh) |
| First trace site | not started | needs phones and a cloud VM (teammates) |

### Next
Week 1 plan to be agreed with the user (see chat on 1 Nov).

## 2026-11-01: Gate A (TQUIC builds, tests pass): passed

Done
- Old-design work archived, nothing deleted:
  - code and docs: branch `archive/old-design` (6da75fb) in this repo;
  - results (1.1 GB), old binaries, old encodes and the old streamer build:
    moved to `~/acn-archive/` (outside the repo). `results/` in the repo is
    empty; the 28 Oct transfer runs remain in git history (5fc8f5a).
  - TQUIC: old branch renamed `team7-old` (10b826e).
- New TQUIC branch `team7`, base **d87a9a0** (tag v1.6.0, "Merge pull request
  #438 from Tencent/release/v1.6.0"), two commits:
  - 108bc38 stall fix in `select_send_path` + unit test;
  - c51c237 `tquic::frame_path` debug log of every sent STREAM frame with its
    path id (logging only; off unless that log target is enabled).
- CLAUDE.md: `modprobe -a` (approved by the user).

Evidence
- `cargo build --release --all`: ok. `cargo test --release` and `cargo test`
  (debug): 552 + 1 passed, 0 failed.
- New test `conn_multipath_probing_path_cwnd_full` on stock v1.6.0 code:
  **fails** (`assertion failed: !packets.is_empty()`: the client sends no
  packet at all); with the fix: passes.

The stall fix, and how it relates to hook b
- Bug (v1.6.0, connection.rs `select_send_path`, first block): once the
  connection is established, any path that needs PATH_CHALLENGE/RESPONSE is
  returned first, without checking its congestion window. `send_packet` checks
  the window before it writes validation frames, so if that path's window is
  full the packet is empty and `send()` returns Done. The next `send()` picks
  the same path again, so nothing (stream data, ACKs) leaves on any path until
  that path's window opens, which needs ACKs that are not being sent. Seen in
  the old design on trace replay with path B near 8 kbit/s during validation.
- Fix: such a path is skipped while `recovery.can_send()` is false; selection
  falls through to the normal multipath branch (scheduler, then ACK/PTO path,
  then active path). Validation frames go out as soon as the window opens (the
  test checks this too).
- Effect on schedulers: identical for all of them; it only matters during path
  validation, before the second path becomes active, so it cannot favour any
  scheduler. Without it, a transfer could stall for reasons unrelated to the
  scheduler under test.
- Hook b (declined-send flag, not built yet) acts in a different place: after
  `on_select` declines, the fallback to the ACK/PTO path or the active path must
  not carry stream data. The fix changes only the probing block that runs
  before `on_select`. The two cannot conflict: when the fix skips a probing
  path, control reaches `on_select`, and if that declines, hook b applies as
  designed. Hook b blocks stream data only, so PATH_CHALLENGE/RESPONSE on a
  fallback path stays allowed. Hook b's unit tests (week 2) will rerun
  `conn_multipath_probing_path_cwnd_full`.

Next: Gate B (scenario presets, 200 ms buffer, outage and trace replay).

## 2026-11-01: Gate B (scenario presets, replay): passed

Done
- `testbed/up.sh`: scenarios control, symmetric, rtt_asym, bw_asym, outage,
  real_pairs (proposal section 4), buffer 200 ms of each path's rate.
- `testbed/replay.py` (adapted from the archived one): outage schedule and
  trace replay; logs each device's starting rate and every change.
- `testbed/trace_site.py` + site format `traces/<site>/site.json` and csv
  `t_ms,mbps,capped,loaded_rtt_ms` (CLAUDE.md's layout line says `t_ms, mbps,
  rtt_ms`; the RTT is now split into unloaded (site.json, used as the replay
  delay) and loaded (csv, reference only), as the user specified on 1 Nov).
- `traces/public_mahimahi/`: two public Mahimahi uplink traces (AT&T LTE,
  T-Mobile UMTS) as a calibration fixture only; their RTTs (40/60 ms) are
  configured, not measured. Not evidence for any hypothesis.

Evidence
- Every scenario comes up; `tc` shows the configured netem delay (RTT/2) and
  tbf rate on each veth end. State file records scenario, site, rates, RTTs.
- Outage schedule computed over 64 s: drops start at 15 s (A), 30 s (B),
  45 s (A), 60 s (B), each lasting 2.0 s. A 3 s replay from offset 14.5 s set
  cA and sA to 8 kbit at t = 500 ms and back to 1800 kbit at t = 2500 ms.
- real_pairs replay from offset 60 s applied the trace bin by bin (cA:
  1560, 1200, ... kbit, matching rows 601-621 of the trace); cB went to
  8 kbit (a zero bin, clamped to the tbf minimum).

Interpretation choices (not hypotheses; flagged to the user)
- Outage "each path in turn at 0 for 2 s every 15 s": implemented as one
  outage every 15 s, alternating A, B, A, ... (each path once per 30 s), both
  directions, "0" = 8 kbit/s (tbf minimum; about one 1500-byte packet per
  1.5 s).
- real_pairs: traces shape the data direction (client -> server); the reverse
  direction (ACKs) stays at the trace mean; buffer bytes fixed at 200 ms of the
  trace mean.

Next: Gate C (calibration report).

## 2026-11-01: Gate C (calibration report): passed

Done
- `testbed/calibrate.sh` (unloaded ping, iperf3 UDP above capacity with
  100 ms receiver bins, loaded ping) and `analysis/calibration.py`.
- `docs/calibration.md`: method, results, what failed first.
- Shaper changed twice because calibration caught errors:
  1. tbf -> htb: tbf refills its bucket on every `tc change`, so trace replay
     delivered 9-25% too much (1 s medians). Also affects the archived old
     design's trace results.
  2. 8 kbit/s "zero" -> plug block: htb's time-based token debt stalled paths
     for up to ~1.2 s after zero bins.

Evidence (final shaper, runs in `results/calibration/`)
- Unloaded RTT: median 0.0-0.4 ms above configured on every path.
- Static rates: mean within 0.2%; 1 s windows within 0.6%.
- Outage: zero-rate bins measure 0; mean within 0.1%.
- Trace replay (public Mahimahi uplink, 3 offsets): mean within 1.5%; 1 s
  windows within about 3% except on near-zero stretches of trace B (p95 +10.9%
  and +19.5%, few packets per second).
- Buffer: loaded RTT = configured RTT + 199-200 ms on four paths.

Failed / excluded runs
- real_pairs_public_mahimahi/7: path-B iperf3 client lost its TCP control
  connection (trace B mostly zero at offset 0); script aborted before
  meta.env; not analysed. Run 10 repeated it (same client error, server log
  complete, used).
- symmetric/1: taken before calibrate.sh had run ids and tc stats (moved into
  `symmetric/1` by me; files unchanged; I briefly appended a note to its
  meta.env and removed it again).
- tbf runs (symmetric/1-2, control/1, rtt_asym/1, bw_asym/1, outage/1,
  real_pairs/1-3) and first htb runs (symmetric/3, */2, real_pairs/4-6) are
  kept as evidence for the shaper changes; not used as calibration.
- The tbf runs record REPO_GIT=c9fadae although calibrate.sh was not yet
  committed (the dirty check covered tracked files only).

Next: Gate D (multipath transfers on the new scenarios, CUBIC).

## 2026-11-01: Gate D (multipath transfer, both paths, MinRTT and RR): passed

TQUIC multipath works; no picoquic fallback needed.

Evidence (`results/week1_transfer/{symmetric,rtt_asym}/{minrtt,roundrobin}-cubic/1-5`,
TQUIC c51c237, CUBIC, 10 MiB download)
- 20 of 20 transfers complete, file identical, 0 stalled.
- Both paths carry 49.3-50.7% of the bytes in every run (veth counters).
- Goodput 2.85-2.94 Mbit/s of 3.2 Mbit/s combined; table in
  `testbed/README.md`.
- Direction: download (server sends), as in TQUIC's tools; the static
  scenarios shape both directions alike. The streamer (week 2) sends client
  -> server.

## 2026-11-01: trace kit (`docs/trace_collection.md`): written

- `docs/trace_collection.md` for teammates: VM setup (iperf3 on 5201/5202,
  firewall incl. ICMP), Termux setup, UDP reachability check, recording
  command (30 s idle ping, 600 s UDP 15 Mbit/s 1200 B with ping alongside,
  30 s idle ping), site log template, hand-off.
- `traces/collect/vm_server.sh` (VM: one iperf3 server per phone, 100 ms bins,
  first-packet time on the VM clock) and `traces/collect/make_site.py`
  (raw files -> `traces/<site>/a.csv, b.csv, site.json`; capped flag = bin
  within 5% of the offered rate; unloaded RTT = median of idle pings; loaded
  RTT per bin for reference).
- make_site.py checked end to end on the testbed (server namespace standing in
  for the VM, bw_asym): IP-level rates 1.97 / 1.18 Mbit/s for 2000 / 1200
  kbit/s wire, unloaded RTT 40.3 / 60.3 ms, loaded 240 / 263 ms; capped flag
  99% when the offered rate was set at capacity. The test site was deleted.
  vm_server.sh not run here (needs tcpdump, not installed in WSL); its syntax
  was checked only.
- Not verified: Termux/Android `ping -D` output format and minimum interval;
  the guide tells teammates to check `ping -D` first.

## 2026-11-01: encodings (Week 1 item 6): done, main clip to be chosen

Done
- `workload/clips.csv` (3 blackboard candidates, 2 controls), `download.sh`,
  `pick_start.sh`, `encode.sh` (intra / gop2), `frame_stats.py`, README with
  the frame-size table. Frame traces and `.cmd.txt` (exact command, x264
  option string) committed; videos are git-ignored.
- Encodes checked: x264 options in the stream show rc=cbr, bitrate=2600,
  vbv_maxrate=2600, vbv_bufsize=1300, bframes=0, rc_lookahead=0, keyint=50,
  scenecut=0, intra_refresh=1 (intra), nal_hrd=none, filler=0. Intra mode has
  exactly one I-frame (frame 0) in every clip.

Problems found and fixed
- First intra encodes had a scene-cut I-frame (stochastic, handwriting):
  x264 still inserts them with intra refresh; now scenecut=0.
- encode.sh's inline Python failed on escaped quotes; replaced by awk.
- Slides control: the median-complexity window (2440 s) was tablet writing;
  now 3050 s, picked by the same rule within the slides-only stretch.

For the user to decide / note
- Main clip: stochastic, linalg or probability (table in workload/README.md).
- License: the YouTube pages say CC BY; NPTEL's policy is CC BY-SA since Sep
  2014 (CC wiki). No page found that states CC BY-NC-SA, as CLAUDE.md says.
- Frame rate: sources are 25 fps; encoded at 25, not the proposal's 30.
- No CBR filler; controls average well below 2.6 Mbit/s.
- Camera cuts make large P-frames even in intra-refresh mode (relevant to H5).

Week 1 status: all gate items done except the first trace site (teammates).
Open: choose main clip; record traces; CLAUDE.md "Main clip" line (license,
fps) once the user decides.

## 2026-11-01: user decisions on clip, licence, fps, outage

Decided by the user (1 Nov):
- Main clip `stochastic`; `linalg` added to the content-control cell (S0, S1,
  F2, R1 x rtt_asym x 8) to show results do not depend on the camera cut.
  Windows were fixed by the median-size rule before any scheduler run
  (recorded in workload/README.md, with an attribution list).
- Licence: CC BY-SA (NPTEL since Sep 2014) in CLAUDE.md and proposal.
- 25 fps accepted: proposal now says frame interval 40 ms; the freeze rule is
  unchanged, which at 25 fps gives a 190 ms threshold.
- Outage reading confirmed: alternate A, B, A, ... every 15 s.
- Budget: content controls 3 contents x 4 configs x 8 = 96 runs (was 64);
  total ~1,240 runs.
Edited by me at the user's request: docs/proposal.md (licence, fps, freeze
threshold at 25 fps, controls, budget) and CLAUDE.md (same). Hypotheses
H1-H5, thresholds and P1-P6 untouched. The claude.ai copy of the proposal
(linked at its top) was not updated.

## 2026-11-01: week 2, TQUIC hooks a-c and ECF: done

TQUIC `team7` now (on d87a9a0 = v1.6.0): 108bc38 stall fix, c51c237
frame_path log, a218a69 hooks a+b, 6024ae7 hook c, a98f01d ECF.
`cargo test --release`: 563 + 1 passed.

CLAUDE.md "TQUIC facts" checked in source (1 Nov): 1 (global stream queue),
2 (fallback writes stream data after a declined on_select), 3 (urgency 127,
incremental), 4 (app_limited=false to on_ack; CUBIC pacing from cwnd),
5 (persistent congestion stub) all hold; line numbers have shifted.

- Hook a, `Connection::stream_set_path_preference(stream_id, Some((local,
  remote)))`: packets for another active path skip the stream; it is put
  back in the queue afterwards. A preference for an inactive path is
  ignored. Note for F1/F2: the scheduler must also pick the preferred path,
  or the data waits (a path-unaware scheduler can starve such streams).
- Hook b: after a declined on_select the fallback packet carries no stream
  data, except PTO probes. Built-in schedulers decline only when no active
  path can send, so they behave as before (their fallback could only send
  stream data in probes, which stay allowed).
- Hook c: fact 4 confirmed by a test (cwnd 13472 -> 40162 bytes while bytes
  in flight stayed under a quarter of the window), so the RFC 9002 7.8 rule
  is applied for every scheduler: an ACK is application-limited unless bytes
  in flight before it were >= half the window or within 3 packets of it.
  A second test checks that a window-limited sender still grows. The first
  version of that test was wrong (the test config's 90-byte flow-control
  window kept the sender below the window); fixed by raising the
  receiver's limits.
- Each hook test fails with its hook disabled (checked), except the
  inactive-path test, which covers the "ignore" case.
- ECF ported from team7-old (6 unit tests).

Not yet checked: bulk transfer goodput on the testbed with hook c (unit
test only). The streamer runs will show it.

## 2026-11-01: week 2, streamer, run script, analysis: done; S0-S4 batch started

Done
- `streamer/` rewritten for the new design, reusing the archived event loops
  and logger: sender = QUIC client (lecturer side, adds one path per local
  address), receiver = QUIC server. Each frame part is a stream with a
  16-byte header {frame_id, offset, length, frame_size}; the receiver
  completes a frame when the union of received ranges covers it. Streams are
  non-incremental at one urgency (strict frame order). Unit tests: header,
  range reassembly (out of order, rescue overlap, duplicates), clip rotation.
- TQUIC f60aecc: frame_path log line also gives the path's local address
  (path ids are internal; logging only).
- `scripts/run.sh <scenario> <config> <bitrate> <encoding> <clip> <run_id>`:
  S0 (single best path), S1 minrtt, S2 roundrobin, S3 redundant, S4 ecf;
  clip offset (multiple of 50 frames) and trace/outage offset from a seed per
  run_id (paired); frames.csv in the CLAUDE.md format, paths.csv, cpu.csv,
  meta.json (git hashes, binary hashes, offsets, exits, CPU max, veth bytes,
  stalled flag). Exit 3 = stalled, 4 = CPU > 80%.
- `scripts/batch.sh`: fixed binary snapshot, run_id-major order, resume,
  CPU-rule rerun (discarded runs moved to results/_discarded/), log.
- `analysis/playout.py` (+ 11 hand-computed tests; my first expected values
  for one case were wrong, the code was right: under intra refresh frame 9
  depends on frames 4-5), `analysis/summarize.py` (one row per run).

Evidence
- Dev runs (run_id 0, not part of any analysis): symmetric/S1 and
  rtt_asym/S0-S4: all 1500 frames complete; CPU max 12-28%; per-path stream
  bytes match veth counters within protocol overhead.

Batch `week2_s0s4` (started 1 Nov evening): SCENARIOS control symmetric
rtt_asym bw_asym outage x CONFIGS S0-S4 x RUN_IDS 1-8, 2600 kbit/s, intra,
stochastic. Log: results/batches/week2_s0s4.log. Expected ~4-5 h.

## 2026-11-01 evening: first S0-S4 batch stopped; two bugs fixed; batch restarted

The first batch (`week2_s0s4`, build 40d0216 / TQUIC f60aecc) was stopped
after 23 runs. Its runs are invalid and were moved, unchanged, to
`results/_superseded/week2_s0s4_build40d0216/` (with its log); not analysed.

1. Sender bug (mine): `stream_write` returns Done when the connection has no
   send capacity (here: during a 2 s outage); the sender treated it as fatal
   and exited ("Error: Done"; outage/S0/1 completed 165 of 1500 frames).
   Now Done = nothing written, the frame waits for on_stream_writable.
   build_frames.py now skips (and counts) a log line cut short by a crash.
2. Hook c bug (mine, TQUIC 4d7481d): CUBIC's on_ack returned before updating
   pacing_rate = cwnd/srtt on application-limited ACKs. After an outage the
   rate set while srtt was ~2 s stayed frozen (0.21 Mbit/s while cwnd was
   47 KB and srtt back to 45 ms), the sender stayed pacing-limited, every ACK
   looked application-limited, and the path never recovered (outage dev run
   0: 717 of 1500 frames). RFC 9002 7.8 only forbids window growth; pacing is
   now refreshed. New test cubic_app_limited_ack_updates_pacing_only fails
   without the fix. This could also slow non-outage runs whenever srtt rose,
   which is why every run of the first batch is discarded, not just outage.
   After both fixes: outage dev runs 91 (S0, S1) complete 1500/1500.
TQUIC suite: 564 + 1 pass.

Batch `week2_s0s4_v2` started with the fixed build (same cells, run ids 1-8).
Dev runs (run_id 0 and 91) remain under results/ and are never analysed.

## 2026-11-01: final demo moved to 17 Nov (user)

CLAUDE.md and proposal section 8: week 3 = Nov 12-14 (F1, F2, A1, R2, full
matrix, sweeps, stress; overnight batches), Nov 15-17 = statistics, plots,
report, slides; stretch items only if time is left. About 1,240 runs (~25 h)
now have to fit in about four nights from Nov 11. Hypotheses unchanged.

## 2026-11-01 21:15 UTC: laptop sleep during batch week2_s0s4_v2
- The laptop went to sleep from about 20:38 to 21:11 UTC, during run
  rtt_asym/S1/2600_intra_stochastic/3. That run took 2024 s instead of ~64 s
  and its receiver CPU peaked at 117.4% (catch-up after resume). The batch's
  CPU rule (> 80%) discarded it automatically to
  results/_discarded/results/rtt_asym/S1/2600_intra_stochastic/3.cpu.1793567497
  and reran it: rc=0, 64 s, CPU max 10%. The real cause was the sleep, not load.
- No other run overlapped the sleep: rtt_asym/S0/3 ended 20:37:53, before it.
  WSL did not restart (uptime 20 h) and the namespaces stayed up.
- Since 21:15 a Windows SetThreadExecutionState request (started from WSL with
  powershell.exe, 4 h) blocks idle sleep. Closing the lid can still suspend.
- Status: 64/200 done, 0 failures apart from the discard above.

## 2026-11-02 00:30 UTC: batch week2_s0s4_v2 complete (S0-S4, synthetic scenarios)
Done: 200/200 runs (5 scenarios x S0-S4 x run_id 1-8; 2600 kbps, intra-refresh,
stochastic), repo 52bcb82, TQUIC 4d7481d, 19:20-00:09 UTC. 0 stalled, 0 CPU
discards apart from the sleep-caused one logged above (rerun clean), every
frame completed in every run. Per-run metrics: results/summaries/week2_s0s4_v2_runs.csv
(analysis/summarize.py per scenario dir, run_id 1-8; dev runs 0 and 91 excluded).

Evidence: mean over 8 paired runs, on time at D = 250 ms (freeze fraction at 250 ms)

| scenario | S0 | S1 MinRTT | S2 RR | S3 redundant | S4 ECF |
| --- | --- | --- | --- | --- | --- |
| control 4+4 @40/40 | 99.9% (0.13%) | 100% (0) | 100% (0) | 100% (0) | 100% (0) |
| symmetric 1.6+1.6 @40/40 | 0.4% (99.7%) | 88.7% (11.5%) | 98.7% (1.3%) | 0.4% (99.7%) | 88.2% (11.9%) |
| rtt_asym 1.6+1.6 @30/150 | 0.4% (99.7%) | 83.8% (14.9%) | 98.1% (2.1%) | 0.4% (99.7%) | 83.7% (15.2%) |
| bw_asym 2.0+1.2 @40/60 | 0.8% (99.3%) | 88.1% (11.6%) | 91.7% (7.4%) | 0.7% (99.3%) | 88.5% (10.8%) |
| outage 1.8+1.8 @40/60 | 0.5% (99.6%) | 66.2% (34.3%) | 75.2% (25.1%) | 0.6% (99.5%) | 31.1% (69.2%) |

Median required playout delay (99% on time): control S0 124 / S1 59 / S2 44 /
S3 131 / S4 66 ms; symmetric S1 460 / S2 311 / S4 453; rtt_asym S1 579 / S2 482 /
S4 548; bw_asym S1 494 / S2 455 / S4 497; outage S1 2154 / S2 2027 / S4 3347 ms.
S0 and S3 fail by construction whenever one path is below 2.6 Mbit/s (S3 sends
every byte on both paths: extra bytes 100%), so their delay grows for the
whole run (required delay 21-42 s).

Paired differences (95% bootstrap CI of the mean, 8 pairs; exploratory, not P1-P6):
- S2 - S1 on time at 250 ms: symmetric +10.0 pp [9.2, 10.9], rtt_asym +14.2 pp
  [13.7, 14.8], bw_asym +3.5 pp [2.7, 4.5], outage +8.9 pp [7.5, 10.2]; S2 ahead
  in 8/8 pairs everywhere.
- S4 - S1: rtt_asym -0.2 pp [-0.8, +0.5] (no difference); outage -35.2 pp
  [-41.3, -29.9] (ECF much worse under outages, 0/8 pairs better).

Reading: with 23% spare combined capacity (3.2 vs 2.6 Mbit/s), MinRTT and ECF
still miss 11-16% of frame deadlines at 250 ms; plain round-robin misses 1-2%
on equal-rate paths. This is one bitrate only; H2 is defined on usable capacity
relative to R1 over a bitrate sweep, so H1/H2 are NOT decided by this batch.

Next: R1 ideal-link reference in testbed; bitrate sweeps (S1, S4, R1, and S2
for context) on rtt_asym; look at why ECF collapses under outage (paths.csv,
frame_path) and why S2 beats MinRTT (frame-order interleaving vs MinRTT
filling the 30 ms path's 200 ms buffer first).
Slides: results slide added (docs/slides/deck/project/slides/results.html; PDF rebuilt, 10 pages).

## 2026-11-11: repo published on GitHub for the team
- Status checked today, on the committed code (dafebe4): TQUIC team7 (4d7481d)
  builds; `cargo test --release` 564 + 1 passed. Streamer builds, 6 tests
  pass. Analysis `test_playout` + `test_stats`: 15 tests pass.
- No PROGRESS entry between 2 Nov and today. Still open from the plan: R1
  reference, bitrate sweeps, H1/H2 report to the user (week 2 decision
  point), F1/F2/A1/R2 (week 3), real-site traces (only the public Mahimahi
  calibration fixture is in `traces/`).
- Published: https://github.com/knb2345/acn-mpquic (public; branches main =
  week1, week1, archive/old-design). TQUIC fork https://github.com/knb2345/tquic
  (public, branch team7); `.gitmodules` now points there (was
  Tencent/tquic, which does not have our commits). Root README.md added for
  teammates.

## 2026-11-16: Full evaluation matrix (758 runs), statistics, plots & final slides
- Completed cross-scenario matrix at 2600 kbps (S0-S4, F1, F2, A1, R1, R2 across symmetric, bw_asym, rtt_asym, outage, control) and 7-point rtt_asym bitrate sweep (500-3500 kbps); summarized in `analysis/summary_all.csv`.
- Key findings:
  - H2 confirmed: MinRTT (S1) and ECF (S4) sustain only 1000 kbps with <=1% freeze at D=250 ms on rtt_asym vs 2600 kbps for ideal link R1 (38.5% < 80% bar).
  - H3 conditional win: F2 cuts freeze fraction at 2.6 Mbps by ~10.4 pp on rtt_asym (4.5% vs 14.9%, Holm p=0.043), 10.3 pp on bw_asym (1.3% vs 11.6%, matching R1), and 10.2 pp on symmetric (1.2% vs 11.4%). Whole-frame pinning (F1) collapses (>76% freeze), proving byte-splitting is essential. At app-limited bitrates (1.0-1.6 Mbps) and in 2-s outages, 1-s max delivery-rate estimation and 1-PTO rescue inject 17-23% duplicate bytes.
  - H4 rejected: single MPQUIC connection (F2) outperforms two separate QUIC connections (A1, which stalls at 2.6 Mbps).
- Generated publication figures (`analysis/plots/fig1_cross_scenario_ontime.png`, `fig2_rtt_asym_sweep.png`, `fig3_path_share_overhead.png`) and built 12-slide final deck `docs/slides/Team7-final.pdf`.

## 2026-11-17 evening: final-evaluation package (report, slides, README)
Done
- Pulled teammates' work (origin/main 50b8884: 758-run matrix, sweep, plots,
  12-slide deck) into local main (fast-forward).
- Slides: team slide (Kabir Nawal Bajaj, Prasanna Bhavan, Divyanshu, equal
  contribution, as the team decided), names on the cover, eval date 18 Nov,
  related-work references, provenance line on the results slide; PDF rebuilt
  (`docs/slides/Team7-final.pdf`, 13 pages; `deck_to_pdf.sh` now takes the
  output name).
- Report: `docs/report.md` and `docs/Team7-final-report.pdf` (9 pages):
  problem, related work, methodology, results, analysis, limitations,
  contributions. Numbers from `analysis/summary_all.csv` via the stats part of
  `analysis/generate_report_artifacts.py`.
- README rewritten for the evaluation (deliverables, results, team, code status).

Evidence / checks
- Holm-corrected paired tests (11 comparisons): F2 vs S1 freeze on rtt_asym
  4.5 % vs 14.9 % (p 0.043), symmetric 1.2 % vs 11.4 % (p 0.043), bw_asym
  1.3 % vs 11.6 % (p 0.055); outage F2 87.4 % vs S1 40.3 % (worse).
- Usable capacity on the rtt_asym sweep (freeze <= 1 % at 250 ms): S1 1000,
  S4 1000, R1 2600 kbps (H2: 38 %, one run per sweep point for S1/S4/R1);
  F2 500 kbps (non-monotone: fails at 1.0-2.2 Mbit/s, recovers at 2.6-2.8).
  So H3 is only partly supported; the report says so.

Open issues
- The F1/F2/A1/R2 code is not in this repo or the TQUIC fork: TQUIC 4d7481d
  accepts only minrtt/redundant/roundrobin/ecf, and run.sh passes f1/f2/a1/r2,
  so those runs cannot be reproduced from a clean clone. Their raw results are
  not on GitHub either (results/ was added to .gitignore in 7337b80; files
  tracked before that are still tracked). Prasanna has the code and was asked
  to open one PR with the code (TQUIC changes as patches/), raw per-run data
  and batch scripts. Slides and report flag this until it is merged.
- H1 (real traces), H5 (keyframe stress) and content controls were not run.

## 2026-11-17 20:00 IST: F1/F2/A1/R2 code merged (PR #5) and checked
- Prasanna's PR #5 (e56db2c, merged as f95de03) adds F1/F2/A1/R2 as
  sender-side placement policies in the streamer (`--policy f1|f2|a1|r2`;
  rate estimator, finish-time estimate and balanced split in
  `streamer/src/lib.rs`, placement/rescue/oracle in `mpq_sender.rs`), the
  batch logs, and raw per-run data (frames.csv, paths.csv, meta.json) for all
  F1/F2/A1/R1/R2 runs and the sweep. TQUIC submodule unchanged (4d7481d).
- Checks: streamer builds; recomputing `analysis/summarize.py` from the raw
  data for rtt_asym, bw_asym and symmetric matches `analysis/summary_all.csv`
  exactly for all 349 F1/F2/A1/R1/R2 runs (max |diff| on_time_250 = 0).
- The PR had replaced five existing streamer tests (reassembly with rescue
  overlap, gaps, duplicates; clip rotation) with one shorter test; restored
  them next to the new estimator/split tests: streamer 9 tests pass.
  TQUIC 564 + 1 and analysis 15 also pass.
- Removed the "code being merged" notes from slides, report and README;
  report section 3.4 now says where F1/F2/A1/R2 live. Report PDF rebuilt.

## 2026-11-17 late: sanity check found a bad summary; all numbers recomputed
- `analysis/summary_all.csv` (758 rows, from PR #4) had only 667 distinct
  (scenario, config, bitrate, run_id) keys: 91 duplicate rows (reruns, runs
  from before two bug fixes, e.g. a pre-fix F2 rtt_asym run 2 with 0.1 % on
  time next to the valid 96.2 %; the superseded outage/S1/1 run that stalled)
  and 80 development-run rows (run ids 0, 91). The slides' means and the
  11-comparison Holm tests (n = 9 incl. run 0) were computed from it.
- New `analysis/summary_final.csv`: `analysis/summarize.py` over each
  `results/<scenario>/` (one row per run directory, so no duplicates), run ids
  1-8 only: 609 runs, 8 per cell, except the rtt_asym sweep points for S1, S2,
  S4 and R1 (1 run each, R1 also at 2.6 Mbit/s). `summary_all.csv` is kept,
  marked superseded. `generate_report_artifacts.py` now reads the new file,
  tests P3/P4 (F2 vs the better of S1/S4 on rtt_asym, bw_asym) as the Holm
  family and reports the rest as bootstrap CIs; figures regenerated.
- Corrections: F2 rtt_asym on time 95.7 % (was 86.2 %), S1 outage 66.2 % (was
  54.4 %); P3 rtt_asym F2 4.5 % vs S1 14.9 % freeze, P4 bw_asym 1.3 % vs S4
  10.8 %, Holm p = 0.016 each, 8/8 pairs (was p = 0.043 over 11 tests).
- F1 and A1 stalled in all 8 runs of every 2.6 Mbit/s cell (and F2/R2 in every
  outage run, F2 4/8 and R2 3/8 in control). Per protocol, stalled runs are
  reported, not averaged into claims: the "splitting beats pinning" and
  "H4 rejected, MPQUIC wins" claims were removed; H4 is now "not tested".
- Round-robin (S2) is a strong baseline: better than F2 on rtt_asym (2.1 % vs
  4.5 % freeze), tied on symmetric, worse on bw_asym; added to the slides.
- R2 (true rates) also fails at 1.0-1.6 Mbit/s, so "rate estimation is the
  primary bottleneck" was replaced by "the estimate is not the only cause".
- Slide 5 (method) no longer says phone recordings are "next"; they were not
  done. Slides, report, README and both PDFs rebuilt. Tests: analysis 15 OK.

## 2026-11-17 night: team slide removed, live demo added; F2 does not reproduce
- Slides: team/equal-contribution slide and "Questions?" removed (user);
  "Live demo" slide added (slide 12).
- `scripts/demo.sh` + `scripts/demo_dashboard.py`: live terminal dashboard
  (frames on time at 250 ms, time frozen, per-frame delay, bytes per link)
  while scripts/run.sh streams, then a side-by-side figure
  (results/demo/<time>/demo.png); `replay` mode shows recorded runs without the
  network. Live runs use run ids >= 900 (never analysed).
- First live attempt failed: streamer/target/release was built before PR #5
  and lacked --policy; demo.sh now always rebuilds.
- **F2 does not reproduce from the repository.** All 352 recorded F1/F2/A1/R2
  runs were built from commit 096c4a8 (meta.json), which is not in this repo or
  in Prasanna's fork; PR #5 re-created the code on top of a39ac4e. Re-running
  F2 on rtt_asym, 2.6 Mbit/s with main (f329f44): run ids 901-903 gave 17.9 %,
  2.8 %, 38.6 % on time (recorded runs 1-8: 95.7 %). S1 rerun (run id 900/901):
  85.7 % vs 83.8 % recorded, so the testbed is fine. Asked Prasanna to push
  096c4a8 exactly. Until then the live demo uses S1 vs S2 (built-in, they
  reproduce) and shows F2 only as a replay of recorded runs; the README says
  F2 does not reproduce yet; the results slide no longer claims it does.

## 2026-11-18 00:00 IST: F1/F2/A1 fixed; everything F-related rerun overnight
Done
- Why PR #5's F2 did not reproduce (3-39 % on time vs 95.7 % recorded), found
  by comparing per-path bytes and RTTs with the recorded runs:
  1. U_p was bytes in flight, so bytes waiting in the stream send buffer were
     invisible and F2 kept loading path A (2.56 MB vs 18.6 KB on B in the first
     200 frames of run 902). Now U_p = bytes assigned and not yet sent
     (SendTracker in streamer/src/lib.rs, fed by TQUIC's frame_path lines,
     unit-tested). With only that, F2 reached 31-33 % and R2 (true rates)
     68.9 %: srtt - min_rtt lags the bottleneck queue because CUBIC's window
     is several BDPs. The queueing term is now the bytes in flight beyond one
     round trip (bottleneck_queue_bytes, unit-tested). R2: 98.3 % (recorded
     97.4 %, same 58.5 % byte share on path A); F2: 94.7 %, 96.1 %.
     This is a change to the 1 Nov design formula (CLAUDE.md, report 3.4, 6).
  2. Rescue retired parts on a cumulative-ACK shortcut; a part now stays
     unfinished until its stream closes.
  3. Receiver keyed partial streams by stream id only; A1's two connections
     reuse ids and their parts were mixed ("payload bytes, header says ...").
     Keyed by (connection, stream) now. F1: 90.9 %, A1: 97.5 % (both stalled
     in every recorded run before).
  Dev runs 900-910: results/_superseded/dev_fix_20261117/ (never analysed).
- All F1/F2/A1/R2/R1 runs and the S1/S2/S4 sweep points built from 096c4a8 /
  f63e606 moved unchanged to results/_superseded/build096c4a8/; their
  summaries, figures and the draft slides to .../analysis_artifacts/.
  (The 16 Nov entry's "H4 rejected" was based on those runs.)
- .gitignore: dropped the blanket "results/" line from 7337b80.
- Analysis rules fixed before the final batch was analysed (commit b04bcc0):
  stalled runs reported, never averaged in; one Holm family P1-P5; R1 not
  used for outage; H3 symmetric part tested as pre-registered.
- Independent read-only review of docs/slides/code; its points are being
  worked through (premise reframed around queueing: MinRTT/ECF keep path A's
  srtt at 159-160 ms median vs 39 ms under round-robin, min RTT 30 ms).

Running (laptop must stay on): batch final_main_2600 (5 scenarios x F2 R1 R2
F1 A1 x run ids 1-8, build da6f450), then scripts/overnight_20261118b.sh:
rtt_asym sweep S1 S4 R1 F2 A1 at 1.0-2.8 Mbit/s, then 0.5 and 3.5, then
extras (S2 R2 F1). Logs: results/batches/final_*.log.

## 2026-11-18 06:45 IST: final batch complete; slides, report, README final
Batch (build da6f450 = 63baae1 code, TQUIC 4d7481d), 17 Nov 18:12 - 18 Nov 01:09 UTC:
- final_main_2600: 5 scenarios x F2 R1 R2 F1 A1 x run ids 1-8 (200 runs).
- rtt_asym sweep, run ids 1-8: S1 S4 R1 F2 A1 at 1.0, 1.6, 2.2, 2.8 Mbit/s;
  F2 A1 at 0.5 Mbit/s (moved ahead of 2.2 after F2 froze at 1.0 Mbit/s, so P5
  could be computed). Not run: 0.5 Mbit/s for S1/S4/R1 (all three are at 0 %
  freeze at 1.0, so usable capacity does not depend on it), 3.5 Mbit/s, and the
  S2/R2/F1 sweeps. Drivers: scripts/overnight_20261118{,b,c}.sh, logs
  results/batches/final_*.log, overnight_*.out.
- No stalled run. Four runs went above 80 % receiver CPU (one-off spikes,
  probably the Docker containers running on this machine); batch.sh moved
  them to results/_discarded/ and reran them cleanly (outage/F2/6,
  rtt_asym/F2/1000/1, rtt_asym/S1/1600/8, rtt_asym/F2/2800/1).
- At 03:19 the part-2 driver did not start because my own wait loop's command
  line matched its pgrep pattern; found and fixed within 6 minutes.

Results (analysis/summary_final.csv, 576 runs; analysis/tables/):
- P1/P2 (H2): usable capacity S1/R1 and S4/R1 per run median 0.42 (0.38-0.45),
  Holm p = 0.020 each: H2 supported (MinRTT/ECF 1.0 Mbit/s, R1 2.6 Mbit/s).
- P3/P4 (H3): F2 vs S1 on rtt_asym 4.4 % vs 14.9 % freeze, F2 vs S4 on bw_asym
  3.8 % vs 10.8 %, 8/8 pairs, Holm p = 0.023. Symmetric part: F2 10.2 pp
  better than S1 (not "equivalent"). Outage: F2 37.7 % vs S1 34.3 % vs S2 25.1 %.
- P5 (H4): usable capacity F2 0.5 vs A1 1.0 Mbit/s, not equivalent. At 2.6
  Mbit/s F2 and A1 within 1 pp.
- F2 low-load stall: at 1.0 Mbit/s parts assigned to the fast path wait a
  median 120 ms (p90 192 ms, 8 runs) in the sender before their first byte is
  sent (send.csv vs frame_path.csv); 0.2 ms at 2.6 Mbit/s; A1 0.1 ms. Not
  fixed (would need a TQUIC send-loop change and a rerun).
- Live demo (scripts/demo.sh, run ids 900+) re-tested: MinRTT 83.9 %, F2
  94.6 % on time; replay mode works.
- Slides, report (11 pages), README filled from analysis/tables; PDFs rebuilt.

## 2026-11-18 09:30 IST: final submission
- Report now opens with team contributions (even split, per the course's
  submission instructions); demo note in section 8. PDF rebuilt (12 pages).
- Submitted on Moodle (user): Team7_final_submission.zip = report PDF, slides
  PDF, code (streamer, testbed, scripts, analysis, workload), TQUIC team7
  patches (7 commits on v1.6.0), README with the GitHub link.
- Repo shared with mukulika-maity (collaborator invite, project repo only).
