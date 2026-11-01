# Where these tables come from

Every CSV in this folder was written by `python -m hybrid.cli report` (`src/hybrid/report.py`)
on 31 October 2026. No CSV was edited by hand. For each experiment, `run_meta.csv` records the
commit it ran from (`git_commit`) and that the working tree had no uncommitted changes outside
this folder (`git_dirty` is False).

The two negprice experiments ran again on 1 November 2026, from a later commit, to add one
column: `full_export_premium_by_technology_eur`. Every cell their tables held before is
unchanged, and so is every row of `window_stats.csv`. They read the same cached responses, so
their `input_sha256` rows are unchanged too.

## How the set was run

The publish set is `PUBLISH_SET` in `src/hybrid/report.py`. This run split it into four parts
that ran side by side, each with `--only`. They ran from the repository root, with the
project's `src` folder first on `PYTHONPATH`, so that Python imported the `hybrid` package of
the checkout being recorded:

```bash
python -m hybrid.cli report --only ladder_2025-01-01_30d
python -m hybrid.cli report --only ladder_2025-07-01_30d
python -m hybrid.cli report --only sizing_2024 negprice_2024 decompose_2024 decompose_2024_conn-30
python -m hybrid.cli report --only sizing_2025 negprice_2025 decompose_2025 decompose_2025_conn-30 b3_ablation_synthetic_demo
```

Each experiment writes its own table and replaces only its own rows in `window_stats.csv` and
`run_meta.csv`, so the four parts add up to the whole set. `run_meta.csv` keeps each
experiment's command (`command`) and the part it ran in (`argv`). To rerun the whole set in one
process, run `python -m hybrid.cli report`; in series it takes about an hour.

The rerun of 1 November 2026 ran two parts side by side, in the same way, for about 7 minutes each:

```bash
python -m hybrid.cli report --only negprice_2024
python -m hybrid.cli report --only negprice_2025
```

Software: Python 3.12.10, numpy 2.5.4, pandas 3.1.0, PuLP 3.3.0 and the CBC binary that PuLP
ships (section `environment` of `run_meta.csv`). The machine was a 4-core laptop (Intel Core
i7-10510U, Windows 11), and the four parts took 22 minutes on it. They shared its cores, so the
wall times in `run_meta.csv` and B3's solve times in the ladder tables show the order of
magnitude, not a benchmark.

## Windows

| Experiments | Window, in Berlin time | Steps of 15 minutes |
|---|---|---:|
| sizing, negprice, decompose | the calendar year 2024, and the calendar year 2025 | 35,136 and 35,040 |
| ladder (B0 to B3) | 30 days from 1 January 2025, and 30 days from 1 July 2025 | 2,880 each |
| b3-ablation | the three synthetic days of the portfolio showcase (`showcase.py`, demo_05) | 288 |

The ladder experiments also read 2024. B3's price-forecast error is sized on the standard
deviation of the 2024 day-ahead price. B3 plans each month on the realised premium rate of the
month before, so the January window plans on December 2024's. Every rung is settled on the
realised annual market value of 2025.

## Data and licences

