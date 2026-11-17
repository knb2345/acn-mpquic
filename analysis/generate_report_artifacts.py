import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from stats import paired, wilcoxon_p, holm, tost_paired, bootstrap_ci

# summary_final.csv = analysis/summarize.py over results/<scenario>/ (one row per run
# directory, so no duplicates), run_id 1-8 only (run 0 and 91 are dev runs, never
# analysed). It supersedes summary_all.csv, which mixed in duplicate, pre-fix and dev runs.
df = pd.read_csv("analysis/summary_final.csv")
df = df[df["run_id"].between(1, 8)]
df_main = df[df["bitrate_kbps"] == 2600].copy()
df_sweep = df[df["scenario"] == "rtt_asym"].copy()

print("=== 1. USABLE CAPACITY ON rtt_asym (Highest bitrate with freeze_frac_250 <= 0.01) ===")
sweep_agg = df_sweep.groupby(["config", "bitrate_kbps"])[["on_time_250", "freeze_frac_250", "required_delay_ms"]].mean().reset_index()
for cfg in ["S1", "S2", "S4", "F1", "F2", "A1", "R2", "R1"]:
    sub = sweep_agg[sweep_agg["config"] == cfg].sort_values("bitrate_kbps")
    usable = sub[sub["freeze_frac_250"] <= 0.01]["bitrate_kbps"].max()
    print(f"Config {cfg:3s}: usable_cap = {usable} kbps")
    for _, r in sub.iterrows():
        print(f"   {int(r.bitrate_kbps):4d} kbps | on_time_250={r.on_time_250:.3f} | freeze_250={r.freeze_frac_250:.3f} | req_delay={r.required_delay_ms:.1f}ms")


# Usable capacity per run id (paired design: run id k streams the same clip
# segment under every config): the highest swept bitrate at which run k froze
# for <= 1 % of the time at D = 250 ms.
def usable_by_run(cfg):
    sub = df_sweep[df_sweep["config"] == cfg]
    out = {}
    for rid, g in sub.groupby("run_id"):
        ok = g[g["freeze_frac_250"] <= 0.01]["bitrate_kbps"]
        out[rid] = float(ok.max()) if len(ok) else 0.0
    return out

def full_sweep(cfg):
    sub = df_sweep[df_sweep["config"] == cfg]
    return sub["bitrate_kbps"].nunique() >= 5 and sub.groupby("bitrate_kbps").size().min() >= 8

print("\n=== 1b. PRE-REGISTERED PRIMARY COMPARISONS P1-P5 (Holm over those run) ===")
primary = []  # (name, description, p)
r1 = usable_by_run("R1")
for name, cfg in [("P1", "S1"), ("P2", "S4")]:
    if full_sweep(cfg) and full_sweep("R1"):
        u = usable_by_run(cfg)
        ids = sorted(set(u) & set(r1))
        ratio = np.array([u[i] / r1[i] for i in ids if r1[i] > 0])
        # H2: ratio < 0.8. One-sided Wilcoxon signed-rank of 0.8 - ratio.
        from scipy import stats as _st
        p = float(_st.wilcoxon(0.8 - ratio, alternative="greater").pvalue) if np.any(ratio != 0.8) else 1.0
        primary.append((name, f"usable capacity {cfg}/R1 on rtt_asym: median {np.median(ratio):.2f} (n={len(ratio)}), H2 bar < 0.80", p))
for name, scen in [("P3", "rtt_asym"), ("P4", "bw_asym")]:
    sub = df_main[df_main["scenario"] == scen]
    best = min(["S1", "S4"], key=lambda c: sub[sub["config"] == c]["freeze_frac_250"].mean())
    da = dict(zip(sub[sub.config == "F2"].run_id, sub[sub.config == "F2"].freeze_frac_250))
    db = dict(zip(sub[sub.config == best].run_id, sub[sub.config == best].freeze_frac_250))
    xa, xb, ids = paired(da, db)
    if len(ids) >= 2:
        md, lo, hi = bootstrap_ci(xa, xb)
        primary.append((name, f"{scen} freeze F2 {xa.mean()*100:.1f}% vs {best} {xb.mean()*100:.1f}% (n={len(ids)}), diff {md*100:+.1f} pp [{lo*100:+.1f}, {hi*100:+.1f}], F2 lower in {(xa < xb).sum()}/{len(ids)}", wilcoxon_p(xa, xb)))
