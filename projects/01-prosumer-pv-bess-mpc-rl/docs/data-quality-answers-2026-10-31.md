# Project 01: data-quality answers, 2026-10-31

Answered on 2026-10-31. The audit started at commit 4c13a4a, and each of the four questions is condensed under its heading. Each answer names the commit it was checked at; its file:line references, path bases and lists of claims to fix refer to that commit. The fixes came in later commits; where one changed a point in the text, the point says "fixed in" with that commit.

Method: one investigator and two adversarial verifiers per question, merged into one answer. Results derived from the restricted company PV exports, numbers and rankings alike, are left out; for those, only the method is described.

## Q1. Price resolution and the 2025-10-01 interval change

**Question.** Where are the spot prices fetched, and at what native resolution in 2024, in 2025 before October and from 2025-10 on (counted in `datakit/cache/`)? How is an hourly price spread over four quarter-hours, and does a flat price inside the hour bias B2 and B3 downward? Is the change detected, documented or tested, what does the quarter-hour study compare, and which other series are hourly and upsampled?

Checked at commit 7ee3bfa. The five commits after 4c13a4a touch none of the price, solver or experiment code. Code paths are relative to `projects/01-prosumer-pv-bess-mpc-rl/src/prosumer/`. `tests/`, `reports/` and "README" mean P01's own; `scripts/`, `showcase.html` and `HANDOFF.md` are at the repository root. Data was counted on the local, gitignored data files.

1. **Where prices come from, and their resolution.**
   - **One chain feeds every 2024-2026 result.** `prosumer build-data` (`cli.py:188-190`) calls `build_site_dataset`, which calls `fetch_spot_prices` (`data/dataset.py:47`). That queries Energy-Charts (`data/loaders.py:25, 71`) and caches `data/raw/prices/energycharts_price_DE-LU_2024-01-01_2026-09-18.json` (`loaders.py:66`). The payload credits "Bundesnetzagentur | SMARD.de" under CC BY 4.0.
   - **Counted per Europe/Berlin day in that file:**
     - 24 timestamps on 2024-03-15, 2024-11-15 and 2025-03-15; 96 on 2025-10-01, 2025-11-15 and 2026-03-15.
     - Before 2025-10-01: 636 days of 24, two of 23, one of 25. From 2025-10-01: 351 days of 96, one of 92, one of 100.
     - The first quarter-hour product starts at 2025-09-30 22:00 UTC (00:00 CEST).
   - **So:** prices are hourly in 2024 and until 2025-09-30, and quarter-hourly from 2025-10-01. The spot column of all three site parquets is exactly this file, forward-filled (maximum difference 0.0).
   - **`datakit/cache/` is not P01's price source.**
     - No README or reports result reads datakit. Only the showcase sweep and the showcase dispatch figures do, through `datakit/processed/`, which is not committed.
     - `datakit/cache/` is not committed either. The local copy holds 2025 only.
     - Counted there, it agrees: 24 timestamps on 2025-03-15, 96 on 2025-11-15, and the same switch instant.
   - **Provenance gap.** The tracked `data/raw/prices/SOURCE.md` names the 2025-06-15..19 endpoint, because `fetch_spot_prices` rewrites that file on every cache miss (`loaders.py:75-82`). The repository does not record where the 2024-2026 file came from.

2. **The upsampling code.** `data/dataset.py:55` reads `"spot_eur_per_kwh": spot.reindex(idx, method="ffill")`, where `idx` is the 15-minute UTC index (`:36-38`). Each hourly price is held over its four quarter-hours.
   - Measured: 0 of 15,335 MTU-60 hours carry more than one price; 8,430 of 8,448 MTU-15 hours do.
   - The other forward-fills, `loaders.to_resolution` (`loaders.py:116-118`) and `attach_prices` (`:128`), feed only the superseded measured-week tables of README section 0 (`cli.py:27-32`).
   - One showcase script interpolates instead, breaking the package's own rule (`loaders.py:96-97`, "Forward-fill, never interpolate"): `scripts/generate_day_regimes.py:22`.

3. **The economic consequence.**
   - **The objective that runs.** B2 (`experiments/rolling_eval.py:88`) and every B3 re-solve (`baselines/rolling.py:132`) use the `lp_fast` objective (`baselines/lp_fast.py:27-31`):
     - cost = sum dt*(pi*p_imp - pe*p_exp) + c_deg*dt*sum p_dis - v_T*E_end
     - Charging carries no wear, because `EXTENSION_RUN` uses `wear_basis="discharge"` (`thesis.py:37`, `scenarios.py:20-24`; `lp_fast.py:27`).
     - Settlement is the same sum without v_T (`rolling_eval.py:66`).
     - The prices are pi = 1.19*spot + 0.1937 and pe = max(0, 1.19*spot - 0.01) (`thesis.py:52-57`).
   - **Where `milp.py` runs.** No 2025-2026 result solves the `milp.py` objective. It runs only in three places: Gate 0 (the hourly thesis year 2024, `tests/test_thesis_reproduction.py:80-89`), the superseded section-0 tables, and the showcase sweep. Its helper `default_terminal_price` also runs in the "blend TV" row (`rolling.py:125-128`).
   - **No price arbitrage inside an hour with one price.** The hour costs pi times its import, minus pe times its export, plus wear on its discharge. Moving energy between its quarter-hours does not change that.
     - It still changes cost when a quarter-hour moves between net import and net export. The balance holds per quarter-hour (`lp_fast.py:38-40`), so that move is worth the import-export spread, 0.20 EUR/kWh at the median.
     - So one price per hour rules out price arbitrage inside the hour, but not every gain from quarter-hour control.
   - **No bias against the market that existed.** Until 2025-09-30 the auction cleared hourly products. B2 is optimised and settled on the same per-step prices (`rolling_eval.py:88-90`).
   - **B2 is the ceiling up to a small LP artefact, not exactly.**
     - In 49 test quarter-hours the import price is negative: 12 on 2025-05-11 from 12:00 to 14:45, and 37 on 2026-04-25/26 and 2026-05-01. There are none in 2024.
     - With 1e6 kW grid limits (`thesis.py:30, 35`), the LP then imports and exports about 1e6 kW at once. Its one-day objective on 2025-05-11 is -244,904 EUR, so it misprices grid charging. B3 re-solves the same LP.
     - Re-solving those four days as a MILP with an import/export binary, with start and end SoC fixed to B2's, improves the settled B2 by 0.18 EUR.
     - All of that gain falls after the switch (0.0000 EUR on 2025-05-11). A crude upper bound is 12.26 EUR.
     - `HANDOFF.md:439` records an unreviewed fix.
   - **Compared with a quarter-hour market, the hourly period lacks the intra-hour price signal.**
     - After the switch that signal was worth 22.66 EUR to B2 and 26.32 EUR to B3 over 352 days. That is 9.6 % and 11.2 % of the period's 235.63 EUR headroom (`reports/quarter_hour_study_H28/resolution_summary.csv:3-4`).
     - No quarter-hour prices exist for January to September 2025, so the signal's value there cannot be established.
     - Even after the switch, the sign is measured, not guaranteed: the 37 negative-price quarter-hours fall inside the study.
   - **The published 625-day figures average two market designs.**
     - Of the 59,996 test steps, 26,204 (272.96 days, 43.7 %) have hourly prices and 33,792 (352 days) have quarter-hour prices. All 35,136 training steps of 2024 are hourly.
     - Split at the month boundary in the committed monthly CSV, H28 has 156.99 EUR of headroom at 87.15 % capture before the switch, and 234.70 EUR at 90.95 % after. Overall it is 391.69 EUR and 89.43 %. For H31 the two captures are 84.18 % and 87.39 %.
     - This split does not measure the switch. The same month's headroom differs between 2025 and 2026 by a factor of 0.66 (January, February) to 1.78 (July). The quarter-hour signal is worth only 0.33 to 3.74 EUR a month to B2.
     - In the study, the hourly-signal B3 captures 88.27 % of its own B2, but only 79.78 % of the quarter-hour B2.
   - **Not measured:** B3's terminal values (`rolling.py:159-168`) and the RL policy (`experiments/rl_eval.py:57-64`, price-shape features at `envs/realistic_env.py:72-75`) are learned on the all-hourly 2024 and used unchanged after the switch. What that does to the results is unknown.

