"""The safety layer with aFRR reservations: headroom, energy bands and reserve not held.

The battery is the default one: 20 MW, 40 MWh, a state of charge from 2 to 38 MWh and
efficiencies of 0.95. Every test here fails on 87ce39a, whose safety layer takes no
reservation: plant has no Reservation and reserve no ReserveRule, so the module does not
import, and battery_interval, dispatch_step and simulate take no reservation.
"""
from __future__ import annotations

import numpy as np
import pytest

from hybrid import plant
from hybrid.config import MarketConfig, PlantConfig
from hybrid.plant import (TOL, TOL_SOC, Reservation, battery_interval, check_feasible,
                          dispatch_step, simulate, soc_next)
from hybrid.reserve import ReserveRule

DT = 0.25
CFG = PlantConfig()
MARKET = MarketConfig()
# the default rule: 4 hours of positive reserve, 1 hour of negative, at least 15 minutes of
# the battery's 20 MW
RULE = ReserveRule(MARKET.afrr_energy_hours_up, MARKET.afrr_energy_hours_down,
                   MARKET.afrr_floor_hours * CFG.bess_mw)
TODAYS_CHECKS = ["soc_low", "soc_high", "bess_rating", "export_cap", "grid_charging",
                 "negative_curtailment"]


def _up_band(mw: float, rule: ReserveRule = RULE) -> float:
    """The lowest state of charge, MWh, at which mw of positive reserve is held in full."""
    return CFG.soc_min + max(mw * rule.hours_up, rule.floor_mwh) / CFG.eta_d


def _down_ceiling(mw: float, rule: ReserveRule = RULE) -> float:
    """The highest state of charge, MWh, at which mw of negative reserve is held in full."""
    return CFG.soc_max - max(mw * rule.hours_down, rule.floor_mwh) * CFG.eta_c


def test_random_setpoints_never_use_reserved_headroom():
    """Whatever the controller proposes, the battery keeps the power headroom both
    reservations need, export keeps the positive one's MW free below the connection, the
    plant never imports, the battery charges only from export above the negative
    reservation, and its state of charge stays within its limits: random proposals,
    curtailment, whole-MW reservations and starting states of charge, at three connections.
    Where both reservations together exceed the connection, export is held at the
    connection less the positive one, below the negative one, and the battery may still
    store what the connection would curtail.

    Negative control: today's layer, which ignores the reservations, uses that headroom on
    the same inputs, e.g. by discharging 20 MW while 15 MW of positive reserve are held.
    """
    rng = np.random.default_rng(7)
    n = 2000
    for conn in (20.0, 30.0, 60.0):
        cfg = PlantConfig(conn_mw=conn)
        wind, pv = rng.uniform(0, 50, n), rng.uniform(0, 30, n) * (rng.random(n) < 0.6)
        p_bat, curt = rng.uniform(-30, 30, n), rng.random(n) * (rng.random(n) < 0.3)
        up, down = (rng.integers(0, 21, n).astype(float) for _ in range(2))
        soc0 = rng.uniform(cfg.soc_min, cfg.soc_max)

        res = simulate(wind, pv, p_bat, curt, DT, cfg, soc0=soc0, reserve_up=up,
                       reserve_down=down, rule=RULE)
        today = simulate(wind, pv, p_bat, curt, DT, cfg, soc0=soc0)

        assert check_feasible(res, cfg) == dict.fromkeys(
            TODAYS_CHECKS + ["reserve_power", "reserve_connection"], 0)
        assert np.all(res["p_bat"] + up <= cfg.bess_mw + 1e-9)
        assert np.all(down - res["p_bat"] <= cfg.bess_mw + 1e-9)
        assert np.all(res["export"] + up <= conn + 1e-9)
        assert res["import"].max() == 0.0
        charging = res["p_bat"] < 0
        least_export = np.minimum(down, np.maximum(0.0, conn - up))
        assert np.all(res["export"][charging] >= least_export[charging] - 1e-9)
        assert cfg.soc_min - 1e-9 <= res["soc"].min() <= res["soc"].max() <= cfg.soc_max + 1e-9
        assert np.any(today["p_bat"] + up > cfg.bess_mw + 1e-6)
        assert np.any(today["export"] + up > conn + 1e-6)


