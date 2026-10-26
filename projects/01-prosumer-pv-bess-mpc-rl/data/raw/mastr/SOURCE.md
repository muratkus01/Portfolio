# MaStR home-storage fleet series

Six CSV exports from [battery-charts.de](https://battery-charts.de), which publishes the
German Marktstammdatenregister (MaStR) battery register as monthly time series. Downloaded
2026-10-26. Public data, no access restriction.

The files themselves are not committed (`.gitignore` blocks `/projects/*/data/`). This file
records what to download again to reproduce the analysis.

| File here | Chart on battery-charts.de |
|---|---|
| `battery_energy_by_state_lithium_ion.csv` | Battery energy by federal state (Lithium-ion) |
| `battery_power_by_state_lithium_ion.csv` | Battery power by federal state (Lithium-ion) |
| `home_storage_mean_energy_power.csv` | Mean battery energy and power of all home storages in Germany (MaStR) |
| `battery_count_by_segment.csv` | Number of battery storages in Germany (All battery technologies, MaStR) |
| `battery_energy_by_segment.csv` | Battery energy in Germany (All battery technologies, MaStR) |
| `battery_power_by_segment.csv` | Battery power in Germany (All battery technologies, MaStR) |
| `state_counts/<state>.csv` | Number of battery storages in &lt;state&gt; (Lithium-ion, MaStR), one chart per state, 16 files |

The 16 files under `state_counts/` are the only per-state HOME count in the download, and
they are what makes a per-state fleet analysis possible at all. Their state spellings differ
from the combined export, including a typo (`Brandeburg`) and a different number of hyphens
and m's in Mecklenburg; `mastr._STATE_FILE_CODE` maps both spellings on purpose, so a fresh
download reproduces without renaming anything.

Read them with `prosumer.data.mastr`, never with a bare `pd.read_csv`. Four properties of the
export will produce silently wrong numbers otherwise.

## 1. Units live inside the values

A cell reads `5.38 GWh`, `932 kWh` or `6.09 kW`. `parse_quantity` normalises everything to kWh
or kW and raises on an unknown suffix, because a GWh read as a kWh is a factor of a million.

## 2. Three significant figures

Values are rounded before export. A state at `5.38 GWh` is known to about plus or minus
5 MWh. Fleet totals are unaffected; differencing two neighbouring months is not meaningful at
that resolution.

## 3. The last row is a partial month

The series runs `01.Jan2000` to `13.Jan2026`, 314 rows. The final row is the snapshot date, not
a month boundary, so **2026 contains exactly one full month**. Any result labelled "2026" from
this download is a January 2026 snapshot. For a date later in 2026, re-export with a later
range. `mastr.coverage()` prints this.

## 4. The per-state files are all lithium-ion, not home storage

`battery_*_by_state_lithium_ion.csv` includes large-scale and industrial batteries. Home
storage is 83.9 % of installed energy in January 2024 and 80.1 % in January 2026, so the
non-home share grows. Per-state **home-storage counts are not in this download at all**, which
means a per-state mean size cannot be derived from these files; only Germany-wide means can.
Distributing the Germany-wide home-storage count by each state's lithium-ion energy share is an
assumption, and has to be labelled as one wherever it is used.

## Reference values, verified 2026-10-26

Germany-wide, at each 1 January snapshot:

| Date | Home storages | Mean energy | Mean power |
|---|---:|---:|---:|
| 2024-01-01 | 1,155,690 | 8.96 kWh | 5.53 kW |
| 2025-01-01 | 1,722,290 | 8.92 kWh | 5.66 kW |
| 2026-01-01 | 2,253,070 | 8.73 kWh | 5.55 kW |

Per-state home-storage counts, lithium-ion, same snapshots, from `state_counts/`:

| 1 January | Germany, lithium-ion | Germany, all chemistries | lithium-ion share |
|---|---:|---:|---:|
| 2024 | 1,119,240 | 1,155,690 | 96.8 % |
| 2025 | 1,670,776 | 1,722,290 | 97.0 % |
| 2026 | 2,190,625 | 2,253,070 | 97.2 % |

The per-state sum is lithium-ion only and is therefore about 3 % below the all-chemistry
count. Mixing the two understates the fleet by just enough to pass unnoticed.

The mean size **falls** after 2024: newly registered home storages are smaller than the
installed base, not larger. Any extrapolation that assumes a rising average is wrong in sign.

Within-year growth of the count is large, +45.7 % across 2024 and +29.2 % across 2025, while
the mean size moves by under 2 %. For a national aggregate the monthly count series matters;
for a single household one size per year is enough.
