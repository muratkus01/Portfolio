"""Command-line entry point for the pumped-storage project.

    python -m psw.cli ladder  --year 2025 --days 30     B1/B2/B3 on real DE-LU prices
    python -m psw.cli lambda  --year 2025 --days 14     revenue vs grid-security frontier
    python -m psw.cli exempt  --year 2025 --days 14     s118(6) network-charge sensitivity
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .baselines import run_ladder
from .config import MarketConfig, PlantConfig, RunConfig
from .market import activation_series, block_index, expand_blocks, rebap_series

# datakit lives at the repository root; add it to the path rather than vendoring a copy
_DATAKIT = Path(__file__).resolve().parents[4] / "datakit"
if str(_DATAKIT) not in sys.path:
    sys.path.insert(0, str(_DATAKIT))


def load_prices(year: int, days: int, dt: float) -> np.ndarray:
    """Real DE-LU day-ahead prices, resampled to the model resolution.

    Prices are held (step function) when upsampling: a quarter-hour inside an hourly product
    carries that product's price.
    """
    import datakit

    df = datakit.day_ahead_price(f"{year}-01-01", f"{year + 1}-01-01")
    s = df["price_eur_per_mwh"]
    target = pd.date_range(s.index[0], periods=int(days * 24 / dt), freq=f"{int(dt * 60)}min",
                           tz="UTC")
    return s.reindex(s.index.union(target)).ffill().reindex(target).to_numpy()


def build_capacity_bids(n: int, run: RunConfig, fraction: float = 0.15) -> tuple:
    """A simple static capacity offer: a fixed share of rating in every block.

    Deliberately naive. The point of the project is that choosing this well is hard and
    interacts with the energy schedule; a static offer is the baseline that a learned or
    optimised bidder has to beat.
    """
    bs = run.market.block_steps
    nb = int(np.ceil(n / bs))
    pos = np.full(nb, run.plant.p_turb_max * fraction)
    neg = np.full(nb, run.plant.p_pump_max * fraction)
    return expand_blocks(pos, n, bs), expand_blocks(neg, n, bs)


def _report(res: dict, run: RunConfig, title: str) -> None:
    print(f"\n{title}")
    print("=" * len(title))
    hdr = (f"{'controller':<22}{'net EUR':>12}{'energy':>11}{'capacity':>10}"
           f"{'wear':>9}{'security':>10}{'viol.':>7}")
    print(hdr)
    print("-" * len(hdr))
    for name, r in res.items():
        c = r["cost"]
        print(f"{name:<22}{c['net_revenue']:>12,.0f}"
              f"{c['energy_revenue'] - c['pump_cost']:>11,.0f}"
              f"{c['capacity_revenue']:>10,.0f}{c['wear_cost']:>9,.0f}"
              f"{r['security']['index']:>10.3f}{sum(r['violations'].values()):>7d}")
    if "B2 perfect foresight" in res and "B3 rolling MPC" in res:
        b1 = res["B1 price threshold"]["cost"]["net_revenue"]
        b2 = res["B2 perfect foresight"]["cost"]["net_revenue"]
        b3 = res["B3 rolling MPC"]["cost"]["net_revenue"]
        print(f"\nheadroom B2 - B1 = {b2 - b1:,.0f} EUR   "
              f"B3 recovers {(b3 - b1) / max(b2 - b1, 1e-9) * 100:.1f}%")
        print(f"remaining for RL: B2 - B3 = {b2 - b3:,.0f} EUR")
        if "solve_time_s" in res["B3 rolling MPC"]:
            st = res["B3 rolling MPC"]["solve_time_s"]
            print(f"B3 solve time: mean {st.mean() * 1000:.0f} ms, "
                  f"max {st.max() * 1000:.0f} ms ({len(st)} solves)")


def _setup(args) -> tuple:
    run = RunConfig(dt=0.25, horizon_steps=args.horizon,
                    plant=PlantConfig(para_118_6_exempt=not args.no_exemption),
                    market=MarketConfig(afrr_enabled=not args.no_afrr))
    price = load_prices(args.year, args.days, run.dt)
    n = len(price)
    rng = np.random.default_rng(run.seed)
    sold_pos, sold_neg = build_capacity_bids(n, run) if run.market.afrr_enabled \
        else (np.zeros(n), np.zeros(n))
    act = activation_series(n, sold_pos, sold_neg, run.market, rng)
    reb = rebap_series(n, price, run.market, rng)
    return run, price, sold_pos, sold_neg, act, reb


def cmd_ladder(args) -> None:
    run, price, sp, sn, act, reb = _setup(args)
    print(f"{len(price)} steps | {args.days} days | mean price "
          f"{price.mean():.1f} EUR/MWh | negative in "
          f"{(price < 0).mean() * 100:.1f}% of steps")
    print(f"plant: {run.plant.p_turb_max:.0f} MW turbine / {run.plant.p_pump_max:.0f} MW pump "
          f"| {run.plant.e_max:.0f} MWh | round-trip {run.plant.eta_round_trip:.3f}")
    res = run_ladder(price, run, sp, sn, act, reb, include_b3=not args.fast,
                     progress=args.progress)
    _report(res, run, f"Pumped-storage ladder, {args.days} days of {args.year}")


def cmd_lambda(args) -> None:
    """Sweep the operating mode: revenue vs grid-security readiness."""
    run, price, sp, sn, act, reb = _setup(args)
    rows = []
    for lam in (0.0, 0.25, 0.5, 0.75, 1.0):
        r = run_ladder(price, run.with_(lam=lam), sp, sn, act, reb, include_b3=False)
        b2 = r["B2 perfect foresight"]
        rows.append({"lambda": lam, "net_EUR": b2["cost"]["net_revenue"],
                     "security_index": b2["security"]["index"],
                     "up_reserve": b2["security"]["up_reserve"],
                     "duration": b2["security"]["duration"]})
    df = pd.DataFrame(rows)
    print("\nOperating-mode frontier (B2; lambda enters the RL reward, not the LP objective)")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    print("\nNOTE: lambda currently shapes only the RL reward. Making the LP itself "
          "security-aware (a constraint on retained headroom) is the next step, and is what "
          "turns this table into a genuine Pareto frontier.")


def cmd_exempt(args) -> None:
    """s118(6) EnWG: what the network-charge exemption on pumping is worth."""
    rows = []
    for exempt in (True, False):
        args.no_exemption = not exempt
        run, price, sp, sn, act, reb = _setup(args)
        r = run_ladder(price, run, sp, sn, act, reb, include_b3=False)
        for name, v in r.items():
            rows.append({"s118(6) exempt": exempt, "controller": name,
                         "net_EUR": v["cost"]["net_revenue"],
                         "network_cost_EUR": v["cost"]["network_cost"]})
    df = pd.DataFrame(rows).pivot(index="controller", columns="s118(6) exempt",
                                  values="net_EUR")
    df["loss_EUR"] = df[True] - df[False]
    print("\ns118(6) EnWG network-charge exemption sensitivity")
    print(df.to_string(float_format=lambda x: f"{x:,.0f}"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="psw", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--year", type=int, default=2025)
        sp.add_argument("--days", type=int, default=30)
        sp.add_argument("--horizon", type=int, default=192)
        sp.add_argument("--no-afrr", action="store_true")
        sp.add_argument("--no-exemption", action="store_true")
        sp.add_argument("--fast", action="store_true", help="skip B3")
        sp.add_argument("--progress", action="store_true")

    for name, fn in (("ladder", cmd_ladder), ("lambda", cmd_lambda), ("exempt", cmd_exempt)):
        sp = sub.add_parser(name)
        common(sp)
        sp.set_defaults(func=fn)

    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