def test_a_reservation_restricts_the_battery_but_never_forces_it():
    """Restrict, never force: below the band of its positive reserve the battery may idle or
    charge but not discharge; above the ceiling of its negative reserve it may idle or
    discharge but not charge; with no generation it is not made to discharge so as to export
    its negative reserve. 0 MW is always in the interval."""
    below, above = _up_band(6.0) - 1.0, _down_ceiling(6.0) + 1.0
    up, down = Reservation(6.0, 0.0, RULE), Reservation(0.0, 6.0, RULE)

    lo, hi = battery_interval(below, DT, CFG, up)
    assert hi == 0.0 < -lo
    assert dispatch_step(10.0, 0.0, below, 15.0, 0.0, DT, CFG, reserve=up)["p_bat"] == 0.0
    lo, hi = battery_interval(above, DT, CFG, down)
    assert lo == 0.0 < hi
    assert dispatch_step(40.0, 0.0, above, -15.0, 0.0, DT, CFG, reserve=down)["p_bat"] == 0.0
    night = dispatch_step(0.0, 0.0, 20.0, 0.0, 0.0, DT, CFG, reserve=down)
    assert (night["p_bat"], night["export"]) == (0.0, 0.0)

    rng = np.random.default_rng(3)
    for _ in range(2000):
        reserve = Reservation(float(rng.integers(0, 21)), float(rng.integers(0, 21)), RULE)
        lo, hi = battery_interval(rng.uniform(CFG.soc_min, CFG.soc_max), DT, CFG, reserve)
        assert lo <= 0.0 <= hi


def test_a_reservation_beyond_the_plants_limits_is_counted_never_forced():
    """No plan reserves more than the battery's 20 MW, or more positive reserve than the
    connection, but the layer must not turn such a reservation into a forced move. Above
    the rating, positive reserve only stops discharging and negative reserve only stops
    charging. Above the connection, positive reserve only stops export: at 20 MW, 25 MW of
    it leave 40 MW of wind to charge the battery's 20 MW and be curtailed, never to import
    or to run below 0. check_feasible counts the headroom such a result lacks, in every step.
    """
    beyond_up, beyond_down = Reservation(25.0, 0.0, RULE), Reservation(0.0, 25.0, RULE)
    for soc in (CFG.soc_min, 20.0, CFG.soc_max):
        lo, hi = battery_interval(soc, DT, CFG, beyond_up)
        assert lo <= 0.0 == hi
        lo, hi = battery_interval(soc, DT, CFG, beyond_down)
        assert lo == 0.0 <= hi

    cfg = PlantConfig(conn_mw=20.0)
    windy = dispatch_step(40.0, 0.0, 20.0, 10.0, 0.0, DT, cfg, reserve=beyond_up)
    assert (windy["p_bat"], windy["export"], windy["import"]) == (-20.0, 0.0, 0.0)
    assert windy["gen"] == windy["reserve_curtail"] == 20.0

    n = 3
    res = simulate(np.array([0.0, 40.0, 10.0]), np.zeros(n), np.array([10.0, 10.0, -10.0]),
                   np.zeros(n), DT, cfg, soc0=20.0, reserve_up=np.full(n, 25.0),
                   reserve_down=np.array([0.0, 25.0, 25.0]), rule=RULE)
    assert check_feasible(res, cfg) == {**dict.fromkeys(TODAYS_CHECKS, 0), "reserve_power": n,
                                        "reserve_connection": n}