4. **Is the change detected, flagged, documented or tested?** It is documented, but nothing detects it from the data or uses it.
   - **Docstring and column.** The docstring at `data/dataset.py:10-12` states the switch. `MTU_SWITCH` is hard-coded (`dataset.py:28`), and `price_mtu_min` is computed from it (`:49, 56`), not detected from the data. It matches the counted switch exactly.
   - **No reader.** Nothing reads `price_mtu_min`; a grep finds it only in `dataset.py`.
   - **One test, with three gaps.** `tests/test_data_2024_2026.py:149-158`:
     - skips when the dataset is missing, and CI builds none (`.github/workflows/tests.yml:39-40` runs only pytest);
     - reads only the H0 dataset (`:150`), not the HTW parquets behind the headline figures;
     - uses UTC slices that leave 2025-09-30 22:00-23:45 UTC out of both assertions, so the switch instant itself is never checked.
     On the local data, both assertions hold.
   - **README.** It names the switch at lines 11, 20, 28, 52, 129-142, 283 and 535. Yet each 625-day result (lines 58-73, 104-127, 144-201, 203-240) is reported as a single figure.
   - **No split evaluation.** `rolling_eval.py:70`, `rl_eval.py:32`, `sizing.py:57` and `household_sweep.py:48` all train on 2024 and test from 2025-01-01.

5. **What the quarter-hour study compares.**
   - **Range.** `prosumer quarter-hour-study` (`cli.py:363-367`, default data `site_2024_2026_htw_H28.parquet`) runs `run_resolution_study` with `EXTENSION_RUN` from 2025-10-01 00:00 CEST to 2026-09-17 23:45 CEST (`experiments/resolution_study.py:40, 56-59`). That is 33,792 quarter-hours, all MTU 15, so no hourly product contaminates it.
   - **Three cases:**
     - (a) B2 and B3 optimised and settled on the real quarter-hour prices;
     - (b) optimised on the hourly means of those prices (`:43-45, 63-65`) and settled on the quarter-hour prices (`:67`);
     - (c) an hourly model, with load, PV and prices averaged to hours (`:93-103`).
     B1 is price-blind, so its (c) error of -12.09 EUR isolates the effect of averaging the energy balance to hours.
   - **Remaining issues:**
     - Both arms use terminal values fitted on the all-hourly 2024 (`:60`).
     - The hourly signal is a synthetic mean, not an hourly auction price (`:19-21`, README:283).
     - The run starts from `soc_init`, so October 2025 differs slightly from the rolling eval (B2 -25.6021 against -24.7675 EUR).
     - Its B2 arms contain the 37 negative-price quarter-hours from point 3.
     - The column "value of 15-min signal EUR/yr" is value*12/len(monthly) over 12 months, the last of them only 17 days long (`:119-120`). So it equals the 352-day total. Annualised, it is about 27.3 EUR/a for B3 and 23.5 EUR/a for B2. (Fixed in 47643f0: the column now scales by 365 over the days covered, and a "days covered" column says 352.)

6. **Other hourly or upsampled series.** No other price component is hourly.
   - **Grid fees, levies, electricity tax and supplier margin** are one constant, `C_GRID = 0.1936996` EUR/kWh gross (`thesis.py:41, 52-55`; `market/tariff.py:33`).
   - **Time-varying fees and dimming are not used.** Time-varying fees exist only as s14a module_3 (`tariff.py:38-45`), which `THESIS_RETAIL_TARIFF` does not set. Dimming is built only by the superseded `ladder` command (`cli.py:116`), and the rolling path passes none (`rolling.py:138`).
   - **VAT** is a 19 % multiplier (`tariff.py:57`).
   - **Feed-in follows the spot.** It is max(0, 1.19*spot - 0.01), and zero when the spot is negative (`tariff.py:80-82`), so it is hourly until 2025-09-30, like the spot.
   - **The fixed EEG rate** of 0.0786 EUR/kWh (`config.py:88`) is used by no README result, only by the showcase sweep (`scripts/run_overnight_simulations.py:154`).
   - **Settlement** uses the same per-step arrays as the optimiser.
   - **reBAP** appears nowhere in P01 code. README:372 mentions it as background only.
   - **The one hourly-held input is the PV forecast `pv_fc_kw`.**
     - The Open-Meteo previous-runs weather is hourly (`data/weather.py:91, 99-103`), and `dataset.py:44-45` holds it over the hour (`ffill`, `limit=3`).
     - It is flat in 100 % of 11,903 daylight hours. It is NaN in 1,776 quarter-hours until 2024-01-19 10:45 UTC, where `rolling.py:77-78` substitutes yesterday's PV.
     - It feeds B3 (`rolling.py:118`) and the RL observation (`realistic_env.py:78`).
   - **Not held:** actual PV (Open-Meteo `minutely_15`, `weather.py:79`), the HTW load and the H0 forecast. None of their hours is flat.

**Published claims to fix:**
- P01 README: lines 3, 52, 60, 66, 83, 136, 413, 450, 470, 496/501/504/511, 534-536, 554 and 557.
- Root README: line 55.
- showcase.html: 1411, 1426, 1453, 1523-1527, 1550-1551, 1576 (plus the YEAR_CONFIG `cli` strings), the simulator cube (1610, 1636, 1645, 4866), 1692, and 2858/2862.
- The "EUR/yr" column of both quarter-hour CSVs.

**Measurement scripts** are not committed. A rerun of the verifiers' negative-price check settles the full B2 LP at -716.6751 EUR, as published, and gives a MILP gain of 0.1816 EUR.

## Q2. How sizing changes from one household to the whole country

**Question.** Does `national_impact.py` multiply a quarter-hourly dispatch time series by a household count, or only annual scalars? Which basis do published peak figures rest on, given a peak-to-mean of 11.62 for one HTW household and an implied Bavarian peak of 1,436 MW against 205 MW from H0? Is the asset size one national mean, what genuinely varies per state, and do `pv_states` and `aggregate_load` feed `national_impact` at all?

Checked on 2026-10-31 at commit 45374c6. `git diff --stat 4c13a4a..45374c6` touches only `mastr.py`, `household_sweep.py`, `showcase.py`, three tests and the root docs. None of those changes affects a line cited below. Data was read from the local, gitignored files. Nothing under `data/raw/thesis*/` was opened. Code paths are under `projects/01-prosumer-pv-bess-mpc-rl/` unless they start with `showcase` or `HANDOFF`.

1. **Gate, answered first: `pv_states` and `aggregate_load` do not feed `national_impact`.** They are a branch that was built but never connected.
   - `national_impact.py:39` imports only `from ..data import mastr`. A grep over every `.py` file finds neither module in `national_impact`, `cli.cmd_national_impact`, `showcase.py` or `dashboards/app.py`.
   - The only link runs the other way. `aggregate_load_states.py:38` has `from .national_impact import federal_states`, which it uses to read the 1 January counts.
   - Nothing calls `pv_states.generate` (`pv_states.py:259`); since eceb491 only tests do. Only `tests/test_aggregate_load.py` calls `aggregate_load.household_load` and `fleet_load`.
   - `unit_load` has one caller outside the tests: `aggregate_load_states.py:50-51, 93`. That code writes `reports/aggregate_load/state_summary.csv` and the V10 rows of `validation_summary.csv` (lines 125-129).
   - What the 2026-10-26 work reached:
     - The MaStR part reached published numbers: the showcase fleet totals and all 48 per-state rows.
     - `aggregate_load` reached only its own committed reports and README:242-276.
     - The PV half of `pv_states` (`model_1kwp`, `generate`, calibration) reached nothing. The calibration is written to the gitignored `data/processed/pv_state_calibration.json` (`pv_states.py:91`).
     - The `pv_states` coordinate table `STATE_SITES` did reach published tables, but only as the temperature sites of the load model (`aggregate_load.py:381`). `aggregate_load_fit.py:90` writes these coordinates into the committed `state_normals.csv`, so they shape `state_summary.csv` and README:269.
   - Indirectly, the load model's clock check (`aggregate_load_validation.py:84`, README:273) found the HTW clock defect. Its fix, c07847e, moved every headline number that uses an HTW household as truth (README:282: B3 capture went from 87.9 to 89.4 %).
   - No national figure depends on `pv_states` or `aggregate_load`.

