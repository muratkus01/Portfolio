"""Realistic B3: information set, fast LP, and the ladder invariant. Synthetic, always runs."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from prosumer.baselines.ladder import b1_rule_based
from prosumer.baselines.lp_fast import solve_window_fast
from prosumer.baselines.milp import solve_window
from prosumer.baselines.rolling import (InformationModel, b3_rolling_realistic,
                                        forecast_arrays, price_known_end)
from prosumer.market import settlement
from prosumer.model import site
from prosumer.scenarios import EXTENSION_RUN as RUN


def _synthetic(days=4, seed=0):
    idx = pd.date_range("2025-06-02", periods=days * 96, freq="15min",
                        tz="Europe/Berlin").tz_convert("UTC")
    h = (np.arange(len(idx)) % 96) / 4
    rng = np.random.default_rng(seed)
    pv = np.clip(6 * np.sin(np.pi * (h - 6) / 14), 0, None) * rng.uniform(0.6, 1.0, len(idx))
    load = 0.3 + 0.5 * ((h > 17) & (h < 22)) + 0.05 * rng.random(len(idx))
    spot = 0.08 + 0.06 * np.cos(2 * np.pi * (h - 19) / 24) + 0.01 * rng.standard_normal(len(idx))
    df = pd.DataFrame({"load_kw": load, "pv_kw": pv, "pv_fc_kw": pv * 1.1,
                       "spot_eur_per_kwh": spot}, index=idx)
    pi = 1.19 * spot + 0.1937
    pe = np.maximum(0, 1.19 * spot - 0.01)
    return df, pi, pe


def test_prices_become_known_at_13_local():
    idx = pd.date_range("2025-06-02 00:00", periods=3 * 96, freq="15min",
                        tz="Europe/Berlin").tz_convert("UTC")
    end = price_known_end(idx, "13:00")
    loc = idx.tz_convert("Europe/Berlin")
    i_1245 = np.flatnonzero((loc.day == 2) & (loc.hour == 12) & (loc.minute == 45))[0]
    i_1300 = i_1245 + 1
    assert end[i_1245] == 96                  # only today (Jun 2) known
    assert end[i_1300] == 192                 # tomorrow published: window to end of Jun 3


def test_no_price_leak_beyond_publication():
    """The window never reaches past the last published price."""
    df, pi, pe = _synthetic()
    r = b3_rolling_realistic(df, pi, pe, RUN, InformationModel(pv_forecast="perfect"),
                             terminal="none")
    end = price_known_end(df.index)
    assert np.all(np.arange(len(df)) + r["horizon_steps"] <= end)
    assert r["horizon_steps"].max() <= 35 * 4


def test_forecast_uses_nwp_and_falls_back_to_yesterday():
    df, _, _ = _synthetic()
    df.loc[df.index[:96], "pv_fc_kw"] = np.nan
    pv_fc, load_fc = forecast_arrays(df, InformationModel(), 96)
    np.testing.assert_allclose(pv_fc[96:], df["pv_fc_kw"].to_numpy()[96:])
    np.testing.assert_allclose(load_fc, df["load_kw"])   # no load_fc_kw column: profile = truth


def test_fast_lp_equals_pulp_lp():
    df, pi, pe = _synthetic(days=2)
    args = (df["load_kw"].to_numpy(), df["pv_kw"].to_numpy(), pi, pe, 0.25, RUN.site, 4.0)
    a = solve_window(*args, terminal_price=0.1)
    b = solve_window_fast(*args, terminal_price=0.1)
    assert b["objective"][0] == pytest.approx(a["objective"][0], abs=1e-6)


def test_ladder_invariant_and_feasibility():
    df, pi, pe = _synthetic()
    load, pv = df["load_kw"].to_numpy(), df["pv_kw"].to_numpy()
    cost = lambda r: settlement.settle(r, pi, pe, RUN.dt, RUN.site)["net_cost"]
    b2 = solve_window_fast(load, pv, pi, pe, RUN.dt, RUN.site, RUN.site.soc_init)
    c_b2 = cost(site.simulate(b2["p_bat"], load, pv, RUN.dt, RUN.site))
    for info in (InformationModel(pv_forecast="perfect"), InformationModel()):
        r = b3_rolling_realistic(df, pi, pe, RUN, info, terminal="none")
        assert sum(site.check_feasible(r, RUN.dt, RUN.site).values()) == 0
        assert cost(r) >= c_b2 - 1e-6
    assert cost(b1_rule_based(load, pv, RUN.dt, RUN)) >= c_b2 - 1e-6
