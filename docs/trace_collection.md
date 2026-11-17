# Recording two-operator uplink traces

Status (18 Nov 2026): this collection was planned but not carried out; no
phone traces were recorded, so H1 and the real_pairs scenario are untested.

How to record the cellular traces the project needs: two phones on two
different operators, both sending to one cloud server at the same time, at
three sites. These recordings answer RQ1 (is one uplink enough for a 2.6 Mbit/s
lecture?) and become the `real_pairs` scenario in the emulator.

Please follow the steps exactly; every site has to be recorded the same way
for the results to be comparable. If something does not work as described,
write down what happened and tell Kabir; do not change the commands.

## What you need

- Two Android phones, each with a SIM from a **different** operator (for
  example Jio and Airtel). Mobile data on, WiFi off.
- Termux on both phones, installed from F-Droid (the Play Store version is
  outdated).
- The cloud server (one person sets it up once; see below). You need its
  public IP address.
- About 11 minutes per site, two people (or one person with both phones).
- Mobile data: up to about 1.1 GB per phone per site (15 Mbit/s for 10
  minutes). Operators bill only what actually gets through, so at weak sites it
  is much less. Check that each SIM's pack has at least 1.5 GB left.

Sites (three in total, by 9 Nov; the first one this week):

1. indoor, on campus (a classroom or lab);
2. outdoor (a courtyard, street or bus stop);
3. moving vehicle (bus, auto or car; someone else drives).

## 1. Cloud server (once, before the first site)

A small Linux VM in an Indian region (for example Azure Central India, AWS or
GCP Mumbai), Ubuntu 24.04, 1 vCPU is plenty.

1. In the cloud console's firewall / network security group, allow inbound
   **UDP and TCP on ports 5201-5202** and **ICMP echo (ping)** from anywhere.
2. On the VM:

   ```bash
   sudo apt update && sudo apt install -y iperf3 tcpdump tmux
   ```

3. Copy `traces/collect/vm_server.sh` from the repo to the VM, then start it
   inside tmux so it keeps running when you disconnect:

   ```bash
   tmux new -s traces
   ```

   ```bash
   chmod +x vm_server.sh && sudo ./vm_server.sh ~/traces_raw
   ```

   It runs two iperf3 servers: port **5201 for phone A**, port **5202 for
   phone B**. Each test produces `~/traces_raw/p<port>_<time>.json` (the
   received rate in 100 ms bins) and `.first` (the server-clock time of the
   first packet, used to line up the two phones). Both phones are measured on
   the VM's clock, which is why both must use the same VM.
4. Note the VM's public IP. Stop the servers (Ctrl+C in tmux) when the
   collection is over: they accept tests from anyone.

## 2. Phones (once)

In Termux on each phone:

```bash
pkg update && pkg install -y iperf3
```

```bash
termux-setup-storage
```

Then check that `ping -D` prints a timestamp in brackets at the start of each
line (replace VM_IP):

```bash
ping -D -c 3 VM_IP
```

If there are no `[1793...]` timestamps, or ping does not reach the VM, stop and
tell Kabir.

Phone settings for every recording: WiFi off, VPN off, battery saver and data
saver off, screen kept on, no other apps using data, phone not charging from a
laptop that shares its connection.

## 3. At each site

Decide who is phone **A** (port 5201) and who is phone **B** (port 5202); keep
the same operator on A at every site.

### 3a. UDP reachability check (each phone)

Some operators block or throttle UDP. Run (A uses 5201, B uses 5202):

```bash
iperf3 -c VM_IP -p 5201 -u -b 1M -l 1200 -t 5
```

Look at the last line marked `receiver`. If it shows data received (for
example `0.59 MBytes ... 1.00 Mbits/sec`), UDP works. If the command fails, or
the receiver line shows 0 bytes or 100% loss, write "UDP blocked" for that
operator in the site log, skip that phone at this site, and tell Kabir. Do not
switch to TCP.

### 3b. Recording (both phones at the same time)

Paste this into Termux on each phone, after editing the first line (SITE: one
of `campus_indoor`, `outdoor`, `vehicle`; OP: operator name in lower case;
PORT: 5201 on phone A, 5202 on phone B):

