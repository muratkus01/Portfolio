"""Every number the README's 24-hour example day quotes, from a run of B1 and B3 on that day.

The day is a real run of both controllers, as `reporting.figures.example_day_dispatch` draws
it: B3 is the deployable rolling controller with terminal values fitted on 2024, warmed up
over the three days before, and both are scored on the project's own tariff. The figures
need the household dataset, which is gitignored (`prosumer build-data`, then the H28 load),
so no test repeats them.

Run from the P01 folder:

    python scripts/example_day_stats.py
    python scripts/example_day_stats.py --data data/processed/site_2024_2026_htw_H28.parquet

It reads only that dataset.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from prosumer.baselines.ladder import b1_rule_based
from prosumer.baselines.rolling import InformationModel, b3_rolling_realistic, fit_terminal_values
from prosumer.data.dataset import load_dataset
from prosumer.experiments.rolling_eval import _prices, _step_costs
from prosumer.scenarios import EXTENSION_RUN

LOCAL_TZ = "Europe/Berlin"
DATASET = "data/processed/site_2024_2026_htw_H28.parquet"


def _runs(clock: pd.DatetimeIndex, mask: np.ndarray) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """First and last quarter-hour of each unbroken run of True in `mask`."""
    out, start = [], None
    for i, flag in enumerate(mask):
        if flag and start is None:
            start = i
        if start is not None and (not flag or i == len(mask) - 1):
            end = i if flag else i - 1
            out.append((clock[start], clock[end]))
            start = None
    return out


def day_stats(df: pd.DataFrame, day: str = "2025-06-18", warmup_days: int = 3) -> dict:
    """The example day's prices, dispatch and cost for B1 and the deployable B3.

    df: the site dataset with load_kw, pv_kw, pv_fc_kw and spot_eur_per_kwh on a UTC
        15-minute index, from 2024 (the terminal values are fitted on it) to past `day`.
    day: the local date to report, e.g. "2025-06-18".
    warmup_days: days B3 runs before `day`, so it enters the day as in continuous operation.
    Returns {README quantity: value}: energies, prices and shares to one decimal; grid
    charging, powers and costs to two. The README rounds the prices and B3's state of charge
    at 00:00 further, to whole cents and a whole per cent.
    """
    run, dt = EXTENSION_RUN, EXTENSION_RUN.dt
    train = df[df.index < pd.Timestamp("2025-01-01").tz_localize(LOCAL_TZ)]
    tv = fit_terminal_values(train, *_prices(train, run), run)
    d0 = pd.Timestamp(day, tz=LOCAL_TZ)
    w = df[(df.index >= d0 - pd.Timedelta(days=warmup_days))
           & (df.index < d0 + pd.Timedelta(days=1))]
    pi, pe = _prices(w, run)
    load, pv = w["load_kw"].to_numpy(), w["pv_kw"].to_numpy()
    b1 = b1_rule_based(load, pv, dt, run)
    b3 = b3_rolling_realistic(w, pi, pe, run, InformationModel(), terminal=tv)

    m = w.index.tz_convert(LOCAL_TZ).strftime("%Y-%m-%d") == day
    hrs = np.arange(int(m.sum())) * dt
    clock = w.index.tz_convert(LOCAL_TZ)[m]
    zero_exp = pe[m] <= 1e-9
    evening = (hrs >= 18) & (hrs < 23)
    soc = lambda r: 100 * (r["soc"][m] - run.site.soc_min) / run.site.usable_kwh
    full = lambda r: next((f"{clock[i]:%H:%M}" for i, s in enumerate(soc(r)) if s >= 99.0), "never")
    # charging beyond the PV surplus can only come from the grid
    surplus = np.maximum(pv[m] - load[m], 0)
    grid_charge = lambda r: float(np.sum(np.maximum(r["p_ch"][m] - surplus, 0) * dt))
    # the LP can leave round-off far below a watt-hour; count only real charging
    late_charge = (b3["p_ch"][m] * dt > 1e-6) & ~zero_exp
    return {
        "zero export price": ", ".join(f"{a:%H:%M}-{b + pd.Timedelta('15min'):%H:%M}"
                                       for a, b in _runs(clock, zero_exp)) or "none",
        "negative spot quarter-hours": int((w["spot_eur_per_kwh"].to_numpy()[m] < 0).sum()),
        "max import price ct": round(float(pi[m].max() * 100), 1),
        "max import price at": f"{clock[int(np.argmax(pi[m]))]:%H:%M}",
        "min import price ct": round(float(pi[m].min() * 100), 1),
        "morning export price ct (08:00)": round(float(pe[m][(hrs == 8)][0] * 100), 1),
        "B1 full at": full(b1), "B3 full at": full(b3),
        "B3 soc at 00:00 %": round(float(soc(b3)[0]), 1),
        "B3 soc 06-11 max %": round(float(soc(b3)[(hrs >= 6) & (hrs < 11)].max()), 1),
        "B1 export kWh": round(float(b1["p_exp"][m].sum() * dt), 1),
        "B1 zero-price export kWh": round(float(b1["p_exp"][m][zero_exp].sum() * dt), 1),
        "B3 zero-price export kWh": round(float(b3["p_exp"][m][zero_exp].sum() * dt), 1),
        "B3 charge in zero-price hours kWh": round(float(b3["p_ch"][m][zero_exp].sum() * dt), 1),
        "B3 charge outside zero-price hours kWh": round(
            float(b3["p_ch"][m][~zero_exp].sum() * dt), 1),
        "B3 charge outside zero-price hours at": ", ".join(
            f"{t:%H:%M}" for t in clock[late_charge]) or "none",
        "B3 evening discharge kWh": round(float(b3["p_dis"][m][evening].sum() * dt), 1),
        "B3 evening max discharge kW": round(float(b3["p_dis"][m][evening].max()), 2),
        "B1 evening discharge kWh": round(float(b1["p_dis"][m][evening].sum() * dt), 1),
        "B3 discharge whole day kWh": round(float(b3["p_dis"][m].sum() * dt), 1),
        "B1 discharge whole day kWh": round(float(b1["p_dis"][m].sum() * dt), 1),
        "grid charging kWh B1/B3": (round(grid_charge(b1), 2), round(grid_charge(b3), 2)),
        "day cost EUR B3 (with wear)": round(float(_step_costs(b3, pi, pe, run)[m].sum()), 2),
        "day cost EUR B1 (with wear)": round(float(_step_costs(b1, pi, pe, run)[m].sum()), 2),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default=DATASET, help="site dataset with the H28 load")
    p.add_argument("--day", default="2025-06-18")
    args = p.parse_args()
    print(f"example day {args.day}, {args.data}")
    for key, value in day_stats(load_dataset(args.data), args.day).items():
        print(f"  {key:38s} {value}")


if __name__ == "__main__":
    main()
