#!/usr/bin/env bash
# Build the streamer and copy its binaries to .cache/bin/<tag>/ with a
# BUILD_INFO file, so a long batch keeps running the exact build it started
# with while the sources change.
#
# Usage: testbed/freeze_bins.sh <tag>
#   then: BIN_DIR=.cache/bin/<tag> testbed/run_stream.sh <scenario> ...
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/.." && pwd)
tag=${1:?usage: freeze_bins.sh <tag>}
dst=$repo/.cache/bin/$tag
[[ -e $dst ]] && { echo "freeze_bins.sh: $dst exists" >&2; exit 1; }

cargo build --release --manifest-path "$repo/streamer/Cargo.toml" 2>&1 | tail -1
mkdir -p "$dst"
cp "$repo/streamer/target/release/mpq_sender" "$repo/streamer/target/release/mpq_receiver" "$dst/"
{
    echo "BUILD_TAG=$tag"
    echo "BUILD_DATE=$(date -Iseconds)"
    echo "TQUIC_COMMIT=$(git -C "$repo/third_party/tquic" rev-parse HEAD)"
    echo "TQUIC_DIRTY=$(git -C "$repo/third_party/tquic" status --porcelain -uno | wc -l)"
    echo "REPO_COMMIT=$(git -C "$repo" rev-parse HEAD)"
    # Hash of the streamer sources as built (they may not be committed yet).
    echo "STREAMER_SRC_SHA=$(cd "$repo/streamer" && cat Cargo.toml src/*.rs src/bin/*.rs | sha256sum | cut -c1-16)"
} > "$dst/BUILD_INFO"
cat "$dst/BUILD_INFO"
