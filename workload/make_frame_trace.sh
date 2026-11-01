#!/usr/bin/env bash
# Encode a lecture video the way a live stream would be (x264, zerolatency, no
# B-frames, fixed 2 s GOP: at 30 fps one I-frame then 59 P-frames), and write
# its per-frame trace for the streamer. Rate control is constant quality (CRF)
# under a bitrate cap, like OBS-style encoders: static slides give small
# P-frames and large I-frames. Strict CBR instead makes every frame about the
# same size, which hides the I-frame problem the scheduler is about.
#
# Usage: workload/make_frame_trace.sh <input video> <name> [crf, default 20] [cap, default 3M]
#   Env: START, DURATION (seconds; cut a clip), FPS (default 30; use the
#   source's own rate for real footage, as converting adds duplicate frames).
#   The I-frame interval stays 2 s at any FPS.
# Writes workload/<name>.mp4 (git-ignored) and workload/<name>.csv:
#   frame_id,frame_type,size_bytes,pts_ms
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
in=${1:?usage: make_frame_trace.sh <input video> <name> [crf] [cap]}
name=${2:?usage: make_frame_trace.sh <input video> <name> [crf] [cap]}
crf=${3:-20}
cap=${4:-3M}
fps=${FPS:-30}
gop=$(( fps * 2 ))
clip=()
[[ -n ${START:-} ]] && clip+=(-ss "$START")
[[ -n ${DURATION:-} ]] && clip+=(-t "$DURATION")

ffmpeg -hide_banner -loglevel error -y "${clip[@]}" -i "$in" -an -r "$fps" -s 1280x720 \
    -c:v libx264 -preset veryfast -tune zerolatency -bf 0 \
    -g "$gop" -keyint_min "$gop" -sc_threshold 0 \
    -crf "$crf" -maxrate "$cap" -bufsize "$cap" -pix_fmt yuv420p \
    "$here/$name.mp4"

# No B-frames, so decode order is presentation order and each packet is one
# frame; the K flag marks I-frames.
{
    echo "frame_id,frame_type,size_bytes,pts_ms"
    ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,size,flags \
        -of csv=p=0 "$here/$name.mp4" |
    awk -F, '{ printf "%d,%s,%d,%.3f\n", NR - 1, ($3 ~ /K/ ? "I" : "P"), $2, $1 * 1000 }'
} > "$here/$name.csv"

python3 -I - "$here/$name.csv" <<'EOF'
import csv, statistics, sys
rows = list(csv.DictReader(open(sys.argv[1])))
size = lambda t: [int(r["size_bytes"]) for r in rows if r["frame_type"] == t]
pts = [float(r["pts_ms"]) for r in rows]
dur = (pts[-1] + statistics.median(b - a for a, b in zip(pts, pts[1:]))) / 1000
total = sum(int(r["size_bytes"]) for r in rows)
print(f"{sys.argv[1]}: {len(rows)} frames, {dur:.1f} s, {total * 8 / dur / 1e6:.2f} Mbit/s; "
      f"I: {len(size('I'))} x median {statistics.median(size('I')) / 1000:.1f} KB (max {max(size('I')) / 1000:.1f}); "
      f"P: median {statistics.median(size('P')) / 1000:.1f} KB (p95 {statistics.quantiles(size('P'), n=20)[18] / 1000:.1f}, max {max(size('P')) / 1000:.1f})")
EOF
