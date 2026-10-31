# 09 · Learned Forecast Layer, and a Review of Three Proposed Projects

> **Status, 2026-10-31.** Backbone A is built for the forecast-to-bid project, which is now
> `projects/03-probabilistic-forecast-to-bid` (it was "Project 06" when this was written; the
> text below keeps the numbers of its date: 05 is now 02, 06 is now 03, and 02, 03 and 04
> are archived). What was built, and where it departs from the plan below:
>
> - **Generation first, not price first.** Project 03's store forecasts the portfolio's
>   output, and its prices are realised, so a generation model plugs in without touching a
>   policy. The price model was not built.
> - **Weather from the Open-Meteo previous-runs archive**, not the historical-forecast API
>   named in section 3: that one stitches the shortest lead of every run and would leak.
>   ICON-D2-EPS has no public archive to backtest on, so no ensemble is used.
> - **The ladder F0 to F4** as specified: climatology, persistence, linear quantile
>   regression, LightGBM (an optional dependency), and the published TSO forecast as the
>   benchmark. F2 and F3 are recalibrated on the last 60 days of their training window.
> - **The code stays in `f2b`** (`features.py`, `models.py`, `evaluate.py`); moving the store
>   to `datakit/forecast/` waits until a second project uses it.
> - **Leakage is tested where it happens.** `audit_no_lookahead` could not fail and is gone;
>   `stages.audit_stages` checks decision times, and a test corrupts everything outside
>   each feature row's information set and requires identical features.
> - **The capture ratio uses the zero-imbalance reference**, which is not a bound under
>   signed imbalance settlement. The value table therefore leads with each rung's EUR gain
>   over climatology and its bootstrap interval, and gives the ratio only as a share of the
>   gap from climatology to that reference.
> - **Split by target time**: train 2024-02-18 to 2025-06-30, validation to 2025-12-30, test
>   2026-01-01 to 2026-10-30, evaluated once.
>
> Results and the commands that produce them: `projects/03-probabilistic-forecast-to-bid/README.md`.

Written 2026-09-25. Evaluates an external suggestion to merge Project 03 into Project 02,
and specifies the backbone of the work that should happen instead.

**Verdict in one line: the diagnosis is right, the prescription is wrong. The portfolio does
not need a merge and it does not need a seventh project. It needs one thing it currently does
not contain anywhere: a forecasting model fitted to data.**

---

## 1. What is actually on disk

Checked before writing, not taken from a README.

| Project | What it calls a forecast | Is a model fitted to data? |
|---|---|---|
| 01 Prosumer PV + BESS | Open-Meteo previous-run NWP, an independently issued weather forecast, run through the same pvlib model as the analysis | **Partly.** The input is a genuine out-of-sample forecast. The mapping from weather to PV is physical, not learned. Load forecast is the BDEW H0 standard profile, a lookup table. |
| 02 Pumped storage | AR(1) noise added to the true price path | No |
| 03 Smart EV charging | Sessions drawn from a hand-parameterised generator | No |
| 04 Energy sharing REC | Synthetic member profiles | No |
| 05 Hybrid plant | AR(1) noise, `forecast_sigma = 0.15`, `forecast_rho = 0.8` | No |
| 06 Forecast-to-bid | `f2b/forecast.py::build_store` perturbs the truth with AR(1) noise whose spread widens with lead time | **No** |

Project 03 is the important row. Its README reports a forecast-quality table (MAE 12.3 MW at
15 minutes rising to 49.6 MW at 24 hours, CRPS 9.3 to 35.7, calibration error 0.022 to 0.062).
Those numbers are not a measurement of a forecaster. They are a restatement of the noise
parameters that generated them. A reviewer who opens `forecast.py` sees this in about
thirty seconds, and everything downstream of it becomes suspect by association.

This is the actual gap in the portfolio, and it is a single gap, not three.

**What Project 03 does contain, and what is genuinely good in it:** the issue-time forecast
store. Every forecast is materialised once, tagged with the time at which it would really have
been available, and every policy reads the same object. `audit_no_lookahead` checks it
automatically. Look-ahead bias is made structurally impossible rather than a matter of
discipline. That is the most transferable piece of engineering in the repository and it is
worth more than the forecaster that is missing from underneath it.

---

## 2. Review of the proposal: merge Project 03 into Project 02

### What the proposal gets right

- The portfolio has too many projects and not enough depth in any single one.
- No project currently demonstrates supervised machine learning on real data, which is the
  first thing a trading or optimisation employer looks for.
