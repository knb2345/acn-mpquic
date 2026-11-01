#!/usr/bin/env bash
# Stream the lecture frame by frame (streamer/: one QUIC stream per frame) and
# log every frame's completion time and per-path bytes.
#
# Usage: testbed/run_stream.sh <scenario> [scheduler ...]
#   scenario: symmetric | rtt_asym | low_bw | trace | outage
#   scheduler: single (multipath off, path A only; A is the better path in
#   every scenario) or a TQUIC multipath algorithm: minrtt, roundrobin,
#   redundant, ecf, deadline, framepin, learned. Default: single minrtt
#   roundrobin redundant.
#   Env: RUNS (default 5), RUN_START (default 1), CC (default bbr),
#   WORKLOAD (default workload/copyrightx_lecture.csv), DEADLINE_MS (playback
#   delay budget, default 300), LOG_LEVEL (default info), QLOG=1 to keep qlogs,
#   BIN_DIR (binaries frozen by testbed/freeze_bins.sh; default: the live
#   build in streamer/target/release).
# Output: results/<workload>/<scenario>/<scheduler>/<run_id>/frames.csv (one row per
#   frame, columns as in CLAUDE.md), summary.csv, raw sender/receiver logs,
#   counter snapshots, meta.env. Non-bbr CC goes to <scheduler>-<cc>/.
# Trace runs start the traces at an offset drawn from run_id (same offset for
# every scheduler), so runs cover different stretches of the trace.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/.." && pwd)
source "$here/common.sh"

