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
LOCAL_TZ = "Europe/Berlin"


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


def example_day_dispatch(dataset_path: str | Path | None = None, out: Path | None = None,
                         day: str = "2025-06-18", warmup_days: int = 3) -> Path:
    """One day of real dispatch: the price-blind B1 against the deployable B3.

    B3 here is the controller the results are about: rolling, re-planned every quarter-hour,
    seeing only published prices and the day-ahead forecasts, with the terminal values fitted
    on 2024. It is run from `warmup_days` before the plotted day so its state of charge
    enters the day as it would in continuous operation, and it is scored on the project's own
    tariff. Plotting a single-day perfect-foresight solve instead, or a zero export price,
    would show a dispatch that no result in this README refers to.
    """
    import matplotlib.pyplot as plt

    from ..baselines.ladder import b1_rule_based
    from ..baselines.rolling import InformationModel, b3_rolling_realistic, fit_terminal_values
    from ..experiments.rolling_eval import _prices
    from ..scenarios import EXTENSION_RUN

    run = EXTENSION_RUN
    dt = run.dt
    if dataset_path is None or not Path(dataset_path).exists():
        raise FileNotFoundError(
            f"dataset {dataset_path} not found; build it with `prosumer build-data` first")

    df = pd.read_parquet(dataset_path)
    train = df[df.index < pd.Timestamp("2025-01-01").tz_localize(LOCAL_TZ)]
    tv = fit_terminal_values(train, *_prices(train, run), run)

    d0 = pd.Timestamp(day, tz=LOCAL_TZ)
    window = df[(df.index >= d0 - pd.Timedelta(days=warmup_days))
                & (df.index < d0 + pd.Timedelta(days=1))]
    pi, pe = _prices(window, run)
    load, pv = window["load_kw"].to_numpy(), window["pv_kw"].to_numpy()
    b1 = b1_rule_based(load, pv, dt, run)
    b3 = b3_rolling_realistic(window, pi, pe, run, InformationModel(), terminal=tv)

    m = window.index.tz_convert(LOCAL_TZ).strftime("%Y-%m-%d") == day
    hours = np.arange(int(m.sum())) * dt
    soc = lambda r: 100 * (r["soc"][m] - run.site.soc_min) / run.site.usable_kwh
    bat = lambda r: r["p_dis"][m] - r["p_ch"][m]

    fig, (ax_price, ax_power, ax_bat, ax_soc) = plt.subplots(
        4, 1, figsize=(9.0, 7.4), sharex=True, facecolor=SURFACE,
        gridspec_kw={"height_ratios": [1, 1, 1, 1]})
    for ax in (ax_price, ax_power, ax_bat, ax_soc):
        _style(ax)
        ax.grid(axis="x", visible=False)
        ax.grid(axis="y", color=GRID, linewidth=0.8)

    ax_price.plot(hours, pi[m] * 100, color=INK, linewidth=1.5, label="import price")
    ax_price.plot(hours, pe[m] * 100, color=INK_2, linewidth=1.2, linestyle="--",
                  label="export price")
    ax_price.set_ylabel("ct/kWh", color=INK_2)
    ax_price.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=2)
    ax_price.set_title(f"One day of real dispatch, {day}, household H28\n"
                       "price-blind B1 against the deployable B3",
                       loc="left", color=INK, fontsize=11)

    ax_power.plot(hours, pv[m], color=ORANGE, linewidth=1.5, label="PV")
    ax_power.plot(hours, load[m], color=INK_2, linewidth=1.2, linestyle="--", label="load")
    ax_power.set_ylabel("kW", color=INK_2)
    ax_power.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=2)

    ax_bat.axhline(0, color=GRID, linewidth=1)
    ax_bat.plot(hours, bat(b1), color=NEUTRAL, linewidth=1.4, linestyle=":", label="B1")
    ax_bat.plot(hours, bat(b3), color=BLUE, linewidth=1.8, label="B3")
    ax_bat.set_ylabel("battery kW\n(+ discharge)", color=INK_2)
    ax_bat.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=2)

    ax_soc.plot(hours, soc(b1), color=NEUTRAL, linewidth=1.4, linestyle=":", label="B1")
    ax_soc.plot(hours, soc(b3), color=BLUE, linewidth=1.8, label="B3")
    ax_soc.set_ylabel("state of charge %", color=INK_2)
    ax_soc.set_xlabel("local clock hour (CEST)", color=INK_2)
    ax_soc.set_xlim(0, 24)
    ax_soc.set_xticks(range(0, 25, 3))
    ax_soc.set_ylim(-5, 108)
    ax_soc.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=2)

    fig.tight_layout()
    out = Path(out) if out else Path("docs/figures")
    path = out / "example_day_dispatch.png"
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return path


def make_all(reports: str | Path = "reports", out: str | Path = "docs/figures") -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    reports, out = Path(reports), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    figs = [capture_ladder(reports, out), household_sweep(reports, out),
            quarter_hour_value(reports, out)]
    dataset = reports.parent / "data" / "processed" / "site_2024_2026_htw_H28.parquet"
    if not dataset.exists():
        dataset = reports.parents[1] / "data" / "processed" / "site_2024_2026_htw_H28.parquet"
    if dataset.exists():
        figs.append(example_day_dispatch(dataset, out))
    return figs