def test_positive_reserve_holds_four_hours_and_negative_one():
    """With the default rule, r MW of positive reserve keep soc_min + 4 r / eta_d in store
    and r MW of negative reserve keep soc_max - 1 r eta_c as the ceiling (PQ §§ 1.6, 2.7.1):
    discharging as far as the interval allows ends on the band, less TOL_SOC, and charging
    as far as it allows ends on the ceiling, plus TOL_SOC; neither leaves reserve unheld.
    At 6 MW the 5 MWh floor binds in neither direction."""
    assert (RULE.hours_up, RULE.hours_down, RULE.floor_mwh) == (4.0, 1.0, 5.0)
    band, ceiling = CFG.soc_min + 4 * 6.0 / CFG.eta_d, CFG.soc_max - 6.0 * CFG.eta_c

    for reserve, soc, end in ((Reservation(6.0, 0.0, RULE), band + 0.5, band - TOL_SOC),
                              (Reservation(0.0, 6.0, RULE), ceiling - 0.5, ceiling + TOL_SOC)):
        lo, hi = battery_interval(soc, DT, CFG, reserve)
        p_bat = hi if reserve.up else lo
        assert soc_next(soc, p_bat, DT, CFG) == pytest.approx(end, abs=1e-12)
        r = dispatch_step(10.0, 0.0, soc, 3 * p_bat, 0.0, DT, CFG, reserve=reserve)
        assert r["p_bat"] == p_bat
        assert (r["afrr_deficit_up"], r["afrr_deficit_down"]) == (0.0, 0.0)


def test_the_fifteen_minute_floor_binds_below_five_megawatts():
    """Each reserved direction keeps at least 15 minutes of the battery's 20 MW, 5 MWh (PQ
    § 2.7.1, per product as v1.06 § 2.7.2 states): 2 MW of negative reserve keep 5 MWh x eta_c
    of room, not 2 MWh x eta_c, and 1 MW of positive reserve 5 MWh, not 4. With A MWh of room
    left, the 2 MW hold 2 A / 5, so 2 (1 - A / 5) are not held; with A MWh in store, the 1 MW
    hold A / 5, so 4.5 MWh leave 0.1 MW not held, which 4 hours alone would count as held.
    A floor of 0, as B2 plans, relaxes both."""
    no_floor = ReserveRule(4.0, 1.0, 0.0)
    soc = 32.85
    for rule, ceiling in ((RULE, CFG.soc_max - 5.0 * CFG.eta_c),
                          (no_floor, CFG.soc_max - 2.0 * CFG.eta_c)):
        lo, _ = battery_interval(soc, DT, CFG, Reservation(0.0, 2.0, rule))
        assert soc_next(soc, lo, DT, CFG) == pytest.approx(ceiling + TOL_SOC, abs=1e-12)
    assert _up_band(1.0) == pytest.approx(CFG.soc_min + 5.0 / CFG.eta_d)
    for rule in (RULE, no_floor):
        soc = _up_band(1.0, rule) + 0.1
        _, hi = battery_interval(soc, DT, CFG, Reservation(1.0, 0.0, rule))
        assert hi == pytest.approx((0.1 + TOL_SOC) * CFG.eta_d / DT)

    room = (CFG.soc_max - 35.0) / CFG.eta_c
    held = dispatch_step(10.0, 0.0, 35.0, 0.0, 0.0, DT, CFG,
                         reserve=Reservation(0.0, 2.0, RULE))
    assert held["afrr_deficit_down"] == pytest.approx(2.0 * (1 - room / 5.0))
    relaxed = dispatch_step(10.0, 0.0, 35.0, 0.0, 0.0, DT, CFG,
                            reserve=Reservation(0.0, 2.0, no_floor))
    assert relaxed["afrr_deficit_down"] == 0.0

    store = 4.5                                     # MWh the battery can deliver
    soc = CFG.soc_min + store / CFG.eta_d
    held_up, relaxed_up = (dispatch_step(10.0, 0.0, soc, 0.0, 0.0, DT, CFG,
                                         reserve=Reservation(1.0, 0.0, rule))
                           for rule in (RULE, no_floor))
    assert held_up["afrr_deficit_up"] == pytest.approx(1.0 * (1 - store / 5.0))
    assert relaxed_up["afrr_deficit_up"] == 0.0


