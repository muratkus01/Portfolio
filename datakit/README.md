# datakit: shared open-data access

One module for open German market and system data. Its Energy-Charts functions need no
credentials; `entsoe` needs a free ENTSO-E token. Projects 02 and 03 and the archived A2 and
A3 fetch their Energy-Charts data through it, so provenance, caching and licence recording
for that data happen in one place. Project 01 has its own fetchers (`prosumer/data/`), and
Project 03 fetches its weather forecasts itself (`f2b/weather.py`).

```bash
python datakit.py --check        # verify every endpoint is reachable
python datakit.py --pull 2025    # download the standard portfolio dataset for a year
```

## Verified working

Last checked against the live APIs on 2026-08-11:

| Dataset | Function | Resolution | Source | Licence |
|---|---|---|---|---|
| Day-ahead price DE-LU | `day_ahead_price` | 15 min / 60 min | Energy-Charts | CC BY 4.0 |
| Generation by type + load | `public_power` | **15 min** | Energy-Charts | CC BY 4.0 |
| Installed capacity by technology | `installed_power` | annual | Energy-Charts | CC BY 4.0 |
| Cross-border physical flows | `cross_border_flows` | 15 min | Energy-Charts | CC BY 4.0 |

`python datakit.py --check` calls these four. `entsoe`, a raw ENTSO-E Transparency query
(free reuse, **token required**), is not part of that check, because it needs a token.

`python datakit.py --pull 2025` retrieves, for calendar year 2025:

```
price           15483 rows | mean   90.47 EUR/MWh | negative 4.7% of steps
public power    35136 rows | 21 production types
system feats    35136 rows | mean renewable share 54.3%
```

Those figures are a useful sanity check in themselves: Germany's 2025 day-ahead average and
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
| 01 Prosumer PV+BESS | none; it fetches its day-ahead prices with its own loader (`prosumer/data/loaders.py`) |
| 02 Hybrid plant | `day_ahead_price`; `public_power` (onshore wind and solar feed-in); `total_power` (solar generation including self-consumption); `installed_power`, monthly (the fleet behind its capacity factors) |
| 03 Forecast-to-bid | `day_ahead_price`, `public_power`, `installed_capacity_at` (the fleet behind its capacity factors) and `public_power_forecast` (the TSO forecasts, its benchmark); its parametric ablation uses `get_installed_capacity` |
| A2 Pumped storage (archived) | `day_ahead_price`; its aFRR prices and activations and its reBAP are stand-ins, not downloaded data (`psw/config.py`, `psw/market.py`) |
| A3 EV charging (archived) | `day_ahead_price` |

No project calls `cross_border_flows` or `imbalance_proxy`: `--check` calls the first and
`--pull` the second.

## Not yet wrapped

Named here so the gaps are visible rather than discovered later:

- **reBAP imbalance prices and the ID AEP intraday index** (netztransparenz.de) — Project 03
  settles at both. Manual download for now; the site serves CSV but without a documented
  stable API.
- **Balancing capacity and energy auctions** (regelleistung.net) — needed for aFRR, which is
  planned for Project 02's battery but not built; the archived A2 ran on stand-in prices.
- **DWD ICON-D2 / ICON-D2-EPS** NWP fields. Large binary GRIB archives; needs its own
  retrieval and subsetting layer rather than a one-line fetcher. Project 02 leaves wind and PV
  from NWP for later work (`hybrid/data.py`); Projects 01 and 03 take their weather forecasts
  from Open-Meteo instead, with their own fetchers.
- **Marktstammdatenregister** bulk export — the unit register. No project reads it through
  datakit: Project 01 reads monthly home-storage series from battery-charts.de, which is built
  on it (`prosumer/data/mastr.py`).