- "What is a better forecast worth, in euro per year, to this asset?" is the right headline
  question. It is the question the job actually consists of.
- Coupling a distributional forecast to a decision, rather than scoring the forecast on its
  own, is the correct framing and is what separates this from a Kaggle notebook.

### Why the merge itself should not happen

**1. The two projects model different market actors, not two halves of one.**
Project 02 is an asset operator: a 300 MW / 2,400 MWh pumped-storage plant with reversal
wear, a grid-security objective, aFRR capacity commitments and the section 118(6) EnWG
network-charge exemption. Project 03 is a balance responsible party managing a renewable
portfolio across day-ahead, intraday and imbalance stages. Their state, their constraints and
their regulatory exposure have almost nothing in common. Merging them produces one project
that has to explain two market roles before it can state a result.

**2. The proposal describes Project 02 as "this battery".** It is not a battery. Duration,
round-trip efficiency, ramp behaviour, wear model and network-charge treatment all differ from
a grid-scale BESS. The euro-per-MW-per-year headline the proposal wants is a BESS trading
metric and does not transfer to a pumped-storage plant without restatement.

**3. The merge destroys the part of Project 03 worth keeping.** Dissolving it into Project 02
turns a reusable information-set discipline into one project's internal plumbing. The forecast
store should move in the opposite direction: out of Project 03 and into `datakit`, where all
six projects can read it.

**4. The coupling the proposal asks for already exists.** Project 03 already bids the median
(policy B1), already bids a quantile (the newsvendor policy NV), and already optimises over
scenarios built from the quantiles (B3 stochastic MPC), and already reports the spread between
them: 3,287,532 EUR for the point forecast against 3,576,029 EUR for stochastic MPC over 30
days. The architecture is finished. Only the model underneath the quantiles is missing.

**5. "Fixing the P02 bug is part of it" is out of date.** The B3-beats-B2 invariant violation
was found and fixed; the scenario grid now runs 0 violations. There is no outstanding P02 bug
for the merge to justify itself with.

### What survives

The value-of-forecast experiment. It is the right experiment and it gets **stronger**, not
weaker, when it is run across three different assets rather than one, because the answer
differs by asset and that difference is itself the result. A prosumer battery, a pumped-storage
plant and a wind portfolio have different sensitivities to forecast error, and being able to
say which and why is a better answer than a single number.

---

## 3. Backbone A: the learned quantile forecast layer

**The flagship. Do this first, before any new project and before further showcase work.**

### Scope

Replace the synthetic forecast generator with a trained quantile model, behind the interface
that already exists, and then measure what the improvement is worth to three different assets.

In scope:

- A supervised quantile forecaster for two targets: DE-LU day-ahead and intraday price, and
  aggregate wind and PV generation, both at 15-minute resolution.
- Training, validation and test splits that are strictly chronological, with the test period
  never touched during model selection.
- The same `ForecastStore` interface currently produced by `build_store`, so no policy,
  controller or test downstream has to change.
- Consumption of that store by Projects 06, 05 and 02.
- A value-of-forecast evaluation across those three assets.

Out of scope: intraday continuous order-book simulation, cross-border flows, unit-level
fundamental modelling.

### Objective

State, with a number and a confidence interval, what a better forecast is worth per year to
each of three assets, and show the mechanism by which the value arises.

### Background

The forecast-value question has a clean decision-theoretic form. Let `V(f)` be the realised
profit of a controller operating on forecast `f` over the test period. Three reference points
bound the answer:

- `V(perfect)`: the perfect-foresight ceiling, already computed as B2 in every project.
- `V(climatology)`: a naive forecast, for example the seasonal-hourly mean, as the floor.
- `V(learned)` and `V(synthetic)`: the two candidates.

The value of forecast skill is `V(learned) - V(climatology)`, and the fraction of the
attainable gain captured is `(V(learned) - V(climatology)) / (V(perfect) - V(climatology))`.
This is the same capture ratio the portfolio already uses for controller quality in Project 01,
applied to information quality instead. Reusing the metric is deliberate: it makes the two
kinds of improvement directly comparable, and it lets the portfolio answer a question most
applicants cannot, namely whether a better controller or a better forecast is the cheaper next
euro.

### Data requirements

