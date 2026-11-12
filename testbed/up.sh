#!/usr/bin/env bash
# Create the two-path testbed (topology in common.sh). Safe to rerun: it tears
# down any existing testbed first, so it also recovers after a WSL restart.
#
# Usage: testbed/up.sh [scenario] [mode]     default: symmetric default
#   Scenarios follow docs/proposal.md section 4 (rates in kbit/s, RTT in ms):
#     control | symmetric | rtt_asym | bw_asym | outage | real_pairs
#   Modes:
#     default : Standard dual-path network topology.
#     R1      : Ideal single-link reference (Rate = Rate_A + Rate_B, RTT = min(RTT_A, RTT_B)).
#
# Override any value with env vars: A_RATE_KBIT A_RTT_MS B_RATE_KBIT B_RTT_MS
# QUEUE_MS (bottleneck buffer per path, default 200 ms of the path's rate).
# real_pairs needs SITE=<dir under traces/> (see testbed/trace_site.py); its rates
# are the trace means (they size the buffers) and its delays the unloaded
# RTTs measured at the site.
# outage and real_pairs change rates over time: run testbed/replay.py too.
#
# Each veth end is shaped on egress, so both directions of a path see the same
# link: netem adds RTT/2 of delay, then an htb class limits the rate and a
# plug qdisc under it (a byte FIFO that replay.py can block) holds a
# bottleneck queue of QUEUE_MS worth of bytes at that rate. Not tbf: tbf
# refills its whole bucket on every `tc change`, so trace replay (a change
# every 100 ms) let extra bursts through; htb keeps its tokens when a class is
# changed (docs/calibration.md). Data flows client -> server (the lecturer's
# device is the client).

set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
source "$here/common.sh"

scenario=${1:-symmetric}
mode=${2:-default}

case $scenario in
    control)    a_rate=4000 a_rtt=40 b_rate=4000 b_rtt=40 ;;
    symmetric)  a_rate=1600 a_rtt=40 b_rate=1600 b_rtt=40 ;;
    rtt_asym)   a_rate=1600 a_rtt=30 b_rate=1600 b_rtt=150 ;;
    bw_asym)    a_rate=2000 a_rtt=40 b_rate=1200 b_rtt=60 ;;
    outage)     a_rate=1800 a_rtt=40 b_rate=1800 b_rtt=60 ;;
    real_pairs)
        [[ -n ${SITE:-} ]] || { echo "up.sh: real_pairs needs SITE=<traces/ subdir>" >&2; exit 2; }
        eval "$(python3 -I "$here/trace_site.py" env "$SITE")" ;;
    *) echo "up.sh: unknown scenario '$scenario' (control|symmetric|rtt_asym|bw_asym|outage|real_pairs)" >&2; exit 2 ;;
esac

# Handle R1 Ideal Link Reference Mode
if [[ "$mode" == "R1" ]]; then
    r1_rate=$(( a_rate + b_rate ))
    r1_rtt=$(( a_rtt < b_rtt ? a_rtt : b_rtt ))
    a_rate=$r1_rate
    a_rtt=$r1_rtt
    # In R1 mode, all traffic is routed through Path A; Path B is set to a minimal stub
    b_rate=1
    b_rtt=1000
fi

A_RATE_KBIT=${A_RATE_KBIT:-$a_rate}; A_RTT_MS=${A_RTT_MS:-$a_rtt}
B_RATE_KBIT=${B_RATE_KBIT:-$b_rate}; B_RTT_MS=${B_RTT_MS:-$b_rtt}
QUEUE_MS=${QUEUE_MS:-200}

# netem/htb/plug are modules in the WSL2 kernel and are not loaded after a restart.
sudo modprobe -a sch_netem sch_htb sch_plug

"$here/down.sh"
sudo "$IP" netns add "$NS_CLI"
sudo "$IP" netns add "$NS_SRV"

# Each veth pair is created with its two ends already in the two namespaces.
sudo "$IP" link add "$CLI_A" netns "$NS_CLI" type veth peer name "$SRV_A" netns "$NS_SRV"
sudo "$IP" link add "$CLI_B" netns "$NS_CLI" type veth peer name "$SRV_B" netns "$NS_SRV"

