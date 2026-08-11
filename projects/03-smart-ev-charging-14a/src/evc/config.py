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
    n_connectors: int = 40
    p_connector_kw: float = 11.0        # AC wallbox, three-phase
    p_min_kw: float = 4.14              # minimum charging current (6 A, 3-phase) - see below
    site_limit_kw: float = 250.0        # grid connection capacity
    pv_kwp: float = 0.0
    buffer_kwh: float = 0.0
    buffer_kw: float = 0.0

    # A charge point cannot deliver an arbitrarily small power: below roughly 6 A per phase
    # the vehicle stops charging. The action set is therefore {0} union [p_min, p_max], which
    # is what makes the allocation problem genuinely integer rather than a simple LP.
    enforce_min_current: bool = True


@dataclass(frozen=True)
class TariffConfig:
    """Energy, network and peak charges, plus the THG-Quote revenue line."""
    spot_passthrough: bool = True
    supplier_margin: float = 0.025      # EUR/kWh
    network_energy: float = 0.060       # EUR/kWh
    levies: float = 0.015
    electricity_tax: float = 0.02050
    vat: float = 0.0                    # commercial sites reclaim VAT

    # Peak charge: prices the single worst quarter-hour of the billing period. This is what
    # makes the objective non-separable across the whole period, and it is why the MPC
    # baseline must carry a running-peak state variable rather than optimise horizon-locally.
    peak_charge_eur_kw_year: float = 120.0

    # THG-Quote: revenue per kWh charged. Pulls in the OPPOSITE direction to peak shaving -
    # it rewards charging MORE - and that tension is part of what makes the objective
    # interesting rather than trivially "charge as late as possible".
    thg_eur_kwh: float = 0.06

    # s14a EnWG module
    para_14a_module: str = "none"       # none | module_1 | module_2 | module_3
    module_1_annual_reduction: float = 190.0
    module_2_reduction: float = 0.60
    module_3_factors: tuple[float, float, float] = (0.4, 1.0, 1.9)
    module_3_high_hours: tuple[int, ...] = (7, 8, 9, 17, 18, 19, 20)
    module_3_low_hours: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 11, 12, 13, 14)


@dataclass(frozen=True)
class Para14aConfig:
    """The dimming interrupt. No public dataset exists, so it is parameterised and swept."""
    enabled: bool = False
    p_min_total_kw: float = 4.2         # guaranteed minimum for the controllable installation
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
    dt: float = 0.25
    horizon_steps: int = 96
    days: int = 14
    seed: int = 0
    fairness_weight: float = 0.0        # >0 pushes toward proportional-fair allocation

    site: SiteConfig = field(default_factory=SiteConfig)
    tariff: TariffConfig = field(default_factory=TariffConfig)
    para_14a: Para14aConfig = field(default_factory=Para14aConfig)
    archetype: ArchetypeConfig = field(default_factory=lambda: DEPOT)

    @property
    def steps_per_day(self) -> int:
        return int(round(24.0 / self.dt))

    def with_(self, **kwargs) -> "RunConfig":
        return replace(self, **kwargs)
