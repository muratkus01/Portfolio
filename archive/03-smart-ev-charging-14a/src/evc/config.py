"""Configuration for the charging-hub problem.

Three site archetypes share one model. What distinguishes them is not the hardware but the
*slack*: how much dwell time exists relative to the energy that must be delivered. That ratio
determines whether s14a dimming is free or expensive, and it is the variable the whole study
turns on.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class SiteConfig:
    """EV charging hub site configuration.

    Note on planned hardware extensions:
    The following fields are declared for planned hardware extensions and are not
    yet read by the baseline charging heuristics:
    - pv_kwp: co-located on-site rooftop PV peak capacity (kWp)
    - buffer_kwh: stationary battery buffer capacity (kWh); pinned to 0.0 in sweeps
    - buffer_kw: stationary battery peak charge/discharge power (kW)
    """
    n_connectors: int = 40
    p_connector_kw: float = 11.0        # AC wallbox, three-phase
    p_min_kw: float = 4.14              # minimum charging current (6 A, 3-phase, 230 V: 3 * 230 * 6 = 4.14 kW)
    site_limit_kw: float = 250.0        # grid connection capacity
    pv_kwp: float = 0.0                 # declared for planned PV integration extension
    buffer_kwh: float = 0.0             # declared for planned stationary BESS extension
    buffer_kw: float = 0.0              # declared for planned stationary BESS extension

    # A charge point cannot deliver an arbitrarily small power: below roughly 6 A per phase
    # the vehicle stops charging. The action set is therefore {0} union [p_min, p_max], which
    # is what makes the allocation problem genuinely integer rather than a simple LP.
    enforce_min_current: bool = True


@dataclass(frozen=True)
class TariffConfig:
    """Energy, network and peak charges, plus the THG-Quote revenue line.

    Note on planned pricing extensions:
    - spot_passthrough: declared for planned fixed-tariff comparison; currently all models
      receive dynamic day-ahead spot pricing.
    """
    spot_passthrough: bool = True       # declared for planned fixed-tariff comparison
    supplier_margin: float = 0.025      # EUR/kWh commercial supplier margin (BNetzA 2024)
    network_energy: float = 0.060       # EUR/kWh low-voltage network fee (DSO average 2024/2025)
    levies: float = 0.015               # EUR/kWh statutory surcharges: KWKG, § 19 StromNEV, Offshore (2024/2025)
    electricity_tax: float = 0.02050    # EUR/kWh standard electricity tax (§ 3 StromStG, 2024/2025)
    vat: float = 0.0                    # commercial depot sites reclaim VAT (UStG § 15)

    # Peak charge: prices the single worst quarter-hour of the billing period (§ 19(2) StromNEV, 2024/2025).
    # This is what makes the objective non-separable across the whole period, and it is why the MPC
    # baseline must carry a running-peak state variable rather than optimise horizon-locally.
    peak_charge_eur_kw_year: float = 120.0

    # THG-Quote: revenue per kWh charged (38. BImSchV greenhouse gas mitigation quota, 2024).
    # Pulls in the OPPOSITE direction to peak shaving - it rewards charging MORE.
    thg_eur_kwh: float = 0.06

    # § 14a EnWG module choices (BNetzA Festlegung BK6-22-300 / BK8-22/010-A, in force 1 Jan 2024)
    para_14a_module: str = "none"       # none | module_1 | module_2 | module_3
    module_1_annual_reduction: float = 190.0  # EUR/year flat rebate (BNetzA Module 1 standard)
    module_2_reduction: float = 0.60          # 60% network charge reduction on energy component
    module_3_factors: tuple[float, float, float] = (0.4, 1.0, 1.9)
    module_3_high_hours: tuple[int, ...] = (7, 8, 9, 17, 18, 19, 20)
    module_3_low_hours: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 11, 12, 13, 14)


@dataclass(frozen=True)
class Para14aConfig:
    """The dimming interrupt (§ 14a EnWG, BNetzA determination, in force 1 Jan 2024)."""
    enabled: bool = False
    p_min_total_kw: float = 4.2         # guaranteed minimum for controllable installation (BNetzA § 14a mandate)
    events_per_year: float = 60.0
    mean_duration_h: float = 2.0
    concentration_hours: tuple[int, ...] = (17, 18, 19, 20)
    seed: int = 0


@dataclass(frozen=True)
class ArchetypeConfig:
    """Session-generation parameters defining a site archetype."""
    name: str = "depot"
    arrivals_per_day: float = 40.0
    arrival_hour_mean: float = 17.0     # depot: vehicles return in the evening
    arrival_hour_sd: float = 1.5
    dwell_hours_mean: float = 12.0
    dwell_hours_sd: float = 2.0
    energy_kwh_mean: float = 45.0
    energy_kwh_sd: float = 15.0
    # Declared departures are systematically PESSIMISTIC - drivers state an earlier time than
    # they actually leave. A policy that learns the site's own distribution can exploit that,
    # but only if the safety layer still guarantees the DECLARED deadline.
    declaration_bias_h: float = 1.5
    declaration_noise_h: float = 1.0


DEPOT = ArchetypeConfig(
    name="depot", arrivals_per_day=40, arrival_hour_mean=17.5, arrival_hour_sd=1.2,
    dwell_hours_mean=13.0, dwell_hours_sd=1.5, energy_kwh_mean=55.0, energy_kwh_sd=18.0)

WORKPLACE = ArchetypeConfig(
    name="workplace", arrivals_per_day=30, arrival_hour_mean=8.0, arrival_hour_sd=1.0,
    dwell_hours_mean=8.5, dwell_hours_sd=1.5, energy_kwh_mean=22.0, energy_kwh_sd=10.0,
    declaration_bias_h=0.5)

APARTMENT = ArchetypeConfig(
    name="apartment", arrivals_per_day=25, arrival_hour_mean=19.0, arrival_hour_sd=2.5,
    dwell_hours_mean=12.5, dwell_hours_sd=3.0, energy_kwh_mean=18.0, energy_kwh_sd=9.0,
    declaration_bias_h=2.5, declaration_noise_h=2.0)

ARCHETYPES = {a.name: a for a in (DEPOT, WORKPLACE, APARTMENT)}


@dataclass(frozen=True)
class RunConfig:
    """Run configuration for EV charging hub simulations.

    Note on planned multi-objective extensions:
    - fairness_weight: declared for planned proportional-fair allocation extension;
      currently active baselines use earliest-deadline-first or price-greedy allocation.
    """
    dt: float = 0.25
    horizon_steps: int = 96
    days: int = 14
    seed: int = 0
    fairness_weight: float = 0.0        # declared for planned proportional-fair allocation extension

    site: SiteConfig = field(default_factory=SiteConfig)
    tariff: TariffConfig = field(default_factory=TariffConfig)
    para_14a: Para14aConfig = field(default_factory=Para14aConfig)
    archetype: ArchetypeConfig = field(default_factory=lambda: DEPOT)

    @property
    def steps_per_day(self) -> int:
        return int(round(24.0 / self.dt))

    def with_(self, **kwargs) -> "RunConfig":
        return replace(self, **kwargs)
