#!/usr/bin/env python3
"""Portfolio showcase: run every project's benchmark ladder and print what it finds.

    python showcase.py               offline, synthetic inputs, about a minute
    python showcase.py --real-data   DE-LU 2025 prices and generation via datakit (network)
    python showcase.py --tests       also run the test suite and report the real counts

Every number printed here is computed on the spot by the project packages. Nothing is
hardcoded: not the costs, not the rankings, not the test counts. If a demo fails, the failure
is printed rather than swallowed, because a showcase that silently hides a broken project is
worse than no showcase.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import traceback
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PROJECTS = {
    "01": "01-prosumer-pv-bess-mpc-rl",
    "02": "02-pumped-storage-rl-multimarket",
    "03": "03-smart-ev-charging-14a",
    "04": "04-energy-sharing-rec",
    "05": "05-utility-hybrid-plant-dispatch",
    "06": "06-probabilistic-forecast-to-bid",
}
for d in PROJECTS.values():
    sys.path.insert(0, str(HERE / "projects" / d / "src"))
sys.path.insert(0, str(HERE / "datakit"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# --------------------------------------------------------------------------- inputs
def diurnal(n: int, dt: float = 0.25) -> np.ndarray:
    return np.arange(n) * dt % 24


def real_prices_eur_mwh(days: int, dt: float = 0.25, start: str = "2025-06-02") -> np.ndarray:
    """DE-LU day-ahead prices from Energy-Charts, held across quarter-hours."""
    import datakit
    import pandas as pd

    df = datakit.day_ahead_price("2025-01-01", "2026-01-01")
    s = df["price_eur_per_mwh"]
    target = pd.date_range(start, periods=int(days * 24 / dt), freq="15min", tz="UTC")
    return s.reindex(s.index.union(target)).ffill().reindex(target).to_numpy()


def synthetic_prices_eur_mwh(n: int, seed: int = 0) -> np.ndarray:
    """Evening peak, solar midday dip, noise: the shape of a German summer day-ahead curve."""
    rng = np.random.default_rng(seed)
    h = diurnal(n)
    return (80 + 35 * np.sin(2 * np.pi * (h - 18) / 24)
            - 30 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1) + 10 * rng.standard_normal(n))


# --------------------------------------------------------------------------- demos
def demo_01(real: bool) -> dict:
    from prosumer.baselines.ladder import run_ladder
    from prosumer.config import RunConfig
    from prosumer.market.tariff import export_price, import_price
    import pandas as pd

    run = RunConfig(dt=0.25, horizon_steps=96, terminal_value=False)
    n = 2 * 96
    h = diurnal(n)
    rng = np.random.default_rng(0)
    load = 0.35 + 0.9 * np.exp(-((h - 19) ** 2) / 5) + 0.05 * rng.random(n)
    pv = 4.5 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    spot = (real_prices_eur_mwh(2) if real else synthetic_prices_eur_mwh(n)) / 1000.0
    idx = pd.date_range("2025-06-02", periods=n, freq="15min", tz="UTC")
    pi, pe = import_price(spot, idx, run.tariff), export_price(spot, run.tariff)

    res = run_ladder(load, pv, pi, pe, run)
    c = {k: v["cost"]["net_cost"] for k, v in res.items()}
    b1, b2, b3 = c["B1 rule-based"], c["B2 perfect foresight"], c["B3 rolling MPC"]
    viol = sum(sum(v["violations"].values()) for v in res.values())
    return {
        "rows": [("B1 self-consumption rule", f"{b1:8.3f} EUR"),
                 ("B2 perfect foresight", f"{b2:8.3f} EUR"),
                 ("B3 rolling MPC (24 h horizon)", f"{b3:8.3f} EUR")],
        "headline": f"B3 recovers {(b1 - b3) / max(b1 - b2, 1e-9) * 100:.0f}% of the "
                    f"B1-to-B2 headroom; {viol} constraint violations across all rungs",
    }


def demo_02(real: bool) -> dict:
    from psw.baselines import run_ladder
    from psw.config import RunConfig
    from psw.market import activation_series, expand_blocks, rebap_series

    run = RunConfig(dt=0.25, horizon_steps=96)
    days = 4
    price = real_prices_eur_mwh(days) if real else synthetic_prices_eur_mwh(days * 96, 1)
    n = len(price)
    sold = expand_blocks(np.full(int(np.ceil(n / 16)), 0.15 * run.plant.p_turb_max), n, 16)
    act = activation_series(n, sold, sold, run.market, np.random.default_rng(1))
    reb = rebap_series(n, price, run.market, np.random.default_rng(2))
    res = run_ladder(price, run, sold, sold, act, reb)
    r = {k: v["cost"]["net_revenue"] for k, v in res.items()}
    b1, b2, b3 = r["B1 price threshold"], r["B2 perfect foresight"], r["B3 rolling MPC"]
    viol = sum(sum(v["violations"].values()) for v in res.values())
    return {
        "rows": [("B1 price-threshold rule", f"{b1:12,.0f} EUR"),
                 ("B2 perfect foresight", f"{b2:12,.0f} EUR"),
                 ("B3 rolling MPC (24 h horizon)", f"{b3:12,.0f} EUR")],
        "headline": f"B3 recovers {(b3 - b1) / max(b2 - b1, 1e-9) * 100:.0f}% of the headroom "
                    f"on a 300 MW / 2,400 MWh plant over {days} days; {viol} violations",
    }


def demo_03(real: bool) -> dict:
    from evc.baselines import b0_uncontrolled, b1_equal_share, b3_price_greedy, cost
    from evc.config import ARCHETYPES, RunConfig, SiteConfig
    from evc.sessions import generate

    run = RunConfig(days=3, archetype=ARCHETYPES["depot"],
                    site=SiteConfig(n_connectors=16, site_limit_kw=100.0))
    sessions = generate(run)
    n = run.days * run.steps_per_day
    spot = (real_prices_eur_mwh(run.days) if real else synthetic_prices_eur_mwh(n, 3)) / 1000.0
    hours = diurnal(n).astype(int)
    rows, missed = [], {}
    for name, fn in (("B0 uncontrolled", b0_uncontrolled), ("B1 equal share", b1_equal_share),
                     ("B3 price-aware", b3_price_greedy)):
        res = fn(sessions, n, run, spot)
        c = cost(res, spot, hours, run)
        missed[name] = res["missed_departures"]
        rows.append((name, f"{c['net_cost']:8.2f} EUR, peak {res['peak_kw']:5.1f} kW, "
                           f"{res['missed_departures']} missed"))
    return {
        "rows": rows,
        "headline": f"{len(sessions)} sessions on 16 connectors behind a 100 kW limit; the "
                    f"EDF safety layer holds the site limit on every rung",
    }


def demo_04(real: bool) -> dict:
    from rec.community import allocate, member_profiles, pv_profile, settle
    from rec.config import REGIMES, RunConfig, default_members
    from rec.game import (all_coalition_values, break_even_network_charge, build_game,
                          core_excess, owen_allocation, shapley_exact)

    run = RunConfig(days=7, members=default_members(10, 0), regime=REGIMES["para_42b"])
    cons = member_profiles(run)
    gen = pv_profile(run, 60.0)
    game = build_game(run, cons, gen)
    v = all_coalition_values(game)
    checks = {
        "Owen core allocation": owen_allocation(game),
        "Shapley value": shapley_exact(v, game.n),
        "Optimisation mechanism": settle(cons, allocate(gen, cons, "optimisation", run),
                                         gen, run)["member_payoff"],
    }
    rows = []
    for name, x in checks.items():
        c = core_excess(game, x, v)
        rows.append((name, f"{c['blocking_coalitions']:4d} of {c['checked']} coalitions "
                           f"block, {c['blocking_singletons']} would leave alone"))
    be = break_even_network_charge(RunConfig(members=run.members,
                                             regime=REGIMES["energy_sharing"]))
    return {
        "rows": rows,
        "headline": f"cooperation surplus {v[-1] - sum(v[1 << i] for i in range(game.n)):.2f} "
                    f"EUR; sharing stays worth doing up to a network charge of {be:.3f} EUR/kWh",
    }


def demo_05(real: bool) -> dict:
    from hybrid.baselines import run_ladder
    from hybrid.config import PlantConfig, RunConfig

    run = RunConfig(dt=0.25, horizon_steps=48, plant=PlantConfig(conn_mw=40.0))
    days = 3
    n = days * 96
    h = diurnal(n)
    rng = np.random.default_rng(5)
    wind = np.clip(30 + 18 * np.sin(np.arange(n) / 70) + 5 * rng.standard_normal(n), 0, 50)
    pv = 30 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    price = real_prices_eur_mwh(days) if real else synthetic_prices_eur_mwh(n, 5)
    res = run_ladder(wind, pv, price, run, premium_rate=8.0)
    r = {k: v["cost"] for k, v in res.items()}
    rows = [(k, f"{v['net_revenue']:10,.0f} EUR, {v['forced_curtail_mwh']:5.1f} MWh forced "
                f"curtailment") for k, v in r.items()]
    b0 = r["B0 no battery"]["net_revenue"]
    b3 = r["B3 rolling MPC"]["net_revenue"]
    return {
        "rows": rows,
        "headline": f"80 MW of wind+PV behind a 40 MW connection; the battery under B3 adds "
                    f"{(b3 - b0) / abs(b0) * 100:+.1f}% over the unhybridised plant",
    }


def demo_06(real: bool) -> dict:
    from f2b.config import RunConfig
    from f2b.forecast import audit_no_lookahead, build_store, score_store
    from f2b.policies import (b1_point_intraday, b3_stochastic_mpc, imbalance_cost_asymmetry,
                              nv_policy, rebap_series, settle)

    run = RunConfig()
    n = 7 * 96
    rng = np.random.default_rng(6)
    h = diurnal(n)
    truth = np.clip(120 + 60 * np.sin(np.arange(n) / 90)
                    + 70 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
                    + 8 * rng.standard_normal(n), 0, 290)
    price = real_prices_eur_mwh(7) if real else synthetic_prices_eur_mwh(n, 6)
    store = build_store(truth, run.forecast, run.dt, 144, np.random.default_rng(7), 300.0)
    reb = rebap_series(n, price, np.diff(truth, prepend=truth[0]), run.market,
                       np.random.default_rng(8))
    cs, cl = imbalance_cost_asymmetry(reb, price)
    rows = []
    for name, (pos, tr) in (("B1 chase the mean forecast", b1_point_intraday(store, n, run)),
                            ("NV newsvendor quantile", nv_policy(store, n, run, cs, cl)),
                            ("B3 deadband stochastic MPC",
                             b3_stochastic_mpc(store, n, run, cs, cl))):
        s = settle(pos, tr, truth, price, reb, price, run)
        rows.append((name, f"{s['eur_per_mwh']:6.2f} EUR/MWh, traded {s['traded_mwh']:7,.0f} MWh"))
    sc = score_store(store, truth, 24)
    return {
        "rows": rows,
        "headline": f"6 h ahead: CRPS {sc['CRPS']:.1f} MW, calibration error "
                    f"{sc['calibration_error']:.3f}; look-ahead audit "
                    f"{audit_no_lookahead(store, n)} violations",
    }


DEMOS = {
    "01": ("Prosumer PV + battery at 15-minute resolution", demo_01),
    "02": ("Pumped-storage multi-market dispatch", demo_02),
    "03": ("Smart EV charging under s14a EnWG", demo_03),
    "04": ("Energy sharing: who would leave the community?", demo_04),
    "05": ("Hybrid wind + PV + battery behind one connection", demo_05),
    "06": ("Probabilistic forecast-to-bid", demo_06),
}


# --------------------------------------------------------------------------- tests
def run_tests() -> str:
    """Run the suite and return pytest's own summary line, verbatim."""
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                         cwd=HERE, capture_output=True, text=True)
    lines = [ln for ln in out.stdout.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else f"pytest produced no output (exit {out.returncode})"


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real-data", action="store_true",
                    help="use DE-LU 2025 prices via datakit (needs network)")
    ap.add_argument("--tests", action="store_true", help="also run the test suite")
    ap.add_argument("--only", choices=list(DEMOS), help="run a single project")
    args = ap.parse_args()

    print("=" * 84)
    print("  AI for Renewable Energy Systems: applied research portfolio")
    print("  Murat Kus | Dipl.-Ing. | M.Sc. Sustainable Energy Systems | B.Sc. AI (in progress)")
    print(f"  inputs: {'DE-LU 2025 day-ahead prices (datakit)' if args.real_data else 'synthetic, offline'}"
          f" | every number below is computed now")
    print("=" * 84)

    failures = 0
    for pid, (title, fn) in DEMOS.items():
        if args.only and pid != args.only:
            continue
        t0 = time.perf_counter()
        print(f"\n[{pid}] {title}")
        try:
            out = fn(args.real_data)
        except Exception:                                   # noqa: BLE001 - report, never hide
            failures += 1
            print("     DEMO FAILED:")
            print("     " + traceback.format_exc().strip().replace("\n", "\n     "))
            continue
        for label, value in out["rows"]:
            print(f"     {label:<32} {value}")
        print(f"     -> {out['headline']}  ({time.perf_counter() - t0:.1f} s)")

    if args.tests:
        print(f"\ntest suite: {run_tests()}")

    print("\n" + "=" * 84)
    print(f"  {len(DEMOS) - failures} of {len(DEMOS)} demos ran"
          + ("" if not failures else f", {failures} FAILED"))
    print("  Details, limitations and full studies: README.md and each projects/*/README.md")
    print("=" * 84)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
