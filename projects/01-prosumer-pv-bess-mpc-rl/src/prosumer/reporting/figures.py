"""README figures for the thesis extension, built from the committed report CSVs.

Static PNGs on a light surface. Colours are the first slots of a colour-vision-deficiency
validated categorical palette; every bar carries a direct label, so identity and value never
depend on colour alone.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
NEUTRAL = "#b8b7b1"


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def capture_ladder(reports: Path, out: Path) -> Path:
    import matplotlib.pyplot as plt
    s = pd.read_csv(reports / "rolling_eval_htw_H28" / "rolling_eval_summary.csv", index_col=0)
    cap = s["capture of B1->B2 %"]
    rows = [("B2 perfect foresight (ceiling)", 100.0, NEUTRAL),
            ("B3, perfect PV and load forecasts", cap["B3 perfect PV+load, fitted TV"], BLUE),
            ("B3, + real household load", cap["B3 perfect PV, SLP load, fitted TV"], BLUE),
            ("B3, + day-ahead PV forecast (deployable)",
             cap["B3 NWP PV, SLP load, fitted TV"], BLUE)]
    rl = reports / "rl_eval_H28" / "rl_summary.csv"
    if rl.exists():
        r = pd.read_csv(rl, index_col=0)["capture of B1->B2 %"]
        seeds = r[[i for i in r.index if i.startswith("RL SAC")]]
        rows.append((f"RL SAC, same information (median of {len(seeds)} seeds)",
                     float(seeds.median()), ORANGE))
    rows.append(("B1 rule-based", 0.0, NEUTRAL))

    fig, ax = plt.subplots(figsize=(8.6, 0.55 * len(rows) + 1.2), facecolor=SURFACE)
    _style(ax)
    y = np.arange(len(rows))[::-1]
    for yi, (label, v, c) in zip(y, rows):
        ax.barh(yi, max(v, 0.4), height=0.62, color=c, edgecolor=SURFACE, linewidth=2)
        ax.text(max(v, 0) + 1.2, yi, f"{v:.1f} %", va="center", color=INK, fontsize=10)
    ax.set_yticks(y, [r[0] for r in rows], color=INK, fontsize=10)
    ax.set_xlim(0, 112)
    ax.set_xticks([0, 25, 50, 75, 100], ["0 %", "25 %", "50 %", "75 %", "100 %"])
    ax.set_title("Share of the perfect-foresight gain each controller captures\n"
                 "household H28, Jan 2025 to Sep 2026, trained on 2024",
                 loc="left", color=INK, fontsize=11)
    fig.tight_layout()
    path = out / "capture_ladder.png"
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return path


def household_sweep(reports: Path, out: Path) -> Path:
    import matplotlib.pyplot as plt
    w = pd.read_csv(reports / "household_sweep.csv")
    fig, ax = plt.subplots(figsize=(8.6, 3.0), facecolor=SURFACE)
    _style(ax)
    ax.grid(axis="y", visible=False)
    bins = np.arange(74, 93, 1.0)
    ax.hist(w["capture_b3_pct"], bins=bins, color=BLUE, edgecolor=SURFACE, linewidth=2)
    top = ax.get_ylim()[1] * 1.35
    ax.set_ylim(0, top)
    med = w["capture_b3_pct"].median()
    from matplotlib.ticker import MaxNLocator
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.axvline(med, ymax=0.97, color=INK, linewidth=1.5)
    ax.text(med + 0.2, top * 0.93, f"median {med:.1f} %", color=INK, fontsize=10)
    # H31 sits left of the median, so its label goes left of its line; H28 goes right
    for h, note, side in (("H28", "H28 main case", 1), ("H31", "H31 peaks 13.7 kW", -1)):
        v = float(w.loc[w["household"] == h, "capture_b3_pct"].iloc[0])
        ax.plot([v, v], [0, top * 0.78], color=INK_2, linewidth=0.8, linestyle=(0, (2, 2)))
        ax.text(v + 0.15 * side, top * 0.80, note, color=INK_2, fontsize=9, va="center",
                ha="left" if side > 0 else "right")
    ax.set_xlabel("share of the perfect-foresight gain captured by the deployable B3",
                  color=INK_2)
    ax.set_ylabel("households", color=INK_2)
    ax.set_title(f"Robustness across {len(w)} measured households (HTW Berlin profiles)",
                 loc="left", color=INK, fontsize=11)
    fig.tight_layout()
    path = out / "household_sweep.png"
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return path


def quarter_hour_value(reports: Path, out: Path) -> Path:
    import matplotlib.pyplot as plt
    m = pd.read_csv(reports / "quarter_hour_study_H28" / "resolution_monthly_cost.csv",
                    index_col=0)
    v = m["B3 | hourly signal"] - m["B3 | 15-min"]
    fig, ax = plt.subplots(figsize=(8.6, 3.0), facecolor=SURFACE)
    _style(ax)
    ax.grid(axis="x", visible=False)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    x = np.arange(len(v))
    ax.bar(x, v.to_numpy(), width=0.7, color=AQUA, edgecolor=SURFACE, linewidth=2)
    ax.set_ylim(0, v.max() * 1.22)
    for xi, val in zip(x, v.to_numpy()):
        ax.text(xi, val + 0.08, f"{val:.1f}", ha="center", color=INK, fontsize=8.5)
    labels = list(pd.to_datetime(v.index).strftime("%b\n%Y"))
    labels[-1] += "\n(1 to 17)"                       # the data ends on 17 September
    ax.set_xticks(x, labels, fontsize=8.5)
    ax.set_ylabel("EUR per month", color=INK_2)
    ax.set_title("Value of reacting to quarter-hour instead of hourly prices\n"
                 f"{v.sum():.1f} EUR from Oct 2025 to Sep 2026, deployable B3, household H28",
                 loc="left", color=INK, fontsize=11)
    fig.tight_layout()
    path = out / "quarter_hour_value.png"
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return path


def make_all(reports: str | Path = "reports", out: str | Path = "docs/figures") -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    reports, out = Path(reports), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    return [capture_ladder(reports, out), household_sweep(reports, out),
            quarter_hour_value(reports, out)]
