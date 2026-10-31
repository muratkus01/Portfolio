"""Project 03 benchmark figure, drawn only from the committed CSVs in reports/<phase>/.

    python scripts/make_f2b_figure.py [--phase test]

Panels: pinball loss of every forecast rung at every decision stage; reliability of the
day-ahead quantiles; and, once the market value exists, each forecast scheduled day-ahead
against the perfect forecast, split into its bias part and its alignment part
(error_alignment.csv). The three LightGBM seeds are drawn as their median, with the spread
across seeds in the CSVs.
"""
import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

P03 = Path(__file__).resolve().parents[1] / "projects" / "03-probabilistic-forecast-to-bid"
STAGES = ["DA", "ID-12h", "ID-6h", "ID-2h", "ID-0.5h"]
RUNGS = {"F0 climatology": "#9e9e9e", "F1 persistence": "#8d6e63",
         "F2 linear quantile": "#1f77b4", "F3 gradient boosting": "#2ca02c",
         "F4 TSO forecast": "#d62728"}


def merge_seeds(frame: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    """One 'F3 gradient boosting' row per key: the median over the 'F3 seed N' rungs."""
    f3 = frame["rung"].str.startswith("F3 seed")
    keys = [c for c in frame.columns if c not in value_cols and c != "rung"]
    med = (frame[f3].groupby(keys, as_index=False)[value_cols].median() if keys
           else frame[f3][value_cols].median().to_frame().T)
    med.insert(0, "rung", "F3 gradient boosting")
    return pd.concat([frame[~f3], med], ignore_index=True)


def plot_pinball(ax, scores: pd.DataFrame) -> None:
    s = merge_seeds(scores[["rung", "stage", "pinball"]], ["pinball"])
    for rung, color in RUNGS.items():
        row = s[s.rung == rung].set_index("stage").reindex(STAGES)
        ax.plot(STAGES, row["pinball"], marker="o", color=color, lw=2, label=rung)
    ax.set_title("Pinball loss by decision stage (MW, lower is better)", fontsize=10)
    ax.set_xlabel("decision stage, earliest to latest")
    ax.grid(True, ls="--", alpha=0.4)
    ax.legend(fontsize=8)


def plot_reliability(ax, scores: pd.DataFrame) -> None:
    cov_cols = [c for c in scores.columns if c.startswith("coverage_q")]
    levels = np.array([int(c[-2:]) / 100 for c in cov_cols])
    s = merge_seeds(scores[scores.stage == "DA"][["rung", "stage", *cov_cols]], cov_cols)
    ax.plot([0, 1], [0, 1], color="black", lw=0.8, ls=":")
    for rung, color in RUNGS.items():
        row = s[s.rung == rung]
        ax.plot(levels, row[cov_cols].to_numpy()[0], marker="o", color=color, lw=1.5,
                label=rung)
    ax.set_title("Day-ahead quantiles: share of outcomes below each one", fontsize=10)
    ax.set_xlabel("nominal level")
    ax.set_ylabel("observed share")
    ax.grid(True, ls="--", alpha=0.4)


def plot_alignment(ax, alignment: pd.DataFrame) -> None:
    """Scheduling each forecast day-ahead, against the perfect forecast: why euros do not
    follow skill. Bias part plus alignment part equals the total exactly."""
    parts = ["bias_part_eur", "alignment_part_eur", "b0_minus_ref_eur"]
    a = merge_seeds(alignment[["rung", *parts]], parts).set_index("rung").reindex(list(RUNGS))
    x = np.arange(len(a))
    bias, align = a["bias_part_eur"] / 1e3, a["alignment_part_eur"] / 1e3
    ax.bar(x, align, color="#7f7f7f", label="errors aligned with the imbalance price")
    ax.bar(x, bias, bottom=np.where(bias * align > 0, align, 0), color="#ff7f0e",
           label="bias (long earns when reBAP > day-ahead)")
    ax.scatter(x, a["b0_minus_ref_eur"] / 1e3, color="black", zorder=3, label="total")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([r.split(" ", 1)[0] for r in a.index])
    ax.set_title("Day-ahead schedule against the perfect forecast (k EUR)", fontsize=10)
    ax.legend(fontsize=7.5, loc="lower right")
    ax.grid(True, axis="y", ls="--", alpha=0.4)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="test", choices=("validation", "test"))
    args = ap.parse_args()
    folder = P03 / "reports" / args.phase
    scores = pd.read_csv(folder / "forecast_by_stage.csv")
    alignment_path = folder / "error_alignment.csv"
    panels = 3 if alignment_path.exists() else 2
    fig, axes = plt.subplots(1, panels, figsize=(6.2 * panels, 4.6), dpi=200)
    plot_pinball(axes[0], scores)
    plot_reliability(axes[1], scores)
    if panels == 3:
        plot_alignment(axes[2], pd.read_csv(alignment_path))
    fig.suptitle(f"Project 03, {args.phase} window, from reports/{args.phase}/", fontsize=11)
    fig.tight_layout()
    out = P03 / "docs" / "figures" / "f2b_trading_benchmark.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight")
    print("saved", out.relative_to(P03.parents[1]))


if __name__ == "__main__":
    main()
