#!/usr/bin/env bash
# Collect training data for the learned bandwidth predictor: stream the lecture
# under conditions drawn by testbed/train_spec.py (training traces only, never
# the test traces) and log every path's state every 100 ms.
#
# Usage: testbed/collect.sh <first_run_id> <last_run_id> <scheduler ...>
#   Env: WORKLOAD (default workload/copyrightx_lecture.csv), BIN_DIR (frozen
#   binaries, default streamer/target/release), CC (default bbr), DEADLINE_MS,
#   LEARNED_Q (quantile for the learned scheduler; results then go to
#   <scheduler>-q<LEARNED_Q>/), OUT_ROOT (default results/ml_train).
# Output: results/ml_train/<workload>/<scheduler>/<run_id>/ with spec.json,
#   replay.csv (capacity over time = labels), ml_features.csv (inputs), and the
#   usual streaming logs and summary.csv.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/.." && pwd)
source "$here/common.sh"

first=${1:?usage: collect.sh <first_run_id> <last_run_id> <scheduler ...>}
last=${2:?usage: collect.sh <first_run_id> <last_run_id> <scheduler ...>}
shift 2
(( $# )) || { echo "collect.sh: name at least one scheduler" >&2; exit 2; }
WORKLOAD=$(realpath "${WORKLOAD:-$repo/workload/copyrightx_lecture.csv}")
CC=${CC:-bbr}
DEADLINE_MS=${DEADLINE_MS:-300}
LEARNED_Q=${LEARNED_Q:-}
OUT_ROOT=$(realpath -m "${OUT_ROOT:-$repo/results/ml_train}")
bin=$(realpath "${BIN_DIR:-$repo/streamer/target/release}")
cache=$repo/.cache/transfer
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

lq_args=()
[[ -n $LEARNED_Q ]] && lq_args=(--learned-q "$LEARNED_Q")
for sched in "$@"; do
    label=$sched; [[ -n $LEARNED_Q && $sched == learned ]] && label=$sched-q$LEARNED_Q
    for run_id in $(seq "$first" "$last"); do
        out=$OUT_ROOT/$(basename "$WORKLOAD" .csv)/$label/$run_id
        rm -rf "$out"; mkdir -p "$out"
        python3 -I "$here/train_spec.py" "$run_id" "$out/spec.json"
        read -r ar art br brt < <(python3 -I -c 'import json,sys; s=json.load(open(sys.argv[1]))["paths"]
print(s["A"]["base_kbit"], s["A"]["rtt_ms"], s["B"]["base_kbit"], s["B"]["rtt_ms"])' "$out/spec.json")
        A_RATE_KBIT=$ar A_RTT_MS=$art B_RATE_KBIT=$br B_RTT_MS=$brt "$here/up.sh" symmetric > "$out/testbed.txt"
        { cat "$STATE_FILE"
          echo "SCENARIO=ml_train"; echo "SCHEDULER=$label"; echo "RUN_ID=$run_id"; echo "CC=$CC"
          echo "LEARNED_Q=$LEARNED_Q"
          echo "WORKLOAD=${WORKLOAD#$repo/}"; echo "DEADLINE_MS=$DEADLINE_MS"
          echo "BIN_DIR=${bin#$repo/}"
          if [[ -f $bin/BUILD_INFO ]]; then cat "$bin/BUILD_INFO"; fi
          echo "DATE=$(date -Iseconds)"; } > "$out/meta.env"

        { nsu "$NS_SRV" "$bin/mpq_sender" --listen "$SRV_IP:$SRV_PORT" \
            --cert "$cache/cert.crt" --key "$cache/cert.key" --frames "$WORKLOAD" \
            --enable-multipath --multipath-algor "$sched" --cc "$CC" --deadline-ms "$DEADLINE_MS" \
            --out "$out/sent.csv" --frame-path-log "$out/frame_path.csv" --plan-log "$out/plan.log" \
            --ml-features-log "$out/ml_features.csv" --ml-pred-log "$out/ml_pred.csv" \
            "${lq_args[@]}" --log-file "$out/sender.log" || true; } 2> "$out/sender.stderr" &
        sender_job=$!
        for _ in $(seq 300); do
            [[ -n $(nsx "$NS_SRV" ss -Hlun "sport = :$SRV_PORT") ]] && break
            sleep 0.1
        done
        python3 -I "$here/replay_spec.py" "$out/spec.json" "$out/replay.csv" &
        replay_pid=$!
        snapshot "$out" before
        status=0
        timeout $(( clip_s + 90 )) sudo "$IP" netns exec "$NS_CLI" runuser -u "$RUN_AS" -- \
            "$bin/mpq_receiver" --connect "$SRV_IP:$SRV_PORT" --local-addresses "$CLI_A_IP,$CLI_B_IP" \
            --enable-multipath --multipath-algor "$sched" --cc "$CC" --frames "$WORKLOAD" \
            --out "$out/recv.csv" --max-duration-s $(( clip_s + 30 )) --log-file "$out/receiver.log" \
            > "$out/receiver.stdout" 2>&1 || status=$?
        snapshot "$out" after
        kill "$replay_pid"; wait "$replay_pid" || true; replay_pid=
        for _ in $(seq 30); do kill -0 "$sender_job" 2>/dev/null || break; sleep 0.1; done
        stop_ns "$NS_SRV"
        wait "$sender_job" 2>/dev/null || true
        echo "RECEIVER_EXIT=$status" >> "$out/meta.env"
        python3 -I "$repo/analysis/frames.py" run "$out" | sed "s/^/[$(python3 -I -c 'import json,sys;print(json.load(open(sys.argv[1]))["mode"])' "$out/spec.json")] /"
    done
done
"$here/down.sh"