```bash
SITE=campus_indoor; OP=jio; PORT=5201; VM=VM_IP
```

```bash
termux-wake-lock; D=~/storage/shared/traces/${SITE}_${OP}_$(date +%Y%m%d_%H%M); mkdir -p $D; echo "folder: $D"
```

Count down together and press Enter on both phones within a few seconds of
each other:

```bash
ping -D -i 0.2 -w 30 $VM > $D/ping_before.txt; ping -D -i 0.2 -w 600 $VM > $D/ping_during.txt & iperf3 -c $VM -p $PORT -u -b 15M -l 1200 -t 600 -i 0.1 -J > $D/iperf_client.json; wait; ping -D -i 0.2 -w 30 $VM > $D/ping_after.txt; echo DONE
```

This takes about 11 minutes: 30 s of ping with the link idle, 10 minutes of
UDP at 15 Mbit/s with ping running alongside, then 30 s of ping idle again.
Do not touch the phones until both print `DONE`. Keep them still (sites 1 and
2) or in the same vehicle, side by side (site 3).

Why the steps are as they are:

- **15 Mbit/s offered:** more than these uplinks usually carry, so what the
  server receives is the link's capacity. When a link could carry more than
  15 Mbit/s, the trace shows 15 Mbit/s instead of the true capacity. Every
  100 ms bin within 5% of the offered rate is flagged `capped` in the trace
  and the share of capped bins is reported.
- **Ping before and after (link idle):** this is the path's own delay, and it
  becomes the emulated path's delay. The ping during the test (link full) also
  contains the phone's uplink queue; the emulator adds its own 200 ms
  buffer, so using the loaded RTT would count queueing twice. The loaded RTT
  is kept in the trace for reference only.
- **Same VM, same time:** H1 compares the two operators second by second, so
  both recordings must cover the same period on one clock.
- **1200-byte packets:** the size QUIC packets have in our experiments.

### 3c. Site log

Fill in one row per phone per site, in a file `site_log.md` in the site's
folder (or a shared sheet):

| field | example |
| --- | --- |
| site | campus_indoor |
| date, start time (IST) | 2026-11-03, 14:05 |
| place | Lab 2, ground floor, CS building (or GPS coordinates) |
| indoor / outdoor / moving | indoor |
| phone (A/B), model | A, Redmi Note 12 |
| operator, network type shown | Jio, 5G (or 4G/LTE) |
| signal | 3 bars; dBm from Settings > About phone > SIM status if available |
| UDP reachability | ok / blocked |
| VM region, IP | Azure Central India, 20.x.x.x |
| notes | anything unusual: call received, network type changed, phone got hot, run stopped early |

## 4. After each site

1. On each phone, the folder `Internal storage/traces/<site>_<operator>_<time>/`
   holds `ping_before.txt`, `ping_during.txt`, `ping_after.txt`,
   `iperf_client.json` and `site_log.md`. Copy it to the shared drive.
2. From the VM, copy the `.json` and `.first` files from `~/traces_raw/`
   whose times match the recording (one pair on port 5201, one on 5202):

   ```bash
   scp -r user@VM_IP:traces_raw ./traces_raw_<site>
   ```

3. Tell Kabir. He builds the trace site with
   `traces/collect/make_site.py`, which writes `traces/<site>/a.csv`,
   `b.csv` and `site.json` (format below); the raw files are kept alongside.

Data use: the operator app shows what each recording used; please add it to
the site log.

## Trace format (for reference)

`traces/<site>/a.csv` and `b.csv`, one row per 100 ms bin, both on the same
time axis:

- `t_ms`: 0, 100, 200, ...
- `mbps`: IP-level rate the server received in that bin (UDP payload plus
  28 bytes of UDP/IP header per packet);
- `capped`: 1 if the bin's payload rate is within 5% of the offered
  15 Mbit/s;
- `loaded_rtt_ms`: median ping RTT during the test in that bin (reference
  only; empty if no ping fell in the bin).

`traces/<site>/site.json`: operators, `unloaded_rtt_ms` per path (median of
the idle pings before and after; the emulator's delay), number of bins, share
of capped bins, offered rate, source file names.
