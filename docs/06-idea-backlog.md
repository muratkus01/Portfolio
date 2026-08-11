# Idea Backlog

Concepts that were scoped and assessed but deliberately not opened as full projects yet —
kept here so the portfolio's selection logic is visible, and so promising directions are not
lost. Each entry states why it is interesting, what would make it hard, and what would have
to be true to promote it to a full project.

---

## B1 · Green hydrogen electrolyser dispatch under RED III temporal correlation

**Idea.** Optimise the dispatch of a grid-connected PEM electrolyser co-located with wind/PV
against the EU's additionality, geographic and **temporal correlation** requirements for
renewable hydrogen (RFNBO), where the matching requirement tightens from monthly to hourly.
Objective: minimise levelised hydrogen cost subject to compliant sourcing, electrolyser
degradation, stack cold-start dynamics and a downstream offtake profile.

**Why interesting.** The hourly-matching rule turns a procurement problem into a genuine
sequential decision problem under uncertainty, and the interaction between compliance
constraints and spot price volatility is not well characterised. Highly relevant to German
industrial decarbonisation and the Kernnetz build-out.

**Why not yet.** The compliance rules are detailed and still settling in delegated-act
guidance; getting them wrong would invalidate the study. Electrolyser degradation models
with defensible parameters are hard to source publicly.

**Promotion criteria.** A stable, citable statement of the temporal-correlation rules plus a
publicly defensible degradation model.

---

## B2 · Distribution-grid congestion forecasting and `§14a` dimming prediction

**Idea.** From the DSO's side: predict low-voltage congestion hours and learn a dimming
policy that minimises the total flexibility curtailed while respecting voltage and thermal
limits — the counterpart to Projects 01 and 03, which treat dimming as exogenous.

**Why interesting.** Closes the loop: an agent that anticipates the DSO's own control policy
behaves very differently from one that treats it as noise. Directly addresses the operational
question DSOs actually face as heat pumps and wallboxes scale.

**Why not yet.** Requires realistic LV network data with measured loading; synthetic networks
(SimBench) make the result a statement about the synthetic network, not about Germany.

**Promotion criteria.** Access to a real (even anonymised) LV feeder dataset with
measurements, or a defensible argument that SimBench feeders are representative.

---

## B3 · Onshore wind farm wake-steering control with learned surrogates

**Idea.** Farm-level yaw and derating control to maximise farm output (or revenue, under
price-aware operation), using a learned surrogate of a wake model in place of expensive CFD,
with loads and fatigue as a constrained objective.

**Why interesting.** Physically rich, well-instrumented, real economic upside, and a clean
demonstration of surrogate modelling plus constrained control.

**Why not yet.** Overlaps only weakly with the market-facing spine of this portfolio, and
credible validation needs turbine SCADA that is essentially always confidential.

---

## B4 · Agentic assistant for renewable project permitting and regulatory Q&A

**Idea.** A retrieval-grounded assistant over German energy law, BNetzA determinations, TSO
prequalification documents and grid connection guidelines, that answers operational
compliance questions with citations to the governing paragraph and flags where the law is
in motion.

**Why interesting.** The regulatory research burden documented in
[`01-german-market-regulatory-primer.md`](01-german-market-regulatory-primer.md) is real and
recurring; a well-evaluated, citation-grounded system would be genuinely useful.

**Why not yet.** The value is entirely in evaluation rigour and hallucination control, which
means building a curated expert-answered benchmark first. That is the project, and it is a
large one on its own.

---

## B5 · District heating and power-to-heat sector coupling

**Idea.** Joint dispatch of a heat pump / electric boiler / CHP / thermal store serving a
German district heating network, under the heat planning law's decarbonisation trajectory and
the volatility of the electricity spot market.

**Why interesting.** Thermal storage is cheap and large; the coupling to the power market is
where much near-term German flexibility actually lives. Strong link to the SES background.

**Why not yet.** Needs a heat network model and network-specific demand data; the physics is
a project in itself before any control question is reached.

---

## B6 · Multi-market co-optimisation with explicit prequalification and pool constraints

**Idea.** Extend Projects 02/05 from a single asset to an aggregator's **pool**: which units
to prequalify, how to allocate a balancing capacity obligation across a heterogeneous pool in
real time, and how to price internal transfers between pool members.

**Why interesting.** The realistic commercial setting; the allocation problem inside the pool
is where the aggregator's margin comes from.

**Why not yet.** Only meaningful once the single-asset case (Projects 02 and 05) has produced
validated results. **This is the most likely next full project.**

---

## B7 · Counterfactual evaluation of controllers from logged operational data

**Idea.** Offline / off-policy evaluation: estimate what a proposed controller *would* have
earned, using only logged historical operation, with confidence bounds — avoiding the
simulator-fidelity problem that the B1 validation gate exists to manage.

**Why interesting.** It is the honest answer to "how do we trust your simulator?", and it is
the form of evidence an asset owner is most likely to accept before allowing a controller
near a real plant.

**Why not yet.** Needs logged data with genuine action diversity; historical operation of a
rule-based plant is close to deterministic, which is the classic obstacle to off-policy
evaluation.

---

## Selection criteria applied to this portfolio

A concept was promoted to a full project only if it satisfies all five:

1. **Reproducible on open data** in at least one configuration.
2. **A real regulatory hook** — a German rule that materially shapes the problem, so the work
   is not a generic RL benchmark in an energy costume.
3. **A defensible strong baseline** exists (a MILP-MPC that can actually be built).
4. **A plausible mechanism** by which learning beats that baseline — or a well-posed reason to
   expect it does not, which is itself a publishable answer.
5. **Distinct** from the other projects in either the asset physics or the market interface.
