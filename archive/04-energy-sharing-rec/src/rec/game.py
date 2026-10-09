"""Cooperative game analysis of an energy community.

The allocation mechanisms in `community.py` decide who gets which kWh. This module asks the
question that decides whether the community survives: **given the value the community
creates, how should it be divided so that no group of members would rather leave?**

The characteristic function
---------------------------
Each member i consumes c_i(t) and owns a share k_i of the community PV, which produces g(t).
A coalition S pools its members' PV shares and their consumption, and shares internally:

    v(S) = max(s, 0) * dt * sum_t min( K_S * g(t), C_S(t) )

with K_S = sum_{i in S} k_i, C_S(t) = sum_{i in S} c_i(t), and s the value of one shared kWh
(`community.sharing_spread`). v(S) is the saving of S against its members going alone.

Why this is well posed
----------------------
For each quarter-hour, min(G, C) is the optimum of a small linear programme whose resources
(generation, consumption) are owned additively by the players. That makes the community a
**linear production game** (Owen, 1975), and such games always have a non-empty core. The
dual of the grand coalition's programme gives a core allocation constructively:

    in a step where generation is scarce, the value is attributed to the PV owners
    in a step where consumption is scarce, the value is attributed to the consumers

That is `owen_allocation`, and it is also the easiest rule to explain to a member.

The Shapley value is the other classical answer: each member's average marginal contribution
over all orders of joining. It is fair in a precise axiomatic sense, but for this class of
game it is **not guaranteed to lie in the core**. Whether it does in practice is an empirical
question, and `core_excess` answers it exactly for small communities.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import factorial

import numpy as np

from .community import ownership_shares, sharing_spread
from .config import RunConfig


@dataclass(frozen=True)
class Game:
    """The data a coalition value needs, and nothing else."""
    g: np.ndarray          # (T,)   community PV generation, kW
    k: np.ndarray          # (n,)   ownership shares, sum to 1
    c: np.ndarray          # (T, n) member consumption, kW
    dt: float
    s: float               # EUR per shared kWh

    @property
    def n(self) -> int:
        return len(self.k)


def build_game(run: RunConfig, consumption_kw: np.ndarray, generation_kw: np.ndarray) -> Game:
    return Game(g=np.asarray(generation_kw, dtype=float), k=ownership_shares(run),
                c=np.asarray(consumption_kw, dtype=float), dt=run.dt,
                s=sharing_spread(run.regime))


def coalition_value(game: Game, members) -> float:
    """v(S) for a coalition given as an index list or boolean mask."""
    idx = np.flatnonzero(members) if np.asarray(members).dtype == bool else np.asarray(members)
    if len(idx) == 0 or game.s <= 0:
        return 0.0
    shared = np.minimum(game.k[idx].sum() * game.g, game.c[:, idx].sum(axis=1))
    return float(game.s * game.dt * shared.sum())


def all_coalition_values(game: Game, max_bytes: float = 400e6) -> np.ndarray:
    """v(S) for every coalition, indexed by bitmask. Exponential: small communities only.

    The table stores each coalition's pooled consumption profile so every value is built from
    its parent in O(T). Memory is 2^n * T * 8 bytes, so the limit is set in bytes rather than
    players: 12 members over two weeks is ~44 MB, 16 members would be ~700 MB.
    """
    n = game.n
    need = (1 << n) * len(game.g) * 8
    if need > max_bytes:
        raise ValueError(f"{n} players over {len(game.g)} steps needs {need / 1e6:,.0f} MB; "
                         "use shapley_monte_carlo and a sampled core_excess instead")
    values = np.zeros(1 << n)
    if game.s <= 0:
        return values
    k_mask = np.zeros(1 << n)
    c_mask = np.zeros((1 << n, len(game.g)))
    for mask in range(1, 1 << n):
        low = mask & -mask
        i = low.bit_length() - 1
        rest = mask ^ low
        k_mask[mask] = k_mask[rest] + game.k[i]
        c_mask[mask] = c_mask[rest] + game.c[:, i]
        values[mask] = game.s * game.dt * np.minimum(k_mask[mask] * game.g, c_mask[mask]).sum()
    return values


def shapley_exact(values: np.ndarray, n: int) -> np.ndarray:
    """Shapley value from the full table of coalition values."""
    phi = np.zeros(n)
    weights = [factorial(s) * factorial(n - s - 1) / factorial(n) for s in range(n)]
    sizes = np.array([bin(m).count("1") for m in range(1 << n)])
    for i in range(n):
        bit = 1 << i
        without = np.array([m for m in range(1 << n) if not m & bit])
        marg = values[without | bit] - values[without]
        phi[i] = float(np.sum(marg * np.take(weights, sizes[without])))
    return phi


def shapley_monte_carlo(game: Game, n_perm: int = 2000,
                        rng: np.random.Generator | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Shapley value by sampling join orders. Returns (estimate, standard error).

    Each permutation adds members one at a time, carrying the coalition's pooled generation
    share and consumption forward, so one permutation costs O(n * T) rather than O(2^n).
    The standard error is reported, not hidden: a Shapley figure without one is a single
    draw from a sampling distribution.
    """
    rng = rng or np.random.default_rng(0)
    n = game.n
    samples = np.zeros((n_perm, n))
    if game.s <= 0:
        return np.zeros(n), np.zeros(n)
    for p in range(n_perm):
        order = rng.permutation(n)
        k_s = 0.0
        c_s = np.zeros(len(game.g))
        v_prev = 0.0
        for i in order:
            k_s += game.k[i]
            c_s = c_s + game.c[:, i]
            v_now = game.s * game.dt * np.minimum(k_s * game.g, c_s).sum()
            samples[p, i] = v_now - v_prev
            v_prev = v_now
    return samples.mean(axis=0), samples.std(axis=0, ddof=1) / np.sqrt(n_perm)


