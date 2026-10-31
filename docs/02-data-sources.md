# Data Source Catalogue

Data sources vetted for the portfolio, with their licence terms. Section 0 lists what the
active projects actually read; sections 1 to 5 are the wider catalogue and include sources
that no active project uses. Each project's README documents its own inputs in detail.

**Principle:** every project must be reproducible from **openly licensed data** in at least
one configuration. Where a project would ideally use proprietary asset data (SCADA, metering,
trading records), the design specifies a *public-data fallback* — a synthetic or
publicly-derived substitute — so the method can be demonstrated and reviewed without an
NDA. Proprietary data, where obtained, improves fidelity but is never a precondition.

---

## 0. What the active projects read

| Project | Input | Source | Resolution | In the repository |
|---|---|---|---|---|
| 01 | Day-ahead price, DE-LU | Energy-Charts (data from Bundesnetzagentur / SMARD.de) | hourly products until 2025-09-30, quarter-hourly from 2025-10-01; the dataset records which (`price_mtu_min`) | fetched and cached by `prosumer build-data`, not committed |
| 01 | Weather behind the PV model | Open-Meteo historical-forecast archive, Munich | 15 min | fetched and cached, not committed |
| 01 | Weather behind the PV forecast | Open-Meteo previous-runs archive, the forecast issued one day earlier | hourly, held over four quarter-hours | fetched and cached, not committed |
| 01 | Household load | HTW Berlin, 74 households measured in 2010 (Tjaden et al. 2015) | 15-minute smart-meter data, published refined to 1 s; the 1-minute files are averaged back to 15 min | downloaded by hand, not redistributed |
| 01 | Load forecast | BDEW H0 standard load profile, from the thesis repository | 15 min | fetched, not committed |
| 01 | Daily mean temperature (aggregate load model) | ERA5 through the Open-Meteo archive API, at the state capitals | daily | fetched and cached, not committed |
| 01 | Home-storage fleet (national analysis) | MaStR battery register as monthly series from battery-charts.de | monthly | downloaded by hand, not committed ([SOURCE](../projects/01-prosumer-pv-bess-mpc-rl/data/raw/mastr/SOURCE.md)) |
| 01 | Thesis input year 2024 (Gate 0) | the thesis repository, tag `thesis-v1.0` | hourly | fetched, not committed |
| 03 | Portfolio output | Energy-Charts public feed-in of wind (onshore + offshore) and solar, divided by the month's installed capacity (also Energy-Charts) and scaled to 200 MW wind + 100 MW PV | 15 min | fetched and cached by `python -m f2b.cli build-data`, not committed |
| 03 | TSO forecasts (the F4 benchmark) | Energy-Charts: the transparency-platform day-ahead, intraday and current forecasts of the same fleet, scaled the same way | 15 min | fetched and cached, not committed |
| 03 | Weather forecasts | Open-Meteo previous-runs archive at the 16 state capitals and two offshore clusters, as forecast one and two days earlier | hourly | fetched and cached, not committed ([SOURCE](../projects/03-probabilistic-forecast-to-bid/data/raw/open_meteo/SOURCE.md)) |
| 03 | Day-ahead price, DE-LU | Energy-Charts | hourly products until 2025-09-30, quarter-hourly after | fetched and cached, not committed |
| 03 | Imbalance price (reBAP) and intraday price index (ID AEP) | netztransparenz.de, CSV export | 15 min | downloaded by hand, not committed ([SOURCE](../projects/03-probabilistic-forecast-to-bid/data/raw/netztransparenz/SOURCE.md)) |

Project 02 lists its inputs in its own README. The data folders are gitignored apart from
their `SOURCE.md` files; the results derived from the data are committed as CSVs under each
project's `reports/`.

---

## 1. Market and system data

