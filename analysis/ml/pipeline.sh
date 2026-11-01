#!/usr/bin/env bash
# The learned scheduler end to end, after training data has been collected
# (testbed/collect.sh, see analysis/ml/README.md): train, build, choose the
# quantile on training conditions, check the Rust model against Python, and
# run the final comparison. Stops at the first failed step.
#
# Usage: analysis/ml/pipeline.sh
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo"
step() { echo "=== $* $(date +%H:%M)"; }

# Usage: SKIP_REDO=1 to skip the first step once it has run.
if [[ ${SKIP_REDO:-0} != 1 ]]; then
    step "redo runs disturbed by laptop sleep"
    WORKLOAD=workload/synthetic_lecture.csv BIN_DIR=.cache/bin/ml0 testbed/collect.sh 13 13 roundrobin
    BIN_DIR=.cache/bin/final WORKLOAD=workload/synthetic_lecture.csv RUN_START=3 RUNS=1 \
        testbed/run_stream.sh symmetric single
fi

step "dataset and training"
python3 -I analysis/ml/features.py results/ml_train/train.npz results/ml_train/*/*/*
python3 -I analysis/ml/train.py results/ml_train/train.npz --use-q 0.2
# The quantiles must come out roughly calibrated on held-out conditions.
python3 -I - <<'EOF'
import json, sys
r = json.load(open("results/ml_train/train.train.json"))
cov = dict(zip(r["quantiles"], r["val_coverage"]))
print("validation coverage:", cov)
bad = [q for q, c in cov.items() if abs(c - q) > 0.15]
sys.exit(f"badly calibrated quantiles {bad}" if bad else 0)
EOF

step "build TQUIC with the trained model"
(cd third_party/tquic && cargo test --release 2>&1 | grep -E '^test result' | head -1)
(cd third_party/tquic && git add src/multipath_scheduler/ml_model.rs &&
    git commit -q -m "team7: trained bandwidth predictor

$(head -2 src/multipath_scheduler/ml_model.rs | tail -1 | sed 's|^// ||')

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
rm -rf .cache/bin/ml1 && testbed/freeze_bins.sh ml1

step "choose the quantile on 4 fresh training conditions"
rm -rf results/ml_tune
for q in 0.1 0.2 0.3 0.5; do
    LEARNED_Q=$q OUT_ROOT=results/ml_tune BIN_DIR=.cache/bin/ml1 testbed/collect.sh 21 24 learned
done
q=$(python3 -I - <<'EOF'
import csv, glob, statistics as st
best = None
for d in sorted(glob.glob("results/ml_tune/copyrightx_lecture/learned-q*")):
    rows = [next(csv.DictReader(open(f))) for f in glob.glob(d + "/*/summary.csv")]
    miss = st.mean(float(r["miss_pct"]) for r in rows)
    stall = st.mean(float(r["stall_ms"]) for r in rows)
    q = d.rsplit("-q", 1)[1]
    print(f"q={q}: mean miss {miss:.2f}%, mean stall {stall:.0f} ms over {len(rows)} runs", file=__import__("sys").stderr)
    if best is None or (miss, stall) < best[0]:
        best = ((miss, stall), q)
print(best[1])
EOF
)
echo "chosen quantile: $q"
idx=$(python3 -I -c "print([0.1, 0.2, 0.3, 0.5].index(float('$q')))")
sed -i "s/^pub(crate) const USE_Q: usize = [0-9]*;/pub(crate) const USE_Q: usize = $idx;/" \
    third_party/tquic/src/multipath_scheduler/ml_model.rs
(cd third_party/tquic && if ! git diff --quiet; then git commit -q -am "team7: learned scheduler uses the q$q estimate (chosen on training conditions)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"; fi)
rm -rf .cache/bin/ml2 && testbed/freeze_bins.sh ml2

step "check the Rust model against Python"
BIN_DIR=.cache/bin/ml2 RUNS=1 testbed/run_stream.sh trace learned
python3 -I analysis/ml/eval_predictor.py --parity results/ml_train/train.model.npz \
    results/copyrightx_lecture/trace/learned/1

step "final comparison, real lecture at 1.57 Mbit/s (workload/README.md)"
for s in low_bw trace outage rtt_asym symmetric; do
    step "lecture $s"
    BIN_DIR=.cache/bin/ml2 WORKLOAD=workload/copyrightx_lecture_crf22.csv \
        testbed/run_stream.sh "$s" learned deadline framepin minrtt ecf roundrobin redundant single
done

step "learned scheduler, synthetic lecture"
for s in low_bw trace outage rtt_asym symmetric; do
    BIN_DIR=.cache/bin/ml2 WORKLOAD=workload/synthetic_lecture.csv testbed/run_stream.sh "$s" learned
done
step "pipeline done"