| Input | Source | Access | Note |
|---|---|---|---|
| DE-LU day-ahead prices, 15 min | ENTSO-E Transparency, or Energy-Charts via `datakit` | free, already cached for 2024 to 2026 | hourly products before 2025-10-01, quarter-hour after; the regime change must be handled explicitly |
| Intraday prices and index | EPEX via SMARD | free download | needed for the intraday stage in Project 03 |
| Actual wind and PV generation, 15 min | SMARD or Energy-Charts | free, already cached | |
| Day-ahead TSO generation forecast | ENTSO-E Transparency, or netztransparenz.de | free | this is the operational benchmark to beat, not an input to copy |
| Load forecast and actual | ENTSO-E | free | |
| Weather: wind speed at hub height, irradiance, temperature | Open-Meteo historical forecast API | free, already used in Project 01 | must be the forecast issued at the time, not the reanalysis |
| Installed capacity by technology | already in `datakit` | | needed to normalise generation across years |
| Cross-border net position, gas and carbon price | ENTSO-E, EEX | free or partly free | second-tier features, add only if the first tier underfits |

The unavoidable discipline: every feature must be timestamped by **when it became known**, not
by what it describes. A day-ahead price forecast issued at 10:00 may use the TSO wind forecast
published at 08:00 but not the one published at 18:00. This is exactly what the existing
`ForecastStore` and `audit_no_lookahead` enforce, which is why the store moves to `datakit`
first and the model is built behind it second.

### Structure

```
datakit/
  forecast/
    store.py        moved from f2b/forecast.py, unchanged interface
    audit.py        audit_no_lookahead, promoted to a shared CI check
    features.py     calendar, lags, rolling statistics, weather joins, all issue-time safe
    targets.py      price and generation target construction, regime-aware
    models.py       quantile baselines and the learned model behind one fit/predict interface
    backtest.py     rolling-origin evaluation, pinball loss, CRPS, PIT, calibration error
```

Model ladder, mirroring the benchmark ladder the portfolio already uses for control:

| Rung | Model | Why it is there |
|---|---|---|
| F0 | Climatology: seasonal and hourly quantiles | the floor, and the denominator of the capture ratio |
| F1 | Persistence and seasonal persistence | the honest naive benchmark for short lead times |
| F2 | Linear quantile regression on calendar and weather features | shows how much is linear before any tree is fitted |
| F3 | Gradient-boosted quantile regression, one model per quantile level or a single pinball-loss multi-output model | the working model |
| F4 | Published TSO or exchange forecast where one exists | the operational reference the model must be compared against |

The ladder matters for the same reason the control ladder does. A gradient-boosted model
reported on its own means nothing; reported as beating persistence by a stated margin at each
lead time, it means something. Report F3 against F0, F1 and F4 at every lead time, and expect
F4 to be hard to beat for generation. Saying so plainly is worth more than a table that hides
it.

Quantile crossing must be handled explicitly, by monotone rearrangement after prediction, and
the rearrangement must be applied before the quantiles enter the store, because every
downstream policy assumes an ascending set.

### Deliverables

1. `datakit/forecast/` with the store moved and the model ladder implemented.
2. A reliability diagram and a pinball-loss-by-lead-time figure per target, both generated
   from committed CSVs.
3. Project 03 re-run on the learned store, with its README table restated and explicitly
   labelled as measured rather than parameterised.
4. Projects 05 and 02 switched from `forecast_sigma` noise to the learned store, with their
   B3 results restated.
5. One cross-project table: value of forecast skill in EUR per year and as a capture ratio,
   for the prosumer battery, the pumped-storage plant and the wind portfolio.

### Acceptance

- `f2b` tests pass unchanged after the store moves, proving the interface was preserved.
- `audit_no_lookahead` runs in CI over the learned store and passes.
- F3 beats F0 and F1 in pinball loss at every lead time, or the README states where it does
  not and why.
- The synthetic `build_store` path is either deleted or kept only as a labelled ablation.

### Honest risks

- **Price forecasting is hard and the model may not beat a good naive benchmark at day-ahead
  lead times.** That outcome is publishable as long as it is reported. It is a better result
  than a silently tuned test set.
- **The 2025-10-01 switch to quarter-hour products breaks stationarity** in the middle of the
  available data. Either train on one regime and test on the same regime, or model the regime
  explicitly. Do not pool them silently.
- **Generation forecasting against the published TSO forecast is a high bar.** Expect to lose
  at long lead times and to win, if at all, at short ones.

---

## 4. Backbone B: learned session forecasting for Project 03

**An upgrade to Project 03, not a replacement for it and not a new project.**

