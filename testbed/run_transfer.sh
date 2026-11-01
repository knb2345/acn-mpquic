#!/usr/bin/env bash
# Week 1 experiment: download one file over a multipath TQUIC connection and
# record how many bytes each path carried (veth counters, not qlog: TQUIC's
# qlog events carry no path id).
#
# Usage: testbed/run_transfer.sh <scenario> [scheduler ...]
#   schedulers default to "minrtt roundrobin"; RUNS (default 5) runs each.
#   Env: RUNS, RUN_START (first run_id, default 1; use it to add runs without
#   overwriting old ones), CC (congestion control, default bbr = TQUIC's
#   default), TIMEOUT_S (client timeout, default 300), LOG_LEVEL (default
#   info; trace shows why the sender holds back), FILE_BYTES (default 10 MiB),
#   SEED (default 7, for the file content)
# Output: results/week1_transfer/<scenario>/<scheduler>/<run_id>/ with logs, qlogs, counter
#   snapshots and summary.csv; then a table over all runs of the scenario.
#   With a CC other than bbr the directory is <scheduler>-<cc>.
#
# The server sends the file, so the server's --multipath-algor is the one that
# decides which path the data takes. Both ends get the same value.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/.." && pwd)
source "$here/common.sh"

scenario=${1:?usage: run_transfer.sh <scenario> [scheduler ...]}
shift
if (( $# )); then schedulers=("$@"); else schedulers=(minrtt roundrobin); fi
RUNS=${RUNS:-5}
RUN_START=${RUN_START:-1}
CC=${CC:-bbr}
TIMEOUT_S=${TIMEOUT_S:-300}
LOG_LEVEL=${LOG_LEVEL:-info}
FILE_BYTES=${FILE_BYTES:-10485760}
SEED=${SEED:-7}

bin=$repo/third_party/tquic/target/release
[[ -x $bin/tquic_server && -x $bin/tquic_client ]] || {
    echo "run_transfer.sh: build TQUIC first (see testbed/README.md)" >&2; exit 1; }

# Certificate and input file are reused across runs.
cache=$repo/.cache/transfer
mkdir -p "$cache/data"
if [[ ! -f $cache/cert.crt ]]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj /CN=example.org \
        -keyout "$cache/cert.key" -out "$cache/cert.crt" 2>/dev/null
fi
file=file_${FILE_BYTES}_s${SEED}
if [[ ! -f $cache/data/$file ]]; then
    python3 -I -c 'import random,sys; sys.stdout.buffer.write(random.Random(int(sys.argv[1])).randbytes(int(sys.argv[2])))' \
        "$SEED" "$FILE_BYTES" > "$cache/data/$file"
fi

stop_server() {
    for pid in $(sudo "$IP" netns pids "$NS_SRV" 2>/dev/null); do kill "$pid" 2>/dev/null || true; done
    # Reap the background job quietly (no "Terminated" line).
    [[ -n ${srv_job:-} ]] && { wait "$srv_job" || true; } 2>/dev/null
    srv_job=
}
trap stop_server EXIT

# snapshot DIR TAG: raw interface and qdisc counters from both namespaces.
snapshot() {
    for ns in "$NS_CLI" "$NS_SRV"; do
        sudo "$IP" -n "$ns" -s -j link show > "$1/$2.$ns.link.json"
        sudo tc -n "$ns" -s -j qdisc show > "$1/$2.$ns.qdisc.json"
    done
}

for sched in "${schedulers[@]}"; do
    label=$sched; [[ $CC == bbr ]] || label=$sched-$CC
    for run_id in $(seq "$RUN_START" $(( RUN_START + RUNS - 1 ))); do
        out=$repo/results/week1_transfer/$scenario/$label/$run_id
        rm -rf "$out"; mkdir -p "$out"

        # Fresh namespaces per run: empty queues, counters near zero.
        "$here/up.sh" "$scenario" > "$out/testbed.txt"
        { cat "$STATE_FILE"
          echo "SCHEDULER=$label"; echo "RUN_ID=$run_id"; echo "CC=$CC"
          echo "FILE_BYTES=$FILE_BYTES"; echo "SEED=$SEED"
          echo "TQUIC_COMMIT=$(git -C "$repo/third_party/tquic" rev-parse HEAD)"
          echo "DATE=$(date -Iseconds)"; } > "$out/meta.env"

        # stderr goes to a file: startup errors land there, and so does bash's
        # "Terminated" notice when the server is killed at the end of the run.
        { nsu "$NS_SRV" "$bin/tquic_server" -l "$SRV_IP:$SRV_PORT" \
            --enable-multipath --multipath-algor "$sched" --active-cid-limit 4 \
            --congestion-control-algor "$CC" \
            --cert "$cache/cert.crt" --key "$cache/cert.key" --root "$cache/data" \
            --qlog-dir "$out/qlog_server" --log-file "$out/server.log" \
            --log-level "$LOG_LEVEL" || true; } 2> "$out/server.stderr" &
        srv_job=$!
        for _ in $(seq 50); do
            [[ -n $(nsx "$NS_SRV" ss -Hlun "sport = :$SRV_PORT") ]] && break
            sleep 0.1
        done

        snapshot "$out" before
        # Monotonic clock: WSL2 steps the wall clock (see CLAUDE.md).
        t0=$(python3 -I -c 'import time; print(time.monotonic())')
        status=0
        timeout "$TIMEOUT_S" sudo "$IP" netns exec "$NS_CLI" runuser -u "$RUN_AS" -- "$bin/tquic_client" \
            -c "$SRV_IP:$SRV_PORT" --enable-multipath --multipath-algor "$sched" \
            --local-addresses "$CLI_A_IP,$CLI_B_IP" --active-cid-limit 4 \
            --congestion-control-algor "$CC" \
            --dump-dir "$out/dump" --qlog-dir "$out/qlog_client" \
            --log-file "$out/client.log" --log-level "$LOG_LEVEL" \
            "https://example.org/$file" > "$out/client.out" 2>&1 || status=$?
        t1=$(python3 -I -c 'import time; print(time.monotonic())')
        snapshot "$out" after
        stop_server

        file_ok=0
        cmp -s "$out/dump/$file" "$cache/data/$file" && file_ok=1
        rm -rf "$out/dump"
        # debug/trace logs run to tens of MB per run.
        [[ $LOG_LEVEL == info || $LOG_LEVEL == warn ]] || gzip -f "$out/server.log" "$out/client.log"
        { echo "CLIENT_EXIT=$status"; echo "FILE_OK=$file_ok"
          echo "TRANSFER_S=$(echo "$t1 - $t0" | bc)"; } >> "$out/meta.env"

        python3 -I "$repo/analysis/transfer_summary.py" run "$out"
    done
done

"$here/down.sh"
python3 -I "$repo/analysis/transfer_summary.py" table "$repo/results/week1_transfer/$scenario"
