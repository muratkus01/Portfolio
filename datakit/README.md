# datakit — shared open-data access

One module, no credentials, covering the German market and system data the portfolio's
projects depend on. Every project imports from here rather than writing its own fetcher, so
provenance, caching and licence recording happen in exactly one place.

```bash
python datakit.py --check        # verify every endpoint is reachable
python datakit.py --pull 2024    # download the standard portfolio dataset for a year
```

## Verified working

Last checked against the live APIs on 2026-08-11:

| Dataset | Function | Resolution | Source | Licence |
|---|---|---|---|---|
| Day-ahead price DE-LU | `day_ahead_price` | 15 min / 60 min | Energy-Charts | CC BY 4.0 |
| Generation by type + load | `public_power` | **15 min** | Energy-Charts | CC BY 4.0 |
| Installed capacity by technology | `installed_power` | annual | Energy-Charts | CC BY 4.0 |
| Cross-border physical flows | `cross_border_flows` | 15 min | Energy-Charts | CC BY 4.0 |
| ENTSO-E Transparency (raw) | `entsoe` | varies | ENTSO-E | free reuse, **token required** |

`python datakit.py --pull 2024` retrieves, for calendar year 2024:

```
price            8808 rows | mean   78.30 EUR/MWh | negative 5.3% of steps
public power    35232 rows | 21 production types      (= 366 days x 96 quarter-hours)
system feats    35232 rows | mean renewable share 54.2%
```

Those figures are a useful sanity check in themselves: Germany's 2024 day-ahead average and
renewable share both land where the published statistics put them, which is the cheapest
available evidence that the pipeline is reading the right thing.

## Design rules

- **Open data only.** No function here requires a commercial licence. Every project must run
  end-to-end without one; commercial sources may improve fidelity but are never a dependency.
- **Cache the raw response**, not the parsed frame. `cache/SOURCE.md` gets one line per
  retrieval recording the URL, timestamp and licence, so any number can be traced to its bytes.
- **UTC everywhere.** Returned frames are tz-aware UTC with a `timestamp` index name.
- **Units are explicit in column names** (`price_eur_per_mwh`, `load_mw`) so that a unit
  mistake is visible at the call site rather than three modules later.

## Which project uses what

| Project | Datasets |
|---|---|
| 01 Prosumer PV+BESS | `day_ahead_price` (tariff base) |
| 02 Pumped storage | `day_ahead_price`, `public_power`; balancing data from `regelleistung.net` and reBAP from `netztransparenz.de` are downloaded manually until an API wrapper is added |
| 05 Hybrid plant | `day_ahead_price`, `public_power`, `installed_power` (fleet normalisation) |
| 06 Forecast-to-bid | all of the above plus `cross_border_flows`; `imbalance_proxy` builds the residual-load and ramp features that drive imbalance-price tails |

## Not yet wrapped

Named here so the gaps are visible rather than discovered later:

- **reBAP imbalance prices** (netztransparenz.de) — Project 06's core target variable. Manual
  download for now; the site serves CSV but without a documented stable API.
- **Balancing capacity and energy auctions** (regelleistung.net) — Project 02's revenue data.
- **DWD ICON-D2 / ICON-D2-EPS** NWP fields — Projects 05 and 06. Large binary GRIB archives;
  needs its own retrieval and subsetting layer rather than a one-line fetcher.
- **Marktstammdatenregister** bulk export — asset registers for Projects 02 and 05.