| Source | Content | Resolution | Access | Licence / terms |
|--------|---------|-----------|--------|-----------------|
| **SMARD.de** (Bundesnetzagentur) | Day-ahead & intraday prices, generation by fuel, load, balancing, cross-border flows, installed capacity | 15 min / 1 h | Web download + documented CSV/JSON endpoints | Free reuse with attribution (BNetzA terms) |
| **ENTSO-E Transparency Platform** | Pan-EU load, generation, outages, cross-border, balancing, imbalance prices | 15 min / 1 h | REST API (free registration, security token) | Free reuse under platform terms, attribution required |
| **netztransparenz.de** (the four TSOs) | reBAP imbalance prices, the ID AEP intraday price index, balancing energy volumes, EEG plant master data, redispatch measures, network charge data | 15 min | Web download + API | Free reuse, attribution |
| **regelleistung.net** | FCR/aFRR/mFRR capacity & energy auction results, demand, prequalification rules | Per 4-h product | Web download + API | Free reuse, attribution |
| **Energy-Charts** (Fraunhofer ISE) | Curated German/European electricity data: day-ahead prices, public generation by type and load, monthly installed capacity, the TSOs' generation forecasts | 15 min / 1 h | Public JSON API, no key (`datakit`) | CC BY 4.0 |
| **Open Power System Data** | Harmonised historical time series, plant registers, weather-derived capacity factors | 15 min / 1 h | Static packaged CSV | Mostly CC BY 4.0 (per-package) |
| **EPEX SPOT** | Authoritative order-book-level DA/ID data | Tick / 15 min | Commercial | Licence required — **not** a dependency of any project |
| **aWATTar / Tibber APIs** | Forward-looking dynamic retail tariff prices (`§41a EnWG`) | 1 h / 15 min | Public / customer API | Per provider terms. No project reads them: Project 01's tariff is the day-ahead price plus fixed adders |

**Pitfall:** the day-ahead price changed native granularity on 1 October 2025, when SDAC
moved from hourly to 15-minute products. Any multi-year study must handle the resolution
break explicitly: upsampling the earlier hourly period introduces an artificial loss of
intra-hour variance that will flatter any controller evaluated across the boundary. Project
01's dataset records the market time unit of every quarter-hour (`price_mtu_min`); Project 03
holds each hourly price over its four quarter-hours.

---

## 2. Weather and renewable resource

| Source | Content | Resolution | Access | Licence |
|--------|---------|-----------|--------|---------|
| **DWD Open Data** | ICON-D2 / ICON-EU NWP fields, MOSMIX station forecasts, observation archive (CDC) | 15 min – 3 h, hourly grid | Open FTP/HTTPS | Free (GeoNutzV), attribution |
| **DWD ICON-D2-EPS** | Ensemble NWP. Not used: no public archive to backtest against (Project 03 README, section 5) | 1 h, 20 members | Open FTP/HTTPS, recent runs only | Free (GeoNutzV) |
| **Open-Meteo** | Previous-runs archive (what a weather model forecast one or more days earlier), historical-forecast archive, ERA5 reanalysis | Hourly; 15 min in the historical-forecast archive | Public JSON API, no key | CC BY 4.0 |
| **PVGIS** (JRC) | PV yield, irradiance climatology, typical meteorological years | Hourly | Web + API | Free reuse, attribution |
| **Renewables.ninja** | Wind & PV capacity factor time series from MERRA-2/SARAH | Hourly | Web + API | CC BY-NC 4.0 — **non-commercial**, check before reuse |
| **ERA5 / CDS** (Copernicus) | Reanalysis wind, irradiance, temperature | Hourly | API | Copernicus licence, free with attribution |
| **FINO 1/2/3** | Offshore met-mast measurements | 10 min | Registration | Research use, attribution |

**Pitfall:** Open-Meteo's historical-forecast archive stitches together the shortest lead of
every model run. Project 01 uses it only as the observed weather behind its PV model; as a
forecast input it would leak, which is why Projects 01 and 03 take their forecasts from the
previous-runs archive.

---

## 3. Asset and plant registers

| Source | Content | Access | Licence |
|--------|---------|--------|---------|
| **Marktstammdatenregister (MaStR)** | Every registered generation/storage unit in Germany: technology, capacity, commissioning date, location, network operator | Bulk download + API | Free reuse (BNetzA terms) |
| **netztransparenz EEG plant data** | EEG-remunerated plants and payments | Download | Free reuse |
| **OpenStreetMap / OpenInfraMap** | Grid topology approximation, substation and line geometry | API / extracts | ODbL — share-alike, attribution |
| **SimBench / pandapower networks** | Realistic synthetic German LV/MV/HV benchmark networks | Python package | Open (per package) |

