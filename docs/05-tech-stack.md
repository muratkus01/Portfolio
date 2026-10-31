# Technical Stack & Engineering Conventions

What the code of each project imports, checked against the source. The projects share this
small stack, and Projects 02 and 03 share the `datakit` data module; each project keeps its
own market model, safety checks and evaluation code.

---

## Layers

| Layer | Choice | Where it is used |
|-------|--------|------------------|
| Language | Python 3.10+ (`requires-python`); CI tests 3.11 and 3.12 | Every project |
| Numerics and data | NumPy, pandas; PyArrow for the Parquet caches | Every project |
| Linear programming | SciPy `linprog` with the **HiGHS** solver | Project 01: the perfect-foresight LP (B2) and every rolling-MPC window (B3), `baselines/lp_fast.py` |
| Optimisation models | **PuLP** with the **CBC** solver | Project 01's thesis MILP (Gates 0 and 1, `baselines/milp.py`); Project 02's dispatch models; the archived pumped-storage plant |
| PV model | pvlib | Project 01: PV output and PV forecast from Open-Meteo weather |
| RL environment | **Gymnasium** | Project 01 (`envs/`); the archived pumped-storage plant |
| RL algorithm | **Stable-Baselines3** SAC on **PyTorch** | Project 01's RL agent (`experiments/rl_eval.py`, `train_rl.py`); the archived pumped-storage plant used PPO |
| Forecasting | **LightGBM** quantile regression (F3) and linear quantile regression fitted with SciPy's L-BFGS (F2), both in the optional `[forecast]` extra; CI does not install LightGBM, so the F3 test skips there | Project 03 (`models.py`) |
| Data access | `datakit`: Energy-Charts over standard-library HTTP, no API key; Open-Meteo through the weather modules of Projects 01 and 03 | Projects 02 and 03 import `datakit`; Project 01 has its own loaders |
| Figures | Matplotlib | Project 01's figures; `scripts/make_f2b_figure.py` |
| Testing | pytest | Every project; the root `pyproject.toml` collects all six suites |
| CI | GitHub Actions | The full test suite on Python 3.11 and 3.12; a second workflow publishes `index.html` and `showcase.html` on GitHub Pages |
| Lint | ruff | Declared as a dev dependency, not run in CI |
| Packaging | One `pyproject.toml` per project (setuptools), installed with `pip install -e` | Every project |
| Docs | Markdown with Mermaid diagrams, rendered by GitHub | READMEs and `docs/` |

No project uses a commercial solver, a deep-learning forecaster, an experiment tracker, a
config framework or a data-versioning tool.

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
