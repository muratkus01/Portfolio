"""Configuration dataclasses.

Every number that was a module-level constant in the original `smart_weekly.py` lives here,
so that a run is fully described by its config objects. Nothing in the model, the baselines
or the RL environment reads a magic number.

Default site parameters are the ones from the original thesis MILP.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class SiteConfig:
    """Physical site: battery, inverter, grid connection.

    Sign convention used everywhere in this package:
        battery power  p_bat > 0  ->  DISCHARGING (battery supplies the site)
                       p_bat < 0  ->  CHARGING
        grid           import > 0, export > 0, both non-negative and mutually exclusive
    """

    # --- battery (values from the original smart_weekly.py) ---
    e_bess: float = 8.9          # kWh nameplate
    soc_min: float = 0.884       # kWh  (10 % of nameplate)
    soc_max: float = 7.956       # kWh  (90 % of nameplate)
    soc_init: float = 4.42       # kWh
    eta_c: float = 0.95          # charging efficiency
    eta_d: float = 0.95          # discharging efficiency

    # --- power limits ---
    p_inv: float = 5.51          # kW inverter rating (battery charge and discharge)
    p_imp_max: float = 6.4668    # kW maximum grid import
    p_exp_max: float = 20.345    # kW maximum grid export

    # --- degradation / throughput ---
    # The original model capped daily throughput at E_max = 28.288 kWh. That is a hard cap
    # standing in for wear. Here it is replaced by an explicit price on throughput, which is
    # both more realistic and differentiable; the hard cap remains available for comparison.
    c_deg: float = 0.03          # EUR per kWh of wear energy (see wear_basis)
    e_throughput_max_per_day: float | None = None   # kWh/day, None = disabled (use c_deg)
    # Which energy c_deg and the daily cap apply to:
    #   "throughput" - charged + discharged energy (default)
    #   "discharge"  - discharged energy only (the published thesis MILP: C_MBCC * P_dc)
    wear_basis: str = "throughput"

    def __post_init__(self) -> None:
        if self.wear_basis not in {"throughput", "discharge"}:
            raise ValueError(f"unknown wear_basis {self.wear_basis!r}")
        if not 0 < self.eta_c <= 1 or not 0 < self.eta_d <= 1:
            raise ValueError("efficiencies must be in (0, 1]")
        if not self.soc_min <= self.soc_init <= self.soc_max:
            raise ValueError("soc_init outside [soc_min, soc_max]")
        if self.soc_min >= self.soc_max:
            raise ValueError("soc_min must be below soc_max")

    @property
    def usable_kwh(self) -> float:
        return self.soc_max - self.soc_min


@dataclass(frozen=True)
class TariffConfig:
    """German retail price build-up and export remuneration.

    All components in EUR/kWh unless stated. Defaults are order-of-magnitude representative
    for a German household around 2024/25 and are meant to be overridden with the actual
    supplier and DSO price sheet for a real study.

    IMPORTANT: the original MILP priced import and export identically at the spot price.
    That single change - a realistic import/export spread - is the largest single driver of
    the difference between the old and the new results. See docs/08-milp-to-rl-roadmap.md D2.
    """

    # --- import side ---
    supplier_margin: float = 0.030        # EUR/kWh
    network_charge: float = 0.085         # EUR/kWh energy component of the network charge
    levies: float = 0.015                 # KWKG, offshore, StromNEV s19 etc.
    electricity_tax: float = 0.02050      # Stromsteuer
    vat: float = 0.19                     # applied to the whole import price
    spot_passthrough: bool = True         # False -> fixed energy price instead of spot-linked
    fixed_energy_price: float = 0.090     # EUR/kWh, used when spot_passthrough is False

    # --- export side ---
    # "feed_in_tariff" | "market_premium" | "spot" | "spot_gross_floored"
    export_mode: str = "feed_in_tariff"
    feed_in_tariff: float = 0.0786        # EUR/kWh, EEG Teileinspeisung class
    market_premium_fee: float = 0.005     # EUR/kWh direct-marketing fee, market_premium mode

    # --- negative-price rule (EEG s51, tightened for new plants) ---
    # "none"     : premium always paid (older vintages / simplification)
    # "new_2025" : no export remuneration in any quarter-hour with a negative spot price
    negative_price_rule: str = "none"

    # --- s14a EnWG network-charge module ---
    # "none"     : no participation
    # "module_1" : flat annual reduction -> does NOT change the marginal price
    # "module_2" : percentage reduction on the network energy component
    # "module_3" : time-variable network charge (low / standard / high windows)
    para_14a_module: str = "none"
    module_1_annual_reduction: float = 190.0   # EUR/a, informational (no marginal effect)
    module_2_reduction: float = 0.60           # fraction off the network energy component
    module_3_factors: tuple[float, float, float] = (0.4, 1.0, 1.9)  # low / standard / high
    # hours-of-day assigned to the HIGH window and the LOW window (local time)
    module_3_high_hours: tuple[int, ...] = (7, 8, 9, 17, 18, 19, 20)
    module_3_low_hours: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 11, 12, 13, 14)

    def __post_init__(self) -> None:
        if self.export_mode not in {"feed_in_tariff", "market_premium", "spot",
                                    "spot_gross_floored"}:
            raise ValueError(f"unknown export_mode {self.export_mode!r}")
        if self.negative_price_rule not in {"none", "new_2025"}:
            raise ValueError(f"unknown negative_price_rule {self.negative_price_rule!r}")
        if self.para_14a_module not in {"none", "module_1", "module_2", "module_3"}:
            raise ValueError(f"unknown para_14a_module {self.para_14a_module!r}")


@dataclass(frozen=True)
class Para14aConfig:
    """Grid-orientated control (s14a EnWG) dimming process.

    No public dataset of realised dimming events exists, so the process is parameterised and
    swept. `p_min_kw` is the guaranteed minimum power the network operator must leave
    available to the controllable devices.
    """
    enabled: bool = False
    p_min_kw: float = 4.2               # guaranteed minimum per the BNetzA determination
    events_per_year: float = 60.0       # expected number of dimming events
    mean_duration_h: float = 2.0        # mean event duration
    concentration_hours: tuple[int, ...] = (17, 18, 19, 20)  # when events cluster
    seed: int = 0


@dataclass(frozen=True)
class RunConfig:
    """A single experiment."""
    dt: float = 0.25                    # hours per step. 1.0 reproduces the original model.
    horizon_steps: int = 96             # B3 rolling horizon (96 = 1 day at 15 min)
    terminal_value: bool = True         # value terminal SoC (see roadmap D4)
    terminal_price: float | None = None # EUR/kWh; None -> median import price of the horizon
    use_binaries: bool = False          # exact mutual exclusion; see b2_milp docstring
    forecast_sigma: float = 0.15        # relative AR(1) forecast error for B3 / RL
    forecast_rho: float = 0.75
    seed: int = 0

    site: SiteConfig = field(default_factory=SiteConfig)
    tariff: TariffConfig = field(default_factory=TariffConfig)
    para_14a: Para14aConfig = field(default_factory=Para14aConfig)

    @property
    def steps_per_day(self) -> int:
        return int(round(24.0 / self.dt))

    def with_(self, **kwargs) -> "RunConfig":
        """Return a copy with fields replaced - convenient for ablation sweeps."""
        return replace(self, **kwargs)


LEGACY_TARIFF = TariffConfig(
    supplier_margin=0.0, network_charge=0.0, levies=0.0, electricity_tax=0.0, vat=0.0,
    spot_passthrough=True, export_mode="spot",
    negative_price_rule="none", para_14a_module="none",
)
"""The original model's implicit tariff: import and export both at the raw spot price, no
levies, no VAT. Minimising `net_cost` under this tariff is algebraically identical to the
original objective `max sum(EP[t]*(P_dc[t] - P_ch[t]))`, because the site's own net load
contributes a constant. Kept only so the regression test can prove the port is faithful."""

LEGACY_RUN = RunConfig(
    dt=1.0,
    horizon_steps=24,
    terminal_value=False,
    use_binaries=True,
    site=SiteConfig(c_deg=0.0, e_throughput_max_per_day=28.288),
    tariff=LEGACY_TARIFF,
)
"""Configuration reproducing the original `smart_weekly.py`: hourly, no degradation price,
hard daily throughput cap, no terminal value, exact binaries. Used by the regression test."""
