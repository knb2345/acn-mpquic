# Shared names and helpers for the testbed scripts. Sourced, not executed.
#
#  ns mpq-cli                               ns mpq-srv
#   cA 10.10.1.1/24 ---- path A (veth) ---- sA 10.10.1.2/24
#   cB 10.10.2.1/24 ---- path B (veth) ---- sB 10.10.2.2/24
#                                           lo 10.10.0.1/32  (server address)
#
# The client reaches 10.10.0.1 over A or B depending on the source address
# (policy routing), so each QUIC path is pinned to one veth pair.

NS_CLI=mpq-cli
NS_SRV=mpq-srv

CLI_A=cA; SRV_A=sA; CLI_A_IP=10.10.1.1; SRV_A_IP=10.10.1.2
CLI_B=cB; SRV_B=sB; CLI_B_IP=10.10.2.1; SRV_B_IP=10.10.2.2
SRV_IP=10.10.0.1
SRV_PORT=4433

# up.sh writes the active link parameters here; wiped with the namespaces on
# a WSL restart, which is what we want.
# XDG_RUNTIME_DIR can point to a folder that does not exist (e.g. after a WSL
# restart); fall back to /tmp then.
STATE_DIR=${XDG_RUNTIME_DIR:-/tmp}
[[ -d $STATE_DIR && -w $STATE_DIR ]] || STATE_DIR=/tmp
STATE_FILE=$STATE_DIR/mpq-testbed.env

# The user who invoked the script; processes inside the namespaces run as this
# user so that result files are not owned by root.
RUN_AS=$(id -un)

# The sudoers rule (testbed/README.md) names /usr/bin/ip; plain "sudo ip"
# resolves to the /usr/sbin/ip symlink, which sudo does not match.
IP=/usr/bin/ip

# Run a command as root inside a namespace.
nsx() { local ns=$1; shift; sudo "$IP" netns exec "$ns" "$@"; }

# Run a command as $RUN_AS inside a namespace.
nsu() { local ns=$1; shift; sudo "$IP" netns exec "$ns" runuser -u "$RUN_AS" -- "$@"; }

ns_exists() { ip netns list 2>/dev/null | grep -qw "$1"; }
