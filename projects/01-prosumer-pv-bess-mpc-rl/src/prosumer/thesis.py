"""The published M.Sc. thesis as a configuration of this package.

Source: https://github.com/muratkus01/optimization, tag `thesis-v1.0`
("Techno-Economic Assessment of Residential BESS with PV and Dynamic Tariffs", 2025).

The thesis studies one Bavarian household over the full year 2024 at hourly resolution.
Everything needed to reproduce its three scenarios with this package's own controllers lives
here, so the published numbers remain a fixed point that later extensions are checked against
(`tests/test_thesis_reproduction.py`).

Two properties of the thesis model are reproduced on purpose, not endorsed:

* The MILP optimises against `EPEX +/- C_grid`, while the household is billed at
  `EP_buy`/`EP_sell` (`THESIS_OBJECTIVE_TARIFF` vs `THESIS_RETAIL_TARIFF`). The rest of this
  package optimises against the price the household actually pays.
* The wear rules differ between strategies: the MILP prices and caps discharged energy
  (`wear_basis="discharge"`), the rule-based controller caps charged plus discharged energy.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import RunConfig, SiteConfig, TariffConfig

# --------------------------------------------------------------------------- site
_E_BESS = 9.37
_ETA = (0.95 ** 0.5) * 0.97                  # one-way: sqrt(battery round trip) * inverter
_SOC_MIN, _SOC_MAX = 0.05 * _E_BESS, 0.95 * _E_BESS
_E_MAX_DAY = 2 * (_SOC_MAX - _SOC_MIN)       # two usable cycles per day
_UNLIMITED_KW = 1e6                          # the thesis model has no grid connection limit

THESIS_SITE = SiteConfig(
    e_bess=_E_BESS, soc_min=_SOC_MIN, soc_max=_SOC_MAX, soc_init=0.5 * _E_BESS,
    eta_c=_ETA, eta_d=_ETA, p_inv=5.63,
    p_imp_max=_UNLIMITED_KW, p_exp_max=_UNLIMITED_KW,
    c_deg=0.05438366526,                     # C_MBCC, marginal battery cycling cost
    e_throughput_max_per_day=_E_MAX_DAY, wear_basis="discharge",
)
THESIS_PV_KWP = 8.0
THESIS_DOD = 0.90                            # used for cycle counting only
C_GRID = 0.1936996                           # EUR/kWh, grid fees, levies and taxes (gross)

# --------------------------------------------------------------------------- tariffs
THESIS_OBJECTIVE_TARIFF = TariffConfig(
    supplier_margin=0.0, network_charge=C_GRID, levies=0.0, electricity_tax=0.0, vat=0.0,
    export_mode="market_premium", market_premium_fee=C_GRID,
)
"""Prices the thesis MILP optimises against: import at EPEX + C_grid, export at EPEX - C_grid.
Minimising net cost under it equals the thesis objective
`max sum EPEX*(P_dc - P_ch) - C_grid*(P_from + P_to) - C_MBCC*P_dc` up to a constant."""

THESIS_RETAIL_TARIFF = TariffConfig(
    supplier_margin=0.0, network_charge=C_GRID / 1.19, levies=0.0, electricity_tax=0.0,
    vat=0.19, export_mode="spot_gross_floored", market_premium_fee=0.01,
)
"""What the household pays and earns (thesis columns EP_buy and EP_sell):
EP_buy = 1.19 * EPEX + C_grid,  EP_sell = max(0, 1.19 * EPEX - 0.01).
Recovered from the published 2024 data, exact to 1e-6 EUR/kWh."""

# --------------------------------------------------------------------------- runs
THESIS_MILP_RUN = RunConfig(
    dt=1.0, horizon_steps=8784, terminal_value=False, use_binaries=True,
    site=THESIS_SITE, tariff=THESIS_OBJECTIVE_TARIFF,
)
"""The thesis 'optimized' scenario: one perfect-foresight MILP over all of 2024."""

THESIS_RULE_RUN = RunConfig(
    dt=1.0, terminal_value=False,
    site=SiteConfig(**{**THESIS_SITE.__dict__, "c_deg": 0.0, "wear_basis": "throughput"}),
    tariff=THESIS_OBJECTIVE_TARIFF,
)
"""The thesis 'rule-based' scenario: self-consumption, daily cap on charge plus discharge."""


# --------------------------------------------------------------------------- costs
@dataclass(frozen=True)
class CostProfile:
    """Fixed costs and CAPEX of one thesis scenario (they differ per scenario)."""
    name: str
    monthly_fixed: tuple[float, ...] = field(default_factory=tuple)
    extra_capex: tuple[float, ...] = field(default_factory=tuple)
    has_battery: bool = True

    @property
    def annual_fixed(self) -> float:
        return sum(self.monthly_fixed) * 12

    def capex(self, site: SiteConfig, pv_kwp: float = THESIS_PV_KWP) -> float:
        battery = site.e_bess * 350 if self.has_battery else 0.0
        return pv_kwp * 130 + battery + site.p_inv * 190 + sum(self.extra_capex)


# A price-optimised system needs an energy management system and smart meter: +500 EUR CAPEX
# and a higher metering fee than the rule-based system.
COSTS_OPTIMIZED = CostProfile("optimized", (5, 4.18285, 4.167), (135, 500, 800, 2000, 2500))
COSTS_RULE_BASED = CostProfile("rule_based", (3.8568, 4.18285, 4.167), (135, 800, 2000, 2500))
COSTS_PV_ONLY = CostProfile("just_pv", (4.18285, 1.667, 3.8568), (800, 2000, 2500),
                            has_battery=False)
