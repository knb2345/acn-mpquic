#!/usr/bin/env bash
# One streaming run: one scenario, one config, one encoding of one clip.
#
# Usage: scripts/run.sh <scenario> <config> <bitrate_kbps> <encoding> <clip> <run_id>
#   scenario: control symmetric rtt_asym bw_asym outage real_pairs (SITE=...)
#   config:   S0 (best single path) S1 (minrtt) S2 (roundrobin) S3 (redundant)
#             S4 (ecf) F1 (frame-split) F2 (frame-split+rescue)
#             A1 (F2 over 2 single-path TCP/QUIC) R1 (ideal link) R2 (true trace F2)
#   encoding: intra | gop2;   clip: name in workload/clips.csv
#   Env: BIN (streamer binaries, default streamer/target/release), CC
#        (default cubic), LOG_LEVEL (default info), SITE (real_pairs).
#
# Paired design: the clip start (a multiple of 50 frames = 2 s, so gop2 starts
# on a keyframe) and the trace / outage-schedule start are drawn from a seed
# that depends only on run_id, so every config sees the same ones.

set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/.." && pwd)
source "$repo/testbed/common.sh"

usage="usage: run.sh <scenario> <config> <bitrate_kbps> <encoding> <clip> <run_id>"
scenario=${1:?$usage}; config=${2:?$usage}; kbps=${3:?$usage}
encoding=${4:?$usage}; clip=${5:?$usage}; run_id=${6:?$usage}
BIN=${BIN:-$repo/streamer/target/release}
CC=${CC:-cubic}
LOG_LEVEL=${LOG_LEVEL:-info}
WARMUP_MS=1000

frames=$repo/workload/${clip}_${encoding}_${kbps}.csv
[[ -f $frames ]] || { echo "run.sh: no $frames (workload/encode.sh $clip $encoding $kbps)" >&2; exit 2; }
for b in mpq_sender mpq_receiver; do
    [[ -x $BIN/$b ]] || { echo "run.sh: no $BIN/$b (cargo build --release in streamer/)" >&2; exit 2; }
done

# Map config IDs to TQUIC / streamer algorithms and multipath flags
case $config in
    S0) algor=minrtt; mp=0 ;;
    S1) algor=minrtt; mp=1 ;;
    S2) algor=roundrobin; mp=1 ;;
    S3) algor=redundant; mp=1 ;;
    S4) algor=ecf; mp=1 ;;
    F1) algor=f1; mp=1 ;;
    F2) algor=f2; mp=1 ;;
    A1) algor=a1; mp=0 ;;
    R1) algor=minrtt; mp=0 ;;
    R2) algor=r2; mp=1 ;;
    *) echo "run.sh: unknown config $config (S0-S4, F1, F2, A1, R1, R2)" >&2; exit 2 ;;
esac

scen_dir=$scenario${SITE:+_$SITE}
out=$repo/results/$scen_dir/$config/${kbps}_${encoding}_${clip}/$run_id
[[ -e $out ]] && { echo "run.sh: $out exists; results are never overwritten" >&2; exit 1; }
mkdir -p "$out"
cache=$repo/.cache/transfer
[[ -f $cache/cert.crt ]] || openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -subj /CN=example.org -keyout "$cache/cert.key" -out "$cache/cert.crt" 2>/dev/null

# Bring up testbed (R1 uses ideal-link single path mode; all others use default mode)
if [[ $config == "R1" ]]; then
    "$repo/testbed/up.sh" "$scenario" R1 > "$out/testbed.txt"
else
    "$repo/testbed/up.sh" "$scenario" default > "$out/testbed.txt"
fi
source "$STATE_FILE"

