# Contributing

This is a personal research portfolio, but review, correction and collaboration are welcome —
particularly on the two things hardest to get right alone.

## Where help is most valuable

**1. Regulatory correction.** German energy law changes fast, and
[`docs/01-german-market-regulatory-primer.md`](docs/01-german-market-regulatory-primer.md) is
a working document. If a statement there is outdated or wrong, please open an issue citing
the governing instrument. Corrections of this kind are the single most useful contribution.

**2. Data partnership.** Several projects have a named data gap — realised `§14a EnWG`
dimming events, site-level metered generation, German EV charging sessions, intraday
order-book depth. Each project's *Collaboration* section states what would most improve it.

## Reporting an issue

Useful issue types:

- **Regulatory correction** — cite the paragraph or determination and its date.
- **Methodological objection** — especially "your baseline is weak because…", which is the
  criticism this portfolio most wants to receive before, not after, publication.
- **Data source suggestion** — with the licence.
- **Reproducibility failure** — a documented `make` target that does not reproduce its result.

## Code contributions

If a project has an implementation:

1. Open an issue first to agree the direction.
2. Branch from `main`; keep the change focused.
3. `make test` must pass: ruff, mypy, and the property tests on energy balance, time handling
   and safety-layer feasibility.
4. New physics or new market rules require a test with a hand-checkable worked example.
5. Any change touching `model/`, `market/` or `safety/` affects **every** rung of the benchmark
   ladder — say so in the PR description and re-run the baselines.

## The standards this repository holds itself to

These are not negotiable in a contribution either:

- Hard constraints are enforced in the safety layer, never priced into a reward.
- Baselines are made as strong as practical and their configuration is published.
- Forecast parity between the learned policy and the classical baseline is structural.
- Negative results are reported. "MPC won" is a finding.
- No number is reported that a committed config and a hashed data snapshot cannot regenerate.

## Licence of contributions

Documentation contributions are accepted under CC BY 4.0 and code under the MIT Licence,
matching [`LICENSE`](LICENSE).
