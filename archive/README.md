# Archive

Three projects were moved here on 2026-10-09 when the portfolio was narrowed from six to
three. Nothing was deleted and nothing was abandoned mid-flight: each of these ran, was
tested, and produced the results its README reports. Their 88 tests are still collected by
the root `pyproject.toml` and still run in CI, so the code stays runnable and the history
stays honest.

## Why narrow the portfolio

Six projects could be listed but not defended. A reviewer who asks a second question about a
project expects the author to answer it, and the author had six surfaces to keep current
instead of three to know properly. The three that remain were chosen against the skills that
actually recur in the job postings collected in `Documents/Jobsuche/bewerbung/stellen.md`:
energy market and regulatory knowledge, mathematical optimisation, BESS dispatch, and time
series forecasting.

The pre-archive state of the repository is preserved at the tag
`archive/six-project-portfolio`.

## What is here

### `02-pumped-storage-rl-multimarket/` (package `psw`, 27 tests)

A 300 MW / 2,400 MWh pumped-storage plant co-optimising day-ahead energy, aFRR capacity and a
switchable grid-security objective, with mode-reversal wear and the §118(6) EnWG pumping
network-charge exemption.

**What it established.** The two-timescale action, a capacity commitment held across a
four-hour block while energy is dispatched every quarter-hour, and the invariant that caught a real
bug: B3 beat B2, which is impossible, because the continuous relaxation let the plant turbine
and pump in the same quarter-hour to game the ramp limit. The fix was a binary direction
variable.

**Why set aside.** Almost no employer in the target set operates a pumped-storage plant.
Germany has roughly thirty, and essentially no new ones. The market knowledge transfers; the
asset does not. The piece worth keeping is the aFRR capacity and energy co-optimisation, which
should move into Project 05, where the asset class is one that the target companies actually
own. Project 05 already declares `afrr_enabled`, `afrr_capacity_eur_mw_h` and
`afrr_block_steps` for exactly that extension.

### `03-smart-ev-charging-14a/` (package `evc`, 20 tests)

A depot charging hub under §14a EnWG, with the three dimming modules, a site connection limit,
and an earliest-deadline-first aggregate feasibility floor that guarantees no vehicle misses
its departure.

**What it established.** Two bugs worth remembering. Missed departures turned out to be an
artefact of truncating sessions at the end of the simulation window, fixed by right-censoring
rather than by changing the controller. And a per-connector minimum current silently undid the
EDF floor, fixed by tracking the required energy explicitly. Both took the counter from
18 missed departures to zero without weakening the guarantee.

**Why set aside.** Only one posting in the target set is a close match, and it is a team lead
role. The charging sessions are synthetic, so the project cannot yet claim a result on
measured arrivals and departures. The §14a knowledge is retained in
`docs/01-german-market-regulatory-primer.md`.

### `04-energy-sharing-rec/` (package `rec`, 41 tests)

Cost allocation inside a renewable energy community: a linear production game, the Owen dual
allocation, the Shapley value, core excess, and the break-even network charge at which sharing
stops paying.

**What it established.** The cleanest mathematics in the portfolio. The Owen allocation is in
the core by theorem, the Shapley value is not guaranteed to be, and the sweep across all 1,023
sub-coalitions shows where that matters. The break-even network charge comes out as a sharp
threshold rather than a gradual decline.

**Why set aside.** Cooperative game theory appears in none of the nineteen postings collected
for the job search. It is the most elegant project here and the least employable one.

## Running them

Nothing special. The root configuration already knows where they are:

```bash
pytest archive/02-pumped-storage-rl-multimarket/tests -q
pytest archive/03-smart-ev-charging-14a/tests -q
pytest archive/04-energy-sharing-rec/tests -q
```

Their figures, reports and scenario results are untouched under each project directory and
under `simulations/results/`. They are no longer shown in `showcase.html`.
