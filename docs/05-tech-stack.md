# Technical Stack & Engineering Conventions

A single stack across the portfolio, so components (market model, forecast store, safety
layer, evaluation harness) are shared rather than rewritten per project.

---

## Layers

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.11+ | Domain standard; solver and RL ecosystems both live here |
| Numerics | NumPy, pandas, xarray | `xarray` for NWP grids and ensemble dimensions |
| Optimisation modelling | **PuLP** (primary, MILP/LP), `linopy` (large LP) | PuLP with CBC/HiGHS for reproducible mixed-integer plant models |
| Solvers | **HiGHS** (open, default), Gurobi (academic licence, benchmarking) | Every model must solve with HiGHS so results are reproducible without a commercial licence |
| Power system | **pandapower** (LV/MV power flow), **PyPSA** (system-level), SimBench networks | Needed where grid constraints bind (Projects 03, 04) |
| Energy system modelling | oemof.solph (cross-checking) | Independent implementation to validate MILP formulations |
| RL environments | **Gymnasium**, PettingZoo (multi-agent, Project 04) | Standard APIs keep environments reusable |
| RL algorithms | **Stable-Baselines3** (PPO, SAC), CleanRL for custom variants | SB3 for trustworthy reference implementations; CleanRL where the algorithm is modified |
| Deep learning | PyTorch | Forecasting models and custom policies |
| Forecasting | statsmodels, scikit-learn, PyTorch (DeepAR/TFT-style, quantile & distributional heads), `properscoring` | Probabilistic first — CRPS and pinball loss, not just RMSE |
| Experiment tracking | **MLflow** | Runs, params, metrics, artefacts |
| Config | **Hydra** + OmegaConf | Config-driven runs; no magic numbers |
| Data versioning | **DVC** | Data snapshot hashes tied to results |
| Testing | pytest, hypothesis | Property tests on energy balance and constraint feasibility |
| Quality | ruff, black, mypy, pre-commit | Enforced in CI |
| CI | GitHub Actions | Lint, type-check, unit tests, a fast end-to-end smoke run |
| Docs | MkDocs Material, Mermaid diagrams | Diagrams live in Markdown, render natively on GitHub |
| Packaging | `uv` / `pyproject.toml`, Docker | Reproducible environments |

---

## Standard project layout

Every project implementation follows [`templates/project-template/`](../templates/project-template/):

```
project/
├── README.md               Dossier: background, scope, data, method, evaluation
├── pyproject.toml
├── Makefile                data · train · evaluate · reproduce-<experiment>
├── conf/                   Hydra configs
│   ├── config.yaml
│   ├── asset/              Physical parameters
│   ├── market/             Products, fees, tariffs, regulatory switches
│   ├── forecast/           Forecast model + issue-time policy
│   ├── baseline/           B1, B2, B3 configurations
│   └── agent/              RL algorithm and hyperparameters
├── data/                   (git-ignored; reconstructed via DVC/manifest)
│   ├── raw/ interim/ processed/
├── src/<pkg>/
│   ├── data/               Ingestion, cleaning, alignment, resolution handling
│   ├── model/              Digital model of the asset/site (the "plant")
│   ├── market/             Market products, settlement, fees, regulatory rules
│   ├── forecast/           Forecast models + immutable issue-time forecast store
│   ├── baselines/          B1 rule-based · B2 perfect-foresight · B3 rolling MPC
│   ├── envs/               Gymnasium environment wrapping model + market
│   ├── safety/             Projection / action masking layer
│   ├── agents/             Policies and training loops
│   └── eval/               Metrics, statistics, plots, report generation
├── tests/
├── notebooks/              Exploration only — never a source of reported results
└── reports/                Generated figures and tables
```

**Rule:** the `model/`, `market/` and `safety/` modules are shared by *all* rungs of the
benchmark ladder. B3 and the RL agent must be physically incapable of seeing different
physics or different fees — that identity is enforced by construction, not by discipline.

---

## Modelling conventions

**Time.** UTC internally, `Europe/Berlin` only for display. Left-closed, left-labelled
15-minute intervals. DST transitions covered by explicit unit tests.

**Sign convention.** Consumption/import positive, generation/export negative, at every
interface, consistently. Every energy balance is asserted in tests rather than assumed.

**Units.** kW/kWh behind the meter, MW/MWh in front of it; prices €/MWh; conversion only at
module boundaries with explicit unit-carrying types where practical.

**Efficiency.** Piecewise-linear (SOS2) in MILP; the same curve, unlinearised, in the
simulation model. The linearisation error is quantified and reported — it is one of the
structural advantages a learned policy may exploit.

**Degradation.** Battery: throughput plus rainflow-based cyclic ageing, valued at a
configurable €/kWh-throughput. Hydro: start/stop counting and mode-change cost.

---

## RL conventions

| Aspect | Convention |
|--------|-----------|
| Observation | Physical state + calendar features + forecast vectors (with their own uncertainty where available) + market state. Normalised with train-set statistics only. |
| Action | Continuous power setpoints (SAC) or discrete mode + level (PPO with masking), depending on the asset |
| Reward | Economic result of the step, plus soft terms (degradation, comfort). **Never** hard-constraint penalties |
| Episode | Typically 1–7 days at 15 min; terminal storage state valued via a fitted terminal value function to avoid horizon-end drain |
| Exploration | Domain-randomised over forecast error realisations and price regimes to prevent memorising a specific year |
| Evaluation | Deterministic policy, fixed evaluation episodes, ≥ 5 seeds |

---

## Non-negotiables

1. No result is reported that cannot be regenerated by a `make` target from a committed
   config and a hashed data snapshot.
2. No notebook output is ever cited as a result.
3. No model runs without its safety layer in any configuration presented as deployable.
4. Every MILP formulation has a unit test asserting energy balance and constraint
   feasibility on a small, hand-checkable instance.
5. Every project must run end-to-end with **open data and open solvers**.