---

## 4. Demand, mobility and building data

| Source | Content | Resolution | Notes |
|--------|---------|-----------|-------|
| **BDEW standard load profiles (SLP)** | H0 (household), G0–G6 (commercial), L0–L2 (agriculture) | 15 min | The regulatory default for non-interval-metered customers; the *reference*, not ground truth |
| **HTW Berlin representative household profiles** | 74 German household load profiles, measured in 2010 | 15-minute smart-meter data, published refined to 1 s and 1 min | Free for research, not redistributed here; the standard benchmark for German BTM storage studies |
| **VDI 4655** | Reference load profiles for heat and electricity in residential buildings | Hourly | Standard, purchase required |
| **ElaadNL open datasets** | Measured EV charging transactions and profiles (NL) | Per session | Open; NL behaviour — requires re-weighting for DE |
| **ACN-Data (Caltech)** | ~50k real workplace/campus EV charging sessions with arrival, departure, energy | Per session | Free for research |
| **MiD / MOP** (German mobility surveys) | Trip chains, dwell times, vehicle usage in Germany | Trip level | Scientific-use files on request — the right way to synthesise German EV availability |
| **When2Heat** | Heat demand and heat-pump COP time series for European countries | Hourly | CC BY 4.0 |

**Pitfall:** using Dutch or Californian charging behaviour to represent a German depot is
a validity threat, not a detail. Project 03 uses ACN-Data/ElaadNL to fit *session-level
structure* (energy per session, dwell-time distributions, arrival clustering) and re-weights
arrival/departure distributions with German mobility statistics; the substitution and its
effect are reported as an explicit limitation.

---

## 5. Hydro and pumped storage

| Source | Content | Notes |
|--------|---------|-------|
| **MaStR** | German pumped-storage units, capacities, commissioning dates | Public |
| **JRC Hydro-power database** | European hydro plants incl. pumped storage, reservoir volumes, head | Open, CC BY |
| **ENTSO-E Transparency** | Aggregated hydro filling rates, generation and pumping per bidding zone | Weekly filling, hourly generation |
| **Operator technical publications** | Head, volume, unit ratings, efficiency curves for named plants | Public disclosures / literature |

Project 02 builds a **publicly-derived reference plant** from these sources rather than using
any confidential operator data, so the entire study is publishable and reproducible.

---

## 6. Data engineering conventions

All projects follow the same conventions so pipelines and evaluation code are portable:

- **Time.** Everything stored in **UTC** with an explicit `tz`; local wall-clock
  (`Europe/Berlin`) only at presentation. DST transitions are tested explicitly — the
  duplicated and missing hours in October/March are a classic silent corruption of German
  energy datasets, and both are covered by unit tests in the project template.
- **Indexing.** Left-closed, left-labelled 15-minute intervals (`[t, t+15min)`), matching
  market MTU conventions.
- **Layout.** `data/raw/` (immutable, never edited) → `data/interim/` → `data/processed/`
  (analysis-ready Parquet). Only `data/processed` is consumed by models.
- **Versioning.** DVC or a manifest with content hashes; every result records the data
  snapshot hash it was produced from.
- **Provenance.** Every dataset ships a `SOURCE.md` recording URL, retrieval timestamp,
  licence and any transformation applied.
- **No raw data in git.** `.gitignore` excludes `data/`; a `make data` target and a
  documented manifest reconstruct it.

---

## 7. Licence compliance summary

| Category | Rule applied |
|----------|--------------|
| CC BY / attribution sources | Attribution recorded in `data/*/SOURCE.md` and in publication material |
| CC BY-NC (e.g. Renewables.ninja) | Research/portfolio use only; never used in a configuration presented as commercial |
| ODbL (OSM) | Derived geodata kept separable and share-alike obligations noted |
| Commercial (EPEX SPOT, VDI, some DWD products) | Never a hard dependency; every project has an open-data path |
| Any operator/company data | Only under an explicit agreement, never committed, never in a public result without clearance |
