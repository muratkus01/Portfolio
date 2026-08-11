"""Command-line entry point for the energy-community project.

    python -m rec.cli mechanisms            compare the four allocation mechanism families
    python -m rec.cli regimes               individual / Mieterstrom / s42b / energy sharing
    python -m rec.cli policy                RQ5: sensitivity to charges on shared energy
    python -m rec.cli correlation           how much of the benefit is a modelling artefact
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from .community import (allocate, feeder_violations, individual_rationality, member_prices,
                        member_profiles, pv_profile, settle)
from .config import REGIMES, RegimeConfig, RunConfig, default_members

MECHANISMS = ("static_key", "dynamic_proportional", "optimisation", "market")


def _setup(args, regime: str | None = None, correlation: float | None = None):
    run = RunConfig(days=args.days, seed=args.seed,
                    members=default_members(args.members, args.seed),
                    regime=REGIMES[regime or args.regime],
                    correlation=args.correlation if correlation is None else correlation)
    cons = member_profiles(run)
    gen = pv_profile(run, run.community.shared_pv_kwp)
    return run, cons, gen


def _row(name, run, cons, gen, alloc):
    s = settle(cons, alloc, gen, run)
    ir = individual_rationality(s)
    return {
        "mechanism": name,
        "community_cost_EUR": s["net_community_cost"],
        "vs_individual_EUR": s["community_baseline"] - s["net_community_cost"],
        "self_consumption": s["self_consumption"],
        "self_sufficiency": s["self_sufficiency"],
        "worse_off": ir["members_worse_off"],
        "min_saving_EUR": ir["min_saving"],
        "gini": ir["gini"],
        "feeder_viol": feeder_violations(cons, alloc, gen, run.community),
    }


def cmd_mechanisms(args) -> None:
    run, cons, gen = _setup(args)
    p_shared, p_grid, p_export = member_prices(run.regime)
    print(f"{len(run.members)} members | {args.days} days | regime {run.regime.name}")
    print(f"shared {p_shared:.4f} | grid {p_grid:.4f} | export {p_export:.4f} EUR/kWh | "
          f"correlation {run.correlation:.2f}")
    print(f"community demand {cons.sum() * run.dt:,.0f} kWh | "
          f"shared PV generation {gen.sum() * run.dt:,.0f} kWh")

    rows = [_row(m, run, cons, gen, allocate(gen, cons, m, run)) for m in MECHANISMS]
    df = pd.DataFrame(rows)
    print("\nAllocation mechanisms")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    print("\n'worse_off' counts members who would pay LESS on individual supply. A mechanism "
          "that is collectively efficient but leaves members worse off will lose them, and "
          "then it is not efficient either - which is why this column is reported next to the "
          "community total rather than below it.")


def cmd_regimes(args) -> None:
    rows = []
    for name in REGIMES:
        run, cons, gen = _setup(args, regime=name)
        alloc = (np.zeros_like(cons) if name == "individual"
                 else allocate(gen, cons, args.mechanism, run))
        r = _row(name, run, cons, gen, alloc)
        r["mechanism"] = name
        rows.append(r)
    df = pd.DataFrame(rows)
    print(f"\nLegal regimes (mechanism: {args.mechanism})")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))


def cmd_policy(args) -> None:
    """RQ5: is grid-spanning energy sharing viable? It is a function of one policy choice."""
    rows = []
    for charge in (0.0, 0.01, 0.02, 0.03, 0.05, 0.085):
        base = REGIMES["energy_sharing"]
        reg = RegimeConfig(**{**base.__dict__, "shared_network_charge": charge})
        run = RunConfig(days=args.days, seed=args.seed,
                        members=default_members(args.members, args.seed),
                        regime=reg, correlation=args.correlation)
        cons = member_profiles(run)
        gen = pv_profile(run, run.community.shared_pv_kwp)
        alloc = allocate(gen, cons, args.mechanism, run)
        r = _row(f"charge {charge:.3f}", run, cons, gen, alloc)
        r["shared_network_charge"] = charge
        rows.append(r)
    df = pd.DataFrame(rows)
    print("\nNetwork charge on SHARED energy - the decisive open policy parameter")
    print(df[["shared_network_charge", "community_cost_EUR", "vs_individual_EUR",
              "worse_off", "min_saving_EUR"]]
          .to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    print("\nGerman energy sharing across the public grid was still moving through EnWG\n"
          "amendment drafts, so this parameter is unsettled - and it decides viability. The\n"
          "model is deliberately parametric in it so the study stays valid whichever variant\n"
          "is enacted, and so the table can be read as a policy recommendation.")


def cmd_correlation(args) -> None:
    """How much of the reported community benefit is an artefact of the profile model?"""
    rows = []
    for corr in (0.0, 0.2, 0.35, 0.6, 0.9):
        run, cons, gen = _setup(args, correlation=corr)
        alloc = allocate(gen, cons, args.mechanism, run)
        r = _row(f"corr {corr:.2f}", run, cons, gen, alloc)
        r["correlation"] = corr
        rows.append(r)
    df = pd.DataFrame(rows)
    print("\nInter-member load correlation vs community benefit")
    print(df[["correlation", "vs_individual_EUR", "self_consumption", "self_sufficiency",
              "worse_off"]].to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    print("\nA community's value comes from COMPLEMENTARITY between members. Independent\n"
          "synthetic profiles (correlation 0) overstate it; a study that does not report this\n"
          "sensitivity is reporting a property of its profile generator, not of energy sharing.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rec", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("mechanisms", cmd_mechanisms), ("regimes", cmd_regimes),
                     ("policy", cmd_policy), ("correlation", cmd_correlation)):
        sp = sub.add_parser(name)
        sp.add_argument("--days", type=int, default=14)
        sp.add_argument("--members", type=int, default=20)
        sp.add_argument("--seed", type=int, default=0)
        sp.add_argument("--regime", default="para_42b", choices=list(REGIMES))
        sp.add_argument("--mechanism", default="dynamic_proportional", choices=MECHANISMS)
        sp.add_argument("--correlation", type=float, default=0.35)
        sp.set_defaults(func=fn)
    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
