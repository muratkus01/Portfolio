"""Economic metrics: annual savings, simple payback, and ROCE for prosumer BESS.

Extends the M.Sc. thesis economic methodology to multi-year 15-minute data.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import SiteConfig
from ..thesis import CostProfile, THESIS_PV_KWP


@dataclass(frozen=True)
class InvestmentMetrics:
    capex_eur: float
    annual_savings_eur: float
    simple_payback_years: float
    roce_pct: float
    opt_profit_annual_eur: float
    cycles_per_year: float


def compute_investment_metrics(cost_annual_nobat: float, cost_annual_bat: float,
                               site: SiteConfig, cost_profile: CostProfile,
                               pv_kwp: float = THESIS_PV_KWP,
                               annual_cycles: float = 0.0) -> InvestmentMetrics:
    """Compute payback, ROCE, and annual economics for adding BESS to PV."""
    # Marginal CAPEX for battery storage + inverter
    bat_capex = site.e_bess * 350.0 + site.p_inv * 190.0
    annual_savings = max(0.0, cost_annual_nobat - cost_annual_bat)

    payback = bat_capex / annual_savings if annual_savings > 1e-6 else np.inf
    roce = (annual_savings / bat_capex) * 100.0 if bat_capex > 0 else 0.0
    opt_profit = annual_savings - cost_profile.annual_fixed

    return InvestmentMetrics(
        capex_eur=bat_capex,
        annual_savings_eur=annual_savings,
        simple_payback_years=payback,
        roce_pct=roce,
        opt_profit_annual_eur=opt_profit,
        cycles_per_year=annual_cycles,
    )