def test_upward_reserve_keeps_connection_headroom_and_labels_its_curtailment():
    """Positive reserve keeps its MW free below the connection, so that a call could be
    exported. 70 MW behind 60 MW, with a battery that can take 4.2 MW: without reserve it
    charges those and the connection forces the remaining 5.8 MW away; with 5 MW of positive
    reserve it charges the same, export stops at 55 MW, the forced curtailment is the same,
    and the 5 MW more it needs are "reserve_curtail", neither forced nor chosen. A battery
    with room stores the excess over 55 MW first: 62 MW charge 7 MW and nothing is
    curtailed."""
    soc = CFG.soc_max - 1.0
    plain = dispatch_step(50.0, 20.0, soc, 0.0, 0.0, DT, CFG)
    held = dispatch_step(50.0, 20.0, soc, 0.0, 0.0, DT, CFG,
                         reserve=Reservation(5.0, 0.0, RULE))

    absorbed = 1.0 / (CFG.eta_c * DT)               # the 1 MWh of room, as MW over a step
    assert held["p_bat"] == plain["p_bat"] == pytest.approx(-absorbed)
    assert (plain["export"], held["export"]) == (60.0, 55.0)
    assert held["forced_curtail"] == plain["forced_curtail"] == pytest.approx(10.0 - absorbed)
    assert held["reserve_curtail"] == pytest.approx(5.0)
    assert held["chosen_curtail"] == plain["chosen_curtail"] == 0.0

    stored = dispatch_step(62.0, 0.0, 25.0, 0.0, 0.0, DT, CFG,
                           reserve=Reservation(5.0, 0.0, RULE))
    assert (stored["p_bat"], stored["export"]) == (-7.0, 55.0)
    assert [stored[k] for k in ("forced_curtail", "reserve_curtail", "chosen_curtail")] == [
        0.0, 0.0, 0.0]


def test_negative_reserve_never_draws_from_the_grid():
    """A negative call is served by exporting less, so the battery charges only from export
    above the negative reservation (R4): at night, asked to charge, it does not, even where
    grid charging would be allowed, and the reservation's shortfall is what export lacks,
    r_dn - export. With 10 MW of wind it charges from the 4 MW above 6 MW of reserve. The
    export counts after the controller's own curtailment: of 20 MW of wind, half curtailed,
    it charges the same 4 MW; of 10 MW, half curtailed, it charges nothing, and the 1 MW the
    5 MW of export lack is not held."""
    down = Reservation(0.0, 6.0, RULE)
    for cfg in (CFG, PlantConfig(grid_charging_allowed=True)):
        night = dispatch_step(0.0, 0.0, 20.0, -10.0, 0.0, DT, cfg, reserve=down)
        assert (night["import"], night["p_bat"], night["afrr_deficit_down"]) == (0.0, 0.0, 6.0)

    dusk = dispatch_step(0.0, 0.0, 20.0, 2.5, 0.0, DT, CFG, reserve=down)
    assert (dusk["export"], dusk["afrr_deficit_down"]) == (2.5, 3.5)
    windy = dispatch_step(10.0, 0.0, 20.0, -10.0, 0.0, DT, CFG, reserve=down)
    assert (windy["p_bat"], windy["export"], windy["afrr_deficit_down"]) == (-4.0, 6.0, 0.0)

    curtailed = dispatch_step(20.0, 0.0, 20.0, -10.0, 0.5, DT, CFG, reserve=down)
    assert (curtailed["p_bat"], curtailed["export"], curtailed["afrr_deficit_down"]) == (
        -4.0, 6.0, 0.0)
    short = dispatch_step(10.0, 0.0, 20.0, -10.0, 0.5, DT, CFG, reserve=down)
    assert (short["p_bat"], short["export"], short["afrr_deficit_down"]) == (0.0, 5.0, 1.0)


