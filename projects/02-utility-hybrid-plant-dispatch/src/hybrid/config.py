"""Configuration for the co-located wind + PV + battery plant.

The defining feature of the asset is that the grid connection is deliberately UNDERSIZED
relative to the sum of nameplate ratings, because wind and PV peaks rarely coincide. That
undersizing is both the source of the value and the source of the difficulty: when the peaks
do coincide, something must give - curtail, or charge the battery - and the right answer
depends on price, state of charge, forecast and remuneration regime.

Sign convention:
    battery p_bat > 0  DISCHARGING (adds to export)
                < 0    CHARGING
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class PlantConfig:
    """Co-located utility hybrid plant configuration.

    Note on planned ramp rate constraints:
    - ramp_mw_per_step: declared for planned grid-code compliance extensions;
      defaults to None (inverter-coupled plant operates without active ramp limits).
    """
    wind_mw: float = 50.0
    # A DC rating, MWp: data.load_year divides national PV generation by the DC fleet, so
    # pv_mw scales that factor to a plant of this many MWp.
    pv_mw: float = 30.0
    bess_mw: float = 20.0
    bess_mwh: float = 40.0
    conn_mw: float = 60.0            # point-of-interconnection export limit

    soc_min_frac: float = 0.05
    soc_max_frac: float = 0.95
    # None starts the battery at soc_min_frac, i.e. empty, and that is what the ladder needs.
    # No rung has an end-of-window state-of-charge condition, so energy found in the battery
    # at the start is sold as revenue nobody paid for: half full, that was 18 MWh, which every
    # rung with a battery sold and which counted as the battery's value. Starting empty needs
    # no price for stored energy.
    soc_init_frac: float | None = None
    eta_c: float = 0.95
    eta_d: float = 0.95
    c_deg_eur_mwh: float = 3.0       # EUR per MWh of throughput

    # Whether the battery may charge FROM THE GRID or only from co-located generation.
    # This is a regulatory and metering question, not a technical one, and the answer
    # materially changes the business case - hence a switch rather than an assumption.
    grid_charging_allowed: bool = False

    ramp_mw_per_step: float | None = None   # None = unlimited (inverter-coupled plant)

    def __post_init__(self) -> None:
        # A start outside the limits is a state the plant cannot be in, and the safety layer
        # forces the battery back: from below it charged, from the grid even where grid
        # charging is forbidden; from above it sold energy nobody paid for.
        if self.soc_init_frac is not None and not (
                self.soc_min_frac <= self.soc_init_frac <= self.soc_max_frac):
            raise ValueError(f"soc_init_frac {self.soc_init_frac} lies outside [soc_min_frac "
                             f"{self.soc_min_frac}, soc_max_frac {self.soc_max_frac}]")

    @property
    def installed_mw(self) -> float:
        """Installed generation: MW of wind plus MWp of PV (a DC rating, see `pv_mw`)."""
        return self.wind_mw + self.pv_mw

    @property
    def overbuild_ratio(self) -> float:
        """(MW wind + MWp PV) / MW connection. Above 1 the connection can bind and force
        curtailment."""
        return self.installed_mw / self.conn_mw

    @property
    def soc_min(self) -> float:
        return self.bess_mwh * self.soc_min_frac

    @property
    def soc_max(self) -> float:
        return self.bess_mwh * self.soc_max_frac

    @property
    def soc_init(self) -> float:
        frac = self.soc_min_frac if self.soc_init_frac is None else self.soc_init_frac
        return self.bess_mwh * frac


# Negative-price rules the settlement implements, oldest vintage first.
NEGATIVE_PRICE_RULES = ("none", "six_hour", "four_hour", "three_hour", "two_hour",
                        "one_hour", "new_2025")
# What an aFRR reservation can be paid on (`reserve.paid_prices`): B2 on any of them, as a
# sensitivity; B3, which bids, always on "bid".
AFRR_PRICE_BASES = ("bid", "average", "marginal")
# The shortest energy band, in hours of the reserved MW: PQ conditions v1.05 § 2.7.1 set 60
# minutes of the marketed power as the lower limit for FRR. The safety layer's slack on the
# bands (plant.TOL_SOC) is sized on it, so that following a band is never counted as reserve
# not held.
AFRR_MIN_ENERGY_HOURS = 1.0


@dataclass(frozen=True)
class MarketConfig:
    """EEG direct marketing, the negative-price rule, imbalance settlement and aFRR capacity.

    The afrr_* fields describe aFRR capacity reserved by the battery, Phase A: capacity in the
    12 daily 4-hour products, positive ("up") and negative ("down"), without activation. They
    set what a reservation is paid and how much energy it keeps in the battery. With
    afrr_enabled off, as by default, no rung reserves aFRR and they change no result.
    Raises ValueError for a setting outside what its rule allows.
    """

    # --- EEG market premium ---
    # Revenue = market price + premium, where premium = anzulegender Wert - the technology's
    # national market value of the calendar month, or of the calendar year for plants
    # commissioned from 2023 on and, if tendered, awarded from 2023 on (EEG 2023 Anlage 1
    # Nr. 2; market.market_values, market.premium_table).
    eeg_enabled: bool = True
    # The BNetzA ceilings (Höchstwerte) of the 2025 tenders, not an award: onshore wind
    # (EEG 2023 § 36) 7.35 ct/kWh, as in 2024; ground-mounted solar (§ 37) 6.80 ct/kWh, down
    # from 7.37 ct/kWh in 2024 (BNetzA press releases of 14 Dec 2023 and 19 Dec 2024).
    anzulegender_wert_wind: float = 73.5     # EUR/MWh
    anzulegender_wert_pv: float = 68.0       # EUR/MWh
    direct_marketing_fee: float = 3.0        # EUR/MWh standard trader management fee (2024/2025)

    # --- negative-price rule (EEG § 51 by vintage: commissioning or tender date, market.py) ---
    # "none"       : commissioned before 2016, premium always paid
    # "six_hour"   : 2016-2020, no premium for the WHOLE run once negative prices last 6 hours
    # "four_hour"  : 2021-2022, the same with 4 hours
    # "three_hour", "two_hour", "one_hour" : the 2023 to 24 Feb 2025 vintage, whose threshold
    #                falls by calendar year (market.vintage_rules)
    # "new_2025"   : from 25 Feb 2025 (Solarspitzengesetz), no premium in ANY negative period
    negative_price_rule: str = "new_2025"

    # --- imbalance ---
    imbalance_enabled: bool = True
    rebap_sigma: float = 55.0                # EUR/MWh, heavy-tailed around the spot price

    # --- aFRR capacity reserved by the battery (Phase A: no activation) ---
    afrr_enabled: bool = False
    # What B2 is paid for a reservation: "bid", B3's bid where it was accepted; or, as
    # sensitivities, "average", the realised German average, or "marginal", the realised
    # German marginal (`reserve.paid_prices`). B3 is always paid its bid.
    afrr_price_basis: str = "bid"
    # Hours of the reserved MW kept in the battery while a product is reserved. Up: 4 hours,
    # what the battery delivers from store alone, as without grid charging no storage
    # management secures 4 hours of positive delivery (PQ conditions v1.05 §§ 1.6, 2.7.2,
    # 2.7.3); 1 hour is a sensitivity that needs such a concept. Down: 1 hour of room, as the
    # battery's storage management sells energy and needs no grid charging (§ 2.7.1). Neither
    # may be shorter than AFRR_MIN_ENERGY_HOURS.
    afrr_energy_hours_up: float = 4.0
    afrr_energy_hours_down: float = 1.0
    # At least 15 minutes of the PQ power, PlantConfig.bess_mw, per reserved direction: the
    # second lower limit of § 2.7.1, applied per product as v1.06 § 2.7.2 states. 0 drops it.
    afrr_floor_hours: float = 0.25
    # EUR per MWh of reserve not held, charged on top of the cut of its capacity payment. It
    # stands in for MfRRA's incentive component (§ 25 (2)(a)(i)(2)), whose intraday index
    # the model does not load, and lies above every capacity and day-ahead price of
    # 2024-2025, so no plan profits from holding less than it sold.
    afrr_shortfall_penalty_eur_mwh: float = 10_000.0

    # --- redispatch (§ 13a EnWG) ---
    redispatch_prob_per_step: float = 0.001
    redispatch_depth: float = 0.5            # fraction of export capacity ordered away

    def __post_init__(self) -> None:
        if self.negative_price_rule not in NEGATIVE_PRICE_RULES:
            raise ValueError(f"unknown negative_price_rule {self.negative_price_rule!r}")
        if self.afrr_price_basis not in AFRR_PRICE_BASES:
            raise ValueError(f"unknown afrr_price_basis {self.afrr_price_basis!r}; it is one "
                             f"of {AFRR_PRICE_BASES}")
        # a band below PQ's 60 minutes, or a penalty of 0, would let a reservation keep too
        # little energy, or let reserve not held cost nothing
        for name, least, inclusive in (
                ("afrr_energy_hours_up", AFRR_MIN_ENERGY_HOURS, True),
                ("afrr_energy_hours_down", AFRR_MIN_ENERGY_HOURS, True),
                ("afrr_floor_hours", 0.0, True),
                ("afrr_shortfall_penalty_eur_mwh", 0.0, False)):
            value = getattr(self, name)
            if not (math.isfinite(value) and (value >= least if inclusive else value > least)):
                bound = f"at least {least:g}" if inclusive else f"above {least:g}"
                raise ValueError(f"{name} must be finite and {bound}, not {value!r}")


@dataclass(frozen=True)
class RunConfig:
    dt: float = 0.25
    horizon_steps: int = 96
    # Off by default, on evidence: with a receding horizon of 12 h or more, re-solved every
    # step, the linear terminal value makes B3 hoard energy and fall below B1. The
    # b3-ablation command measures both settings; see `default_terminal_price`.
    terminal_value: bool = False
    terminal_price: float | None = None
    forecast_sigma: float = 0.15
    forecast_rho: float = 0.8
    # What B3's price-forecast error is sized on, EUR/MWh: the error's standard deviation is
    # forecast_sigma times this. It must be known before the window, or later prices reach
    # B3's earlier forecasts. None sizes it on the effective price of the first planning
    # horizon, the only prices B3's first plan sees (`baselines.make_forecasts`).
    forecast_price_scale: float | None = None
    seed: int = 0
    # B3 with aFRR derates the generation its plans count on for a reservation, the charging
    # that fills positive reserve's band and the export that covers negative reserve, by this
    # many of its forecast errors, forecast_sigma, and by all of it at most: 0.30 at today's
    # sigma (R8, `reserve.add_reserve_terms`). Set by B3's own forecast error, not tuned; 0
    # with perfect forecasts.
    afrr_forecast_margin_sigmas: float = 2.0

    plant: PlantConfig = field(default_factory=PlantConfig)
    market: MarketConfig = field(default_factory=MarketConfig)

    def __post_init__(self) -> None:
        sigmas = self.afrr_forecast_margin_sigmas
        if not (math.isfinite(sigmas) and sigmas >= 0):
            raise ValueError(f"afrr_forecast_margin_sigmas must be finite and at least 0, "
                             f"not {sigmas!r}")

    @property
    def afrr_derate(self) -> float:
        """The share by which B3 with aFRR derates the generation its reservations rest on:
        afrr_forecast_margin_sigmas x forecast_sigma, at most 1."""
        return min(1.0, self.afrr_forecast_margin_sigmas * self.forecast_sigma)

    @property
    def steps_per_day(self) -> int:
        return int(round(24.0 / self.dt))

    def with_(self, **kwargs) -> "RunConfig":
        return replace(self, **kwargs)
