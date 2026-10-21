"""Trade-off curve: share of failed-run spend saved against share of successful runs lost.

Colours, marks and chrome follow the dataviz reference palette (validated for light and dark):
2px lines, >=8px markers with a 2px surface ring, hairline solid gridlines, text in ink tokens.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

THEMES: dict[str, dict[str, str]] = {
    "light": {
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "ink2": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "series1": "#2a78d6",
        "series2": "#eb6834",
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#ffffff",
        "ink2": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "series1": "#3987e5",
        "series2": "#d95926",
    },
}
DPI = 150
PX = 72 / DPI  # points per pixel


def _xy(curve: list[dict[str, float]]) -> tuple[list[float], list[float]]:
    points = sorted(curve, key=lambda p: (p["successes_lost"], p["failed_spend_saved"]))
    return [p["successes_lost"] for p in points], [p["failed_spend_saved"] for p in points]


def plot_dataset(doc: dict[str, Any], out: Path, theme: str) -> None:
    c = THEMES[theme]
    primary = doc["protocols"]["batch-normalized"]
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=DPI)
    fig.patch.set_facecolor(c["surface"])
    ax.set_facecolor(c["surface"])

    points = []
    for target, summary in primary["targets"].items():
        test = summary.get("test")
        if test is not None:
            points.append((target, test["successes_lost_share"], test["failed_spend_saved_share"]))
    x_max = max([0.25, *(1.6 * x for _, x, _ in points)])
    x_max = min(1.0, x_max)

    for alpha in (0.05, 0.10):
        ax.axvline(alpha, color=c["grid"], linewidth=1 * PX * 2, zorder=1)
        ax.text(
            alpha,
            1.005,
            f"α = {alpha:.2f}",
            color=c["muted"],
            fontsize=8,
            ha="center",
            va="bottom",
            transform=ax.get_xaxis_transform(),
        )

    series: list[tuple[str, str, str, Any]] = [  # (protocol, label, colour, line style)
        ("batch-normalized", "Batch-normalised arrival order (primary)", c["series1"], "-")
    ]
    if "absolute" in doc["protocols"]:
        series.append(
            ("absolute", "Recorded start times (model-batch shift)", c["series2"], (0, (4, 3)))
        )
    for key, label, colour, style in series:
        curve = doc["protocols"][key]["curve"]
        if curve:
            xs, ys = _xy(curve)
            ax.step(
                xs,
                ys,
                where="post",
                color=colour,
                linewidth=2 * PX * 2,
                linestyle=style,
                label=label,
                solid_joinstyle="round",
                solid_capstyle="round",
                zorder=3,
            )

    # Short direct labels above the curve (it only rises to the right): up and to the left of
    # the point, or to the right near the y axis. The full numbers are in the report tables.
    for i, (target, x, y) in enumerate(sorted(points, key=lambda p: p[1])):
        left = x > 0.25 * x_max
        ax.plot(
            [x],
            [y],
            marker="o",
            markersize=6,
            color=c["series1"],
            markeredgecolor=c["surface"],
            markeredgewidth=2 * PX * 2,
            zorder=4,
        )
        ax.annotate(
            f"target recall {target}",
            xy=(x, y),
            xytext=(-14 if left else 14, 34 + 18 * i),
            textcoords="offset points",
            ha="right" if left else "left",
            color=c["ink2"],
            fontsize=8,
            arrowprops={
                "arrowstyle": "-",
                "color": c["muted"],
                "linewidth": 1 * PX * 2,
                "shrinkA": 0,
                "shrinkB": 4,
            },
        )
    if not points:
        status = primary["targets"]["0.90"]
        ax.text(
            0.5,
            0.5,
            f"No calibrated policy: {status['status'].replace('_', ' ')}",
            color=c["ink2"],
            fontsize=10,
            ha="center",
            va="center",
            transform=ax.transAxes,
        )

    ax.set_xlim(0, x_max)
    ax.set_ylim(0, 1.0)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Successful runs lost (share of test successes)", color=c["ink2"], fontsize=9)
    ax.set_ylabel("Failed-run spend saved (share)", color=c["ink2"], fontsize=9)
    ax.grid(True, color=c["grid"], linewidth=1 * PX * 2, linestyle="-", zorder=0)
    ax.tick_params(colors=c["muted"], labelsize=8, length=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(c["axis"])
        ax.spines[side].set_linewidth(1 * PX * 2)
    fig.suptitle(
        doc["title"],
        x=0.02,
        ha="left",
        color=c["ink"],
        fontsize=11,
        fontweight="bold",
    )
    ax.set_title(
        f"Held-out test split. {doc['runs']:,} runs ({doc['successes']:,} successes, "
        f"{doc['failures']:,} failures); cost in {doc['cost_unit']}",
        loc="left",
        color=c["ink2"],
        fontsize=8,
        pad=14,
    )
    if ax.get_legend_handles_labels()[0]:
        legend = ax.legend(loc="upper left", frameon=False, fontsize=8)
        for text in legend.get_texts():
            text.set_color(c["ink2"])
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=c["surface"])
    plt.close(fig)
