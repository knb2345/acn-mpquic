"""Tables, statistics and figures for the final report and slides.

  PYTHONPATH=analysis python analysis/generate_report_artifacts.py

Input: analysis/summary_final.csv, built by analysis/make_summary.py from
results/<scenario>/ (one row per run directory, run ids 1-8).

Rules, fixed before the final batch was analysed:
- Stalled runs (the run ended with frames still missing, run.sh exit 3) are
  counted and reported, never averaged in: they are left out of every mean,
  test and figure. A cell with 4 or more of its 8 runs stalled is reported as
  "stalled" instead of a mean.
- One Holm family: the pre-registered primary comparisons that could be run
  (P1-P5; P6 needs the keyframe encoding, which was not run). Everything else
  is exploratory: 95 % bootstrap CIs, no multiple-comparison claim.
- R1 is not shown for the outage scenario: in R1 mode the single combined link
  follows the outage schedule and goes fully dark, so it is not an upper bound
  there.
Writes figures to analysis/plots/ and docs/slides/deck/project/slides/assets/,
and the tables used in the report to analysis/tables/.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats as st

from stats import paired, wilcoxon_p, holm, tost_paired, bootstrap_ci

OUT_PLOTS = [Path("analysis/plots"), Path("docs/slides/deck/project/slides/assets")]
OUT_TABLES = Path("analysis/tables")
SCENARIOS = ["symmetric", "bw_asym", "rtt_asym", "outage", "control"]
NAMES = {"S0": "Single path (S0)", "S1": "MinRTT (S1)", "S2": "Round-robin (S2)",
         "S3": "Redundant (S3)", "S4": "ECF (S4)", "F1": "Whole frame (F1)",
         "F2": "Frame split (F2)", "A1": "F2 over 2 conns (A1)",
         "R1": "Ideal link (R1)", "R2": "F2, true rates (R2)"}
COLORS = {"S0": "#9AA3AF", "S1": "#C8691F", "S2": "#5A8F3C", "S3": "#B9A04B", "S4": "#8A5BB0",
          "F1": "#D98BA6", "F2": "#2F6FB0", "A1": "#3BA3A3", "R1": "#14213D", "R2": "#7FA7D6"}

df_all = pd.read_csv("analysis/summary_final.csv")
df_all = df_all[df_all["run_id"].between(1, 8)]
df_all["stalled"] = df_all["stalled"].astype(str).str.lower().eq("true")
df_all = df_all[~((df_all["config"] == "R1") & (df_all["scenario"] == "outage"))]
df = df_all[~df_all["stalled"]]                      # completed runs only
main_all = df_all[df_all["bitrate_kbps"] == 2600]
df_main = df[df["bitrate_kbps"] == 2600]
df_sweep = df[df["scenario"] == "rtt_asym"]
sweep_all = df_all[df_all["scenario"] == "rtt_asym"]
OUT_TABLES.mkdir(parents=True, exist_ok=True)
for d in OUT_PLOTS:
    d.mkdir(parents=True, exist_ok=True)


def cell(scen, cfg, metric, data=df_main, base=main_all):
    """(mean over completed runs or None if >= 4 stalled, n completed, n stalled)."""
    b = base[(base.scenario == scen) & (base.config == cfg)]
    g = data[(data.scenario == scen) & (data.config == cfg)]
    n_st = int(b["stalled"].sum())
    if len(b) == 0:
        return None, 0, 0
    mean = None if n_st >= 4 or len(g) == 0 else float(g[metric].mean())
    return mean, len(g), n_st


# ---------------------------------------------------------------- tables
cfgs_main = ["S0", "S1", "S4", "S2", "S3", "F1", "F2", "A1", "R2", "R1"]
rows = []
for scen in SCENARIOS:
    for cfg in cfgs_main:
        on, n, ns = cell(scen, cfg, "on_time_250")
        fr, _, _ = cell(scen, cfg, "freeze_frac_250")
        rd, _, _ = cell(scen, cfg, "required_delay_ms")
        sh, _, _ = cell(scen, cfg, "share_a")
        ex, _, _ = cell(scen, cfg, "extra_bytes_frac")
        g = df_main[(df_main.scenario == scen) & (df_main.config == cfg)]
        med_rd = float(g["required_delay_ms"].median()) if len(g) and on is not None else None
        if n or ns:
            rows.append(dict(scenario=scen, config=cfg, n_completed=n, n_stalled=ns,
                             on_time_250=on, freeze_frac_250=fr, median_required_delay_ms=med_rd,
                             share_a=sh, extra_bytes_frac=ex))
main_tab = pd.DataFrame(rows)
main_tab.to_csv(OUT_TABLES / "main_2600.csv", index=False)
print("=== MAIN MATRIX, 2.6 Mbit/s, D = 250 ms (completed runs; stalled counted) ===")
print("on time %:")
print((main_tab.pivot(index="scenario", columns="config", values="on_time_250") * 100)
      .reindex(index=SCENARIOS, columns=cfgs_main).round(1).to_string())
print("freeze %:")
print((main_tab.pivot(index="scenario", columns="config", values="freeze_frac_250") * 100)
      .reindex(index=SCENARIOS, columns=cfgs_main).round(1).to_string())
print("stalled runs (of 8):")
print(main_tab.pivot(index="scenario", columns="config", values="n_stalled")
      .reindex(index=SCENARIOS, columns=cfgs_main).to_string())
print("median required delay (ms):")
print(main_tab.pivot(index="scenario", columns="config", values="median_required_delay_ms")
      .reindex(index=SCENARIOS, columns=cfgs_main).round(0).to_string())

# Sweep on rtt_asym: mean per bitrate and usable capacity (completed runs).
sweep_cfgs = ["S1", "S4", "S2", "F2", "A1", "R2", "F1", "R1"]
sw = []
for cfg in sweep_cfgs:
    for b in sorted(sweep_all[sweep_all.config == cfg].bitrate_kbps.unique()):
        on, n, ns = cell("rtt_asym", cfg, "on_time_250", df_sweep[df_sweep.bitrate_kbps == b],
                         sweep_all[sweep_all.bitrate_kbps == b])
        fr, _, _ = cell("rtt_asym", cfg, "freeze_frac_250", df_sweep[df_sweep.bitrate_kbps == b],
                        sweep_all[sweep_all.bitrate_kbps == b])
        sw.append(dict(config=cfg, bitrate_kbps=int(b), n_completed=n, n_stalled=ns,
                       on_time_250=on, freeze_frac_250=fr))
sweep_tab = pd.DataFrame(sw)
sweep_tab.to_csv(OUT_TABLES / "sweep_rtt_asym.csv", index=False)
print("\n=== rtt_asym SWEEP: freeze % (n completed / stalled) and usable capacity ===")
for cfg in sweep_cfgs:
    s = sweep_tab[sweep_tab.config == cfg].sort_values("bitrate_kbps")
    if s.empty:
        continue
    ok = s[(s.freeze_frac_250.notna()) & (s.freeze_frac_250 <= 0.01)]
    usable = int(ok.bitrate_kbps.max()) if len(ok) else 0
    pts = ", ".join(f"{r.bitrate_kbps}: " + ("stalled" if pd.isna(r.freeze_frac_250) else f"{r.freeze_frac_250*100:.1f}")
                    + f" ({r.n_completed}/{r.n_stalled})" for r in s.itertuples())
    print(f"{cfg}: usable {usable} kbps | {pts}")


# ---------------------------------------------------------------- primary comparisons
def usable_by_run(cfg):
    """Usable capacity per run id: highest swept bitrate at which that run id
    completed and froze for <= 1 % of the time at D = 250 ms (0 if none)."""
    sub = sweep_all[sweep_all.config == cfg]
    out = {}
    for rid, g in sub.groupby("run_id"):
        ok = g[(~g.stalled) & (g.freeze_frac_250 <= 0.01)]["bitrate_kbps"]
        out[rid] = float(ok.max()) if len(ok) else 0.0
    return out


def full_sweep(cfg):
    sub = sweep_all[sweep_all.config == cfg]
    return sub.bitrate_kbps.nunique() >= 5 and sub.groupby("bitrate_kbps").size().min() >= 8


primary = []  # (name, description, raw p)
r1 = usable_by_run("R1") if full_sweep("R1") else {}
for name, cfg in [("P1", "S1"), ("P2", "S4")]:
    if r1 and full_sweep(cfg):
        u = usable_by_run(cfg)
        ids = sorted(set(u) & set(r1))
        ratio = np.array([u[i] / r1[i] for i in ids if r1[i] > 0])
        # H2: ratio < 0.8; one-sided Wilcoxon signed-rank on 0.8 - ratio.
        p = float(st.wilcoxon(0.8 - ratio, alternative="greater").pvalue) if np.any(ratio != 0.8) else 1.0
        primary.append((name, f"H2 rtt_asym usable capacity {cfg}/R1 per run: median {np.median(ratio):.2f}, "
                              f"range {ratio.min():.2f}-{ratio.max():.2f} (n={len(ratio)}); bar < 0.80", p))
for name, scen in [("P3", "rtt_asym"), ("P4", "bw_asym")]:
    best = min(["S1", "S4"], key=lambda c: df_main[(df_main.scenario == scen) & (df_main.config == c)]["freeze_frac_250"].mean())
    sub = df_main[df_main.scenario == scen]
    da = dict(zip(sub[sub.config == "F2"].run_id, sub[sub.config == "F2"].freeze_frac_250))
    db = dict(zip(sub[sub.config == best].run_id, sub[sub.config == best].freeze_frac_250))
    xa, xb, ids = paired(da, db)
    if len(ids) >= 2:
        md, lo, hi = bootstrap_ci(xa, xb)
        primary.append((name, f"H3 {scen} freeze F2 {xa.mean()*100:.1f}% vs {best} {xb.mean()*100:.1f}% (n={len(ids)}), "
                              f"diff {md*100:+.1f} pp [{lo*100:+.1f}, {hi*100:+.1f}], F2 lower in {(xa < xb).sum()}/{len(ids)}",
                        wilcoxon_p(xa, xb)))
# P5 (H4): F2 and A1 equivalent within 5 % of usable capacity (paired TOST).
if full_sweep("A1") and full_sweep("F2"):
    xa, xb, ids = paired(usable_by_run("F2"), usable_by_run("A1"))
    tp, eq = tost_paired(xa, xb, margin_frac=0.05)
    primary.append(("P5", f"H4 rtt_asym usable capacity F2 {xa.mean():.0f} vs A1 {xb.mean():.0f} kbps (n={len(ids)}); "
                          f"TOST margin +-{0.05*xb.mean():.0f} kbps: equivalent={eq}", tp))
else:
    # Deviation, used only if the A1 sweep is incomplete: the same TOST on the
    # on-time ratio at 2.6 Mbit/s on rtt_asym (margin 5 % of A1's mean).
    sub = df_main[df_main.scenario == "rtt_asym"]
    xa, xb, ids = paired(dict(zip(sub[sub.config == "F2"].run_id, sub[sub.config == "F2"].on_time_250)),
                         dict(zip(sub[sub.config == "A1"].run_id, sub[sub.config == "A1"].on_time_250)))
    if len(ids) >= 2:
        tp, eq = tost_paired(xa, xb, margin_frac=0.05)
        primary.append(("P5*", f"H4 (deviation: A1 sweep incomplete) rtt_asym on-time at 2.6 Mbit/s F2 {xa.mean()*100:.1f}% "
                               f"vs A1 {xb.mean()*100:.1f}% (n={len(ids)}); TOST margin +-{5*xb.mean():.1f} pp: equivalent={eq}", tp))
print("\n=== PRE-REGISTERED PRIMARY COMPARISONS (one Holm family) ===")
prim_rows = []
for (name, desc, p_raw), ph in zip(primary, holm([x[2] for x in primary]) if primary else []):
    print(f"{name:4s} raw p={p_raw:.4f} Holm p={ph:.4f} | {desc}")
    prim_rows.append(dict(name=name, description=desc, p_raw=p_raw, p_holm=ph))
pd.DataFrame(prim_rows).to_csv(OUT_TABLES / "primary.csv", index=False)

# H3, symmetric part as pre-registered: F2 equivalent (5 %) to the better of S1/S4.
sub = df_main[df_main.scenario == "symmetric"]
best = min(["S1", "S4"], key=lambda c: sub[sub.config == c]["freeze_frac_250"].mean())
xa, xb, ids = paired(dict(zip(sub[sub.config == "F2"].run_id, sub[sub.config == "F2"].freeze_frac_250)),
                     dict(zip(sub[sub.config == best].run_id, sub[sub.config == best].freeze_frac_250)))
if len(ids) >= 2:
    tp, eq = tost_paired(xa, xb, margin_frac=0.05)
    md, lo, hi = bootstrap_ci(xa, xb)
    print(f"H3 symmetric (pre-registered equivalence, F2 vs {best}): F2 {xa.mean()*100:.1f}% vs {xb.mean()*100:.1f}% freeze, "
          f"diff {md*100:+.1f} pp [{lo*100:+.1f}, {hi*100:+.1f}]; TOST margin +-{5*xb.mean():.2f} pp: equivalent={eq} (p={tp:.3f})")

# ---------------------------------------------------------------- exploratory
print("\n=== EXPLORATORY (95 % bootstrap CI of the paired mean difference; freeze % at 2.6 Mbit/s) ===")
expl = [("symmetric", "F2", "S2"), ("bw_asym", "F2", "S2"), ("rtt_asym", "F2", "S2"),
        ("rtt_asym", "F2", "R2"), ("rtt_asym", "F2", "F1"), ("rtt_asym", "F2", "A1"),
        ("bw_asym", "F2", "R1"), ("rtt_asym", "S2", "S1"), ("outage", "F2", "S1"),
        ("outage", "F2", "S2"), ("outage", "S4", "S1"), ("control", "F2", "S1")]
ex_rows = []
for scen, a, b in expl:
    sub = df_main[df_main.scenario == scen]
    xa, xb, ids = paired(dict(zip(sub[sub.config == a].run_id, sub[sub.config == a].freeze_frac_250)),
                         dict(zip(sub[sub.config == b].run_id, sub[sub.config == b].freeze_frac_250)))
    if len(ids) < 2:
        print(f"{scen:10s} {a} vs {b}: fewer than 2 completed pairs")
        continue
    md, lo, hi = bootstrap_ci(xa, xb)
    print(f"{scen:10s} {a} vs {b} (n={len(ids)}): {xa.mean()*100:.1f}% vs {xb.mean()*100:.1f}% | "
          f"diff {md*100:+.1f} pp [{lo*100:+.1f}, {hi*100:+.1f}] | {a} lower in {(xa < xb).sum()}/{len(ids)}")
    ex_rows.append(dict(scenario=scen, a=a, b=b, n=len(ids), mean_a=xa.mean(), mean_b=xb.mean(),
                        diff=md, ci_lo=lo, ci_hi=hi))
pd.DataFrame(ex_rows).to_csv(OUT_TABLES / "exploratory.csv", index=False)


# ---------------------------------------------------------------- figures
def save(fig, name):
    for d in OUT_PLOTS:
        fig.savefig(d / name, dpi=200)
    plt.close(fig)


def ci95(values):
    v = np.asarray(values, float)
    if len(v) < 2:
        return 0.0, 0.0
    rng = np.random.default_rng(7)
    means = rng.choice(v, (5000, len(v)), replace=True).mean(axis=1)
    return v.mean() - np.percentile(means, 2.5), np.percentile(means, 97.5) - v.mean()


plt.rcParams.update({"font.size": 11})

# Figure 1: frames on time at 2.6 Mbit/s, per scenario and scheduler.
f1_cfgs = ["S0", "S1", "S4", "S2", "F1", "F2", "A1", "R2", "R1"]
fig, ax = plt.subplots(figsize=(12, 5.2))
w = 0.85 / len(f1_cfgs)
for j, cfg in enumerate(f1_cfgs):
    xs, ys, el, eh = [], [], [], []
    for i, scen in enumerate(SCENARIOS):
        g = df_main[(df_main.scenario == scen) & (df_main.config == cfg)]
        on, n, ns = cell(scen, cfg, "on_time_250")
        if on is None:
            continue
        lo, hi = ci95(g.on_time_250 * 100)
        xs.append(i - 0.425 + w * (j + 0.5)); ys.append(on * 100); el.append(lo); eh.append(hi)
    ax.bar(xs, ys, w, yerr=[el, eh], color=COLORS[cfg], label=NAMES[cfg], capsize=1.5,
           error_kw={"elinewidth": 0.8})
ax.set_xticks(range(len(SCENARIOS)), SCENARIOS)
ax.set_ylabel("frames on time at D = 250 ms (%)")
ax.set_ylim(0, 105)
ax.set_title("Frames on time at 2.6 Mbit/s (mean of completed runs, 95 % CI)")
ax.grid(axis="y", linestyle="--", alpha=0.4)
ax.legend(ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.08), fontsize=9, frameon=False)
fig.tight_layout()
save(fig, "fig1_cross_scenario_ontime.png")

# Figure 2: rtt_asym sweep, on-time and freeze per bitrate.
fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6))
for cfg in ["S1", "S4", "S2", "F2", "A1", "R2", "R1"]:
    s = sweep_tab[(sweep_tab.config == cfg)].sort_values("bitrate_kbps")
    s = s[s.on_time_250.notna()]
    if len(s) < 2:
        continue
    n = int(s.n_completed.min())
    lab = f"{NAMES[cfg]}, n={n}" if n == int(s.n_completed.max()) else f"{NAMES[cfg]}, n={n}-{int(s.n_completed.max())}"
    style = "-" if cfg in ("F2", "R1") else "--"
    a1.plot(s.bitrate_kbps / 1000, s.on_time_250 * 100, style, marker="o", ms=4, color=COLORS[cfg], label=lab, lw=1.8)
    a2.plot(s.bitrate_kbps / 1000, s.freeze_frac_250 * 100, style, marker="o", ms=4, color=COLORS[cfg], label=lab, lw=1.8)
a2.axhline(1.0, color="#9A4D12", ls=":", lw=1.2)
a2.text(0.52, 2.5, "1 % freeze (usable capacity bar)", color="#9A4D12", fontsize=9)
a1.set_title("rtt_asym: frames on time (D = 250 ms)")
a2.set_title("rtt_asym: time frozen (D = 250 ms)")
for ax, yl in ((a1, "frames on time (%)"), (a2, "time frozen (%)")):
    ax.set_xlabel("video bitrate (Mbit/s)")
    ax.set_ylabel(yl)
    ax.grid(True, linestyle="--", alpha=0.4)
a1.legend(fontsize=8, loc="lower left")
fig.tight_layout()
save(fig, "fig2_rtt_asym_sweep.png")

# Figure 3: share of frame bytes on path A, and bytes sent beyond the frame bytes.
fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6))
f3 = ["S1", "S4", "S2", "F2", "A1", "R2"]
w = 0.85 / len(f3)
for j, cfg in enumerate(f3):
    for ax, metric in ((a1, "share_a"), (a2, "extra_bytes_frac")):
        xs, ys = [], []
        for i, scen in enumerate(SCENARIOS):
            v, n, ns = cell(scen, cfg, metric)
            if v is not None:
                xs.append(i - 0.425 + w * (j + 0.5)); ys.append(v * 100)
        ax.bar(xs, ys, w, color=COLORS[cfg], label=NAMES[cfg])
a1.axhline(50, color="gray", ls="--", lw=1)
a1.set_title("Frame bytes on path A (%)")
a2.set_title("Bytes sent beyond the frame bytes (%)")
for ax in (a1, a2):
    ax.set_xticks(range(len(SCENARIOS)), SCENARIOS, fontsize=9)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
a2.legend(fontsize=8)
fig.tight_layout()
save(fig, "fig3_path_share_overhead.png")
print("\nfigures written to", ", ".join(str(d) for d in OUT_PLOTS), "; tables to", OUT_TABLES)
