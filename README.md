# ⚡ AI for Renewable Energy Systems: Applied Research Portfolio

> Six engineering research projects applying **reinforcement learning, model predictive control, mathematical optimization (MILP), and probabilistic forecasting** to European power systems: strictly grounded in actual German market designs, grid codes, and regulatory frameworks.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://github.com/muratkus01/Portfolio/actions/workflows/tests.yml/badge.svg)](https://github.com/muratkus01/Portfolio/actions/workflows/tests.yml)
[![Optimization](https://img.shields.io/badge/stack-PuLP%20%7C%20Gymnasium%20%7C%20Stable--Baselines3-orange.svg)](docs/05-tech-stack.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Author:** Murat Kus · Dipl.-Ing. Mechanical Engineering · M.Sc. Sustainable Energy Systems (in progress) · B.Sc. Artificial Intelligence (in progress)  
**Domain:** Power System Economics, Energy Market Optimization, Sequential Decision-Making under Uncertainty  
**Focus Markets:** Germany (DE-LU bidding zone), EPEX SPOT Day-Ahead / Intraday (SDAC/SIDC), `regelleistung.net` Balancing (FCR/aFRR), and German **reBAP** Imbalance Settlement.

---

## 🎯 60-Second Executive Quickstart

Run the master interactive showcase dashboard to simulate live benchmarks across all 6 projects:

```bash
# Clone and install all 6 packages in editable mode
git clone https://github.com/muratkus01/Portfolio.git
cd Portfolio
pip install -e projects/01-prosumer-pv-bess-mpc-rl -e projects/02-pumped-storage-rl-multimarket -e projects/03-smart-ev-charging-14a -e projects/04-energy-sharing-rec -e projects/05-utility-hybrid-plant-dispatch -e projects/06-probabilistic-forecast-to-bid

# Run the Master Showcase Runner
python showcase.py

# Run all unit tests across the portfolio
pytest -v
```

---

## 🚀 The 6 Portfolio Projects

Each project is a fully structured Python package with clean modular `src/`, reproducible unit test suite in `tests/`, and CLI execution tools:

```mermaid
flowchart TB
    subgraph FOUND["Shared Foundations"]
        F1["5-Rung Benchmark Ladder: B0 → B1 → B2 → B3 → RL"]
        F2["15-Min MTU Market Model (DA, ID, aFRR, reBAP)"]
        F3["German Regulatory Constraint Layer (§14a, §41a, EEG §51)"]
        F4["Probabilistic Uncertainty & Forecasting Engine (P06)"]
    end

    subgraph BTM["Behind the Meter (Distributed Flexibility)"]
        P1["01 · Prosumer PV + BESS<br/>(15-min Dynamic Tariffs)"]
        P3["03 · Smart EV Charging<br/>(§14a EnWG Dimming)"]
        P4["04 · Energy Sharing & REC<br/>(P2P Game-Theoretic Settlement)"]
    end

    subgraph FTM["Front of the Meter (Utility & Market Operations)"]
        P5["05 · Utility Hybrid Plant<br/>(Wind+PV+BESS Behind Connection Cap)"]
        P2["02 · Pumped Storage Hydro<br/>(Multi-Market Co-Optimization)"]
        P6["06 · Forecast-to-Bid Pipeline<br/>(reBAP Imbalance Risk Minimization)"]
    end

    FOUND --> BTM
    FOUND --> FTM
    P1 -->|Aggregation| P4
    P3 -->|Flexibility Pooling| P4
    P4 -->|Aggregated Portfolio| P6
    P5 -->|Market Bidding| P6
    P6 -->|Scenarios & Quantiles| P2
    P6 -->|Scenarios & Quantiles| P5
```

---

### Project Breakdown & CLI Quickstarts

| # | Project Dossier | Core Method | Asset / Scale | Regulatory Framework | Quickstart Command |
|:---:|---|---|---|---|---|
| **01** | [**Prosumer PV+BESS Energy Management**](projects/01-prosumer-pv-bess-mpc-rl/) | Fast LP-MPC + Rescaled SAC RL | 8 kWp PV, 9.37 kWh BESS, 5.63 kW Inverter | §14a / §41a EnWG, EEG 2023 | `prosumer rolling-eval` |
| **02** | [**Pumped-Storage Hydro Multi-Market**](projects/02-pumped-storage-rl-multimarket/) | Multi-Market LP + Mode-Reversal Wear | 300 MW / 2,400 MWh PSH | FCR/aFRR, §118(6) EnWG Exemptions | `python -m psw.cli ladder` |
| **03** | [**Smart EV Charging Hub Control**](projects/03-smart-ev-charging-14a/) | Constrained Greedy/LP + EDF Reserve | 40-Connector Depot / 250 kW Limit | §14a EnWG Modules 1-3, Dimming | `python -m evc.cli ladder` |
| **04** | [**Energy Sharing in RECs**](projects/04-energy-sharing-rec/) | Mechanism Design + Cooperative Game Theory | 20-Member Community / 100 kW PV | EU RED II Art. 22, §42b EnWG | `python -m rec.cli mechanisms` |
| **05** | [**Utility Hybrid Wind+PV+BESS Plant**](projects/05-utility-hybrid-plant-dispatch/) | Co-located LP + B3 Rolling MPC | 50 MW Wind, 30 MW PV, 40 MWh BESS | EEG §51 / §51a Negative Price Rules | `python -m hybrid.cli ladder` |
| **06** | [**Probabilistic Forecast-to-Bid**](projects/06-probabilistic-forecast-to-bid/) | Quantile Loss + Newsvendor Fractile | 300 MW Portfolio | SIDC Intraday, German reBAP | `python -m f2b.cli policies` |

---

## 🛡️ Bugs the Invariants Caught

Every project asserts structural physical, electrical, and market invariants at runtime. Each invariant caught a real defect that would otherwise have produced plausible but invalid optimization results:

| Project | Invariant | What It Caught |
|:---:|---|---|
| **01** | B2 <= B3 <= B1; zero violations under random actions | Linear terminal storage valuation priced stored energy at full retail import, driving MPC to buy from the grid at every horizon boundary. Continuous action clipping truncated policy outputs at 80% (resolved via action rescaling). |
| **02** | Zero violations; symmetrical reservation across rungs | Reservoir bounds overrode ramp limits during overtopping (resolved by modeling spill). Capacity sold in aFRR auctions was previously omitted from heuristic baselines, letting B1 trade through reserved headroom and artificially depressing B3 recovery. |
| **03** | Zero missed declared departures | Per-connector feasibility checks failed to detect collective contention: the price-aware policy missed 108 departures until the Earliest Deadline First (EDF) aggregate floor was implemented. Truncating boundary arrivals at horizon termination falsely scored uncompleted visits as misses (resolved via right-censoring). |
| **04** | Core non-emptiness & individual rationality | Coalition value formulation previously mixed retail import savings with operator feed-in revenue; correcting the cooperative surplus proved cooperation remains viable up to a 0.110 EUR/kWh network charge. |
| **05** | B3 >= B1; every rung <= perfect foresight | B3 applied a planned curtailment fraction from forecast generation to actual generation, discarding deliverable energy. Voluntarily gating curtailment to negative prices lifted B3 to +5.4% over the unhybridized plant. |
| **06** | Lookahead audit clean; monotonic quantiles | Test fixtures previously shared stateful RNG instances between forecast synthesis and evaluation. Adding strict `IssueTimeStore` prevents any feature from leaking future market states. |

---

## 📐 Methodological Spine: The 5-Rung Benchmark Ladder

Every project enforces the same rigorous evaluation protocol:

| Rung | Controller | Purpose & Criterion |
|:---:|---|---|
| **B0** | Historical / Status-Quo | Baseline uncoordinated or unhedged reference. |
| **B1** | Rule-Based Expert Baseline | Industry-standard heuristic (e.g. greedy self-consumption, threshold shaving). |
| **B2** | Perfect-Foresight Optimization | Theoretical performance ceiling over the entire simulation horizon. |
| **B3** | Rolling-Horizon MPC | **The deployable classical benchmark** operating under imperfect lookahead. |
| **RL** | Learned Policy (PPO / SAC) | **Must beat B3 on out-of-sample data** to justify algorithmic deployment. |

---

## 📂 Repository Structure

```
Portfolio/
├── pyproject.toml                     ← Global multi-project test runner configuration
├── showcase.py                        ← Master terminal showcase dashboard
├── README.md                          ← You are here
├── HANDOFF.md                         ← Durable session state & dual-assistant memory
├── CLAUDE.md                          ← Assistant instructions & quickstart guide
├── datakit/                           ← Shared market data loaders & spot price connectors
├── docs/                              ← Energy market primers, regulatory guides & math
└── projects/
    ├── 01-prosumer-pv-bess-mpc-rl/    ← Package: prosumer (76 tests, 8 skipped without private data)
    ├── 02-pumped-storage-rl-multimarket/ ← Package: psw (27 tests passing)
    ├── 03-smart-ev-charging-14a/      ← Package: evc (20 tests passing)
    ├── 04-energy-sharing-rec/         ← Package: rec (41 tests passing)
    ├── 05-utility-hybrid-plant-dispatch/ ← Package: hybrid (26 tests passing)
    └── 06-probabilistic-forecast-to-bid/ ← Package: f2b (18 tests passing)
```

---

## 📜 Citation & License

This project is licensed under the [MIT License](LICENSE).  
If you use this portfolio or its formulations in academic research, please cite using [CITATION.cff](CITATION.cff).