2. **Nothing is multiplied by N except installed stock.** No time series is scaled, and no EUR/a or kWh/a figure either.
   - The only product with a household count is in `national_impact.py:168-170`: `total_count += count`, `total_capacity_mwh += (count * cap) / 1000.0` and `total_power_mw += (count * pwr) / 1000.0`. Here `cap` and `pwr` are the national-mean nameplate values (lines 114-115).
   - `compute_grid_exchange_shift` (line 199, `opt - rule`) is called only by `tests/test_national_impact.py:92`.
   - `evaluate_fossil_gas_displacement` is plain arithmetic on two numbers: `displaced = min(export_added, fossil_gas_generation_mwh)` (line 222).
   - Its only production caller is `cli.py:286`. That caller feeds it the hard-coded values in `REPRESENTATIVE_DAYS` (lines 235-276), for example `"peak_shift_mwh": -5264.31` (line 256).
   - No result of the 15-minute extension is scaled to a state or to Germany anywhere in the repository.

3. **Published peak and peak-shaving figures, and the basis of each.**
   - **The question's 11.62 and 1,436 MW** (also HANDOFF:313-318).
     - 11.62 is the median peak-to-mean of a single home among the 74 HTW homes. H28, the benchmark household, has 10.47; the range is 7.22 to 37.23.
     - The sum of all 74 homes gives 2.70. H0 gives 2.353 (`reports/aggregate_load/insample_2010.csv:4`).
     - 1,436 MW is 4,562 / 8,760 x 11.62 x 237,322 (Bavarian home storages on 1 January 2024).
     - Setting it against the 205 MW that H0 gives at 3,221 kWh/a mixes energy levels. At 3,221 kWh/a one household's profile gives 1,014 MW, so coincidence alone accounts for a factor of 4.94 (HANDOFF:325 says the same).
   - **Table 10-13** (`showcase.html:1571, 2004-2046`): national hourly shifts of -5,264.3, -5,775.9, -2,493.1, -692.5 and +5,448.1 MWh, with captions "Heavy peak shaving" and "Morning demand ramp shaving". **The basis is not established.**
     - The values are hard-coded (`national_impact.py:235-276`). No code and no committed CSV derives them. The test only checks shift / gas = percent.
     - Per unit of the thesis 2024 fleet (1,116,967 units, 6,167 MW, `tests/test_national_impact.py:50`), they are 0.62 to 5.17 kWh in one hour.
     - On the summer, negative-price and scarcity days that is 85.4, 88.3 and 93.7 % of the 5.52 kW mean inverter. Nearly every home battery in Germany would have to run close to full power in the same hour, all following one shared price. The thesis optimiser also had perfect foresight (README:18).
     - On the thesis clock (CET without summer time, `thesis_data.py:6`), those three hours are the day's highest, lowest and highest price.
     - Read these figures as an upper bound, not as what a real fleet would do.
     - Whether the thesis computed them as one household's dispatch times N cannot be told here. That is the method HANDOFF:317 rules out. A fleet of identical batteries optimised on the same price would give the same per-unit ratio.
   - **Fleet power** of 6.19 / 9.46 / 12.16 GW (`showcase.html:1569, 5350-5366`) and the per-state "Total Power (MW)" column (`showcase.html:5176-5231`) are unit count times the national-mean inverter rating. This is installed nameplate power, not a peak that occurs at the same moment. It is safe only when read that way.
   - **`state_summary.csv`**, columns `fleet_peak_mw` and `h0_fleet_peak_mw` (`aggregate_load_states.py:68-69`):
     - These do multiply a time series by N, but the series is the average-household model or H0, never one household. This is the diversified basis.
     - For Bavaria in 2024 at 3,221 kWh/a: 198.5 MW from the model, 204.8 MW from H0.
     - The model figure runs slightly low, because a real fleet's peak-to-mean lies above the model's 2.30 (README:266).
     - Neither the README nor the showcase quotes these columns.
   - **One household, no N:** README:339-340 (B2 peak import 3.66 to 5.76 kW), README:95 (5.63 kW inverter) and the `peak_kw` column of `reports/household_sweep.csv`.
     - README:339-340 ran on the `SiteConfig()` defaults, not on the thesis household (see item 4).
     - Its peak comes from B2 charging the battery from the grid. B1's peak import is 0.72 kW at both resolutions.
   - **Curtailment:** no modelled P01 figure is published. README:496 only names the `p_curt` variable.
     - The showcase simulator does show a one-household "Peak Grid Export" computed as `Math.min(inv, pv * 0.88)`.
     - It also shows "Clipping (x kW Overload)" when `pv > inv * 1.5` (`showcase.html:4880-4884`).
     - Both are rules of thumb. No model or CSV is behind them.

4. **Asset size: one national mean per snapshot, the same in all 16 states.**
   - `national_impact.py:114-115` gives every state `mastr.home_storage_mean()`, the published mean for all of Germany. Measured: 8.96 kWh / 5.53 kW on 1 January 2024, 8.92 / 5.66 in 2025 and 8.73 / 5.55 in 2026. There is one value per year.
   - **The reasoning is written only in code and in the HANDOFF:** `national_impact.py:45-61` ("therefore the same for every state. That is deliberate"), `mastr.py:178-188`, `tests/test_national_impact.py:54-55` and HANDOFF:297-299. The README and the showcase do not explain it.
   - **`data/raw/mastr/SOURCE.md` gives an older reason.** `national_impact.py:10` sends readers to this committed file for caveats. Its section 4 (lines 47-54) says per-state home counts "are not in this download at all". That contradicts the file's own lines 18-21, and `mastr.count_by_state` reads exactly those counts (fixed in c4e3918).
   - **The bias figures reproduce.** Dividing state energy by state home count gives 14.75 kWh for Bremen and 11.25 kWh for Bavaria. Scaled by the national home share, Bavaria gives 9.44 kWh. The home share of energy is 0.8387 in 2024 and 0.8008 in 2026.
   - **Household side.**
     - The thesis-extension results use `THESIS_SITE`: 9.37 kWh and 5.63 kW (`thesis.py:26, 34`).
     - Most reach it through `scenarios.EXTENSION_RUN` (`scenarios.py:20-25`; `rolling_eval.py:69`, `household_sweep.py:48`, `resolution_study.py:52`, `rl_eval.py:55`, `sizing.py:58`).
     - Gate 0 reaches it through `THESIS_MILP_RUN` and `THESIS_RULE_RUN` (`thesis.py:61-71`).
   - **README section 0 ran on the `SiteConfig()` defaults.** This covers README:288-361: Gate 1, the winter-week ladder, the resolution study and the August week.
     - The defaults are 8.9 kWh, 5.51 kW, 0.95 efficiency each way, a wear cost of 0.03 EUR/kWh on charged plus discharged energy, and a 6.4668 kW import limit (`config.py:25-46`).
     - `cli.py:72` uses `LEGACY_RUN`, whose site is built from those defaults (`config.py:176`).
     - `cli.py:111, 130, 159` build `RunConfig` without a site, so `config.py:148` fills in `SiteConfig()`. `train_rl.py:82` and `showcase.py:71` do the same.
     - It was already like this at ddd7a74, the commit that wrote section 0. `cli.py` has never referenced `THESIS_SITE` or `EXTENSION_RUN` (`git log -S`).
     - A rerun reproduces README:324-336 on `SiteConfig()` and not on `THESIS_SITE` (item 7).
   - **Country side.**
     - The fleet uses the national mean, not either household asset.
     - No code maps a household asset onto the fleet, because no national dispatch exists.
     - `pv_states` models 1 kWp per state (`pv_states.py:174-175`), so no PV size per state or per household exists either.

