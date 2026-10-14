"""Static figures for the memo, deck and README.

One series is one colour (blue).  Where a chart contrasts two groups, the
second group is orange; everything else is recessive grey.  No dual axes, thin
marks, hairline grid - the same rules as the dashboard.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import config as C  # noqa: E402

BLUE = "#2a78d6"
ORANGE = "#eb6834"
GREY = "#c3c2b7"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    "font.size": 10,
    "axes.edgecolor": GRID,
    "axes.labelcolor": INK2,
    "axes.titlecolor": INK,
    "axes.titlesize": 12,
    "axes.titleweight": "semibold",
    "axes.titlelocation": "left",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "legend.frameon": False,
})


def _save(fig, name: str) -> str:
    path = C.FIG_DIR / f"{name}.png"
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return str(path)


def _hbar(ax, labels, values, highlight=None, fmt="{:.0%}"):
    colors = [BLUE if (highlight is None or i in highlight) else GREY
              for i in range(len(values))]
    y = np.arange(len(values))[::-1]
    ax.barh(y, values, color=colors, height=0.62, edgecolor=SURFACE, linewidth=2)
    ax.set_yticks(y, labels)
    ax.tick_params(axis="y", colors=INK2, length=0)
    ax.grid(axis="y", visible=False)
    xmax = max(values) if len(values) else 1
    for yi, v in zip(y, values):
        ax.text(v + xmax * 0.01, yi, fmt.format(v), va="center", color=INK2, fontsize=9)
    ax.set_xlim(0, xmax * 1.15)


def flows(fl: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(7.5, 2.6))
    _hbar(ax, fl["item_category"], fl["share"].to_numpy(), highlight={0})
    ax.set_title("Which matching flow PO lines go through")
    ax.set_xlabel("Share of PO lines")
    return _save(fig, "flows")


def compliance(checks: list[dict]) -> str:
    d = pd.DataFrame(checks).sort_values("n_cases", ascending=False)
    fig, ax = plt.subplots(figsize=(7.5, 2.6))
    _hbar(ax, d["description"], d["n_cases"].to_numpy(), highlight={0},
          fmt="{:,.0f}")
    ax.set_title("Policy breaches, PO lines affected")
    ax.set_xlabel("PO lines")
    return _save(fig, "compliance")


def days_to_clear(inv: pd.DataFrame) -> str:
    d = inv.loc[inv["pairing_ok"] & inv["days_to_clear"].notna(), "days_to_clear"]
    d = d.clip(upper=200)
    fig, ax = plt.subplots(figsize=(7.5, 3.0))
    bins = np.arange(0, 205, 5)
    ax.hist(d, bins=bins, color=BLUE, edgecolor=SURFACE, linewidth=1)
    ax.axvline(C.POLICY.late_days, color=INK2, linewidth=1)
    ax.text(C.POLICY.late_days + 2, ax.get_ylim()[1] * 0.92,
            f"{C.POLICY.late_days}-day EU default", color=INK2, fontsize=9)
    ax.set_title("Days from invoice recorded to invoice paid (first invoice per PO line)")
    ax.set_xlabel("Days (200+ grouped at 200)")
    ax.set_ylabel("PO lines")
    ax.grid(axis="x", visible=False)
    return _save(fig, "days_to_clear")


def bottlenecks(tr: pd.DataFrame, n: int = 8) -> str:
    d = tr.head(n)
    labels = [f"{a}  →  {b}" for a, b in zip(d["from_activity"], d["to_activity"])]
    fig, ax = plt.subplots(figsize=(8.5, 3.4))
    _hbar(ax, labels, d["share_of_all_waiting"].to_numpy(), highlight={0, 1})
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_title("Where the waiting time goes: hand-offs ranked by total days")
    ax.set_xlabel("Share of all waiting time in the process")
    return _save(fig, "bottlenecks")


def vendor_pareto(vp: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(6.5, 3.2))
    ax.plot(vp["share_of_vendors"], vp["cumulative_share_of_late"], color=BLUE,
            linewidth=2)
    ax.plot([0, 1], [0, 1], color=GREY, linewidth=1)
    k = vp.index[vp["cumulative_share_of_late"] >= 0.8][0]
    x, y = vp.loc[k, "share_of_vendors"], vp.loc[k, "cumulative_share_of_late"]
    ax.scatter([x], [y], s=36, color=BLUE, edgecolor=SURFACE, linewidth=2, zorder=3)
    ax.annotate(f"{x:.0%} of vendors → 80% of slow-paid lines",
                (x, y), xytext=(12, -18), textcoords="offset points",
                color=INK2, fontsize=9)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_title("Slow payment is concentrated in a few vendors")
    ax.set_xlabel("Vendors, most slow-paid lines first")
    ax.set_ylabel("Cumulative share of slow-paid lines")
    return _save(fig, "vendor_pareto")


def two_group(title: str, labels: tuple[str, str], values: tuple[float, float],
              name: str, ylabel: str = "Paid after 60 days") -> str:
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    ax.bar([0, 1], values, color=[GREY, BLUE], width=0.55, edgecolor=SURFACE,
           linewidth=2)
    ax.set_xticks([0, 1], labels)
    ax.tick_params(axis="x", colors=INK2, length=0)
    for i, v in enumerate(values):
        ax.text(i, v + 0.01, f"{v:.0%}", ha="center", color=INK2, fontsize=10)
    ax.set_ylim(0, max(values) * 1.25)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_ylabel(ylabel)
    ax.grid(axis="x", visible=False)
    ax.set_title(title)
    return _save(fig, name)


def gains(curves: dict[str, pd.DataFrame], review_share: float) -> str:
    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    style = {"gradient_boosting": (BLUE, 2.0, "Gradient boosting"),
             "logistic": (ORANGE, 2.0, "Logistic regression"),
             "vendor_history_rule": (INK2, 1.2, "Rule: vendor's past record"),
             "random": (GREY, 1.0, "Random order")}
    for k, (c, lw, lab) in style.items():
        g = curves[k]
        ax.plot(g["share_reviewed"], g["share_captured"], color=c, linewidth=lw, label=lab)
    ax.axvline(review_share, color=MUTED, linewidth=0.8)
    ax.text(review_share + 0.01, 0.04, f"review {review_share:.0%}", color=MUTED, fontsize=8)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("Share of new invoices reviewed (riskiest first)")
    ax.set_ylabel("Share of slow-paid lines caught")
    ax.set_title("Out-of-time test: how many slow payments a review queue catches")
    ax.legend(loc="lower right", fontsize=9)
    return _save(fig, "gains")


def workload(win: pd.DataFrame, preds: pd.DataFrame, method: str) -> str:
    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.plot(win["week"], win["manual_events"], color=BLUE, linewidth=2,
            label="Actual manual steps")
    p = preds[preds["method"] == method].sort_values("week")
    ax.plot(p["week"], p["forecast"], color=ORANGE, linewidth=2,
            label="Backtest forecast (4 weeks ahead)")
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
    ax.set_ylabel("Manual steps per week")
    ax.set_title("Weekly manual workload and the backtested forecast")
    ax.legend(loc="lower left", fontsize=9)
    return _save(fig, "workload")
