#!/usr/bin/env bash
# Overnight batch, part 3 (replaces the steps of overnight_20261118b.sh after
# the 1.6 Mbit/s sweep point). F2 froze at 1.0 Mbit/s while every other
# configuration did not, so its usable capacity depends on the 0.5 Mbit/s
# point: run 0.5 Mbit/s for F2 and A1 (P5) first, then 2.2 and 2.8 Mbit/s for
# all five sweep configurations, then the remaining end points and extras.
# Usage: overnight_20261118c.sh <pid of the running batch.sh to wait for>
set -u
cd "$(dirname "$0")/.."
ids="1 2 3 4 5 6 7 8"
step() { local tag=$1; shift; echo "$(date -u +%FT%TZ) start $tag"; env "$@" scripts/batch.sh "$tag"; local rc=$?; echo "$(date -u +%FT%TZ) end $tag rc=$rc"; }
while kill -0 "$1" 2>/dev/null; do sleep 10; done
step final_sweep_500_f2a1 SCENARIOS=rtt_asym CONFIGS="F2 A1" RUN_IDS="$ids" BITRATE=500
for b in 2200 2800; do
    step final_sweep_$b SCENARIOS=rtt_asym CONFIGS="S1 S4 R1 F2 A1" RUN_IDS="$ids" BITRATE=$b
done
step final_sweep_500 SCENARIOS=rtt_asym CONFIGS="S1 S4 R1" RUN_IDS="$ids" BITRATE=500
step final_sweep_3500 SCENARIOS=rtt_asym CONFIGS="S1 S4 R1 F2 A1" RUN_IDS="$ids" BITRATE=3500
echo "$(date -u +%FT%TZ) overnight part 3 done"
