"""Command-line entry point for the charging-hub project.

    python -m evc.cli ladder    --archetype depot
    python -m evc.cli modules   --archetype depot     s14a module comparison (RQ2)
    python -m evc.cli dimming   --archetype depot     cost of s14a by event frequency (RQ1)
    python -m evc.cli archetypes                      all three site types side by side
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .allocate import dimming_series
from .baselines import b0_uncontrolled, b1_equal_share, b3_price_greedy, cost, fairness
from .config import ARCHETYPES, Para14aConfig, RunConfig, SiteConfig, TariffConfig
from .sessions import generate, summarise

_DATAKIT = Path(__file__).resolve().parents[4] / "datakit"
if str(_DATAKIT) not in sys.path:
    sys.path.insert(0, str(_DATAKIT))


def load_prices(year: int, n_steps: int, dt: float) -> tuple[np.ndarray, np.ndarray]:
    """Real DE-LU day-ahead prices in EUR/kWh, plus the local hour of each step."""
    import datakit

    df = datakit.day_ahead_price(f"{year}-01-01", f"{year + 1}-01-01")
    s = df["price_eur_per_mwh"] / 1000.0
    target = pd.date_range(s.index[0], periods=n_steps, freq=f"{int(dt * 60)}min", tz="UTC")
    spot = s.reindex(s.index.union(target)).ffill().reindex(target).to_numpy()
    hours = np.asarray(target.tz_convert("Europe/Berlin").hour)
    return spot, hours


def _setup(args, module: str = "none", events: float = 0.0):
    arch = ARCHETYPES[args.archetype]
    run = RunConfig(
        days=args.days, seed=args.seed, archetype=arch,
        site=SiteConfig(n_connectors=args.connectors, site_limit_kw=args.site_limit),
        tariff=TariffConfig(para_14a_module=module),
        para_14a=Para14aConfig(enabled=events > 0, events_per_year=events))
    sessions = generate(run)
    n_steps = run.days * run.steps_per_day
    spot, hours = load_prices(args.year, n_steps, run.dt)
    dim = dimming_series(n_steps, hours, run, run.para_14a)
    return run, sessions, n_steps, spot, hours, dim


def _row(name, res, spot, hours, run):
    c = cost(res, spot, hours, run)
    return {"controller": name, "net_cost_EUR": c["net_cost"],
            "energy_kWh": res["energy_kwh"], "peak_kW": res["peak_kw"],
            "peak_cost_EUR": c["peak_cost"], "thg_EUR": c["thg_revenue"],
            "missed": res["missed_departures"], "short_kWh": res["shortfall_kwh"],
            "fairness": fairness(res)}


def _ladder(run, sessions, n_steps, spot, hours, dim):
    return pd.DataFrame([
        _row("B0 uncontrolled", b0_uncontrolled(sessions, n_steps, run, spot, dim),
             spot, hours, run),
        _row("B1 equal share", b1_equal_share(sessions, n_steps, run, spot, dim),
             spot, hours, run),
        _row("B3 price greedy", b3_price_greedy(sessions, n_steps, run, spot, dim),
             spot, hours, run),
    ])


def cmd_ladder(args) -> None:
    run, sessions, n_steps, spot, hours, dim = _setup(args)
    print(summarise(sessions, run))
    print(f"price mean {spot.mean() * 1000:.1f} EUR/MWh | {n_steps} steps | "
          f"archetype {run.archetype.name}")
    df = _ladder(run, sessions, n_steps, spot, hours, dim)
    print("\nCharging-hub ladder")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    print("\n'missed' counts sessions that did not reach their DECLARED departure energy. "
          "The safety layer makes this zero whenever it is physically possible; a non-zero "
          "value means the site is genuinely oversubscribed, which is a sizing finding.")


def cmd_modules(args) -> None:
    """RQ2: which s14a module suits this site?"""
    rows = []
    for module in ("none", "module_1", "module_2", "module_3"):
        run, sessions, n_steps, spot, hours, dim = _setup(args, module=module,
                                                          events=args.events)
        df = _ladder(run, sessions, n_steps, spot, hours, dim)
        df["module"] = module
        rows.append(df)
    out = pd.concat(rows)
    print(f"\ns14a EnWG module comparison - {args.archetype}, "
          f"{args.events:.0f} dimming events/year")
    print(out.pivot(index="controller", columns="module", values="net_cost_EUR")
          .to_string(float_format=lambda x: f"{x:,.2f}"))
    print("\nmissed declared departures:")
    print(out.pivot(index="controller", columns="module", values="missed").to_string())


def cmd_dimming(args) -> None:
    """RQ1: what does s14a grid-orientated control cost, as a function of its intensity?"""
    rows = []
    for events in (0, 30, 60, 120, 250, 500):
        run, sessions, n_steps, spot, hours, dim = _setup(args, module="module_2",
                                                          events=events)
        df = _ladder(run, sessions, n_steps, spot, hours, dim)
        df["events_per_year"] = events
        rows.append(df)
    out = pd.concat(rows)
    print(f"\nCost of s14a dimming by event frequency - {args.archetype}")
    print(out.pivot(index="events_per_year", columns="controller", values="net_cost_EUR")
          .to_string(float_format=lambda x: f"{x:,.2f}"))
    print("\nmissed declared departures:")
    print(out.pivot(index="events_per_year", columns="controller", values="missed")
          .to_string())
    print("\nNo public dataset of realised dimming events exists, which is why the result is "
          "reported across a sweep rather than at one assumed frequency. Locate your own grid "
          "situation on this curve.")


def cmd_archetypes(args) -> None:
    rows = []
    for name in ARCHETYPES:
        args.archetype = name
        run, sessions, n_steps, spot, hours, dim = _setup(args, events=args.events)
        df = _ladder(run, sessions, n_steps, spot, hours, dim)
        df["archetype"] = name
        rows.append(df)
        print(f"\n{name}: {summarise(sessions, run)}")
    out = pd.concat(rows)
    print("\nNet cost EUR by archetype")
    print(out.pivot(index="controller", columns="archetype", values="net_cost_EUR")
          .to_string(float_format=lambda x: f"{x:,.2f}"))
    print("\nPeak kW by archetype")
    print(out.pivot(index="controller", columns="archetype", values="peak_kW")
          .to_string(float_format=lambda x: f"{x:,.1f}"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evc", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("ladder", cmd_ladder), ("modules", cmd_modules),
                     ("dimming", cmd_dimming), ("archetypes", cmd_archetypes)):
        sp = sub.add_parser(name)
        sp.add_argument("--archetype", default="depot", choices=list(ARCHETYPES))
        sp.add_argument("--year", type=int, default=2024)
        sp.add_argument("--days", type=int, default=14)
        sp.add_argument("--connectors", type=int, default=40)
        sp.add_argument("--site-limit", type=float, default=250.0)
        sp.add_argument("--events", type=float, default=60.0)
        sp.add_argument("--seed", type=int, default=0)
        sp.set_defaults(func=fn)
    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