def test_reserve_not_held_is_measured_at_both_ends_of_a_step(monkeypatch):
    """A reservation holds at every instant of its step, and the state of charge is linear in
    it, so reserve not held is measured at both ends. The start counts where a step begins
    past its band and moves back, as at a product boundary; the end counts where a step ends
    past its band, which only a slack wider than TOL_SOC allows, so the test widens it to
    0.5 MWh. Following a plan on the band leaves a residue below TOL (1e-4 MW), which counts
    as held. TOL lies between 5e-5 MW, which counts as held, and 2e-4 MW, which counts in
    full. 6 MW each way: the up band needs 24 MWh, the down band 6 MWh of room."""
    band, up = _up_band(6.0), Reservation(6.0, 0.0, RULE)
    ceiling, down = _down_ceiling(6.0), Reservation(0.0, 6.0, RULE)

    def short_up(soc):
        return 6.0 * (1 - (soc - CFG.soc_min) * CFG.eta_d / 24.0)

    def short_down(soc):
        return 6.0 * (1 - (CFG.soc_max - soc) / CFG.eta_c / 6.0)

    rises = dispatch_step(30.0, 0.0, band - 2.0, -10.0, 0.0, DT, CFG, reserve=up)
    falls = dispatch_step(30.0, 0.0, ceiling + 2.0, 10.0, 0.0, DT, CFG, reserve=down)
    assert rises["soc"] > band and falls["soc"] < ceiling
    assert rises["afrr_deficit_up"] == pytest.approx(short_up(band - 2.0))
    assert falls["afrr_deficit_down"] == pytest.approx(short_down(ceiling + 2.0))

    on_band = dispatch_step(0.0, 0.0, band + 0.1, 20.0, 0.0, DT, CFG, reserve=up)
    assert 0.0 < short_up(on_band["soc"]) < TOL and on_band["afrr_deficit_up"] == 0.0

    # numbers, not the imported TOL, so that a changed threshold fails here
    starts = [band - short * 24.0 / 6.0 / CFG.eta_d for short in (5e-5, 2e-4)]
    assert [short_up(s) for s in starts] == pytest.approx([5e-5, 2e-4])
    below, above = (dispatch_step(30.0, 0.0, s, -10.0, 0.0, DT, CFG, reserve=up) for s in starts)
    assert below["afrr_deficit_up"] == 0.0
    assert above["afrr_deficit_up"] == pytest.approx(2e-4)

    monkeypatch.setattr(plant, "TOL_SOC", 0.5)
    past_up = dispatch_step(0.0, 0.0, band + 0.1, 20.0, 0.0, DT, CFG, reserve=up)
    past_down = dispatch_step(30.0, 0.0, ceiling - 0.1, -20.0, 0.0, DT, CFG, reserve=down)
    assert (past_up["soc"], past_down["soc"]) == (pytest.approx(band - 0.5),
                                                  pytest.approx(ceiling + 0.5))
    assert past_up["afrr_deficit_up"] == pytest.approx(short_up(band - 0.5))
    assert past_down["afrr_deficit_down"] == pytest.approx(short_down(ceiling + 0.5))


def test_a_zero_reservation_equals_none():
    """Reservation(0, 0, rule) is today's dispatch, value for value: it keeps no headroom,
    band or floor and limits no charging, with and without grid charging and export caps;
    its reserve curtailment and shortfalls are 0. simulate with zero reservations returns
    today's arrays as well."""
    rng = np.random.default_rng(11)
    zero = Reservation(0.0, 0.0, RULE)
    for grid in (False, True):
        cfg = PlantConfig(conn_mw=30.0, grid_charging_allowed=grid)
        for _ in range(500):
            args = (rng.uniform(0, 50), rng.uniform(0, 30) * (rng.random() < 0.5),
                    rng.uniform(cfg.soc_min, cfg.soc_max), rng.uniform(-30, 30),
                    rng.random() * (rng.random() < 0.3), DT, cfg,
                    rng.uniform(5, 40) if rng.random() < 0.3 else None)
            today, held = dispatch_step(*args), dispatch_step(*args, reserve=zero)
            assert {k: held[k] for k in today} == today
            assert [held[k] for k in ("reserve_curtail", "afrr_deficit_up",
                                      "afrr_deficit_down")] == [0.0, 0.0, 0.0]

    n = 400
    wind, pv, p_bat = rng.uniform(0, 50, n), rng.uniform(0, 30, n), rng.uniform(-30, 30, n)
    today = simulate(wind, pv, p_bat, np.zeros(n), DT, CFG)
    held = simulate(wind, pv, p_bat, np.zeros(n), DT, CFG, reserve_up=np.zeros(n),
                    reserve_down=np.zeros(n), rule=RULE)
    assert all(np.array_equal(held[k], today[k]) for k in today)


