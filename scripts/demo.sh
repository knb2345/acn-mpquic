#!/usr/bin/env bash
# Class demo: stream the 60-s lecture over the two emulated links, once per
# scheduler, with a live dashboard in the terminal; then open a side-by-side
# figure of every frame's delay.
#
# Usage: scripts/demo.sh [live|replay] [scenario] [config ...]
#   live    (default) real runs on the testbed, ~65 s per config. Needs the
#           passwordless sudo rule for ip/tc/modprobe (testbed/README.md).
#   replay  no network: replays recorded run 1 of each config (the fallback).
#   Defaults: S1 F2 (MinRTT vs our frame splitting), scenario rtt_asym
#   (30 ms vs 150 ms paths).
#   Env: SPEED (replay speed, default 1), KBPS (default 2600).
# Live runs are written like any run (scripts/run.sh) with a run id >= 900, so
# they are never part of the analysis (run ids 1-8); figure in results/demo/.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/.." && pwd)
mode=${1:-live}
scenario=${2:-rtt_asym}
shift $(( $# > 2 ? 2 : $# ))
configs=("$@")
if (( ${#configs[@]} == 0 )); then
    configs=(S1 F2)
fi
kbps=${KBPS:-2600}
py=$repo/analysis/.venv/bin/python
[[ -x $py ]] || py=python3
out=$repo/results/demo/$(date +%Y%m%d-%H%M%S)
mkdir -p "$out"
dirs=()

case $mode in
live)
    # Always rebuild (a no-op when up to date): a stale binary may lack --policy.
    (cd "$repo/streamer" && cargo build --release -q) || { echo "streamer build failed" >&2; exit 1; }
    # One run id for all configs, so they stream the same part of the clip.
    id=900
    while :; do
        free=1
        for c in "${configs[@]}"; do
            [[ -e $repo/results/$scenario/$c/${kbps}_intra_stochastic/$id ]] && free=0
        done
        (( free )) && break
        id=$((id + 1))
    done
    for c in "${configs[@]}"; do
        run=$repo/results/$scenario/$c/${kbps}_intra_stochastic/$id
        DEMO_LIVE=1 "$repo/scripts/run.sh" "$scenario" "$c" "$kbps" intra stochastic "$id" > "$out/$c.log" 2>&1 &
        pid=$!
        "$py" "$here/demo_dashboard.py" live --run-dir "$run" --config "$c" --scenario "$scenario" --pid "$pid"
        rc=0; wait "$pid" || rc=$?
        case $rc in
            0) ;;
            3) echo "  (run finished with frames still missing: a stalled run)" ;;
            *) echo "  run.sh exited with $rc; see $out/$c.log"; tail -5 "$out/$c.log" ;;
        esac
        [[ -f $run/frames.csv ]] && dirs+=("$run")
        sleep 2
    done
    ;;
replay)
    for c in "${configs[@]}"; do
        run=$repo/results/$scenario/$c/${kbps}_intra_stochastic/1
        "$py" "$here/demo_dashboard.py" replay --run-dir "$run" --config "$c" --scenario "$scenario" --speed "${SPEED:-1}"
        dirs+=("$run")
        sleep 2
    done
    ;;
*) echo "usage: $0 [live|replay] [scenario] [config ...]" >&2; exit 2 ;;
esac

(( ${#dirs[@]} )) || { echo "no finished runs to compare" >&2; exit 1; }
"$py" "$here/demo_dashboard.py" figure --out "$out/demo.png" "${dirs[@]}"
# Open the figure on the Windows side (WSL), else with the Linux default viewer.
if command -v explorer.exe >/dev/null; then
    explorer.exe "$(wslpath -w "$out/demo.png")" >/dev/null 2>&1 || true
elif command -v xdg-open >/dev/null; then
    xdg-open "$out/demo.png" >/dev/null 2>&1 || true
fi
