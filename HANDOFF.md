---
title: Session Handoff — AI in Renewable Energy Portfolio
tags: [portfolio, renewables, optimization, rl, handoff, github]
updated: 2026-09-02
assistants: [Antigravity, Claude Pro, Claude Code]
---

# 🚀 Session Handoff — AI in Renewable Energy Portfolio

> **Protocol:** Every session (Antigravity or Claude Pro/Code) reads this first. Before ending, update **Current State** and append one line to the **Log**.
> **Hygiene:** This portfolio is public on GitHub (`muratkus01/Portfolio`). **NEVER commit NDA-protected or proprietary ImWind thesis data here.**

---

## 📌 Standing Decisions & Architecture

1. **Monorepo Layout:** 6 structured projects under `projects/` with modular `src/` and `tests/`.
2. **Global Pytest Suite:** Configured in `Portfolio/pyproject.toml` — running `pytest -v` from the root executes all 94 unit tests across all 6 projects with 100% pass rate.
3. **Master Showcase Dashboard:** `python showcase.py` provides an instant executive readout with live simulation results across all 6 projects.
4. **Editable Installation:** All 6 packages (`prosumer`, `psw`, `evc`, `rec`, `hybrid`, `f2b`) are installed in editable mode (`pip install -e`).
5. **Specialized Skill Ecosystem:** 10 portfolio skills in `.agents/skills/` (shared with Claude via `.claude/skills` directory junction):
   - Governance: `portfolio-manager`, `portfolio-idea-generator`, `portfolio-data-fetcher`, `portfolio-showcase-builder`
   - Project Specialists: `project-prosumer-bess`, `project-pumped-storage`, `project-smart-ev-charging`, `project-energy-sharing-rec`, `project-hybrid-dispatch`, `project-probabilistic-forecasting`.

---

## 🚦 Current State: 2026-09-19 (session paused mid-batch, NOTHING COMMITTED)

Git is healthy (toplevel `Portfolio/`, clean history, remote `muratkus01/Portfolio`), but the
global `~/.claude/CLAUDE.md` still forbids git writes here, so all work below is an
uncommitted diff. Review with `git status` / `git diff`.

Done this session (portfolio-manager 5-pillar audit):
* **Clone-breaking bug fixed:** `.gitignore` pattern `data/` also matched the source package
  `src/prosumer/data/`, so `loaders.py` was never committed and Project 01 failed to import
  from GitHub. Patterns are now anchored. Measured-week tests skip cleanly without private data.
* **Tests 94 to 168 (local), coverage measured at 52%, now 93%.** The "100% coverage" claim was
  never measured. New: P01 ladder + env, P02 rolling + env, P05 rolling, offline CLI smoke tests
  for all 6 projects, P04 game. Root `pyproject.toml`: importlib import mode, coverage config.
* **P04 upgraded:** settlement accounting corrected (coalition value = consumer saving + owner
  gain = s x shared kWh), Mieterstrom surcharge moved from export to shared kWh, new
  `rec/game.py` (Shapley exact + Monte Carlo, Owen core allocation, exhaustive core check,
  break-even charge 0.110 EUR/kWh), `rec.cli game`, README section 0 rewritten.
* **`showcase.py` rewritten.** Old version imported about 12 functions that do not exist, every
  demo failed silently, and it printed hardcoded wrong test counts. New one computes every
  number live; offline by default, `--real-data`, `--tests`.
* **LAST EDIT, UNVERIFIED:** `projects/01.../baselines/milp.py` now names SoC constraints
  (`soc_{t}`) and returns `soc_value` (LP duals). Run the tests first.

---

## ▶️ Next Concrete Actions (in order)