The proposal is right that Project 03's sessions are synthetic, and the README already lists
that as its first limitation. It is wrong that this warrants replacing the project. The
optimisation layer, the section 14a EnWG module logic, the earliest-deadline-first feasibility
floor and the right-censoring treatment are all correct and none of them needs rewriting. What
changes is where the sessions come from and what the controller knows about them.

### Scope

Fit a session model to public charging data, forecast departure time and energy demand per
session as distributions, and measure the cost of forecast error against an oracle that knows
both.

### Data

| Source | Contents | Access |
|---|---|---|
| Caltech ACN-Data | ~50k real sessions, arrival, departure, energy delivered, per-charger | free, registration |
| ElaadNL open datasets | Dutch public and semi-public charging sessions and transactions | free download |
| BDEW and German AC wallbox assumptions | for transferring to the German setting | documentation only |

Both sources are non-German, and saying so in the limitations table is mandatory. Arrival
patterns at a Caltech workplace car park are not those of a German apartment block. The
defensible framing is that the *structure* of the uncertainty transfers even when the levels
do not, and that the model is refitted per site.

### Structure

- `evc/sessions/fit.py`: fit arrival intensity by weekday and hour, and joint distribution of
  dwell time and energy, conditioned on arrival time and user history where the dataset has
  user identifiers.
- Quantile prediction of departure and energy at plug-in time, the only moment the controller
  gets information.
- Three information sets, matching the portfolio's existing pattern: oracle, learned
  quantiles, and the naive assumption the industry actually uses, which is a fixed assumed
  dwell time.
- The existing scheduler consumes all three unchanged.

### Headline question

How much of the achievable saving is lost to departure-time uncertainty, and does a
distributional forecast recover more of it than a point forecast plus a safety margin?

This is a question fleet operators pay people to answer, and the honest answer is likely to be
that a modest safety buffer captures most of the value. If so, report that. A result showing
that sophistication is not needed is still a result, and it is more credible than the opposite.

---

## 5. Backbone C: imbalance price direction and reBAP

**A module inside Project 06, not a seventh project.**

The proposal is right that this is the niche few applicants show, that the data is free, and
that BESS traders care about it. It is wrong that it should stand alone. Project 03 already has
an imbalance stage, already settles against reBAP, and already reports imbalance volume by
policy. A standalone project would duplicate that machinery to house one model.

### Scope

Predict the sign and magnitude of the system imbalance and the resulting reBAP, then test
whether a battery positioning itself ahead of the expected imbalance earns more than one that
does not.

### Data

| Source | Contents | Access |
|---|---|---|
| netztransparenz.de | reBAP, NRV saldo, aFRR and mFRR activation, 15 min | free, API and CSV |
| regelleistung.net | aFRR and FCR capacity auction results and activation | free |
| ENTSO-E | cross-border balancing, PICASSO and MARI volumes | free |

### Structure and the one thing that must not be got wrong

reBAP in Germany is a **single uniform price** applied to both directions of imbalance. There
is no dual pricing. A model built on the assumption that being long and being short settle at
different prices is wrong for this market, and it is the most common error in the literature
written for other jurisdictions. Project 06's settlement already handles this correctly and the
new module must not reintroduce the mistake.

The strategy layer must also state its own boundary: deliberately taking an imbalance position
to profit from the reBAP is only lawful within the limits of section 5 of the StromNZV
balancing rules and the BNetzA guidance on system-supporting behaviour. The defensible framing
is **reducing one's own imbalance cost and supporting the system direction**, not speculating
against the balancing mechanism. Write that boundary into the README before writing the model.

### Headline question

Does knowing the likely sign of the system imbalance one to four hours ahead change what a
battery should do, and by how much in euro?

---

## 6. What not to do

- Do not add a seventh project. Six is already more than the portfolio can keep current, and
  the audit of 2026-09-21 found twenty inert parameters and a fabricated figure across the six
  that exist.
- Do not merge Project 02 and Project 06.
- Do not start any of this before Project 01's figures are truthful. A fabricated benchmark
  chart on the public page costs more credibility than a learned forecaster buys.

## 7. Order of work

1. Fix the Project 01 figures. See the 2026-09-25 review in `HANDOFF.md`.
2. Move the forecast store from `f2b` into `datakit` with its interface and tests unchanged.
3. Build the model ladder F0 to F4 for one target, day-ahead price, and report it honestly.
4. Switch Project 03 to the learned store and restate its numbers.
5. Extend to generation, then switch Projects 05 and 02.
6. Publish the cross-asset value-of-forecast table. This is the flagship result.
7. Only then consider Backbone B, and Backbone C after it.