def owen_allocation(game: Game) -> np.ndarray:
    """A core allocation from the dual of the grand coalition's programme.

    Per step, the whole value goes to whichever side is scarce: PV owners in proportion to
    their ownership when generation binds, consumers in proportion to their consumption when
    consumption binds, split evenly on an exact tie. Owen's theorem guarantees the result is
    in the core, so no coalition can do better on its own.
    """
    if game.s <= 0:
        return np.zeros(game.n)
    G = game.g
    C = game.c.sum(axis=1)
    gen_scarce = (G < C)[:, None]
    tie = np.isclose(G, C)[:, None]
    owner_part = game.k[None, :] * G[:, None]
    consumer_part = game.c
    per_step = np.where(tie, 0.5 * owner_part + 0.5 * consumer_part,
                        np.where(gen_scarce, owner_part, consumer_part))
    return game.s * game.dt * per_step.sum(axis=0)


def core_excess(game: Game, x: np.ndarray, values: np.ndarray | None = None,
                n_sample: int = 20000, rng: np.random.Generator | None = None) -> dict:
    """How far an allocation is from the core.

    Excess of coalition S is v(S) - x(S): positive means S would gain by leaving. The core is
    exactly the set of allocations with no positive excess. Checked over every coalition when
    `values` is supplied, otherwise over a random sample (which can prove an allocation is
    OUTSIDE the core, but not that it is inside).
    """
    n = game.n
    if values is not None:
        masks = np.arange(1, 1 << n)
        members = ((masks[:, None] >> np.arange(n)) & 1).astype(bool)
        excess = values[masks] - members @ x
        exhaustive = True
    else:
        rng = rng or np.random.default_rng(0)
        members = rng.random((n_sample, n)) < rng.random((n_sample, 1))
        members = members[members.any(axis=1)]
        excess = np.array([coalition_value(game, m) for m in members]) - members @ x
        exhaustive = False
    worst = int(np.argmax(excess))
    tol = 1e-6 * max(1.0, abs(float(x.sum())))
    # Singletons are reported separately because they answer a different question: not "would
    # a group rather leave" but "would one member rather keep its PV share and self-consume".
    standalone = np.array([values[1 << i] if values is not None
                           else coalition_value(game, [i]) for i in range(n)])
    return {
        "max_excess": float(excess[worst]),
        "blocking_coalitions": int(np.sum(excess > tol)),
        "blocking_singletons": int(np.sum(standalone - x > tol)),
        "checked": int(len(excess)),
        "exhaustive": exhaustive,
        "in_core": bool(excess.max() <= tol),
        "worst_coalition": np.flatnonzero(members[worst]).tolist(),
    }


def break_even_network_charge(run: RunConfig) -> float:
    """The network charge on shared energy at which sharing stops creating value (s = 0).

    Solved in closed form from `sharing_spread`, because s is linear in the charge. This is
    the number a policymaker needs: below it energy sharing is worth doing, above it no
    allocation rule can rescue it.
    """
    reg = run.regime
    from .community import member_prices
    _, p_grid, p_export = member_prices(reg)
    other = (reg.shared_levies + reg.shared_electricity_tax) * (1 + reg.shared_vat) \
        + reg.internal_price * reg.shared_vat
    return (p_grid - p_export + reg.mieterstrom_surcharge - other) / (1 + reg.shared_vat)
