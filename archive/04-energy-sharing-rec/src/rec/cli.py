"""Command-line entry point for the energy-community project.

    python -m rec.cli mechanisms     compare the four allocation mechanism families
    python -m rec.cli regimes        individual / Mieterstrom / s42b / energy sharing
    python -m rec.cli policy         RQ5: charges on shared energy, from three perspectives
    python -m rec.cli game           Shapley value, Owen core allocation, core stability
    python -m rec.cli correlation    how much of the benefit is a modelling artefact
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from .community import (allocate, feeder_violations, individual_rationality, member_prices,
                        member_profiles, pv_profile, settle, sharing_spread)
from .config import REGIMES, RegimeConfig, RunConfig, default_members
from .game import (all_coalition_values, break_even_network_charge, build_game, core_excess,
                   owen_allocation, shapley_exact, shapley_monte_carlo)

MECHANISMS = ("static_key", "dynamic_proportional", "optimisation", "market")


def _fmt(x: float) -> str:
    return f"{x:,.3f}"


def _setup(args, regime: str | RegimeConfig | None = None, correlation: float | None = None):
    reg = regime if isinstance(regime, RegimeConfig) else REGIMES[regime or args.regime]
    run = RunConfig(days=args.days, seed=args.seed,
                    members=default_members(args.members, args.seed), regime=reg,
                    correlation=args.correlation if correlation is None else correlation)
    cons = member_profiles(run)
    # community PV scales with membership, so small test communities stay comparable
    gen = pv_profile(run, run.community.shared_pv_kwp * args.members / 20)
    return run, cons, gen


def _row(name, run, cons, gen, alloc):
    s = settle(cons, alloc, gen, run)
    ir_consumer = individual_rationality(s, "member_saving")
    ir_member = individual_rationality(s, "member_payoff")
    return {
        "mechanism": name,
        "coalition_value_EUR": s["coalition_value"],
        "consumer_saving_EUR": s["consumer_saving"],
        "owner_gain_EUR": s["owner_gain"],
        "self_consumption": s["self_consumption"],
        "self_sufficiency": s["self_sufficiency"],
        "consumers_worse": ir_consumer["members_worse_off"],
        "owners_worse": ir_member["members_worse_off"],
        "gini": ir_consumer["gini"],
        "feeder_viol": feeder_violations(cons, alloc, gen, run.community),
    }


def cmd_mechanisms(args) -> None:
    run, cons, gen = _setup(args)
    p_shared, p_grid, p_export = member_prices(run.regime)
    print(f"{len(run.members)} members | {args.days} days | regime {run.regime.name}")
    print(f"shared {p_shared:.4f} | grid {p_grid:.4f} | export {p_export:.4f} EUR/kWh | "
          f"value per shared kWh s = {sharing_spread(run.regime):+.4f} | "
          f"correlation {run.correlation:.2f}")
    print(f"community demand {cons.sum() * run.dt:,.0f} kWh | "
          f"shared PV generation {gen.sum() * run.dt:,.0f} kWh")

    df = pd.DataFrame([_row(m, run, cons, gen, allocate(gen, cons, m, run))
                       for m in MECHANISMS])
    print("\nAllocation mechanisms")
    print(df.to_string(index=False, float_format=_fmt))
    print("\ncoalition value = consumer saving + owner gain = s x shared kWh. The internal price\n"
          "only moves value between consumers and the PV owner; it never creates or destroys\n"
          "it. 'consumers_worse' judges members as consumers only (third-party PV owner, as in\n"
          "Mieterstrom). 'owners_worse' adds each member's ownership dividend (a member-owned\n"
          "community). Both judge against NO sharing at all; `rec.cli game` applies the\n"
          "stricter test of keeping one's own PV share, and the answer changes.")


def cmd_regimes(args) -> None:
    rows = []
    for name in REGIMES:
        run, cons, gen = _setup(args, regime=name)
        alloc = (np.zeros_like(cons) if name == "individual"
                 else allocate(gen, cons, args.mechanism, run))
        r = _row(name, run, cons, gen, alloc)
        r["mechanism"] = name
        r["s_EUR_kWh"] = sharing_spread(run.regime)
        rows.append(r)
    print(f"\nLegal regimes (mechanism: {args.mechanism})")
    print(pd.DataFrame(rows).to_string(index=False, float_format=_fmt))


def cmd_policy(args) -> None:
    """RQ5: the network charge on shared energy, from the consumer, owner and system view."""
    base = REGIMES["energy_sharing"]
    rows = []
    for charge in (0.0, 0.01, 0.02, 0.03, 0.05, 0.085, 0.11, 0.13):
        reg = RegimeConfig(**{**base.__dict__, "shared_network_charge": charge})
        run, cons, gen = _setup(args, regime=reg)
        s = settle(cons, allocate(gen, cons, args.mechanism, run), gen, run)
        game = build_game(run, cons, gen)
        owen = owen_allocation(game)
        rows.append({
            "charge_EUR_kWh": charge,
            "s_EUR_kWh": sharing_spread(reg),
            "coalition_EUR": s["coalition_value"],
            "consumer_EUR": s["consumer_saving"],
            "owner_EUR": s["owner_gain"],
            "consumers_worse": individual_rationality(s, "member_saving")["members_worse_off"],
            "owen_min_payoff_EUR": float(owen.min()),
        })
    run, _, _ = _setup(args, regime=base)
    print("\nNetwork charge on SHARED energy: consumer, owner and system perspectives")
    print(pd.DataFrame(rows).to_string(index=False, float_format=_fmt))
    print(f"\nbreak-even charge (s = 0): {break_even_network_charge(run):.4f} EUR/kWh")
    print("\nAt the default internal price, consumers start losing once the charge pushes the\n"
          "shared price above the grid price, while the community as a whole still creates\n"
          "value until the break-even charge. The gap between the two is a SETTLEMENT failure,\n"
          "not a value failure: the Owen core allocation keeps every member at or above what\n"
          "they could achieve alone right up to break-even. Above it no settlement can help.")


def cmd_game(args) -> None:
    """Shapley value, Owen core allocation, and whether each is stable."""
    run, cons, gen = _setup(args)
    game = build_game(run, cons, gen)
    exact = game.n <= args.exact_max
    values = all_coalition_values(game) if exact else None

    if exact:
        phi = shapley_exact(values, game.n)
        se = np.zeros(game.n)
        standalone = np.array([values[1 << i] for i in range(game.n)])
        v_grand = float(values[-1])
    else:
        phi, se = shapley_monte_carlo(game, n_perm=args.permutations)
        from .game import coalition_value
        standalone = np.array([coalition_value(game, [i]) for i in range(game.n)])
        v_grand = coalition_value(game, np.ones(game.n, dtype=bool))
    owen = owen_allocation(game)

    mech_payoffs = {m: settle(cons, allocate(gen, cons, m, run), gen, run)["member_payoff"]
                    for m in MECHANISMS}

    print(f"{game.n} members | {args.days} days | regime {run.regime.name} | "
          f"s = {game.s:+.4f} EUR/kWh")
    print(f"grand coalition value v(N) = {v_grand:,.2f} EUR | "
          f"sum of standalone values = {standalone.sum():,.2f} EUR | "
          f"cooperation surplus = {v_grand - standalone.sum():,.2f} EUR")

    table = pd.DataFrame({
        "member": [m.name for m in run.members],
        "standalone": standalone, "shapley": phi, "shapley_se": se, "owen": owen,
        **{f"mech:{m}": x for m, x in mech_payoffs.items()},
    })
    print("\nPayoff per member, EUR over the period")
    print(table.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))

    scope = (f"all {2 ** game.n - 1:,} coalitions" if exact
             else "a random sample of coalitions (can refute core membership, not prove it)")
    print(f"\nCore stability, checked over {scope}")
    rows = []
    for name, x in [("shapley", phi), ("owen", owen), *mech_payoffs.items()]:
        c = core_excess(game, np.asarray(x), values)
        rows.append({"allocation": name, "total_EUR": float(np.sum(x)),
                     "in_core": c["in_core"], "blocking": c["blocking_coalitions"],
                     "singletons_blocking": c["blocking_singletons"],
                     "checked": c["checked"], "max_excess_EUR": c["max_excess"]})
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    print("\nA coalition blocks when it could earn more on its own than the allocation gives it.\n"
          "Owen's allocation is in the core by theorem (this is a linear production game); the\n"
          "Shapley value is not guaranteed to be.\n\n"
          "Two counterfactuals, and they disagree. Against NO sharing at all, every member gains\n"
          "under every mechanism (the `mechanisms` command). Against keeping one's own PV share\n"
          "and self-consuming it, `singletons_blocking` members are underpaid enough to leave on\n"
          "their own. A flat internal price pays PV ownership evenly and underpays the members\n"
          "whose demand absorbs the surplus. The game assumes a leaving member takes its PV\n"
          "ownership share with it; if it cannot, the weaker counterfactual is the right one.")


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
    print(df[["correlation", "coalition_value_EUR", "self_consumption", "self_sufficiency",
              "consumers_worse", "gini"]].to_string(index=False, float_format=_fmt))
    print("\nA community's value comes from COMPLEMENTARITY between members. With the current\n"
          "profile generator the effect is small, because inter-member variation is modelled\n"
          "as noise around one shared daily shape. Fitting per-member shapes from measured\n"
          "profiles is the documented upgrade.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rec", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("mechanisms", cmd_mechanisms), ("regimes", cmd_regimes),
                     ("policy", cmd_policy), ("game", cmd_game),
                     ("correlation", cmd_correlation)):
        sp = sub.add_parser(name)
        sp.add_argument("--days", type=int, default=14)
        sp.add_argument("--members", type=int, default=20 if name != "game" else 12)
        sp.add_argument("--seed", type=int, default=0)
        sp.add_argument("--regime", default="para_42b", choices=list(REGIMES))
        sp.add_argument("--mechanism", default="optimisation", choices=MECHANISMS)
        sp.add_argument("--correlation", type=float, default=0.35)
        sp.add_argument("--exact-max", type=int, default=12,
                        help="largest community solved exactly over all 2^n coalitions")
        sp.add_argument("--permutations", type=int, default=2000)
        sp.set_defaults(func=fn)
    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