# P5 (H4): F2 and A1 equivalent within 5 % of usable capacity (TOST). If the A1
# sweep is incomplete, the same test on freeze time at 2.6 Mbit/s on rtt_asym.
if full_sweep("A1") and full_sweep("F2"):
    uf, ua = usable_by_run("F2"), usable_by_run("A1")
    xa, xb, ids = paired(uf, ua)
    tp, eq = tost_paired(xa, xb, margin_frac=0.05)
    primary.append(("P5", f"usable capacity F2 {xa.mean():.0f} vs A1 {xb.mean():.0f} kbps (n={len(ids)}), TOST 5 %: equivalent={eq}", tp))
else:
    sub = df_main[df_main["scenario"] == "rtt_asym"]
    da = dict(zip(sub[sub.config == "F2"].run_id, sub[sub.config == "F2"].on_time_250))
    db = dict(zip(sub[sub.config == "A1"].run_id, sub[sub.config == "A1"].on_time_250))
    xa, xb, ids = paired(da, db)
    if len(ids) >= 2:
        tp, eq = tost_paired(xa, xb, margin_frac=0.05)
        primary.append(("P5*", f"rtt_asym 2.6 Mbit/s on-time F2 {xa.mean()*100:.1f}% vs A1 {xb.mean()*100:.1f}% (n={len(ids)}), TOST 5 %: equivalent={eq} (A1 sweep incomplete)", tp))
if primary:
    for (name, desc, p_raw), ph in zip(primary, holm([x[2] for x in primary])):
        print(f"{name:4s} raw p={p_raw:.4f} Holm={ph:.4f} | {desc}")

print("\n=== 2. PAIRED STATISTICAL TESTS (Wilcoxon + Holm + Bootstrap CI + TOST) ===")
def run_pair(scen, cfg_a, cfg_b, metric="freeze_frac_250"):
    sub = df_main[df_main["scenario"] == scen]
    da = dict(zip(sub[sub["config"] == cfg_a]["run_id"], sub[sub["config"] == cfg_a][metric]))
    db = dict(zip(sub[sub["config"] == cfg_b]["run_id"], sub[sub["config"] == cfg_b][metric]))
    xa, xb, ids = paired(da, db)
    p = wilcoxon_p(xa, xb)
    mean_d, lo, hi = bootstrap_ci(xa, xb)
    tost_p, eq = tost_paired(xa, xb, margin_frac=0.05)
    return len(ids), xa.mean(), xb.mean(), mean_d, lo, hi, p, tost_p, eq

# P3, P4 (proposal section 2): F2 vs the better of MinRTT/ECF on the asymmetric
# scenarios. These two form the Holm family; the rest are exploratory (CIs).
comparisons = [
    ("rtt_asym", "F2", "S1", "freeze_frac_250"),
    ("bw_asym",  "F2", "S4", "freeze_frac_250"),
]
exploratory = [
    ("symmetric", "F2", "S1"), ("symmetric", "F2", "S2"), ("rtt_asym", "F2", "S2"),
    ("bw_asym", "F2", "S2"), ("rtt_asym", "F2", "R2"), ("rtt_asym", "S2", "S1"),
    ("outage", "F2", "S1"), ("outage", "S4", "S1"),
]

rows = []
for scen, a, b, m in comparisons:
    n, ma, mb, md, lo, hi, p, tp, eq = run_pair(scen, a, b, m)
    rows.append((scen, f"{a} vs {b}", n, ma, mb, md, lo, hi, p, tp, eq))

adj_p = holm([r[8] for r in rows])
for r, hp in zip(rows, adj_p):
    scen, pair, n, ma, mb, md, lo, hi, p, tp, eq = r
    print(f"{scen:10s} | {pair:9s} (n={n}) | {ma:.3f} vs {mb:.3f} | diff={md:+.3f} [{lo:+.3f}, {hi:+.3f}] | p={p:.4f} (Holm={hp:.4f}) | TOST_eq={eq} (p={tp:.3f})")
print("exploratory (95% bootstrap CI; no multiple-comparison claim):")
for scen, a, b in exploratory:
    n, ma, mb, md, lo, hi, p, tp, eq = run_pair(scen, a, b)
    print(f"{scen:10s} | {a} vs {b} (n={n}) | {ma:.3f} vs {mb:.3f} | diff={md:+.3f} [{lo:+.3f}, {hi:+.3f}] | raw p={p:.4f}")

# --- PLOTS ---
plt.rcParams.update({"font.size": 11, "figure.dpi": 200})

