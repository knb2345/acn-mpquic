#!/usr/bin/env bash
# Generate a synthetic stand-in for a lecture recording (until we have a real
# one): mostly static slides whose bullets appear one by one and which change
# every 15 s, a moving pointer, and a small "webcam" inset (a slowly drifting
# smooth pattern with sensor noise; a busier inset made every P-frame as costly as
# the cap). Encoded near-lossless; make_frame_trace.sh does the real encode.
#
# Usage: workload/synth_lecture.sh [out.mkv] [duration_s]
#   default: workload/src/synthetic_lecture.mkv, 60 s
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=${1:-$here/src/synthetic_lecture.mkv}
dur=${2:-60}
mkdir -p "$(dirname "$out")"
font=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf

titles=("Multipath transport" "Scheduling packets" "Head of line blocking" "Deadline aware video")
bullets=("Two paths share one connection" "Each path has its own RTT and rate" \
         "The scheduler picks a path per packet" "Reordering costs latency")

# One slide every 15 s: a title, then one more bullet every 3 s.
vf="[0:v]"
for (( s = 0; s * 15 < dur; s++ )); do
    t0=$(( s * 15 )); t1=$(( t0 + 15 ))
    title=${titles[s % ${#titles[@]}]}
    vf+="drawtext=fontfile=$font:text='$title':x=80:y=60:fontsize=52:fontcolor=0x1d2a44:enable='between(t,$t0,$t1)',"
    for (( b = 0; b < ${#bullets[@]}; b++ )); do
        vf+="drawtext=fontfile=$font:text='- ${bullets[b]} ($((s + 1)).$((b + 1)))':x=110:y=$(( 180 + b * 70 )):fontsize=36:fontcolor=0x333333:enable='between(t,$(( t0 + 2 + b * 3 )),$t1)',"
    done
done
vf+="null[slides];"
# Webcam: slowly drifting smooth pattern with temporal sensor noise. Built
# from a formula and a fixed noise seed so every run gives the same video
# (the gradients source ignores its seed).
vf+="[1:v]geq=lum='110+50*sin(X/37+T/5)*cos(Y/29-T/7)':cb='128+12*sin(T/9)':cr=128,noise=alls=3:allf=t:all_seed=7[cam];"
vf+="[slides][2:v]overlay=x='640+420*sin(t/2.3)':y='420+120*sin(t/3.1)'[ptr];"
vf+="[ptr][cam]overlay=x=W-w-24:y=H-h-24[v]"

ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "color=c=0xf4f1e8:s=1280x720:r=30:d=$dur" \
    -f lavfi -i "color=c=gray:s=320x240:r=30:d=$dur" \
    -f lavfi -i "color=c=red:s=14x14:r=30:d=$dur" \
    -filter_complex "$vf" -map "[v]" -c:v libx264 -preset fast -crf 8 -pix_fmt yuv420p "$out"
echo "wrote $out"
