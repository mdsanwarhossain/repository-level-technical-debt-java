#!/usr/bin/env python3
"""Generate the manuscript figures from the analysis outputs.

Design constraints, in order of priority:

  * **Print-safe.** Journal figures are frequently reproduced in grayscale, so
    identity is carried by line style and marker as well as hue. No chart here
    relies on colour alone.
  * **One axis per chart.** Never two y-scales.
  * **Direct labels over legends** where the series count allows it, which also
    supplies the contrast relief the aqua slot requires.

Palette: slots 1-3 of the validated categorical theme (blue / orange / aqua),
which clear the all-pairs CVD and normal-vision floors in light mode.

Usage:
    python -m pipeline.make_figures
"""
from __future__ import annotations
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import NullFormatter
import pandas as pd
import numpy as np

from .config import FINAL, ROOT

FIGS = ROOT / "figures"
FIGS.mkdir(exist_ok=True)

# Validated categorical slots 1-3 (light mode, all-pairs).
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#8c8b86"
GRID = "#e3e2dd"

ARMS = {
    "ai":       ("AI-assisted",    BLUE,   "-",   "o"),
    "baseline": ("Matched non-AI", ORANGE, "--",  "s"),
    "so":       ("Stack Overflow", AQUA,   ":",   "^"),
}

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 8,
    "axes.edgecolor": MUTED,
    "axes.labelcolor": INK,
    "axes.titlesize": 9,
    "axes.titleweight": "bold",
    "xtick.color": INK2, "ytick.color": INK2,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 400, "savefig.dpi": 400,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
})


def fig_km():
    """Kaplan-Meier survival of introduced line ranges, by provenance arm."""
    f = FINAL / "rq3_km_curves.csv"
    if not f.exists():
        print("  skip KM: no rq3_km_curves.csv")
        return
    d = pd.read_csv(f)
    fig, ax = plt.subplots(figsize=(3.4, 2.5))
    ax.grid(axis="y", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)

    for key, (label, colour, style, _) in ARMS.items():
        if key not in d.columns:
            continue
        s = d[["timeline", key]].dropna()
        s = s[s["timeline"] <= 365]
        if s.empty:
            continue
        ax.step(s["timeline"], s[key], where="post", color=colour,
                ls=style, lw=1.8, zorder=3, label=label)
        # Direct label at the curve's end: identity without a legend box, and
        # the contrast relief the aqua slot requires.
        x, y = s["timeline"].iloc[-1], s[key].iloc[-1]
        ax.annotate(label, xy=(x, y), xytext=(4, 0),
                    textcoords="offset points", va="center",
                    fontsize=7.5, color=INK2, zorder=4)

    ax.set_xlim(0, 365)
    ax.set_ylim(0.55, 1.005)
    ax.set_xlabel("Days since introduction")
    ax.set_ylabel("Fraction of lines undisturbed")
    ax.set_xticks([0, 90, 180, 270, 365])
    ax.set_title("Survival of introduced code, censored at 365 days", loc="left")
    fig.subplots_adjust(right=0.72)
    out = FIGS / "km_survival.pdf"
    fig.savefig(out); plt.close(fig)
    print(f"  wrote {out.name}")


def fig_clustering():
    """Four analyses of the same comparison, and why they disagree.

    The paper's methodological result: AI-attributed code looks markedly more
    durable under the least careful analysis and exactly as durable under the
    most careful one. A dot-and-interval plot, because what matters is where
    each interval sits relative to unity.
    """
    rows = [
        ("Unpaired, exposure-unadjusted",  0.755, 0.603, 0.945, "p=0.013", True),
        ("Cox, stratified + clustered",    0.452, 0.204, 1.002, "p=0.051", False),
        ("Neg. binomial, exposure offset", 0.821, 0.642, 1.049, "p=0.115", False),
        ("Paired, exposure-balanced",      1.000, 0.608, 1.645, "p=1.00",  False),
    ]
    fig, ax = plt.subplots(figsize=(3.4, 2.3))
    ax.axvline(1.0, color=MUTED, lw=1.0, ls=(0, (4, 3)), zorder=1)
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)

    for i, (label, est, lo, hi, pstr, flagged) in enumerate(rows):
        y = len(rows) - 1 - i
        colour = ORANGE if flagged else BLUE
        ax.plot([lo, hi], [y, y], color=colour, lw=4.0,
                solid_capstyle="round", zorder=2)
        ax.plot([est], [y], "o", ms=5.5, color=colour, mec="white", mew=1.2,
                zorder=3)
        ax.annotate(pstr, xy=(hi, y), xytext=(6, 0), textcoords="offset points",
                    fontsize=7, color=INK2, va="center")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in reversed(rows)], fontsize=7.5)
    ax.set_xscale("log")
    ax.set_xlim(0.15, 3.4)
    ax.set_xticks([0.25, 0.5, 1.0, 2.0])
    ax.set_xticklabels(["0.25", "0.5", "1.0", "2.0"])
    # A log axis adds its own minor-tick labels, which collide with the major
    # ones set above; suppress them.
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_xlabel("Effect on disturbance (ratio), log scale")
    ax.set_title("One comparison, four analyses", loc="left")
    ax.set_ylim(-0.7, len(rows) - 0.3)
    out = FIGS / "clustering_effect.pdf"
    fig.savefig(out); plt.close(fig)
    print(f"  wrote {out.name}")


def fig_corpus():
    """How 360 sampled repositories became the measured corpus."""
    stages = [
        ("Java repos sampled", 360),
        ("Carry a provenance signal", 134),
        ("Promoted and analysed", 43),
    ]
    fig, ax = plt.subplots(figsize=(3.4, 1.5))
    ys = range(len(stages))
    ax.barh(list(ys), [v for _, v in stages], height=0.52,
            color=[BLUE, "#7aa9e4", "#bcd3f1"], zorder=3)
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    for i, (label, v) in enumerate(stages):
        ax.annotate(f"{v}", xy=(v, i), xytext=(5, 0),
                    textcoords="offset points", va="center",
                    fontsize=8, color=INK, fontweight="bold")
    ax.set_yticks(list(ys))
    ax.set_yticklabels([s for s, _ in stages], fontsize=7.5)
    ax.invert_yaxis()
    ax.set_xlim(0, 400)
    ax.set_xlabel("Repositories")
    ax.set_title("Corpus attrition", loc="left")
    out = FIGS / "corpus_funnel.pdf"
    fig.savefig(out); plt.close(fig)
    print(f"  wrote {out.name}")


def main():
    print("figures ->", FIGS)
    fig_km()
    fig_clustering()
    fig_corpus()
    return 0


if __name__ == "__main__":
    sys.exit(main())