# Plot 1: Cross-Scenario On-Time Display Ratio @ D=250ms (2600 kbps)
scenarios = ["symmetric", "bw_asym", "rtt_asym", "outage", "control"]
cfgs = ["S0", "S1", "S4", "S2", "F2", "R2", "R1"]  # F1, A1 omitted: every run stalled
piv = df_main.pivot_table(index="scenario", columns="config", values="on_time_250").reindex(index=scenarios, columns=cfgs)
ax = piv.plot(kind="bar", figsize=(11, 5.4), width=0.82, edgecolor="black", linewidth=0.5)
ax.set_ylabel("On-Time Frame Ratio (D = 250 ms)")
ax.set_title("Cross-Scenario Frame Delivery @ 2.6 Mbps (Higher is Better)")
ax.set_ylim(0, 1.08)
ax.grid(axis="y", linestyle="--", alpha=0.4)
plt.xticks(rotation=0)
plt.legend(title="Scheduler", ncol=7, loc="upper center", bbox_to_anchor=(0.5, -0.12), framealpha=0.9)
plt.tight_layout()
plt.savefig("analysis/plots/fig1_cross_scenario_ontime.png")
plt.savefig("docs/slides/deck/project/slides/assets/fig1_cross_scenario_ontime.png")
plt.close()

# Plot 2: rtt_asym Bitrate Sweep (Freeze Fraction & On-Time)
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.5))
sweep_cfgs = [("S1", "MinRTT (S1, n=1)", "o--"), ("S4", "ECF (S4, n=1)", "s--"), ("S2", "Round-robin (S2, n=1)", "^:"), ("F2", "Frame split+rescue (F2, n=8)", "D-"), ("R2", "Oracle split (R2, n=8)", "v-."), ("R1", "Ideal link (R1, n=1)", "k-")]
for cfg, label, style in sweep_cfgs:
    sub = sweep_agg[sweep_agg["config"] == cfg].sort_values("bitrate_kbps")
    if len(sub) > 1:
        ax1.plot(sub["bitrate_kbps"]/1000.0, sub["on_time_250"]*100, style, label=label, linewidth=2, markersize=6)
        ax2.plot(sub["bitrate_kbps"]/1000.0, sub["freeze_frac_250"]*100, style, label=label, linewidth=2, markersize=6)

ax1.set_title("rtt_asym Sweep: On-Time Frames (D=250ms)")
ax1.set_xlabel("Video Bitrate (Mbps)")
ax1.set_ylabel("On-Time Frames (%)")
ax1.grid(True, linestyle="--", alpha=0.4)
ax1.legend(fontsize=9)

ax2.axhline(1.0, color="red", linestyle=":", linewidth=1.2, label="1% Freeze Threshold")
ax2.set_title("rtt_asym Sweep: Freeze Time Fraction (D=250ms)")
ax2.set_xlabel("Video Bitrate (Mbps)")
ax2.set_ylabel("Freeze Time (%)")
ax2.grid(True, linestyle="--", alpha=0.4)
ax2.legend(fontsize=9)

plt.tight_layout()
plt.savefig("analysis/plots/fig2_rtt_asym_sweep.png")
plt.savefig("docs/slides/deck/project/slides/assets/fig2_rtt_asym_sweep.png")
plt.close()

# Plot 3: Path Split (share_a) & Overhead (extra_bytes_frac) across scenarios @ 2600 kbps
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.5))
piv_share = df_main.pivot_table(index="scenario", columns="config", values="share_a").reindex(index=scenarios, columns=["S1", "S4", "S2", "F2", "R2"])
piv_share.plot(kind="bar", ax=ax1, width=0.8, edgecolor="black", linewidth=0.5)
ax1.axhline(0.5, color="gray", linestyle="--", linewidth=1)
ax1.set_title("Traffic Share on Path A (Fast/Primary Path)")
ax1.set_ylabel("Bytes Fraction on Path A")
ax1.set_ylim(0, 1.05)
ax1.tick_params(axis="x", rotation=0)
ax1.grid(axis="y", linestyle="--", alpha=0.4)

piv_ovh = df_main.pivot_table(index="scenario", columns="config", values="extra_bytes_frac").reindex(index=scenarios, columns=["S1", "S4", "S3", "F2", "R2"])
piv_ovh.plot(kind="bar", ax=ax2, width=0.8, edgecolor="black", linewidth=0.5)
ax2.set_title("Extra / Redundant Bytes Overhead")
ax2.set_ylabel("Extra Bytes Fraction")
ax2.tick_params(axis="x", rotation=0)
ax2.grid(axis="y", linestyle="--", alpha=0.4)

plt.tight_layout()
plt.savefig("analysis/plots/fig3_path_share_overhead.png")
plt.savefig("docs/slides/deck/project/slides/assets/fig3_path_share_overhead.png")
plt.close()

print("\nSaved all 3 plots to analysis/plots/ and docs/slides/deck/project/slides/assets/")
