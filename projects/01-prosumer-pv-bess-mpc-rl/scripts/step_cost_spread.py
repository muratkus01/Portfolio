"""The spread of the step cost the original RL reward carried, over the H28 test period.

The README's RL section compares two standard deviations per quarter-hour, in ct: the cost of
the same site without a battery, which load and irradiance set and the battery cannot change,
against the battery's own part of the cost under B1 dispatch (B1's step cost minus the
no-battery one). The figures need the household dataset, which is gitignored
(`prosumer build-data`, then the H28 load), so no test repeats them.

Run from the P01 folder:

    python scripts/step_cost_spread.py
    python scripts/step_cost_spread.py --data data/processed/site_2024_2026_htw_H28.parquet

It reads only that dataset.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from prosumer.baselines.ladder import b1_rule_based
from prosumer.experiments.rolling_eval import _prices, _step_costs
from prosumer.scenarios import EXTENSION_RUN

LOCAL_TZ = "Europe/Berlin"
DATASET = "data/processed/site_2024_2026_htw_H28.parquet"


def step_cost_spread(df: pd.DataFrame, test_start: str = "2025-01-01") -> dict[str, float]:
    """Standard deviation of the no-battery step cost and of the battery's part under B1.

    df: the site dataset with load_kw, pv_kw and spot_eur_per_kwh on a UTC 15-minute index.
    test_start: first local day of the test period, as in every other result.
    Returns {"no battery ct": ..., "battery part under B1 ct": ..., "ratio": ...}.
    """
    run = EXTENSION_RUN
    te = df[df.index >= pd.Timestamp(test_start).tz_localize(LOCAL_TZ)]
    pi, pe = _prices(te, run)
    load, pv = te["load_kw"].to_numpy(), te["pv_kw"].to_numpy()
    net = load - pv
    no_battery = (pi * np.maximum(net, 0) - pe * np.maximum(-net, 0)) * run.dt
    b1 = _step_costs(b1_rule_based(load, pv, run.dt, run), pi, pe, run)
    spread, own = 100 * np.std(no_battery), 100 * np.std(b1 - no_battery)
    return {"no battery ct": spread, "battery part under B1 ct": own, "ratio": spread / own}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default=DATASET, help="site dataset with the H28 load")
    args = p.parse_args()
    s = step_cost_spread(pd.read_parquet(args.data))
    print(f"step cost std: no battery {s['no battery ct']:.3f} ct, battery part under B1 "
          f"{s['battery part under B1 ct']:.3f} ct, ratio {s['ratio']:.2f}")


if __name__ == "__main__":
    main()