5. **What varies per state: in every published national figure, only the unit count.**
   - **Showcase table `THESIS_STATES_DATA`** (`showcase.html:5176-5231`):
     - All 48 counts equal `federal_states()`.
     - "Avg Storage" and "Avg Inverter" hold one value per year. MWh and MW equal units times that value to within 0.5.
     - The data is typed in by hand; nothing in the repository generates it. HANDOFF:303 says "generated from the register", but that was a one-off copy, so the table will silently go out of date after a new MaStR export.
   - **`StateFleetData`** differs per state only in `bess_count_{year}` and geography. It has no per-state load, PV, tariff or dispatch. Table 10-13 and the 48.95 and 85.0 EUR/MWh costs (`national_impact.py:139, 206`) are national constants.
   - **`state_summary.csv`:**
     - Three things differ per state: the holiday calendar (`aggregate_load_states.py:52`, `holidays=state`), the temperature anomaly at the state's site, and N.
     - The model coefficients, the energy level (4,658.9 or 3,221 kWh/a) and the proxy site are the same everywhere.
     - The model's peak per household differs between states by 0.78 % in 2024, 1.90 % in 2025 and 2.59 % in the partial year 2026.
     - Daily energy correlates 0.9938 to 0.9990 with the 16-state mean.
     - `h0_fleet_peak_mw` varies mainly with temperature, not with holidays. H0 is rescaled to the model's realised energy (`aggregate_load_states.py:58`). That energy differs between states by 0.51 % in 2024, while the H0 peak per realised kWh differs by only 0.04 %.
   - **`pv_states`** would differ per state: site weather, altitude and one fitted loss each, all on the same 35-degree south-facing array (`pv.py:31-32`). None of it is published.

6. **Assumptions, in the question and in HANDOFF, that are outdated or imprecise.**
   - Whether the national figures scale "a quarter-hourly dispatch time series" or "annual scalars (EUR/a, kWh/a)" does not arise. `national_impact` holds only installed stock plus the copied hourly 2024 thesis values.
   - The peak-to-mean of 11.62 belongs to the median HTW household, not to one chosen household. 1,436 MW against 205 MW mixes energy levels (item 3).
   - HANDOFF:300-302 says "`cli.py` never overrides `SiteConfig`, so every published result used 8.9 / 5.51". That holds only for section 0.
     - On that basis, 734fd17 (2026-10-26) put 8.9 / 5.51 into seven README lines.
     - cb3b837 restored four of them and left README:413, 470 and 536.
   - Other stale HANDOFF lines:
     - HANDOFF:128 says "16 federal state meta-households", but no meta-household dispatch exists in the code.
     - HANDOFF:291 names `FEDERAL_STATES`; it is now `federal_states()` (HANDOFF:387 notes this).
     - HANDOFF:303: see item 5.
     - HANDOFF:396: fixed in 285155e (`showcase.py:87-93`).
   - The docstrings at `aggregate_load.py:3-4` and `pv_states.py:3-4` say the national analysis multiplies one household's load or PV by N (fixed in 3c0f0a0). The current `national_impact.py` reads neither module.
   - That `slp.h0_profile` raises an error for any holidays other than `None` or `"BY"` (`slp.py:119-120`) is outdated since 7c0ca6d: `slp._holiday_set` (`slp.py:135-141`) accepts all 16 state codes. That is why `h0_fleet_peak_mw` reflects state holidays at all.

7. **How it was measured.** All runs used the code at that commit.
   - `get_national_totals(y)`, with `mastr.RAW` pointed at the local `data/raw/mastr`:

     | 1 January | Units | GWh | GW |
     |---|---:|---:|---:|
     | 2024 | 1,119,240 | 10.028 | 6.189 |
     | 2025 | 1,670,776 | 14.903 | 9.457 |
     | 2026 | 2,190,625 | 19.124 | 12.158 |

   - `htw_74_15min_2010.parquet` with `df.max() / df.mean()`: median 11.621, H28 10.472, maximum 37.234 (H31). The sum of 74 homes gives 2.701. Annual energy: mean 4,685.1 kWh, median 4,562.0 kWh.
   - `prosumer.cli resolution --data data/raw/legacy/09-15_12_2024.csv --fast --export-mode feed_in_tariff`. Values are 15 min / hourly:

     | Run | B1 cost (EUR) | B2 cost (EUR) | B2 peak import (kW) |
     |---|---:|---:|---:|
     | `SiteConfig()` defaults | 2.523 / 3.194 | 1.584 / 1.900 | 5.764 / 3.656 |
     | `RunConfig(site=THESIS_SITE, ...)` | 1.856 / 2.122 | 1.140 / 1.315 | 5.884 / 4.406 |
     | current default export mode | -4.108 / -3.712 | -12.939 / -12.420 | not used |

     B1's peak import on `SiteConfig()` is 0.721 / 0.718 kW. The current default export mode is `spot_gross_floored` (`cli.py:317`); at ddd7a74 it was `feed_in_tariff`.
   - `spot_de_lu_2024.parquet` (UTC index), at the code's hours 19, 17, 8, 9 and 12, in EUR/MWh:

     | Clock | 1 Jul | 12 Dec | 15 Mar | 16 Jan | 12 May |
     |---|---:|---:|---:|---:|---:|
     | CET | 166.79 (day max) | 936.28 (day max) | 68.75 | 138.60 | -135.45 (day min) |
     | German legal time | 102.47 | 936.28 | 68.75 | 138.60 | -100.06 |

     On legal time, -135.45 falls at 13:00.
   - `state_summary.csv`, rows `thesis_3221`: spreads as in item 5. Bavaria 2024: `fleet_peak_mw` 198.522, `h0_fleet_peak_mw` 204.814.

## Q3. Steps of the per-state PV profile (pv_states.py) as a numbered recipe

**Question.** Walk `pv_states.py` end to end as a numbered recipe: the sites and their provenance, the Open-Meteo request, the pvlib chain, the loss fit, what `validate()` leaves out and why, the Bavarian clock special case and whether a UTC reading was tested, whether a caller can tell a calibrated series from the 11 % fallback, and what one due-south array per state does to a fleet peak.

Checked on 2026-10-31 at commit 45374c6. At that commit `pv_states.py` had a single commit, c001488 (2026-10-26).

Where paths are:
- Code paths are under `projects/01-prosumer-pv-bess-mpc-rl/src/prosumer/`.
- `tests/`, `reports/`, `pyproject.toml` and README are relative to `projects/01-prosumer-pv-bess-mpc-rl/`.
- `scripts/` and HANDOFF are at the repository root.

Nothing under `data/raw/thesis*/` was opened.

No published result uses this module's output:
- `generate()` has no caller (since eceb491, only tests call it).
- `cli.py` never imports the module.
- `experiments/national_impact.py:39` imports only `mastr`.

It shows up in published material only through its 16 coordinates (step 1).

