
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# Styling for publication / Beamer slides
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 11,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.labelsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 9.5,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
})

CSV_PATH = "analysis/summary_rtt_asym.csv"
OUT_DIR = "docs/figures"
os.makedirs(OUT_DIR, exist_ok=True)

df = pd.read_csv(CSV_PATH)
order = ["S0", "S1", "S2", "S3", "S4"]
labels = {
    "S0": "S0\n(Single-Path)",
    "S1": "S1\n(Min-RTT)",
    "S2": "S2\n(Round-Robin)",
    "S3": "S3\n(Redundant)",
    "S4": "S4\n(BDP-Aware)",
}
present_order = [c for c in order if c in df["config"].unique()]
x_labels = [labels.get(c, c) for c in present_order]

palette_deadlines = ["#4C72B0", "#55A868", "#C44E52"]
palette_configs = ["#7F7F7F", "#DD8452", "#2CA02C", "#9467BD", "#1F77B4"]


def plot_ontime(ax):
    metrics = ["on_time_150", "on_time_250", "on_time_400"]
    metric_names = ["D = 150 ms", "D = 250 ms", "D = 400 ms"]
    x = np.arange(len(present_order))
    width = 0.25

    for idx, (col, name) in enumerate(zip(metrics, metric_names)):
        means = [df[df["config"] == c][col].mean() * 100 for c in present_order]
        stds = [df[df["config"] == c][col].std() * 100 for c in present_order]
        bars = ax.bar(
            x + (idx - 1) * width,
            means,
            width,
            yerr=stds,
            capsize=3,
            label=name,
            color=palette_deadlines[idx],
            edgecolor="black",
            linewidth=0.6,
            error_kw={"elinewidth": 1.0},
        )
        for bar, val in zip(bars, means):
            if val > 5:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    val + 2.5,
                    f"{val:.1f}%",
                    ha="center",
                    va="bottom",
                    fontsize=7.5,
                    rotation=90,
                )

    ax.set_xticks(x)
    ax.set_xticklabels(x_labels)
    ax.set_ylim(0, 115)
    ax.set_ylabel("On-Time Frames (%)")
    ax.set_title("On-Time Frame Delivery by Playout Deadline (H1)")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.legend(frameon=True, loc="upper left")


def plot_freeze(ax):
    metrics = ["freeze_frac_150", "freeze_frac_250", "freeze_frac_400"]
    metric_names = ["D = 150 ms", "D = 250 ms", "D = 400 ms"]
    x = np.arange(len(present_order))
    width = 0.25

    for idx, (col, name) in enumerate(zip(metrics, metric_names)):
        means = [df[df["config"] == c][col].mean() * 100 for c in present_order]
        stds = [df[df["config"] == c][col].std() * 100 for c in present_order]
        bars = ax.bar(
            x + (idx - 1) * width,
            means,
            width,
            yerr=stds,
            capsize=3,
            label=name,
            color=palette_deadlines[idx],
            edgecolor="black",
            linewidth=0.6,
            error_kw={"elinewidth": 1.0},
        )
        for bar, val in zip(bars, means):
            if val > 2:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    val + 2.5,
                    f"{val:.1f}%",
                    ha="center",
                    va="bottom",
                    fontsize=7.5,
                    rotation=90,
                )

    ax.set_xticks(x)
    ax.set_xticklabels(x_labels)
    ax.set_ylim(0, 118)
    ax.set_ylabel("Playback Freeze Time (%)")
    ax.set_title("Playback Freeze Fraction Across Deadlines (H2)")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.legend(frameon=True, loc="upper right")


def plot_required_delay(ax):
    x = np.arange(len(present_order))
    means = [df[df["config"] == c]["required_delay_ms"].mean() for c in present_order]
    stds = [df[df["config"] == c]["required_delay_ms"].std() for c in present_order]

    bars = ax.bar(
        x,
        means,
        width=0.52,
        yerr=stds,
        capsize=4,
        color=palette_configs[: len(present_order)],
        edgecolor="black",
        linewidth=0.7,
    )
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels)
    ax.set_ylim(100, 150000)
    ax.set_ylabel("Required Playout Delay (ms, log scale)")
    ax.set_title("Zero-Freeze Required Playout Buffer")
    ax.grid(axis="y", which="both", linestyle="--", alpha=0.35)

    for bar, val in zip(bars, means):
        label_str = f"{val/1000:.1f} s" if val >= 1000 else f"{val:.0f} ms"
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            val * 1.22,
            label_str,
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
        )


def plot_path_share(ax):
    x = np.arange(len(present_order))
    share_a = np.array([df[df["config"] == c]["share_a"].mean() * 100 for c in present_order])
    share_b = 100.0 - share_a

    b1 = ax.bar(x, share_a, width=0.52, label="Path A (Fast: 30 ms, 1.6 Mbps)", color="#2B5C8F", edgecolor="black", linewidth=0.7)
    b2 = ax.bar(x, share_b, width=0.52, bottom=share_a, label="Path B (Slow: 150 ms, 1.6 Mbps)", color="#E07A5F", edgecolor="black", linewidth=0.7)

    ax.axhline(50, color="white", linestyle="--", linewidth=1.2, alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels)
    ax.set_ylim(0, 128)
    ax.set_ylabel("Traffic Share (%)")
    ax.set_title("Multipath Traffic Distribution (Path A vs. Path B)")
    ax.legend(frameon=True, loc="upper right")

    for i, (sa, sb) in enumerate(zip(share_a, share_b)):
        ax.text(i, sa / 2, f"{sa:.1f}%", ha="center", va="center", color="white", fontweight="bold", fontsize=9)
        if sb > 8:
            ax.text(i, sa + sb / 2, f"{sb:.1f}%", ha="center", va="center", color="white", fontweight="bold", fontsize=9)


# Save individual figures
for fname, fn in [
    ("fig1_ontime_deadlines.png", plot_ontime),
    ("fig2_freeze_fraction.png", plot_freeze),
    ("fig3_required_delay.png", plot_required_delay),
    ("fig4_path_share.png", plot_path_share),
]:
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    fn(ax)
    fig.savefig(os.path.join(OUT_DIR, fname))
    plt.close(fig)

# Save combined 2x2 figure
fig, axes = plt.subplots(2, 2, figsize=(13.5, 9.2))
plot_ontime(axes[0, 0])
plot_freeze(axes[0, 1])
plot_required_delay(axes[1, 0])
plot_path_share(axes[1, 1])
fig.suptitle("MPQUIC Scheduler Evaluation under Asymmetric Paths (rtt_asym, 2600 kbps)", fontsize=14, fontweight="bold", y=0.995)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "fig_summary_2x2.png"))
plt.close(fig)

print("Generated plots in", OUT_DIR, ":", sorted(os.listdir(OUT_DIR)))
