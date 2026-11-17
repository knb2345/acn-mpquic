#!/usr/bin/env bash
# Overnight batch, 18 Nov 2026: redo every F1/F2/A1/R2/R1 run with the fixed
# build (63baae1), then the rtt_asym bitrate sweep with 8 runs per point.
# Most important first, so a cut-off loses the least: main matrix at 2.6 Mbit/s,
# then the sweep points that decide usable capacity, then the end points, then
# extra sweeps (A1, R2, S2, F1). One log per step in results/batches/.
set -u
cd "$(dirname "$0")/.."
ids="1 2 3 4 5 6 7 8"
step() { local tag=$1; shift; echo "$(date -u +%FT%TZ) start $tag"; env "$@" scripts/batch.sh "$tag"; local rc=$?; echo "$(date -u +%FT%TZ) end $tag rc=$rc"; }
step final_main_2600 SCENARIOS="rtt_asym bw_asym symmetric outage control" CONFIGS="F2 R1 R2 F1 A1" RUN_IDS="$ids" BITRATE=2600
for b in 1000 1600 2200 2800; do
    step final_sweep_$b SCENARIOS=rtt_asym CONFIGS="S1 S4 R1 F2" RUN_IDS="$ids" BITRATE=$b
done
for b in 500 3500; do
    step final_sweep_$b SCENARIOS=rtt_asym CONFIGS="S1 S4 R1 F2" RUN_IDS="$ids" BITRATE=$b
done
for b in 1000 1600 2200 2800 500 3500; do
    step final_sweep_extra_$b SCENARIOS=rtt_asym CONFIGS="A1 S2 R2 F1" RUN_IDS="$ids" BITRATE=$b
done
echo "$(date -u +%FT%TZ) overnight done"
