"""Tests for prosumer economics and sizing grid evaluation."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from prosumer.config import SiteConfig
from prosumer.experiments.sizing import run_sizing_grid
from prosumer.reporting.economics import compute_investment_metrics
from prosumer.thesis import COSTS_OPTIMIZED, THESIS_SITE


def test_investment_metrics_calculation():
    # 9.37 kWh, 5.63 kW inverter: capex = 9.37 * 350 + 5.63 * 190 = 3279.5 + 1069.7 = 4349.2 EUR
    site = THESIS_SITE
    metrics = compute_investment_metrics(
        cost_annual_nobat=1500.0,
        cost_annual_bat=1000.0,
        site=site,
        cost_profile=COSTS_OPTIMIZED,
        annual_cycles=250.0,
    )
    assert metrics.capex_eur == pytest.approx(site.e_bess * 350.0 + site.p_inv * 190.0)
    assert metrics.annual_savings_eur == pytest.approx(500.0)
    assert metrics.simple_payback_years == pytest.approx(metrics.capex_eur / 500.0)
    assert metrics.roce_pct == pytest.approx((500.0 / metrics.capex_eur) * 100.0)
    assert metrics.opt_profit_annual_eur == pytest.approx(500.0 - COSTS_OPTIMIZED.annual_fixed)


def _synthetic_two_periods(days=12):
    """Half in the training year, half after it, so both split halves are non-empty."""
    idx = pd.date_range("2024-12-26", periods=days * 96, freq="15min",
                        tz="Europe/Berlin").tz_convert("UTC")
    h = (np.arange(len(idx)) * 0.25) % 24
    load = 0.4 + 0.8 * np.exp(-((h - 19) ** 2) / 6)
    pv = 4.0 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    spot = 0.08 + 0.04 * np.sin(2 * np.pi * (h - 18) / 24)
    return pd.DataFrame({"load_kw": load, "pv_kw": pv, "pv_fc_kw": pv,
                         "spot_eur_per_kwh": spot}, index=idx)


def test_sizing_grid_runs_on_synthetic():
    res = run_sizing_grid(_synthetic_two_periods(), capacities=(5.0, 10.0),
                          inverters=(3.0, 5.0), controllers=("b3", "b2"), workers=1,
                          progress=False, train=("2024-12-26", "2025-01-01"))

    assert len(res) == 8                      # two controllers x four configurations
    assert set(res.columns) >= {"controller", "battery_kwh", "inverter_kw", "capex_eur",
                                "annual_savings_eur", "simple_payback_years", "roce_pct"}
    assert (res["capex_eur"] > 0).all()
    assert (res["annual_savings_eur"] >= 0).all()


def test_deployable_sizing_never_beats_perfect_foresight():
    """Per configuration, B3 cannot save more than B2, so its payback cannot be shorter."""
    res = run_sizing_grid(_synthetic_two_periods(), capacities=(5.0,), inverters=(3.0, 5.0),
                          controllers=("b3", "b2"), workers=1, progress=False,
                          train=("2024-12-26", "2025-01-01"))
    piv = res.pivot(index=["battery_kwh", "inverter_kw"], columns="controller",
                    values="annual_savings_eur")
    assert (piv["b3"] <= piv["b2"] + 1e-6).all()
