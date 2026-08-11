"""Configuration for the energy-community problem.

The project's premise is that the **allocation rule is not neutral**: how shared generation is
attributed to members determines each member's price signal, which determines their behaviour,
which changes the community's physical operation. Everything regulatory is therefore a switch,
so that sensitivity to the still-unsettled German energy-sharing design is a reported result
rather than an obsolescence risk.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class MemberConfig:
    """One community member."""
    name: str
    annual_kwh: float                 # inflexible demand
    profile: str = "household"        # household | commercial
    pv_kwp: float = 0.0               # individually owned PV
    battery_kwh: float = 0.0
    battery_kw: float = 0.0
    has_heat_pump: bool = False
    has_ev: bool = False
    share_key: float = 1.0            # ownership share of the community assets


@dataclass(frozen=True)
class CommunityConfig:
    shared_pv_kwp: float = 120.0
    shared_battery_kwh: float = 100.0
    shared_battery_kw: float = 50.0
    eta_c: float = 0.95
    eta_d: float = 0.95
    feeder_limit_kw: float = 250.0    # LV feeder thermal limit at the transformer


@dataclass(frozen=True)
class RegimeConfig:
    """The legal route, as a set of switches.

    German energy sharing across the public grid was still moving through successive EnWG
    amendment drafts, so the enacted detail - above all how network charges and levies apply
    to shared energy - is uncertain and decisive. Rather than guess, the model parameterises
    it and reports sensitivity, which is the deliverable most useful to a policymaker.
    """
    name: str = "para_42b"            # individual | mieterstrom | para_42b | energy_sharing

    # what a member pays for a kWh received from within the community
    shared_network_charge: float = 0.0     # EUR/kWh; 0 under building-level supply
    shared_levies: float = 0.0
    shared_electricity_tax: float = 0.0
    shared_vat: float = 0.19

    # residual supply from the grid (full retail price build-up)
    grid_energy: float = 0.090
    grid_margin: float = 0.030
    grid_network_charge: float = 0.085
    grid_levies: float = 0.015
    grid_electricity_tax: float = 0.02050
    grid_vat: float = 0.19

    # export of surplus community generation
    feed_in_tariff: float = 0.0786
    mieterstrom_surcharge: float = 0.0     # s21(3) EEG, when applicable

    # internal price for shared energy, EUR/kWh, before the charges above
    internal_price: float = 0.20

    def __post_init__(self) -> None:
        if self.name not in {"individual", "mieterstrom", "para_42b", "energy_sharing"}:
            raise ValueError(f"unknown regime {self.name!r}")


REGIMES = {
    # Every member supplied separately; PV self-consumption only behind their own meter.
    "individual": RegimeConfig(name="individual", internal_price=0.0),
    # s21(3) EEG: landlord supplies tenants, surcharge payable, full supplier obligations.
    "mieterstrom": RegimeConfig(name="mieterstrom", mieterstrom_surcharge=0.0261,
                                shared_electricity_tax=0.02050, internal_price=0.24),
    # s42b EnWG (Solarpaket I, 2024): lighter-weight building-level sharing.
    "para_42b": RegimeConfig(name="para_42b", internal_price=0.20),
    # Grid-spanning energy sharing: network charges on shared energy are the decisive
    # unknown, so this regime is swept over that parameter rather than fixed.
    "energy_sharing": RegimeConfig(name="energy_sharing", shared_network_charge=0.030,
                                   shared_levies=0.015, shared_electricity_tax=0.02050,
                                   internal_price=0.18),
}


@dataclass(frozen=True)
class RunConfig:
    dt: float = 0.25
    days: int = 14
    seed: int = 0
    mechanism: str = "dynamic_proportional"
    # static_key | dynamic_proportional | optimisation | market
    obedient_members: bool = True     # False = members respond to their own price signal
    correlation: float = 0.35         # inter-member load correlation - see below

    community: CommunityConfig = field(default_factory=CommunityConfig)
    regime: RegimeConfig = field(default_factory=lambda: REGIMES["para_42b"])
    members: tuple[MemberConfig, ...] = ()

    @property
    def steps_per_day(self) -> int:
        return int(round(24.0 / self.dt))

    def with_(self, **kwargs) -> "RunConfig":
        return replace(self, **kwargs)


def default_members(n: int = 20, seed: int = 0) -> tuple[MemberConfig, ...]:
    """A mixed community: mostly households, a few commercial, varied flexibility.

    Heterogeneity is the point. A community of identical members has nothing to share - the
    value comes entirely from *complementarity* between profiles, which is why the load
    correlation parameter matters more than any other modelling choice here.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        commercial = i % 7 == 0
        out.append(MemberConfig(
            name=f"{'com' if commercial else 'hh'}{i:02d}",
            annual_kwh=float(rng.uniform(8000, 25000) if commercial
                             else rng.uniform(1800, 5200)),
            profile="commercial" if commercial else "household",
            pv_kwp=float(rng.choice([0.0, 0.0, 5.0, 8.0])),
            battery_kwh=float(rng.choice([0.0, 0.0, 0.0, 8.0])),
            battery_kw=5.0,
            has_heat_pump=bool(rng.random() < 0.35),
            has_ev=bool(rng.random() < 0.4),
            share_key=1.0,
        ))
    return tuple(out)