for ns in "$NS_CLI" "$NS_SRV"; do
    # No IPv6 chatter in the byte counters; no reverse-path filtering, since
    # replies to path B arrive from an address the main table routes via A.
    nsx "$ns" sysctl -qw net.ipv6.conf.all.disable_ipv6=1 \
        net.ipv4.conf.all.rp_filter=0 net.ipv4.conf.default.rp_filter=0
    sudo "$IP" -n "$ns" link set lo up
done

sudo "$IP" -n "$NS_CLI" addr add "$CLI_A_IP/24" dev "$CLI_A"
sudo "$IP" -n "$NS_CLI" addr add "$CLI_B_IP/24" dev "$CLI_B"
sudo "$IP" -n "$NS_SRV" addr add "$SRV_A_IP/24" dev "$SRV_A"
sudo "$IP" -n "$NS_SRV" addr add "$SRV_B_IP/24" dev "$SRV_B"
sudo "$IP" -n "$NS_SRV" addr add "$SRV_IP/32" dev lo

# shape NS DEV RATE_KBIT RTT_MS
shape() {
    local ns=$1 dev=$2 rate=$3 rtt=$4
    local owd_us=$(( rtt * 1000 / 2 ))
    # A few ms of tokens at high rates; never less than 2 packets.
    local burst=$(( rate * 1000 / 8 / 250 )); (( burst < 3028 )) && burst=3028
    local limit=$(( rate * QUEUE_MS / 8 ));   (( limit < 15140 )) && limit=15140
    nsx "$ns" sysctl -qw "net.ipv4.conf.$dev.rp_filter=0"
    # Offloads off so the shaper sees wire-sized packets, not GSO super-packets.
    nsx "$ns" ethtool -K "$dev" tso off gso off gro off
    sudo "$IP" -n "$ns" link set "$dev" up
    # Large netem limit: drops should happen only at the bottleneck queue.
    sudo tc -n "$ns" qdisc replace dev "$dev" root handle 1: netem delay "${owd_us}us" limit 100000
    sudo tc -n "$ns" qdisc replace dev "$dev" parent 1:1 handle 10: htb default 1
    sudo tc -n "$ns" class replace dev "$dev" parent 10: classid 10:1 \
        htb rate "${rate}kbit" ceil "${rate}kbit" burst "$burst" cburst "$burst" quantum 1514
    # Leaf: a byte-limited FIFO that replay.py can also block (rate 0). plug
    # starts blocked, so release it.
    sudo tc -n "$ns" qdisc replace dev "$dev" parent 10:1 handle 11: plug limit "$limit"
    sudo tc -n "$ns" qdisc change dev "$dev" parent 10:1 handle 11: plug release_indefinite
}

shape "$NS_CLI" "$CLI_A" "$A_RATE_KBIT" "$A_RTT_MS"
shape "$NS_SRV" "$SRV_A" "$A_RATE_KBIT" "$A_RTT_MS"
shape "$NS_CLI" "$CLI_B" "$B_RATE_KBIT" "$B_RTT_MS"
shape "$NS_SRV" "$SRV_B" "$B_RATE_KBIT" "$B_RTT_MS"

# Unbound client sockets use path A; sockets bound to a path's address use
# that path's table.
sudo "$IP" -n "$NS_CLI" route add "$SRV_IP/32" via "$SRV_A_IP" dev "$CLI_A"
sudo "$IP" -n "$NS_CLI" route add "$SRV_IP/32" via "$SRV_A_IP" dev "$CLI_A" table 101
sudo "$IP" -n "$NS_CLI" route add "$SRV_IP/32" via "$SRV_B_IP" dev "$CLI_B" table 102
sudo "$IP" -n "$NS_CLI" rule add from "$CLI_A_IP" table 101 pref 101
sudo "$IP" -n "$NS_CLI" rule add from "$CLI_B_IP" table 102 pref 102

cat > "$STATE_FILE" <<EOF
SCENARIO=$scenario
MODE=$mode
SITE=${SITE:-}
A_RATE_KBIT=$A_RATE_KBIT
A_RTT_MS=$A_RTT_MS
B_RATE_KBIT=$B_RATE_KBIT
B_RTT_MS=$B_RTT_MS
QUEUE_MS=$QUEUE_MS
EOF

echo "testbed up: scenario=$scenario mode=$mode A=${A_RATE_KBIT}kbit/${A_RTT_MS}ms B=${B_RATE_KBIT}kbit/${B_RTT_MS}ms queue=${QUEUE_MS}ms"