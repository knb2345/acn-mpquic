#!/usr/bin/env bash
# Create the two-path testbed (topology in common.sh). Safe to rerun: it tears
# down any existing testbed first, so it also recovers after a WSL restart.
#
# Usage: testbed/up.sh [scenario]        default: symmetric
#   Presets follow docs/proposal.md section 4. Override any value with env vars:
#   A_RATE_KBIT A_RTT_MS B_RATE_KBIT B_RTT_MS QUEUE_MS
#
# Each veth end is shaped on egress, so both directions of a path see the same
# link: netem adds RTT/2 of delay, then tbf limits the rate and holds a
# bottleneck queue of QUEUE_MS worth of bytes at that rate.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
source "$here/common.sh"

scenario=${1:-symmetric}
case $scenario in
    symmetric) a_rate=4000 a_rtt=40 b_rate=4000 b_rtt=40 ;;
    rtt_asym)  a_rate=4000 a_rtt=30 b_rate=4000 b_rtt=150 ;;
    low_bw)    a_rate=1500 a_rtt=60 b_rate=1000 b_rtt=120 ;;
    # Rates of these two change over time (testbed/replay.py); the values here
    # are the starting rates and size the queues (trace means for "trace").
    trace)     a_rate=5170 a_rtt=60 b_rate=2020 b_rtt=120 ;;
    outage)    a_rate=4000 a_rtt=40 b_rate=4000 b_rtt=40 ;;
    *) echo "up.sh: unknown scenario '$scenario' (symmetric|rtt_asym|low_bw|trace|outage)" >&2; exit 2 ;;
esac
A_RATE_KBIT=${A_RATE_KBIT:-$a_rate}; A_RTT_MS=${A_RTT_MS:-$a_rtt}
B_RATE_KBIT=${B_RATE_KBIT:-$b_rate}; B_RTT_MS=${B_RTT_MS:-$b_rtt}
QUEUE_MS=${QUEUE_MS:-100}

# netem/tbf are modules in the WSL2 kernel and are not loaded after a restart.
sudo modprobe -a sch_netem sch_tbf sch_htb

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
    # tbf needs a few ms of tokens at high rates; never less than 2 packets.
    local burst=$(( rate * 1000 / 8 / 250 )); (( burst < 3028 )) && burst=3028
    local limit=$(( rate * QUEUE_MS / 8 ));   (( limit < 15140 )) && limit=15140
    nsx "$ns" sysctl -qw "net.ipv4.conf.$dev.rp_filter=0"
    # Offloads off so tbf sees wire-sized packets, not GSO super-packets.
    nsx "$ns" ethtool -K "$dev" tso off gso off gro off
    sudo "$IP" -n "$ns" link set "$dev" up
    # Large netem limit: drops should happen only at the tbf bottleneck queue.
    sudo tc -n "$ns" qdisc replace dev "$dev" root handle 1: netem delay "${owd_us}us" limit 100000
    sudo tc -n "$ns" qdisc replace dev "$dev" parent 1:1 handle 10: \
        tbf rate "${rate}kbit" burst "$burst" limit "$limit"
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
A_RATE_KBIT=$A_RATE_KBIT
A_RTT_MS=$A_RTT_MS
B_RATE_KBIT=$B_RATE_KBIT
B_RTT_MS=$B_RTT_MS
QUEUE_MS=$QUEUE_MS
EOF
echo "testbed up: $scenario  A=${A_RATE_KBIT}kbit/${A_RTT_MS}ms  B=${B_RATE_KBIT}kbit/${B_RTT_MS}ms  queue=${QUEUE_MS}ms"
