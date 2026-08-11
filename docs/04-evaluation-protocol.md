# Evaluation Protocol

The rules below apply to every project in this portfolio. They exist so that a reviewer,
a supervisor or a hiring engineer can judge whether a reported improvement is real.

---

## 1. Data splitting

**Chronological only. No random splits, no shuffling, no k-fold across time.**

| Split | Typical share | Use |
|-------|--------------|-----|
| Train | earliest ~60 % | Policy learning, MILP parameter fitting, forecast model training |
| Validation | next ~20 % | Hyperparameter selection, early stopping, B3 horizon/terminal-value tuning |
| Test | latest ~20 % | **Run once**, at the end, reported regardless of outcome |

Additional requirements:

- **Regime coverage.** The test period must contain at least one high-volatility and one
  low-volatility interval; German price regimes differ enough between years that a single
  calm test year proves little. Where the record allows, a second test block from a
  structurally different year is added as an out-of-regime check.
- **Embargo.** A gap of at least one storage cycle (or one forecast horizon, whichever is
  longer) is left between splits so that state carry-over cannot leak.
- **No re-tuning after test.** If the test result is disappointing, that is the result. Any
  subsequent change restarts the protocol with a fresh test block, and this is disclosed.

---

## 2. Forecast realism

The most common way an RL-versus-MPC comparison is accidentally rigged is through
information asymmetry. Therefore:

1. Forecasts are **reconstructed at their true issue times** — for NWP-derived features, the
   run that would actually have been available at the decision moment, including its latency.
2. **Both** the learned policy and B3 consume the **same forecast objects**. This is enforced
   by construction: forecasts are materialised once, per issue time, into an immutable store
   that all controllers read from.
3. Where perfect-foresight information is used (B2 only), it is flagged in the run metadata
   and can never enter a deployable configuration.
4. A **forecast-quality ablation** is part of every project: performance is reported against
   perfect, realistic and degraded forecasts. This separates "the controller is better" from
   "the controller had better inputs".

---

## 3. Metrics

### 3.1 Economic

| Metric | Definition |
|--------|-----------|
| Net operating result | All revenues − all costs over the evaluation period, in €, at the asset boundary |
| Δ vs. B3 | Absolute and relative improvement over the deployable classical optimum |
| **B3-gap closure** | `(J_RL − J_B3) / (J_B2 − J_B3)` — headline metric |
| Revenue decomposition | Split by stream (DA, ID, balancing capacity, balancing energy, imbalance, network-charge savings, THG/EEG) |
| Risk-adjusted | Mean, 5 %-quantile and CVaR₅ of the daily result distribution |

### 3.2 Technical

| Metric | Definition |
|--------|-----------|
| Constraint violations | Count and magnitude, by constraint type. Expected: **0** with the safety layer |
| Storage throughput / cycles | Equivalent full cycles; degradation proxy |
| Curtailment | Self-chosen vs. ordered, separately |
| Self-sufficiency / self-consumption | Behind-the-meter projects |
| Schedule adherence | Deviation from committed positions; balancing availability delivered vs. sold |
| Decision latency | Wall-clock time per control decision, against the market gate it must meet |

### 3.3 Forecast (Project 06 and all forecast components)

Point: MAE, RMSE, bias. Probabilistic: **CRPS**, pinball loss per quantile, PIT histogram and
reliability diagram for calibration, Winkler score for intervals. Sharpness is reported only
together with calibration. Skill scores are computed against both climatology and persistence.

Crucially, forecasts are additionally evaluated by their **downstream decision value** — the
€ result obtained when the controller uses them — because a forecast improvement that does not
change any decision is not an improvement worth reporting.

---

## 4. Statistical treatment

- **Seeds.** Minimum 5 independent training seeds per learned configuration. Reported as
  **median with interquartile range**, never the best seed. Learning curves show all seeds.
- **Uncertainty on the test result.** Block bootstrap (block length ≥ 1 day, to respect
  autocorrelation) over the test period yields a confidence interval on the Δ-vs-B3.
- **Paired comparison.** RL and B3 are evaluated on **identical** episodes with identical
  exogenous data, so the difference is paired; the paired difference distribution is what is
  tested.
- **Significance.** A one-sided test on the paired daily differences, with the effect size
  reported alongside — an improvement that is statistically detectable but economically
  trivial is labelled as such.
- **Multiple comparisons.** Where several configurations are compared, this is disclosed and
  the selection procedure described, so the reader can discount accordingly.

---

## 5. Ablations required in every project

| Ablation | Question it answers |
|----------|--------------------|
| Forecast quality (perfect / realistic / degraded) | How much of the result is the controller vs. the inputs? |
| Horizon length (B3) | Is the classical baseline horizon-limited or forecast-limited? |
| Safety layer on/off | What does constraint satisfaction cost, and would the policy violate without it? |
| Reward/objective components | Which term drives behaviour? |
| Observation set | Which inputs actually matter? (Removing them should hurt.) |
| Regulatory parameters | Sensitivity to `§14a` module, negative-price rule vintage, network-charge exemption |

The regulatory ablation is a deliberate feature: it converts the domain's biggest risk
(the law changes) into a reported result.

---

## 6. Reproducibility requirements

Every reported number must be reconstructible from the repository:

- **Config-driven runs.** All parameters in versioned config files (Hydra); no magic numbers
  in code. The exact config is stored with the run.
- **Run metadata.** Git commit hash, data snapshot hash, config, seed, library versions and
  solver version recorded per run (MLflow).
- **Deterministic where possible.** Seeded RNGs; where a solver or GPU op is
  non-deterministic, this is stated rather than pretended away.
- **One command.** `make reproduce-<experiment>` regenerates a reported table or figure.
- **Environment pinned.** Lockfile committed; container definition provided.

---

## 7. Reporting standards

- **Negative results are published.** If MPC wins, the project reports that MPC wins and
  analyses why. Two of the six projects treat this as a genuinely expected outcome.
- **No unfilled placeholders presented as findings.** Un-run results appear as `[X]` and are
  visibly marked as planned structure.
- **Limitations section is mandatory** and specific: which behaviour is synthetic, which
  regulatory rule is simplified, which market coupling is omitted, and what that likely does
  to the result's direction.
- **Baseline configuration published in full**, so a reader can judge whether B3 was made
  weak.
