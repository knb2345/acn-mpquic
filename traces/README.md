# Traces

Format: csv `t_ms,mbps`, one row per 100 ms bin; the replay
(`testbed/replay.py`) sets each path's tbf rate from these every 100 ms.

## Public traces (the setup)

From Mahimahi (https://github.com/ravinet/mahimahi, `traces/`, commit
`f1346c3`, GPLv3; Netravali et al., USENIX ATC 2015). Each Mahimahi trace lists
the milliseconds at which the link can deliver one 1500-byte packet; converted
with:

```bash
python3 traces/convert_mahimahi.py <mahimahi>/traces/ATT-LTE-driving.down traces/att_lte_driving.down.csv
```

| file | role | mean | p10 | p50 | p90 | zero bins |
| --- | --- | --- | --- | --- | --- | --- |
| `att_lte_driving.down.csv` | carrier 1, path A, server -> client | 5.17 | 1.08 | 5.28 | 9.12 | 5.1% |
| `att_lte_driving.up.csv` | carrier 1, path A, client -> server | 0.83 | 0.00 | 0.84 | 1.44 | 14.8% |
| `tmobile_umts_driving.down.csv` | carrier 2, path B, server -> client | 2.02 | 0.00 | 1.80 | 4.08 | 11.7% |
| `tmobile_umts_driving.up.csv` | carrier 2, path B, client -> server | 0.94 | 0.12 | 1.08 | 1.44 | 8.9% |

(Mbit/s, 100 ms bins.) These are US carriers recorded while driving (LTE and
3G UMTS), chosen because their rates fit the low-bandwidth setting: one usable
4G link plus one weak link, about 7 Mbit/s together for a 2 Mbit/s lecture.
The project runs in emulation only, so these public traces are the path
conditions for the trace scenario.

## Own traces: out of scope

The original proposal planned Chennai dual-SIM traces. Since 28 Oct 2026 the
project is emulation-only, so they are not collected. `collect/` keeps the
steps and the converter in case someone records traces later.