scenario=${1:?usage: run_stream.sh <scenario> [scheduler ...]}
shift
if (( $# )); then schedulers=("$@"); else schedulers=(single minrtt roundrobin redundant); fi
RUNS=${RUNS:-5}
RUN_START=${RUN_START:-1}
CC=${CC:-bbr}
WORKLOAD=$(realpath "${WORKLOAD:-$repo/workload/copyrightx_lecture.csv}")
results=$repo/results/$(basename "$WORKLOAD" .csv)
DEADLINE_MS=${DEADLINE_MS:-300}
LOG_LEVEL=${LOG_LEVEL:-info}
QLOG=${QLOG:-0}

bin=$(realpath "${BIN_DIR:-$repo/streamer/target/release}")
[[ -x $bin/mpq_sender && -x $bin/mpq_receiver ]] || {
    echo "run_stream.sh: build the streamer first (see testbed/README.md)" >&2; exit 1; }
cache=$repo/.cache/transfer
if [[ ! -f $cache/cert.crt ]]; then
    mkdir -p "$cache"
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj /CN=example.org \
        -keyout "$cache/cert.key" -out "$cache/cert.crt" 2>/dev/null
fi
# Builds from before the learned scheduler have no ML logging options.
ml_ok=false
"$bin/mpq_sender" --help 2>/dev/null | grep -q -- --ml-features-log && ml_ok=true
clip_s=$(python3 -I -c 'import sys; r=open(sys.argv[1]).read().split()[-1].split(","); print(int(float(r[3])/1000)+1)' "$WORKLOAD")

stop_ns() {
    for pid in $(sudo "$IP" netns pids "$1" 2>/dev/null); do kill "$pid" 2>/dev/null || true; done
}
cleanup() {
    stop_ns "$NS_SRV"
    [[ -n ${replay_pid:-} ]] && kill "$replay_pid" 2>/dev/null || true
}
trap cleanup EXIT

snapshot() {
    for ns in "$NS_CLI" "$NS_SRV"; do
        sudo "$IP" -n "$ns" -s -j link show > "$1/$2.$ns.link.json"
        sudo tc -n "$ns" -s -j qdisc show > "$1/$2.$ns.qdisc.json"
    done
}

for sched in "${schedulers[@]}"; do
    label=$sched; [[ $CC == bbr ]] || label=$sched-$CC
    if [[ $sched == single ]]; then
        mp_args=(); locals=$CLI_A_IP
    else
        mp_args=(--enable-multipath --multipath-algor "$sched"); locals=$CLI_A_IP,$CLI_B_IP
    fi
    for run_id in $(seq "$RUN_START" $(( RUN_START + RUNS - 1 ))); do
        out=$results/$scenario/$label/$run_id
        rm -rf "$out"; mkdir -p "$out"
        [[ $QLOG == 1 ]] && mkdir -p "$out/qlog_sender" "$out/qlog_receiver"
        qs=(); qr=()
        [[ $QLOG == 1 ]] && qs=(--qlog-dir "$out/qlog_sender") && qr=(--qlog-dir "$out/qlog_receiver")

        "$here/up.sh" "$scenario" > "$out/testbed.txt"
        offset_ms=0
        [[ $scenario == trace ]] && offset_ms=$(python3 -I -c \
            'import random,sys; print(random.Random(int(sys.argv[1])).randrange(0, 600) * 1000)' "$run_id")
        { cat "$STATE_FILE"
          echo "SCHEDULER=$label"; echo "RUN_ID=$run_id"; echo "CC=$CC"
          echo "WORKLOAD=${WORKLOAD#$repo/}"; echo "DEADLINE_MS=$DEADLINE_MS"
          echo "TRACE_OFFSET_MS=$offset_ms"
          echo "BIN_DIR=${bin#$repo/}"
          if [[ -f $bin/BUILD_INFO ]]; then cat "$bin/BUILD_INFO"; else
              echo "TQUIC_COMMIT=$(git -C "$repo/third_party/tquic" rev-parse HEAD)"
              echo "TQUIC_DIRTY=$(git -C "$repo/third_party/tquic" status --porcelain -uno | wc -l)"
          fi
          echo "DATE=$(date -Iseconds)"; } > "$out/meta.env"

        ml=()
        $ml_ok && ml=(--ml-features-log "$out/ml_features.csv" --ml-pred-log "$out/ml_pred.csv")
        # stderr goes to a file: startup errors land there, and so does bash's
        # "Terminated" notice if the sender has to be killed.
        { nsu "$NS_SRV" "$bin/mpq_sender" --listen "$SRV_IP:$SRV_PORT" \
            --cert "$cache/cert.crt" --key "$cache/cert.key" --frames "$WORKLOAD" \
            "${mp_args[@]}" --cc "$CC" --deadline-ms "$DEADLINE_MS" --out "$out/sent.csv" \
            --frame-path-log "$out/frame_path.csv" --plan-log "$out/plan.log" \
            "${ml[@]}" \
            --log-file "$out/sender.log" \
            --log-level "$LOG_LEVEL" "${qs[@]}" || true; } 2> "$out/sender.stderr" &
        sender_job=$!
        for _ in $(seq 300); do
            [[ -n $(nsx "$NS_SRV" ss -Hlun "sport = :$SRV_PORT") ]] && break
            sleep 0.1
        done

        replay_pid=
        if [[ $scenario == trace || $scenario == outage ]]; then
            python3 -I "$here/replay.py" "$scenario" "$out/replay.csv" --offset-ms "$offset_ms" &
            replay_pid=$!
        fi

        snapshot "$out" before
        status=0
        timeout $(( clip_s + 90 )) sudo "$IP" netns exec "$NS_CLI" runuser -u "$RUN_AS" -- \
            "$bin/mpq_receiver" --connect "$SRV_IP:$SRV_PORT" --local-addresses "$locals" \
            "${mp_args[@]}" --cc "$CC" --frames "$WORKLOAD" --out "$out/recv.csv" \
            --max-duration-s $(( clip_s + 30 )) --log-file "$out/receiver.log" \
            --log-level "$LOG_LEVEL" "${qr[@]}" > "$out/receiver.stdout" 2>&1 || status=$?
        snapshot "$out" after
        [[ -n $replay_pid ]] && { kill "$replay_pid"; wait "$replay_pid" || true; replay_pid=; }

        # The sender exits by itself once the receiver closes the connection.
        for _ in $(seq 30); do kill -0 "$sender_job" 2>/dev/null || break; sleep 0.1; done
        stop_ns "$NS_SRV"
        wait "$sender_job" 2>/dev/null || true
        echo "RECEIVER_EXIT=$status" >> "$out/meta.env"

        python3 -I "$repo/analysis/frames.py" run "$out"
    done
done

"$here/down.sh"
python3 -I "$repo/analysis/frames.py" table "$results/$scenario"