1. ~~Verify~~ Done 2026-09-19: 168 passed, the milp.py duals edit is sound.
2. **P01 ON HOLD: Project 01 is being expanded in another session (Murat, 2026-09-19). Do not
   edit `projects/01-*` from this thread.** Uncommitted P01 changes already in the working tree
   from this session, all tested: `.gitignore` anchoring (fixes `prosumer/data/` never being
   committed), skip markers in `test_legacy_regression.py` and `test_time_and_data.py`, new
   `test_ladder_and_env.py` and `test_cli_prosumer.py`, and `milp.py` returning `soc_value`
   (SoC duals). Evidence for whoever fixes B3 (synthetic 2-day eval, fit on separate days,
   % of B1-to-B2 headroom recovered by B3):

   | tariff | sigma | H | blend (current default) | fitted linear curve | none |
   |---|---|---|---|---|---|
   | flat net charge | 0 | 24 h | -740% | -54% | **100.0%** |
   | flat net charge | 0.15 | 24 h | -726% | -40% | **23%** |
   | s14a Module 3 | 0.15 | 8 h | -281% | **22%** | -73% |
   | s14a Module 3 | 0.15 | 24 h | -228% | 22% | **72%** |

   With H = 24 h and no terminal value B3 reaches B2 exactly under perfect forecasts, so the
   controller is correct and the blend terminal value is the defect. A LINEAR terminal value
   drives end-of-horizon SoC to a bound; the principled fix is a concave, SoC-dependent
   terminal value. Recommended default: H = 96, `terminal_value=False`. README numbers
   (winter week 25.8%, resolution study) were produced with the blend and need re-running.
3. **P05 B3 loses to B1** (191,816 vs 199,059 EUR in showcase). Cause: B3 applies the LP's
   curtailment FRACTION, planned on forecast generation, to true generation. Fix: apply chosen
   curtailment only when the observed effective price is negative. Add test B3 >= B1.
4. **P03: 11 missed departures on every rung.** Suspect: the min-current rule runs after the
   EDF floor and switches off small floor allocations where per-connector need is 0, undoing
   the reserve. Check with `enforce_min_current=False`; fix by rounding floor allocations up.
5. Rerun `python showcase.py --tests` after 2 to 4.
6. **Root README rewrite:** remove false claims (per-project test counts, "production-grade",
   Pyomo, AT zone, MILP/PPO/quantile-regression claims), remove the skills section with dead
   `file:///c:/Users/...` links, restore the "Bugs the invariants caught" table, add the P04
   game result, real test count and coverage.
7. **Figures** (portfolio-showcase-builder): `scripts/make_figures.py` to `docs/figures/*.png`,
   embed in READMEs. matplotlib is installed.
8. **Skill corrections** (`.agents/skills`, private Thesis repo): reBAP is a single uniform
   price, not dual-price (project-probabilistic-forecasting, also no conformal prediction);
   s14a Module 2 reduces the energy component, not capacity, and no MILP exists yet
   (project-smart-ev-charging); LP not MILP, no variable-speed (project-pumped-storage);
   remove "Loss Attribution" from row 05 (portfolio-manager); add NDA guard
   (project-hybrid-dispatch).
9. **NDA collision:** remove the `baseline_rl_fair.py` (thesis-code) reference in
   `docs/08-milp-to-rl-roadmap.md` line 277.
10. **Prose hygiene:** em/en dashes and banned words across Portfolio `*.md` and docstrings
    (not governance titles). Check `](#` anchors before touching headings.
11. **CI:** `.github/workflows/tests.yml` (pytest on push, Python 3.11 and 3.12).
12. **Murat decides:** the "orphaned .git, no git writes" note in `~/.claude/CLAUDE.md` and
    `Documents/CLAUDE.md` is stale. Once cleared, commit this session's diff and push.
13. **Murat decides:** the public repo contains `tests/reference/12-18_08_2024_hourly_optimization_results.csv`
    (SES master thesis output, not ImWind) and this HANDOFF/CLAUDE with AI workflow logs.

Older roadmap items (still valid, lower priority): P01 RL on multi-year prices, P02
variable-speed efficiency, P03 V2G, P05 battery CAPEX sensitivity, P06 TFT forecaster.

---

## 📜 Session Log

- 2026-09-02 · Claude Code · Reorganized folder structure from `GitHub/` to `Portfolio/`.
- 2026-09-02 · Antigravity · Fixed git root, added root `pyproject.toml` (94 tests passing), deployed 10 specialized portfolio skills, created master `showcase.py`, updated root `README.md`, and pushed repository to GitHub.
- 2026-09-19 · Claude Code · Portfolio audit via portfolio-manager rubric: fixed `.gitignore` that kept `prosumer/data/loaders.py` out of git, tests 94 to 168 and coverage 52% to 93%, P04 settlement corrected plus Shapley/Owen/core game module, `showcase.py` rewritten to compute every number live. Paused mid-batch on request; uncommitted; 13-item to-do above.
