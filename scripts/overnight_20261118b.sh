#!/usr/bin/env bash
# Overnight batch, part 2 (replaces the steps after the main matrix in
# overnight_20261118.sh): A1 joins the rtt_asym sweep, because P5 (H4) is
# defined on usable capacity. Waits for the main-matrix batch to finish, then
# runs the sweep points that decide usable capacity, then the end points, then
# the extra sweeps. Same build (repo code unchanged since 63baae1).
set -u
cd "$(dirname "$0")/.."
ids="1 2 3 4 5 6 7 8"
step() { local tag=$1; shift; echo "$(date -u +%FT%TZ) start $tag"; env "$@" scripts/batch.sh "$tag"; local rc=$?; echo "$(date -u +%FT%TZ) end $tag rc=$rc"; }
while pgrep -f "batch.sh final_main_2600" >/dev/null; do sleep 20; done
for b in 1000 1600 2200 2800; do
    step final_sweep_$b SCENARIOS=rtt_asym CONFIGS="S1 S4 R1 F2 A1" RUN_IDS="$ids" BITRATE=$b
done
for b in 500 3500; do
    step final_sweep_$b SCENARIOS=rtt_asym CONFIGS="S1 S4 R1 F2 A1" RUN_IDS="$ids" BITRATE=$b
done
for b in 1000 1600 2200 2800 500 3500; do
    step final_sweep_extra_$b SCENARIOS=rtt_asym CONFIGS="S2 R2 F1" RUN_IDS="$ids" BITRATE=$b
done
echo "$(date -u +%FT%TZ) overnight part 2 done"