1. **Sites.** `STATE_SITES` (`data/pv_states.py:59-78`) holds one point per state, plus an approximate altitude (6 to 520 m). All 16 points are the city coordinates of the state capitals. They are not centroids and not weighted by population or PV:
   - BW Stuttgart 48.7823, 9.1770
   - BY Munich 48.1374, 11.5753
   - BE Berlin 52.5244, 13.4105
   - BB Potsdam 52.3989, 13.0657
   - HB Bremen 53.0752, 8.8078
   - HH Hamburg 53.5753, 10.0153
   - HE Wiesbaden 50.0826, 8.2493
   - MV Schwerin 53.6296, 11.4148
   - NI Hannover 52.3759, 9.7320
   - NW Duesseldorf 51.2217, 6.7762
   - RP Mainz 49.9842, 8.2791
   - SL Saarbruecken 49.2333, 7.0000
   - SN Dresden 51.0509, 13.7383
   - ST Magdeburg 52.1277, 11.6292
   - SH Kiel 54.3213, 10.1394
   - TH Erfurt 50.9848, 11.0299

   What else is known about these points:
   - Provenance: the city names are in the code, but in another module. `experiments/national_impact.py:78-95` (`_GEO`, from ab443fa on 2026-09-25) lists "Stuttgart" to "Erfurt" with identical coordinates, so `STATE_SITES` was evidently copied from it. Neither table cites a source.
   - The comment at `pv_states.py:46-49` is wrong in two places (fixed in 3c0f0a0):
     - "Capitals, except where ..." names no real exception.
     - Altitude does not "matter only through the Faiman cell-temperature model". `faiman()` takes no altitude. Altitude only enters the solar position, and 520 m against 0 m changes the BY 2024 yield by -0.00094 %.
   - Company sites: the loader reads only one time column and one value column (`:122-138`). So it is unknown whether the company's exports were computed at these points.
   - BY and the household: the BY point is 31 m from `weather.MUNICH` (`weather.py:26`). Its 2024 weather is identical (max abs diff 0.0 over 35,136 rows). So the BY series equals the household PV per kWp to within 2.5e-5 kW/kWp.
   - Use in published material: the points are the ERA5 temperature sites of the aggregate-load model (`data/aggregate_load.py:381-388`, `experiments/aggregate_load_fit.py:72-93`). They are committed in `reports/aggregate_load/state_normals.csv`. The NI point is the proxy (`aggregate_load.py:62` "FIT_PROXY = \"NI\""). It also feeds `fit_temperature_2010.csv` and the README figures at lines 263-269 and 274.
   - Published text names only Hannover (README:274, `reports/aggregate_load/SOURCE.md:31`). It never says the points are capitals.

2. **Weather.** `model_1kwp` (`pv_states.py:174-179`) calls `weather.fetch_weather_actual` for the state's point.
   - Endpoint: `ACTUAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"` (`weather.py:32`). This archive serves the shortest-lead NWP model output for each time (`weather.py:5-7`). It is neither reanalysis nor measurement.
   - Model: the request has no `models` parameter (`weather.py:77-79`), so Open-Meteo chooses the model. Nothing records which one: `data/raw/weather/SOURCE.md` only says "(minutely_15)".
   - Variables (`weather.py:27-31`):
     - shortwave, direct-normal and diffuse radiation, which become ghi, dni and dhi in W/m2;
     - `temperature_2m` in degC;
     - `wind_speed_10m` in km/h (`pv.py:54` divides it by 3.6).
   - Resolution: `minutely_15`. The quarter-hours are real 15-minute values, not interpolated hourly ones: only 6 of 4,183 hours with clear-sky GHI above 300 W/m2 have four quarter-hours on a straight line.
   - Timezone: the request sets `"timezone": "UTC"` (`weather.py:78`). The stamps are parsed with `utc=True` and shifted back 15 min so each one marks the start of its interval (`weather.py:12-14, 63`).
   - Span: the fit and the validation request [2024-01-01, 2025-01-01) (`pv_states.py:190, 205`; `scripts/calibrate_pv_states.py:31` "YEAR = 2024"). There is one request per calendar year (`weather.py:52-58`).
     - The cached BY data run from 2023-12-31 23:45 to 2024-12-31 23:30 UTC (35,136 rows).
     - Every full day has 96 rows. The first day has 1 and the last 95, so the night interval at 2024-12-31 23:45 is missing. That does no harm.
     - All 16 points are cached for 2024 only, so HANDOFF next action 4 is still open.
   - Related README claims:
     - README:451 "Open-Meteo / DWD" cannot be traced: no code calls DWD.
     - README:534 lists the forecast weather as "15 min". It is hourly, and each value is held for four quarter-hours (`weather.py:91-102`, `dataset.py:45`).
     - README:533-534 say "Complete, committed", but only the `SOURCE.md` files are tracked under `data/`.
     - README:280 "0.31 against independent ERA5" has no code behind it. ERA5 is fetched only as daily temperature (`weather.py:112-140`).

3. **pvlib chain** (`data/pv.py:40-58`). `site_system` sets only kWp, loss and location (`pv_states.py:168-171`). Everything else is a `PVSystem` default (`pv.py:28-37`). The chain runs in five steps:
   1. Sun position at the interval midpoint, "idx + step / 2" (`pv.py:47`), with pvlib defaults.
   2. Hay-Davies transposition (`pv.py:52`) at tilt 35 deg and azimuth 180 deg (`pv.py:31-32`). Albedo is not passed, so the pvlib 0.16.1 default of 0.25 applies. `pyproject.toml:19` pins only "pvlib>=0.10".
   3. Faiman cell temperature with its defaults (u0 25.0, u1 6.84), fed the 10 m wind (`pv.py:53-54`).
   4. PVWatts DC at 1000 W per kWp and -0.4 %/K, with no low-light term (`pv.py:55-56`). Total plane-of-array irradiance is used directly, with no IAM, spectral, soiling or shading term.
   5. "ac = (pdc * (1 - system.system_loss)).clip(lower=0.0, upper=system.kwp)" (`pv.py:57`). This is one lumped loss, with no inverter curve and a DC/AC ratio of 1.0. In the 2024 lossless runs the clip binds in at most 3 quarter-hours per state.

   The household PV runs the same chain with defaults (8 kWp, loss 0.0766, Munich).
   - `cli.py:190` passes no `pv_system`, so `dataset.py:32` supplies the default.
   - That is what actually ran. Rerunning `pv_power` on the cached Munich weather reproduces `pv_kw` in `site_2024_2026.parquet` exactly (max abs diff 0.0).
   - The H28, H31 and 2025-06-18 parquets carry the same values. 2024 comes to 1195.08 kWh/kWp.
   - This holds for every result built on those datasets. It does not hold for Gate 0, which uses the thesis PV (`tests/test_thesis_reproduction.py:34`).

   The P01 showcase does not use this model at all:
   - Its dispatch figure plots national Energy-Charts solar output, scaled so the peak is 7.5 kW (`scripts/generate_all_figures.py:114-116`). It is labelled "Empirical Rooftop PV Generation".
   - Its 24-hour widget uses hand-set sine curves (`scripts/generate_day_regimes.py:24-43`). Its "7.6 kW peak" on 26 June 2024 compares with 5.63 kW in the model.

4. **Loss fit** (`pv_states.py:188-195`). One scalar per state, in closed form, fitted on annual energy only: "return float(np.clip(1.0 - target / lossless_yield, 0.0, 0.5))".
   - Target: "target = float(reference.sum())", the sum of every row the loader returns from the state's company 1 kWp export.
     - For 15 states that is the `MODELLING_ERA5_KWH` column (`:123-125`). By its name this is a model output, so the fit matches one model to another, not to metered output.
     - For Bavaria it is the first `P_PV*` column (`:126`), and the `year` argument is ignored there.
   - How it runs: only through `scripts/calibrate_pv_states.py`, never through the CLI. It writes the gitignored, restricted `data/processed/pv_state_calibration.json` (HANDOFF:367-373).
   - Fitted values: they stay in the gitignored calibration file and are not repeated here, because with the public model they would give back the restricted targets.
     - The public household loss is the same fit against the public thesis yield: 1 - 1195.1/1294.22 = 0.07659, the 0.0766 of `pv.py`.
     - So the scalar corrects bias (point weather against a company model, orientation, plus real losses). It is not a physical loss.
   - Missing checks: the docstrings promise "asserts the result is a complete hourly year" (`:22-23`) and "asserts the length matches" (`:101-102`). Neither check exists. (The docstrings were corrected in 3c0f0a0; the checks are still absent.)
     - The index is simply `periods=len(values)` counted from 1 January (`:104-105`).
     - Gaps are counted on the sorted stamps (`:106`), but the values are used in file order (`:109`).
     - Three faulty synthetic files all pass without any error: one of 100 rows starting 2024-03-01, one in reverse time order, and one from 2023.

