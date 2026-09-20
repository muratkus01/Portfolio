"""Configuration for the pumped-storage multi-market problem.

Default plant parameters describe a publicly-derived reference PSW of the size class common
in Germany (Marktstammdatenregister + JRC Hydro-power database + operator technical
publications). No confidential operator data is used anywhere, which is what makes every
result here publishable and independently reproducible - at the cost of fidelity, which is
handled by reporting sensitivity to the uncertain parameters rather than hiding them.

Reservoir state is carried as ENERGY (MWh) rather than volume. At a fixed head that is an
exact change of variable; with head variation it is a linearisation whose error is quantified
by `head_sensitivity` and reported as an ablation.

Sign convention:
    p > 0   TURBINE  (generating, draws energy from the upper reservoir)
    p < 0   PUMP     (consuming, fills the upper reservoir)
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class PlantConfig:
    """Reference pumped-storage plant."""

    # --- reservoir ---
    e_max: float = 2400.0        # MWh usable upper-reservoir energy content (~8 h at rated)
    e_min: float = 120.0         # MWh minimum operating content (water-law + intake cover)
    e_init: float = 1200.0       # MWh
    inflow_mw: float = 5.0       # MW-equivalent natural inflow (small for a closed-loop plant)

    # --- units ---
    n_units: int = 4
    p_turb_max_unit: float = 75.0    # MW per unit generating
    p_turb_min_unit: float = 25.0    # MW per unit (below this, cavitation / rough zone)
    p_pump_unit: float = 78.0        # MW per unit pumping (fixed-speed pumps are near-constant)
    eta_turb: float = 0.90
    eta_pump: float = 0.88

    # --- dynamics ---
    ramp_mw_per_step: float = 150.0  # MW change permitted per 15-min step
    mode_change_cost: float = 300.0  # EUR per pump<->turbine transition (wear, water, time)
    start_cost: float = 120.0        # EUR per unit start
    min_up_steps: int = 2            # 30 min
    min_down_steps: int = 2

    # --- regulatory switches ---
    para_118_6_exempt: bool = True   # s118(6) EnWG network-charge exemption for pumping
    network_charge_pump: float = 12.0  # EUR/MWh applied when the exemption does NOT hold

    # --- water law (HARD constraints, enforced in the safety layer, never in the reward) ---
    e_reserve_low: float = 200.0     # MWh operating floor above the absolute minimum
    e_reserve_high: float = 2300.0   # MWh operating ceiling below the absolute maximum

    head_sensitivity: float = 0.0    # d(eta)/d(fill fraction); 0 = constant-head idealisation

    @property
    def p_turb_max(self) -> float:
        return self.n_units * self.p_turb_max_unit

    @property
    def p_pump_max(self) -> float:
        return self.n_units * self.p_pump_unit

    @property
    def eta_round_trip(self) -> float:
        return self.eta_turb * self.eta_pump


@dataclass(frozen=True)
class MarketConfig:
    """Products the plant may participate in, and their mechanics."""

    # --- balancing capacity (regelleistung.net, 4-hour product blocks) ---
    afrr_enabled: bool = True
    block_steps: int = 16                 # 4 h at 15-min resolution
    afrr_min_bid_mw: float = 5.0          # prequalification minimum bid size
    # Capacity prices are modelled as exogenous (the plant is a price taker) and acceptance
    # is certain at the clearing price. Endogenous bid-price strategy is a documented
    # extension, not part of the groundwork.
    afrr_pos_capacity_eur_mw_h: float = 12.0
    afrr_neg_capacity_eur_mw_h: float = 8.0

    # --- activation (PICASSO merit order; stochastic from the bidder's point of view) ---
    afrr_pos_activation_rate: float = 0.09   # expected share of sold POS capacity called
    afrr_neg_activation_rate: float = 0.07
    activation_rho: float = 0.7              # activation is autocorrelated, not white
    # Activated aFRR energy settles at the balancing energy price; modelled as a spread on
    # the day-ahead price, which is the cheap-but-honest stand-in until regelleistung.net
    # energy prices are wired in.
    afrr_pos_energy_premium: float = 40.0    # EUR/MWh above day-ahead for upward energy
    afrr_neg_energy_discount: float = 30.0   # EUR/MWh below day-ahead for downward energy

    # --- imbalance ---
    rebap_enabled: bool = True
    rebap_sigma: float = 60.0                # EUR/MWh spread of the imbalance price

    # --- redispatch / s13 EnWG override ---
    redispatch_prob_per_step: float = 0.0005


@dataclass(frozen=True)
class RunConfig:
    dt: float = 0.25
    horizon_steps: int = 192          # 48 h - a PSW reservoir cycle is longer than a day
    # Off by default, on evidence: with it, B3 ended every run with the reservoir nearly full
    # (2,220 of 2,300 MWh) and recovered about 22% of the headroom; without it, 83%. A linear
    # terminal value drives storage to a bound. Re-solving every step instead of hourly moved
    # the result by under one point, so `resolve_every=4` stays the default for speed.
    terminal_value: bool = False
    terminal_price: float | None = None
    lam: float = 0.0                  # 0 = pure revenue, 1 = pure grid-security posture
    forecast_sigma: float = 0.12
    forecast_rho: float = 0.75
    seed: int = 0

    plant: PlantConfig = field(default_factory=PlantConfig)
    market: MarketConfig = field(default_factory=MarketConfig)

    @property
    def steps_per_day(self) -> int:
        return int(round(24.0 / self.dt))

    def with_(self, **kwargs) -> "RunConfig":
        return replace(self, **kwargs)
