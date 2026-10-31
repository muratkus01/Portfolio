"""Synthetic inputs of the portfolio showcase's hybrid-plant demo (showcase.py, demo_05).

Three days of wind, PV and a day-ahead price shaped like a German summer curve, for the
default plant behind a 40 MW connection, with B3 on a 12-hour horizon and every rung at a
fixed market premium of 8 EUR/MWh. They are not data: the B3 ablation runs on them so that
its figures and the showcase's come from one source.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np

from .config import PlantConfig, RunConfig


class DemoInputs(NamedTuple):
    """Inputs of one ladder run: per step the available wind and PV (MW) and the day-ahead
    price (EUR/MWh), the run's settings, and the one premium every rung is settled on and
    plans on (EUR/MWh)."""

    wind: np.ndarray
    pv: np.ndarray
    price: np.ndarray
    run: RunConfig
    premium_rate: float


def demo_inputs() -> DemoInputs:
    """demo_05's inputs, value for value, as showcase.py builds them.

    Returns DemoInputs with 288 quarter-hours. The wind noise and the price noise both come
    from a generator seeded 5, as in the showcase, so they share one draw.
    """
    run = RunConfig(dt=0.25, horizon_steps=48, plant=PlantConfig(conn_mw=40.0))
    n = 3 * 96
    hour = np.arange(n) * run.dt % 24
    daylight = np.clip(np.sin(np.pi * (hour - 6) / 12), 0, 1)
    wind = np.clip(30 + 18 * np.sin(np.arange(n) / 70)
                   + 5 * np.random.default_rng(5).standard_normal(n), 0, 50)
    # an evening peak, a midday solar dip and noise: showcase's synthetic_prices_eur_mwh(n, 5)
    price = (80 + 35 * np.sin(2 * np.pi * (hour - 18) / 24) - 30 * daylight
             + 10 * np.random.default_rng(5).standard_normal(n))
    return DemoInputs(wind, 30 * daylight, price, run, 8.0)