5. **validate().**
   - It says why annual yield is left out (`pv_states.py:201-202`): "Annual yield is excluded on purpose: the loss was fitted to it, so agreement there is arithmetic rather than evidence."
   - What it measures instead (`:229-239`):
     - daily R2, which is the squared Pearson correlation of the daily sums;
     - daily and hourly nRMSE;
     - peak ratio;
     - worst month;
     - the offset of the generation centroid. Its comment: "A one-hour error shows here even when the yield matches exactly" (`:215-216`).
   - It still returns the raw yields (`:227-228`), and the script prints them (`calibrate_pv_states.py:69`).
   - `hourly_bias_pct` (`:232`) is the annual-yield error under another name, so it is zero by construction, up to rounding.
   - There are no thresholds, no tests and no committed report.
   - README:53 "daily correlation with the thesis PV 0.95" traces to no committed file, and the other correlations in the same cell trace to nothing at all.

6. **Bavaria clock.** Whether the Bavarian labels mean CET or UTC is neither tested nor worked around. `pv_states.py:134` reads the Bavarian timestamps as fixed UTC+1: "local = ts.dt.tz_localize(dt.timezone(dt.timedelta(hours=1)))".
   - `validate()` reports, per state, the generation-centroid offset and the hourly nRMSE, which is where a one-hour error would show. Its results stay in the gitignored calibration file and are not repeated here.
   - The clock changes no output: the fit uses only the annual sum, and `generate()` never reads the reference.
   - The comment at `:130-133` does not prove its point (fixed in 3c0f0a0):
     - "no repeated hour in March and none missing in October" has the months the wrong way round. As written it rules nothing out.
     - Under pandas 3.1.0, a complete 2024 index without daylight saving fails `tz_localize("Europe/Berlin")` first at 2024-03-31 02:00. Failing at 2024-10-27 02:00, as the comment says, is what an index in legal local time does. A DST-free index only fails there if `nonexistent=` is set.
     - No daylight-saving test can tell fixed UTC+1 from UTC.
   - Two more text errors (fixed in 3c0f0a0):
     - The docstring line "converted to UTC using Europe/Berlin" (`:118-119`) contradicts `:134`.
     - "used by the HTW load profiles" (`:133`) is out of date since c07847e (`htw.py:13`).
   - A file in legal local time would show itself: in a synthetic test it either raises "cannot reindex on an axis with duplicate labels" or `validate()` compares one hour less.
   - A UTC reading was never tested. The only test that touches the module imports `STATE_SITES` (`tests/test_aggregate_load_data.py:24`); `tests/test_pv_states.py`, added in eceb491, tests the fallback loss, not the clock. c001488 already named the UTC test as the next step.
   - The same kind of error was found before: the thesis Munich PV was found to be one hour early, and tested, on 2026-09-19 (d1c1b52, `thesis_data.py:10-17`, `tests/test_thesis_reproduction.py:92-106`). `pv_states.py:27-28` refers to that finding.
   - The BY reference is probably that thesis profile:
     - c001488 calls it "the one the thesis used for its Munich household";
     - its value column `P_PV_1` is named like the thesis column `PV_Bayern_1` (`pv.py:25`).
     - Only the restricted file can confirm it.
   - README:43-44 (the count of identical hours and the 1018.2 EUR) trace to no committed code or CSV.
   - Reading the labels as UTC instead would move the reference one hour later. That changes the centroid offset and the hourly nRMSE, and leaves the daily R2, the worst month, the peak ratio and the loss as they are. This is arithmetic only; it was not run.
   - Open checks, which need the restricted file and so can run only locally:
     - count the 02:00 rows on 2024-03-31 and on 2024-10-27;
     - rerun `validate()` with the timestamps read as UTC.

7. **Calibrated or fallback.** Only a separate call to `system_loss_for(code)` can tell them apart. It returns `(loss, "calibrated" | "fallback")` (`pv_states.py:251-256`). (Fixed in eceb491 and dbba141: `generate()` and `site_system()` now issue a `FallbackLossWarning` that says why, and `generate()` records the loss and its source in the `attrs` of its series.)
   - Everywhere else the source is lost:
     - `generate()` drops it ("loss, _ = system_loss_for(code)", `:261`).
     - `site_system` silently substitutes 0.11 (`:170`).
     - `load_calibration()` returns an empty dict when the json is missing (`:244-248`).
     - `pv_states.py`, `pv.py` and `weather.py` have no warning, no log line and no `attrs`.
     - So "and says so" (`:16`) and HANDOFF:374 are true only for `system_loss_for()`.
   - States can silently mix calibrated and fallback losses (since eceb491 `generate()` warns for each fallback state; the script still does not):
     - The script's subset mode (`calibrate_pv_states.py:12`) and any state whose fit raises (`:54-56`) are left out of the json.
     - The script then overwrites the whole json (`:79-85`), so those states fall back to 11 % without notice.
     - `calibration_path()` is built from `Path(__file__).resolve().parents[3]` (`:44`). A non-editable install would therefore fall back even with the json present.
   - Without `data/processed`, as in a fresh clone, `system_loss_for("BY")` returns `(0.11, "fallback")`.
     - A fresh clone or CI would run all 16 states at 11 %, so annual yield would differ from the calibrated model wherever the fitted loss is not 11 %.
     - Nothing outside the tests calls `generate()` yet, so no result is affected today.

8. **Orientation.** Every state is one 1 kWp array at 35 deg facing due south (`pv_states.py:168-171`, `pv.py:31-32`), under one weather point.
   - With no mix of orientations and one shared cloud pattern per state, output bunches at solar noon. That makes peaks and ramps coincide as much as possible.
   - The calibration only rescales annual energy, so this shape error survives it.
   - An illustration, computed for this answer and not a published number. It assumes an equal mix of E, SE, S, SW and W arrays at 35 deg, because the repo holds no orientation data. BY 2024 weather, and the loss fitted to the public thesis yield (0.0766):
     - At equal kWp, south-only gives 12 % more energy (1195.1 against 1066.8 kWh/kWp). Its highest 15-minute value of the year is 19 % higher (0.921 against 0.773 kW/kWp).
     - At equal annual energy, which is what the calibration enforces:
       - the highest value of the year is +6.3 % (0.921 against 0.866);
       - the mean daily maximum is +7.3 % (0.571 against 0.532).
     - 73.6 % of the energy falls between 09:00 and 15:00 CET, against 70.8 % for the mix. The early and late hours get less: before 09:00, 11.1 % against 12.5 %; after 15:00, 15.2 % against 16.7 %.
     - The peak of the mean May-August day is about the same (0.546 against 0.552).
   - So the bias shows in extremes and ramps, not in average summer middays. The shared cloud pattern overstates them further, by an amount not measured.
   - `validate()` also reports each state's peak ratio against the reference; those results stay local. No fleet peak from this module is published.

How this was measured: read-only, with the code at that commit and local caches, and no network. The calibration json was read for aggregates and booleans only, and none of its results is given here. Both verifiers reproduced the orientation figures, which need only public data.

## Q4. Steps of the per-state load profile (aggregate_load.py, slp.py) as a numbered recipe

