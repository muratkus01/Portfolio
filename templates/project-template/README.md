# Project Template

A proposed layout for a project in this portfolio. No project follows it in full, and the
projects share no evaluation harness or forecast store: each one settles, forecasts and
evaluates in its own code ([`docs/05-tech-stack.md`](../../docs/05-tech-stack.md)). This
directory holds only this README, so there are no skeleton files to copy yet.

## How closely the projects follow it

| Project | Code layout | Dossier checklist (below) |
|---|---|---|
| 01 prosumer | Closest: `data/`, `model/`, `market/`, `baselines/` and `envs/` packages, a `safety.py` module, `reporting/` for evaluation, and a Makefile with targets of its own. Configuration is dataclasses in `config.py`; there is no `forecast/` package and no `agents/`: the reported agent is trained in `experiments/rl_eval.py` (`prosumer rl-eval`), and `train_rl.py` trains on legacy weeks that are not in the repository | Items 1 to 8, 10 and 12; no sections on baselines and evaluation, work packages, repository structure, references or collaboration |
| 02 hybrid | One module per concern in `src/hybrid/`: `plant.py` (the plant and its safety layer), `market.py`, `baselines.py`, `data.py`, and dataclasses in `config.py`. No Makefile: `python -m hybrid.cli report` regenerates `reports/`. No `data/` folder, because datakit caches the inputs; no forecast store, because B3's forecasts are synthetic; no environment or agents, because it stops at B3 | All fifteen items, after a results section |
| 03 f2b | One module per concern in `src/f2b/`. Every forecast of the backtest is tagged with its issue step (`stages.Stage`), and an audit checks each decision's information set (`stages.audit_stages`); the issue-time store `forecast.ForecastStore` serves only the parametric-noise ablation. Dataclasses in `config.py`; `data/raw/` with a `SOURCE.md` for Open-Meteo and for netztransparenz, while the Energy-Charts data come through `datakit`; no Makefile | An order of its own: results, setup, errors found, limits, what is not built, reproduce, data and licences, files |

No project has `conf/` (Hydra), `.env.example`, `notebooks/` or the test files the layout
names.

## Layout

```
<project>/
├── README.md                 Dossier (see the section checklist below)
├── pyproject.toml            Dependencies, pinned; package name matches src/<pkg>
├── Makefile                  data · test · train · evaluate · reproduce-<experiment>
├── .env.example              API tokens (ENTSO-E, etc.) — never commit the real .env
├── conf/                     Hydra configuration — no magic numbers in code
│   ├── config.yaml           Composition root
│   ├── asset/                Physical parameters of the modelled asset/site
│   ├── market/               Products, fees, tariffs, regulatory switches
│   ├── forecast/            Forecast models and issue-time policy
│   ├── baseline/             B1 / B2 / B3 configurations
│   └── agent/                RL algorithm and hyperparameters
├── data/                     git-ignored; reconstructed via DVC or a documented manifest
│   ├── raw/                  Immutable. Never edited in place. Each subdir has SOURCE.md
│   ├── interim/
│   └── processed/            Analysis-ready Parquet — the only thing models read
├── src/<pkg>/
│   ├── data/                 Ingestion, cleaning, alignment, resolution-regime handling
│   ├── model/                Digital model of the asset/site ("the plant")
│   ├── market/               Products, settlement, fees, regulatory rules
│   ├── forecast/             Forecast models + immutable issue-time forecast store
│   ├── baselines/            b1_rule_based.py · b2_perfect_foresight.py · b3_rolling_mpc.py
│   ├── envs/                 Gymnasium environment wrapping model + market
│   ├── safety/               Projection / action masking layer
│   ├── agents/               Policies and training loops
│   └── eval/                 Metrics, statistics, plots, report generation
├── tests/
│   ├── test_energy_balance.py     Property test: energy in = energy out, every model
│   ├── test_time_handling.py      DST transitions, 15-min alignment, tz correctness
│   ├── test_milp_small.py         Hand-checkable MILP instance with a known optimum
│   ├── test_safety_layer.py       Random actions must never produce a violation
│   └── test_settlement.py         Worked examples of the regulatory accounting
├── notebooks/                Exploration only — never a source of a reported result
└── reports/                  Generated figures, tables, and the written report
```

## Structural rules it proposes

1. **`model/`, `market/` and `safety/` are shared by every rung of the benchmark ladder.**
   B3 and the RL agent must be physically unable to see different physics or different fees.
   This is enforced by module structure, not by discipline.
2. **The forecast store is immutable and issue-time-tagged.** Every controller reads the same
   forecast objects. Look-ahead bias becomes structurally impossible.
3. **Hard constraints live in `safety/`, never in the reward.** A policy must not be able to
   trade a violation against profit.
4. **No result outside `reports/`, and nothing in `reports/` that a `make` target cannot
   regenerate** from a committed config and a hashed data snapshot.
5. **`data/` is never committed.** `SOURCE.md` per raw dataset records URL, retrieval
   timestamp, licence and transformations.

Projects 01 and 02 keep rules 1 and 3 in their own module layout: every rung of their ladder
runs through the same plant, safety and market code. Rule 2 holds in Project 03, whose
`ForecastStore` refuses a target before its issue; Project 02 has no forecast store. Rule 4
holds in Projects 02 and 03 through their own commands instead of `make`; Project 01 marks
the few numbers that come from one-off runs. None of the three commits its data (rule 5).

## Dossier section checklist

A project README would cover, in this order:

1. Title and summary table (status, method, asset, resolution)
2. Background and motivation — why this problem, why now
3. Regulatory and market context — the German framework, as model input
4. Scope and objectives — in scope, **out of scope**, objectives
5. Research questions and hypotheses — falsifiable, in a table
6. System architecture — Mermaid diagram
7. Problem formulation — the optimisation model and the MDP, explicitly
8. Data requirements — table with source, resolution, licence, **and gaps named**
9. Baselines and evaluation — the ladder instantiated, KPIs, required ablations
10. Deliverables
11. Work packages and roadmap — with dependencies
12. Risks and limitations — including "the baseline might win"
13. Repository structure
14. References
15. Collaboration — what help would most improve the work

## Makefile targets

Proposed; only Project 01 has a Makefile, and its targets differ from these.

| Target | Does |
|--------|------|
| `make data` | Reconstructs `data/processed` from the manifest |
| `make test` | Lint, type-check, unit and property tests |
| `make baselines` | Runs B1, B2, B3 and writes their results |
| `make train` | Trains the learned policy (all seeds) |
| `make evaluate` | Runs the full evaluation protocol on the test split |
| `make reproduce-<name>` | Regenerates one specific reported table or figure |