# Seeded offsets (depend on run_id only).
n_frames=$(( $(wc -l < "$frames") - 1 ))
trace_bins=0
if [[ $scenario == real_pairs ]]; then
    trace_bins=$(python3 -I -c "
import sys; sys.path.insert(0, '$repo/testbed'); import trace_site
_, p = trace_site.load('$SITE'); print(min(len(v['kbit']) for v in p.values()))")
fi
read -r clip_offset trace_offset_ms < <(python3 -I -c "
import random
rng = random.Random('team7-run-$run_id')
clip = rng.randrange(0, $n_frames, 50)
if '$scenario' == 'outage':
    tr = rng.randrange(0, 30000, 100)
elif '$scenario' == 'real_pairs':
    tr = rng.randrange(0, max(1, $trace_bins * 100 - 100000), 100)
else:
    tr = 0
print(clip, tr)")

# Socket IP bindings
if [[ $config == S0 || $config == R1 ]]; then
    # Single-path execution (Path A)
    locals=$CLI_A_IP; s0_path=A
else
    locals=$CLI_A_IP,$CLI_B_IP; s0_path=
fi

python3 -I - "$out/meta.json" <<EOF
import json, sys, subprocess, datetime, time, hashlib
def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()
def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()
meta = dict(
    scenario="$scenario", site="${SITE:-}", config="$config", algorithm="$algor",
    multipath=bool($mp), s0_path="$s0_path", bitrate_kbps=$kbps, encoding="$encoding",
    clip="$clip", run_id=$run_id, seed="team7-run-$run_id", clip_offset_frames=$clip_offset,
    trace_offset_ms=$trace_offset_ms, cc="$CC", warmup_ms=$WARMUP_MS,
    testbed=dict(a_rate_kbit=$A_RATE_KBIT, a_rtt_ms=$A_RTT_MS, b_rate_kbit=$B_RATE_KBIT,
                 b_rtt_ms=$B_RTT_MS, queue_ms=$QUEUE_MS),
    repo_git=sh("git -C $repo rev-parse --short HEAD")
             + ("" if sh("git -C $repo status --porcelain -- scripts testbed analysis streamer workload") == "" else "-dirty"),
    tquic_git=sh("git -C $repo/third_party/tquic rev-parse --short HEAD")
              + ("" if sh("git -C $repo/third_party/tquic status --porcelain -- src") == "" else "-dirty"),
    sender_sha256=sha("$BIN/mpq_sender"), receiver_sha256=sha("$BIN/mpq_receiver"),
    frames_file="$frames", frames_sha256=sha("$frames"),
    kernel=sh("uname -r"), rustc=sh("rustc --version"),
    start_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    start_mono_us=round(time.monotonic() * 1e6),
)
json.dump(meta, open(sys.argv[1], "w"), indent=1)
EOF

pids=()
cleanup() {
    for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
    for ns in "$NS_SRV" "$NS_CLI"; do
        for p in $(sudo "$IP" netns pids "$ns" 2>/dev/null); do kill "$p" 2>/dev/null || true; done
    done
}
trap cleanup EXIT

snapshot() {
    for ns in "$NS_CLI" "$NS_SRV"; do
        sudo "$IP" -n "$ns" -s -j link show > "$out/$1.$ns.link.json"
        sudo tc -n "$ns" -s -j qdisc show > "$out/$1.$ns.qdisc.json"
    done
}

python3 -I "$here/cpu_sample.py" "$out/cpu.csv" mpq_sender mpq_receiver &
pids+=($!)
mkdir -p "$out/qlog_sender" "$out/qlog_receiver"
nsu "$NS_SRV" "$BIN/mpq_receiver" --listen "$SRV_IP:$SRV_PORT" \
    --cert "$cache/cert.crt" --key "$cache/cert.key" --enable-multipath --cc "$CC" \
    --frames "$frames" --out "$out/recv.csv" --max-duration-s 120 \
    --log-file "$out/receiver.log" --log-level "$LOG_LEVEL" --qlog-dir "$out/qlog_receiver" \
    2> "$out/receiver.stderr" &
rcv_pid=$!
for _ in $(seq 50); do
    [[ -n $(nsx "$NS_SRV" ss -Hlun "sport = :$SRV_PORT") ]] && break
    sleep 0.1
done
snapshot before

if [[ $scenario == outage || $scenario == real_pairs ]]; then
    python3 -I "$repo/testbed/replay.py" "$out/replay.csv" --offset-ms "$trace_offset_ms" --duration-s 100 &
    pids+=($!)
fi

mp_flag=(); (( mp )) && mp_flag=(--enable-multipath)
snd_status=0
timeout 200 sudo "$IP" netns exec "$NS_CLI" runuser -u "$RUN_AS" -- "$BIN/mpq_sender" \
    --connect "$SRV_IP:$SRV_PORT" --local-addresses "$locals" "${mp_flag[@]}" \
    --multipath-algor "$algor" --cc "$CC" --frames "$frames" --clip-offset "$clip_offset" \
    --warmup-ms "$WARMUP_MS" --out "$out/send.csv" --frame-path-log "$out/frame_path.csv" \
    --paths-log "$out/paths.csv" --log-file "$out/sender.log" --log-level "$LOG_LEVEL" \
    --qlog-dir "$out/qlog_sender" 2> "$out/sender.stderr" || snd_status=$?

rcv_status=0; wait "$rcv_pid" || rcv_status=$?
snapshot after
cleanup; trap - EXIT
"$repo/testbed/down.sh"

summary=$(python3 -I "$repo/analysis/build_frames.py" "$out" "$CLI_A_IP" "$CLI_B_IP")
echo "$summary" | head -1
# The raw per-STREAM-frame log is kept, compressed (frames.csv is built from it).
gzip -9 "$out/frame_path.csv"
python3 -I - "$out" "$snd_status" "$rcv_status" "$(echo "$summary" | tail -1)" <<'EOF'
import csv, json, sys
from pathlib import Path
out, snd, rcv, summ = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), json.loads(sys.argv[4])
meta = json.loads((out / "meta.json").read_text())
cpu = {}
for r in csv.DictReader(open(out / "cpu.csv")):
    cpu[r["name"]] = max(cpu.get(r["name"], 0.0), float(r["cpu_pct"]))
def veth(dev, key):
    def get(tag):
        for l in json.loads((out / f"{tag}.mpq-cli.link.json").read_text()):
            if l["ifname"] == dev:
                return l["stats64"][key]["bytes"]
    return get("after") - get("before")
meta.update(summ)
meta.update(sender_exit=snd, receiver_exit=rcv, cpu_max_pct=cpu,
            cpu_over_80=any(v > 80 for v in cpu.values()),
            veth_tx_bytes_a=veth("cA", "tx"), veth_tx_bytes_b=veth("cB", "tx"),
            stalled=summ["frames_complete"] < summ["frames_sent"] or summ["frames_sent"] == 0)
(out / "meta.json").write_text(json.dumps(meta, indent=1))
print(f"cpu max {cpu}; stalled={meta['stalled']}; veth tx A={meta['veth_tx_bytes_a']} B={meta['veth_tx_bytes_b']}")
sys.exit(4 if meta["cpu_over_80"] else 3 if meta["stalled"] else 0)
EOF