**Question.** Walk `aggregate_load.py` and `slp.py` the same way: what `coefficients.csv` holds, whether any weather term survives (the question cited a B3 capture of 94.33 % with H0 as truth against 87.91 % with a measured household), the per-state household counts, which annual kWh figure is used, whether holidays are modelled per state (it stated that `slp.h0_profile` rejects any holidays but Bavaria's), whether the load is gross or net, and whether anything consumes the output.

Checked at commit 45374c6. That is four commits after 4c13a4a, and none of them touches aggregate_load*, slp.py or reports/aggregate_load. Gitignored data was read locally. Every point where the verifications disagreed was re-checked in the code.

**Recipe.** `prosumer aggregate-load` (cli.py:294-302) runs `run_fit` first, then `run_states`.

Fit stage. Only the maintainer runs it, because it needs the HTW parquet and the ERA5 cache (aggregate_load_fit.py:143-161).
1. Read data/processed/htw_74_15min_2010.parquet: 74 homes in 2010, as the 15-minute mean kW of PL1+PL2+PL3 (htw.py:68-73). Its SHA-256 matches fit_meta.csv:13.
2. Drop the homes flagged pv_suspect in reports/htw_pv_screen.csv (aggregate_load_fit.py:115). Only H49 is flagged, so 73 homes remain.
3. Read the file on its own legal-local clock: row i is date i//96 and slot i%96, with no MEZ conversion (aggregate_load_fit.py:40-51). HTW_FILE_TZ equals the pre-fix HTW_TZ ('Etc/GMT-1'), so the clock fix c07847e never affected this fit.
4. Build the fit calendar (aggregate_load_fit.py:54-63). The 9 holidays that all 16 states kept in 2010 count as Sunday-type. Six days are left out: the regional weekday holidays 6 Jan, 3 Jun, 1 Nov and 17 Nov, and the DST Sundays 28 Mar and 31 Oct. That leaves 359 fit days.
5. Fetch ERA5 daily mean 2 m temperature from the Open-Meteo archive (models=era5, timezone Europe/Berlin; weather.py:112-140) at the 16 pv_states.STATE_SITES. All 16 sites are state capitals (pv_states.py:61-76). The comment at pv_states.py:46 implies exceptions, but there are none (fixed in 3c0f0a0). The span is hard-coded as ERA5_SPAN = 1991-01-01 to 2026-10-25 (aggregate_load.py:69), and the cache ends 2026-10-20. Each site gets a 1991-2020 normal made of a constant plus 2 annual harmonics (aggregate_load.py:128-139). Anomaly = temperature minus normal. Hannover (NI) stands in for the unpublished HTW site (aggregate_load.py:62).
6. Fit the daily energy of the 73-home mean by OLS on the 359 days. The regressors are 7 day-class dummies, 3 annual harmonics, the first harmonic x Saturday, the first harmonic x Sunday-type, and the anomaly: 18 coefficients in all (aggregate_load.py:150-162, 193-195).
7. Fit the shape by OLS per shape type (working day, Saturday, Sunday-type): the 96 daily shares on [1, 2 annual harmonics], with no temperature term (aggregate_load.py:171-182, 196-199).
8. Compute a_ref, the predicted energy of all 365 days of 2010 at zero anomaly on the national-only calendar: 4,658.89 kWh/a (aggregate_load.py:200-201). The four regional weekday holidays count as ordinary weekdays here. The clip range is the anomaly range of the fit days, -13.01 to +10.41 K.
9. Write coefficients.csv, fit_meta.csv, state_normals.csv, fit_temperature_2010.csv and the validation tables (aggregate_load_fit.py:154-160).

Apply stage. This is the code that actually wrote state_summary.csv: run_states, state_summary and _state_rows (aggregate_load_states.py:49-130).
10. load_model reads the committed coefficients (aggregate_load.py:334-362). Its round-trip parsing was added in b5de513 (2026-10-29), after the CSVs were written in b3c2328. Today's code still reproduces the 2024 rows of state_summary.csv with max |diff| 0.0.
11. state_anomaly takes ERA5 at the state's single site minus that site's normal (aggregate_load.py:374-391). It raises for any end date after 2026-10-25 (lines 384-385), so later days need a code edit, not only a data refresh.
12. unit_load (aggregate_load.py:244-267) does the following:
    - It builds a 15-minute UTC grid of local dates. DST days keep 92 and 100 slots.
    - Holidays come from slp.state_holidays(state, year).
    - Sundays, the state's holidays and 24/31 Dec get day class 6 and shape type 2 (lines 107-124).
    - The anomaly is clipped to the 2010 range (line 220).
    - Unit load u = E(d) x w(d,s) / (0.25 x a_ref), in kW per kWh/a (line 264).
13. Household kW = u x annual_kwh, at two levels: a_ref and 3,221 (aggregate_load_states.py:55-57). Fleet figures are plain scalar arithmetic with the 1 January MaStR count: fleet_gwh = realised x n / 1e6 and fleet_peak_mw = peak x n / 1000 (line 68). household_load and fleet_load (aggregate_load.py:270-303) are only the documented API. README.md:254-255 and the tests call them; the CLI does not, and no monthly count series is ever used.
14. Loop over 2024, 2025 and 2026 (up to 2026-10-20, which gives 293 days for every state) x 16 states x 2 levels = 96 rows, plus the V10 rows of validation_summary.csv.

**Answers**
- **What coefficients.csv holds.** 1,458 rows in long format.
  - Energy block: 18 coefficients in kWh per average-household-day. These are 7 day-class levels (Mon..Sat, Sunday-type: 12.16, 12.06, 11.98, 12.03, 12.69, 13.70, 14.44), 6 annual-harmonic terms, 4 seasonal weekend-uplift terms, and the slope beta = -0.0715 kWh per K (coefficients.csv:19).
  - Shape block: 3 types x 96 slots x 5 terms = 1,440 dimensionless shares. Per type, the constants sum to 1 and the harmonic terms sum to 0.
  - Basis: OLS on the mean of 73 HTW homes in 2010 (H49 excluded), 15-minute kW on the homes' own clock, 359 fit days, anomaly at the Hannover proxy (fit_meta.csv:2-12).
  - Running the model also needs fit_meta.csv (a_ref and the clip range), state_normals.csv (16 x 5) and ERA5 temperatures for the target days. Without them unit_load raises (aggregate_load.py:257-259).
  - Quirk: in the shape block, the day_class column holds the shape type 0..2 (aggregate_load.py:319-321).
- **Does any weather term survive? Yes, exactly one.**
  - What it is: the daily-mean 2 m temperature anomaly at one ERA5 point per state, against that point's 1991-2020 normal, clipped to -13.01..+10.41 K. It enters daily energy linearly (aggregate_load.py:161-162, 220).
  - It scales all 96 quarter-hours of a day by one factor. The shape takes no weather input (day_shares, aggregate_load.py:225). With and without the anomaly, the shares differ by at most 2e-17 in all 16 states.
  - What is absent: no irradiance, cloud, daylight or intraday term, and no population weighting.
  - Size: 0.56 % of mean daily energy per K. In 2024, daily energy against normal weather runs from -6.39 % (BE) to +5.11 % (SN) across the 16 states. Annual energy runs from -0.57 % (SL) to -1.06 % (SN), which is ratio_to_normal_weather 0.9894 to 0.9943 in state_summary.csv.
  - The absolute slope scales with the chosen energy level: -0.0715 kWh/K/day at 4,659 kWh/a, -0.0495 at 3,221.
  - Caveat 1: the slope depends on the unknown site. Over the eight holiday-consistent proxies it runs from -0.0616 (BE) to -0.0731 (HB) (daily_model_selection.csv:27-48).
  - Caveat 2: the slope depends heavily on one home. H46 (winter/summer mean-load ratio 3.86, 8,635 kWh/a, htw_household_features.csv:74) alone has -0.81. Without it, the 73-home slope is -0.0612 (0.49 %/K).
  - Against H0: slp.h0_profile still has no weather input and rescales every calendar year to exactly annual_kwh (slp.py:192-199). The aggregate model lets weather move the annual total.
  - The link to PV runs only through temperature: a dark day at normal temperature gets no response. The claimed bias of residual-load extremes is not measured anywhere, because no code combines this load with PV.
  - The B3 capture of 87.91 % is the pre-fix figure. rolling_eval_htw_H28/rolling_eval_summary.csv:9 now reads 89.4296, against 94.3329 with H0 as truth (rolling_eval/rolling_eval_summary.csv:8). The gap is therefore 4.90 points, not 6.4 (HANDOFF.md:336). The two runs are comparable like for like (HANDOFF.md:448). Both are single-site runs, and neither uses the aggregate model.
  - That H0 gives all 16 states the same shape now holds only outside holidays. Since 7c0ca6d, h0_profile applies each state's calendar. SN and BY in 2024 differ by 1.4 to 7.5 % on their six differing holidays, and by at most 0.03 % on all other days.
- **Household counts and their source.**
  - The model itself holds no count. Counts enter only in aggregate_load_states._fleet_counts (aggregate_load_states.py:36-42), through national_impact.federal_states.
  - That function reads mastr.count_by_state('Home storage') at 2024-01-01, 2025-01-01 and 2026-01-01 (national_impact.py:101-113). The source is the battery-charts.de export of the MaStR register: 16 files data/raw/mastr/state_counts/<state>.csv, lithium-ion only, ending 2026-01-13.
  - Totals: 1,119,240 / 1,670,776 / 2,190,625. BY: 237,322 / 342,733 / 435,232. All 48 values are in state_summary.csv.
  - These are home-storage units. The column n_households_1jan treats one storage as one household without saying so (now said in reports/aggregate_load/SOURCE.md, c4e3918).
  - Lithium-ion is 96.8 to 97.2 % of all home storages (data/raw/mastr/SOURCE.md:68-72), so fleet totals are about 3 % low. On top of that, the 1 January count is used for a whole year in which the fleet grew 49 % (2024).
  - Without the MaStR files the counts are NaN (aggregate_load_states.py:40-41).
- **Which annual kWh figure the pipeline uses.**
  - It fixes none. household_load requires annual_kwh as a keyword-only argument with no default (aggregate_load.py:270-271). state_summary.csv reports two levels side by side: htw_normal_weather = a_ref = 4,658.89 and thesis_3221 = 3,221 (aggregate_load_states.py:55).
  - HANDOFF.md:426 leaves the level for home-storage households open. No level has reached a published national figure.
  - 4,659: the normal-weather energy of the 73-home MEAN on the 2010 calendar. It is mean-based, so it is the right kind of figure to multiply by N.
  - 4,697.4: the realised 2010 mean of the 73 homes. It is higher because 2010 was 1.48 K below normal.
  - 4,685.1: the 74-home mean including the PV-suspect H49. This is the '4684' of HANDOFF.md:327, already superseded by HANDOFF.md:415.
  - 4,562.0: the 74-home median (the 73-home median is 4,569.2). No code uses it.
  - 3,221: the thesis household. It is THESIS_ANNUAL_KWH (aggregate_load.py:65) and the default of h0_profile (slp.py:166) and replay_on_calendar (htw.py:163). H0 carries exactly 3,221.0 per year in site_2024_2026.parquet. The H28 truth carries 3,234.6 (2024) and 3,247.9 (2025).
  - 3,212: no code, config or committed CSV defines it. It first appears in HANDOFF.md:322, in commit 0d472a5 (2026-10-26 15:09), before the model existed (b3c2328, 17:45). Its origin is not established; 3,221 x 365/366 = 3,212.2 is one candidate. The realised thesis-level values of 3,212.3 to 3,213.0 in state_summary.csv (MV 2024, NW 2025, HH 2024) came later.
  - annual_kwh only approximates a state's normal-weather year, because a_ref uses the 2010 national calendar. On the 2025 calendar, the zero-anomaly energy is 0.22 % (HE) to 0.33 % (BY) above it.
- **Holidays.**
  - slp.h0_profile no longer rejects other states' holidays. The raise sat at slp.py:119-120 until commit 7c0ca6d (2026-10-26). Now _holiday_set accepts None or any of the 16 state codes (slp.py:135-141), and slp.py:119 holds the Women's Day rule.
  - Per-state holidays are modelled in the aggregate. unit_load takes slp.state_holidays for each year (aggregate_load.py:255), which covers all 16 states including the law changes since 2017 (slp.py:90-127).
  - SN vs BY daily energy in 2024: +23.5 % on 20 Nov, +21.6 % on 31 Oct, -16.7 % on 15 Aug, -15.4 % on 30 May, -14.1 % on 1 Nov.
  - The largest gap to the 16-state mean is 20.4 % (SN, 2024-11-20) and 20.6 % (SN, 2025-11-19).
  - Limit: holidays have no coefficient of their own. Class 6 in the fit holds 59 days: 50 Sundays, 5 weekday and 2 Saturday national holidays, and 24 and 31 Dec. Because the 73 homes kept no regional holiday, the assumption that a regional weekday holiday behaves like a Sunday is untested.
  - 24 and 31 Dec are Sunday-like in every state, deliberately against the BDEW rule (aggregate_load.py:110-111).
  - Local holidays are left out, while Bavaria's Assumption Day is applied to the whole state (slp.py:76-80).
  - School holidays and bridge days are not modelled.
  - The single-household benchmark still uses Bavarian holidays (dataset.py:36, htw.py:190).
- **Gross or net load.**
  - Gross, as far as a heuristic screen can tell. The fit uses summed three-phase consumption (htw.py:68-73) and drops PV-suspect homes (aggregate_load_fit.py:115; fit_meta.csv:11 'excluded_households,H49').
  - The screen flags a home if its sunny/cloudy midday ratio is below 0.75, or if its sunny-midday floor share exceeds 1.5 x its night floor share + 0.05 (htw.py:127-129). Export cannot show up as negative values: all 1-minute minima are 40 to 42 W.
  - The nearest unflagged home, H13, sits at 0.789 against the 0.75 threshold, so a small PV system could pass the screen.
  - The intent behind preferring BDEW (gross, no PV netting) is kept, but the source is no longer BDEW. H0 is only a comparison column (h0_fleet_peak_mw) and a validation baseline.
  - Whether any fitted home heats electrically is not recorded (see H46 above).
  - Multiplying by home-storage counts assumes that PV-storage owners consume like the 2010 average home. README.md:276 says the model does not represent PV households.
- **Is the output consumed by anything? No.**
  - prosumer.data.aggregate_load is imported only by the four experiments/aggregate_load_*.py modules and two test files. cli.py imports the fit and states experiments (cli.py:295-296).
  - national_impact.py imports only mastr (national_impact.py:39). It multiplies counts by Germany-wide mean sizes (lines 168-170). Its representative days are literals ('peak_shift_mwh': -5264.31, line 256), which showcase.html:1571 publishes.
  - The dependency runs the other way: aggregate_load_states imports national_impact.federal_states for its counts (aggregate_load_states.py:38).
  - No code computes residual load. The only hit is the docstring at aggregate_load.py:38 (reworded in 73861cc).
  - The docstring at aggregate_load.py:3 ('The national analysis multiplies one household's load by the number of home-storage households') describes the thesis method, not what national_impact.py does (fixed in 3c0f0a0).
  - No fleet MW figure from state_summary.csv is published. Example: BY 2024 at 3,221 kWh/a gives 198.5 MW from the model and 204.8 MW from H0, both on the expectation basis.
  - So the 2026-10-26 load work has not reached any published number.
- **Other stale points.** Both 'two blockers' of HANDOFF.md:343-347 are resolved: h0_profile accepts every state code, and household_load has no default energy. The test fixes that were uncommitted when the audit started are now committed (45374c6).
- **Published claims.** The README aggregate-load figures (README.md:244-276) match the committed CSVs, except the seed-0 CI rounding in README.md:263. The claims to fix:
  - README.md:244, 258, 263, 266, 268, 273, 281, 531, 532, 556
  - showcase.html:1568, 1570, 1957, 1964-1975
  - data/raw/mastr/SOURCE.md:51-52
  - the state_summary.csv column n_households_1jan
  - LICENSE:43
- **How it was measured.** The code at that commit on local data, with ERA5 network calls disabled. The checks, not committed, covered:
  - the committed CSVs;
  - the temperature effect in 16 states, shape invariance, the ERA5 span, the fit calendar and H0 SN vs BY;
  - an exact refit (max |diff| 0.0) and the slope without H46;
  - the annual energies;
  - the counts and the showcase check;
  - the 2024 state_summary rows (max |diff| 0.0);
  - SN vs BY;
  - an inline mastr.by_segment check for the national maxima.
- **H0 cache path.** slp.fetch_h0_table defaults to a cache path relative to the working directory, data/raw/slp (slp.py:37). Run from another directory, it downloads the H0 table again from the thesis GitHub repository; the copy is byte-identical to the cached one.