def test_check_feasible_counts_reserved_headroom_a_result_used():
    """Headroom a result used despite its reservations is a broken hard limit, which
    check_feasible counts and every table requires to be 0: battery power plus either
    reservation above the rating, and export plus positive reserve above the connection.
    A result without reservations keeps today's checks."""
    res = {"soc": np.full(3, 20.0), "p_bat": np.array([15.0, -15.0, 0.0]),
           "export": np.array([50.0, 20.0, 58.0]), "import": np.zeros(3),
           "chosen_curtail": np.zeros(3), "forced_curtail": np.zeros(3),
           "afrr_up": np.array([6.0, 0.0, 3.0]), "afrr_down": np.array([0.0, 6.0, 0.0])}

    assert check_feasible(res, CFG) == {**dict.fromkeys(TODAYS_CHECKS, 0), "reserve_power": 2,
                                        "reserve_connection": 1}
    del res["afrr_up"], res["afrr_down"]
    assert list(check_feasible(res, CFG)) == TODAYS_CHECKS


def test_simulate_refuses_reservations_it_cannot_read():
    """Reservations come as one finite value of at least 0 MW per step in each direction,
    together with their rule; anything else is refused, not guessed at."""
    n, zeros = 4, np.zeros(4)
    args = (zeros, zeros, zeros, zeros, DT, CFG)
    for kwargs in ({"reserve_up": zeros}, {"reserve_up": zeros, "reserve_down": zeros},
                   {"reserve_up": zeros, "reserve_down": zeros[:3], "rule": RULE},
                   {"reserve_up": np.array([0.0, -1.0, 0.0, 0.0]), "reserve_down": zeros,
                    "rule": RULE},
                   {"reserve_up": zeros, "reserve_down": np.full(n, np.nan), "rule": RULE}):
        with pytest.raises(ValueError, match=r"reserve_up|reserve_down"):
            simulate(*args, **kwargs)


def test_the_afrr_settings_refuse_what_their_rules_do_not_allow():
    """MarketConfig and ReserveRule refuse a basis nobody is paid on, bands of 0 hours or
    less, a negative floor, a penalty of 0 and values that are not finite; a floor of 0,
    which B2 plans with, is allowed."""
    for kwargs in ({"afrr_price_basis": "minimum"}, {"afrr_energy_hours_up": 0.0},
                   {"afrr_energy_hours_down": -1.0}, {"afrr_floor_hours": -0.25},
                   {"afrr_floor_hours": float("nan")}, {"afrr_energy_hours_up": float("inf")},
                   {"afrr_shortfall_penalty_eur_mwh": 0.0}):
        with pytest.raises(ValueError, match=next(iter(kwargs))):
            MarketConfig(**kwargs)
    for args in ((0.0, 1.0, 5.0), (4.0, float("nan"), 5.0), (4.0, 1.0, -1.0)):
        with pytest.raises(ValueError, match="ReserveRule"):
            ReserveRule(*args)
    assert MarketConfig(afrr_floor_hours=0.0).afrr_floor_hours == ReserveRule(4, 1, 0).floor_mwh


def test_no_flat_afrr_price_or_block_length_is_left_to_read():
    """The two settings declared before aFRR was built are gone: a flat capacity price, which
    no command may read now that prices come from regelleistung.net, and a block length in
    steps, as products follow Berlin clock time (`afrr_data.capacity_prices`)."""
    for name, value in (("afrr_capacity_eur_mw_h", 10.0), ("afrr_block_steps", 16)):
        with pytest.raises(TypeError, match=name):
            MarketConfig(**{name: value})