All real inputs come from the Energy-Charts API of Fraunhofer ISE
(<https://api.energy-charts.info>), read through the repository's `datakit`:

- `public_power` (country `de`): onshore wind feed-in, the numerator of the wind factor; and
  public solar feed-in, the PV weight of the market-value proxy.
- `total_power` (country `de`): solar generation including self-consumption, the numerator of
  the PV factor.
- `installed_power` (monthly): the onshore wind and Solar DC fleet of each month, the
  denominators of both factors.
- `price` (bidding zone `DE-LU`): the day-ahead price.

Licence: CC BY 4.0 (<https://creativecommons.org/licenses/by/4.0/>). Credit the generation and
capacity data to Energy-Charts (Fraunhofer ISE). Credit the prices to "Bundesnetzagentur |
SMARD.de", as the Energy-Charts price payload does. The tables are derived from these data, so
reuse them with the same credits.

Section `source_url` of `run_meta.csv` names every request. datakit fetched the responses on
31 October 2026 at about 20:20 UTC. It keeps them in `datakit/cache/`, which is not committed.

## Inputs that are synthetic or assumed

- The plant. It is not a real site: 50 MW of onshore wind and 30 MWp of PV, a 20 MW / 40 MWh
  battery and a 60 MW connection. sizing sweeps the connection over 20, 25, 30, 40 and 60 MW.
  decompose runs the connection as built (60 MW, or 30 MW in the `_conn-30` tables) and a
  200 MW connection that never binds.
- The capacity factors. National onshore wind and solar generation, divided by the national
  fleet of the same month. The monthly fleet figure reads as the fleet at the month's end.
  National factors are far smoother than a single site, and they understate what a new plant
  produces.
- The market values. A proxy: the day-ahead price weighted by national generation, following
  EEG 2023 Anlage 1. The TSOs publish the values the premium is settled on, and this project
  does not load them. Section `market_value_proxy_eur_mwh` of `run_meta.csv` holds the proxy by
  technology, month and year.
- The applicable values. 73.5 EUR/MWh for wind and 68.0 EUR/MWh for PV, the 2024/2025 tender
  ceilings that `MarketConfig` cites, not an award. The older vintage in negprice, at 95.0 and
  90.0 EUR/MWh, is a sensitivity.
- B3's forecasts. The true series plus AR(1) noise with a step-to-step correlation of 0.8,
  seeded with 0. In the ladder tables the error is 15 % of wind and PV, and for the price 15 %
  of the 2024 day-ahead price's standard deviation; the ablation runs 0 and 15 %. No forecast
  product is used.
- reBAP. A synthetic series that settles nothing: no rung commits a schedule, so
  `imbalance_cost_eur` is 0 in every row.
- `b3_ablation_synthetic_demo.csv`. Every input is synthetic (`src/hybrid/synthetic.py`): three
  days of wind, PV and price shapes, a 40 MW connection, B3 on a 12-hour horizon and a fixed
  premium of 8 EUR/MWh.

No table includes aFRR: it is not built yet, and its settings in `MarketConfig` are inert. No
table includes intraday trading or a redispatch order either, and grid charging is off.

## Files

| File | Rows | One row holds |
|---|---:|---|
| `sizing_2024.csv`, `sizing_2025.csv` | 20 each | one rung (B0, B1, B2, B2 no battery) at one connection, with the battery's value and the value of curtailing at low prices at that connection |
| `negprice_2024.csv`, `negprice_2025.csv` | 30 each | one rung (B0, B1, B2) of one vintage under one negative-price rule of that year, with the premium that a plant exporting all of its generation would be paid per technology under that rule, as the law pays it, beside the blended rate every rung is settled on |
| `negprice_2024_premium.csv`, `negprice_2025_premium.csv` | 13 each | one vintage and period: market value, premium and the plant's rate, for the recent vintage by year and the older one by month |
| `decompose_2024.csv`, `decompose_2025.csv` | 8 each | one rung (B0, B1, B2, B2 no battery) at the 60 MW connection or at 200 MW, with the battery's value, the value of curtailing at low prices and the connection limit's effect on the battery's value |
| `decompose_2024_conn-30.csv`, `decompose_2025_conn-30.csv` | 8 each | the same, with a 30 MW connection as built |
| `ladder_2025-01-01_30d.csv`, `ladder_2025-07-01_30d.csv` | 4 each | one rung, B0 to B3; B3's row adds its solve times, the scale of its price error and the premium it planned on |
| `b3_ablation_synthetic_demo.csv` | 16 | one rung, B0 to B3, in one of four B3 cases (forecast error 0 or 15 %, terminal value off or on), with B3's share of the headroom from B1 to B2 |
| `window_stats.csv` | 5 | one window: its dates, mean price, quarter-hours with a negative price and below the marketing fee, peak generation, largest combined capacity factor, and whether the connection binds (60 MW on Energy-Charts data, 40 MW in the demo) |
| `run_meta.csv` | 783 | one fact about one experiment: its command, start, wall time, commit, versions, input hashes, source URLs, the monthly capacity its factors were divided by, and the market-value proxy |

Every result row also carries each settlement term (EUR columns end in `_eur`, energy columns in
`_mwh`), its window, the premium it was settled on and the settings it ran with: `plant_*`,
`market_*`, and `b3_*` where B3 ran. `ladder_tolerance_eur` is how far two rungs may differ by
the solver's rounding alone.

## Checking for upstream data drift

datakit keeps each Energy-Charts response forever, so a rerun on the same machine reads the
same bytes. Energy-Charts can revise its data, so a rerun from fresh downloads may read others.
Three sections of `run_meta.csv` tell the two cases apart:

- `input_sha256`: the SHA-256 of each frame an experiment loaded, labelled by the call, for
  example `load_year(2024, days=None, dt=0.25)`. It covers the timestamps and every column
  (`wind_cf`, `pv_cf`, `pv_feed_in_cf`, `price_eur_mwh`), hashed by
  `hybrid.provenance.frame_sha256`, which does not depend on how pandas prints numbers.
- `capacity_mw`: the onshore wind and Solar DC fleet that each month's factors were divided by.
- `market_value_proxy_eur_mwh`: the proxy by technology, month and year.

To check whether Energy-Charts' data has changed:

1. Check out the commit that `git_commit` names, and install the project with
   `pip install -e projects/02-utility-hybrid-plant-dispatch`.
2. Move `datakit/cache/` aside, so that datakit downloads every response again.
3. Hash a fresh frame and compare it with the `input_sha256` row of the same label:

   ```bash
   python -c "from hybrid.data import load_year; from hybrid.provenance import frame_sha256; print(frame_sha256(load_year(2024)))"
   ```

   `load_year(2025)` gives the row `load_year(2025, days=None, dt=0.25)`, and
   `load_year(2025, 30)` the row `load_year(2025, days=30, dt=0.25)`.

Equal hashes mean equal inputs. A table that then differs in a rerun differs because of the code
or the environment: compare `git_commit` and the `environment` rows. At the recorded commit and
versions, a different hash means the data changed. Rerun the set with
`python -m hybrid.cli report`, then `git diff` the `capacity_mw` and
`market_value_proxy_eur_mwh` rows of `run_meta.csv` to see which months moved. A change to
`load_year` also changes the hash, which is why the check starts from the recorded commit.
