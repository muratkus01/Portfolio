# Aggregate household load model: sources, licences, how to rebuild

Everything in this folder is written by one command, run from `projects/01-prosumer-pv-bess-mpc-rl/`:

```bash
prosumer aggregate-load
```

It needs the gitignored HTW 15-minute parquet (`data/processed/htw_74_15min_2010.parquet`, built by `prosumer.data.htw.load_htw_15min`) and downloads ERA5 daily temperatures once into `data/raw/weather/`. `prosumer aggregate-load --states-only` rebuilds only the per-state summary from the committed model.

## Inputs and licences

| Input | Source | Licence |
|---|---|---|
| 73 household load profiles, 2010, 15-minute metered | Tjaden et al. (2015), "Representative electrical load profiles of residential buildings in Germany", HTW Berlin, DOI 10.13140/RG.2.1.5112.0080/1; measured by IZES in the field test "Moderne Energiesparsysteme im Haushalt". Household H49 is excluded (PV behind the meter, `../htw_pv_screen.csv`). | CC BY-NC 4.0 |
| Daily mean 2 m temperature, 1991 to 2026, at the 16 state sites of `pv_states.STATE_SITES` | ERA5 via the Open-Meteo archive API (`models=era5`, local dates Europe/Berlin). Contains modified Copernicus Climate Change Service information. | CC BY 4.0 |
| Public holidays per state | `prosumer.data.slp.state_holidays`, statutory rules | n/a |
| Fleet counts (state summary only) | MaStR via battery-charts.de, see `../../data/raw/mastr/SOURCE.md` | public |

## What is derived from the HTW data

`coefficients.csv`, `fit_meta.csv` and the validation tables are an adaptation of the HTW data: regression coefficients of the 73-household mean and summary statistics of the fit. No HTW time series is stored here, not even the household mean. They inherit CC BY-NC 4.0, which permits this non-commercial portfolio. `state_normals.csv` and `fit_temperature_2010.csv` are ERA5 only.

## Files

| File | Content |
|---|---|
| `coefficients.csv` | The model, long format: 7 day-class levels, 6 seasonal terms, 4 seasonal terms of the Saturday and Sunday-type uplift and the temperature slope of daily energy; 3 x 96 x 5 share coefficients |
| `fit_meta.csv` | Normal-weather annual energy `a_ref_kwh`, the anomaly range the slope is fitted on, proxy site, harmonic orders, households, fit days, SHA-256 of the parquet |
| `state_normals.csv` | 1991-2020 normal of daily mean temperature per state site, constant plus two annual harmonics |
| `fit_temperature_2010.csv` | ERA5 temperature, normal and anomaly at the proxy site (Hannover) for 2010 |
| `clock_check.csv` | V1: morning-ramp step at the 2010 DST changes, file clock against a MEZ reading |
| `holiday_check.csv` | V2: which 2010 holidays the households kept; regional weekdays against nationwide ones |
| `holiday_detection_power.csv` | V2: how often the holiday test sees a holiday kept by a given share of the homes (simulated by giving that share their nearest Sunday) |
| `daily_model_selection.csv` | V3: day classes x seasonal harmonics x temperature form x seasonal weekend uplift x site, blocked cross-validation (7- and 28-day blocks) |
| `shape_model_selection.csv` | V4: BDEW 3 x 3 share table against harmonic shapes, with and without temperature, 15-minute RMSE |
| `shape_temperature.csv` | V4: per hour, correlation of the share residual with the temperature anomaly and the share change per K |
| `slope_uncertainty.csv` | V5: temperature slope with naive, HAC and bootstrap (households, 14-day blocks, both) standard errors, residual autocorrelation |
| `proxy_sensitivity.csv`, `proxy_sensitivity_states_2024.csv` | V6: refit at every state site; spread of 2024 per-state energy across the holiday-consistent sites |
| `split_half.csv` | V7: fit on half the households without one week, predict the other half in that week; against BDEW H0 and the first half's same-day mean |
| `residual_decomposition.csv`, `residual_days.csv` | V8: in-sample residual split into household noise and a common part; the 20 largest daily residuals |
| `insample_2010.csv` | V9: model, measured 73-household mean and H0 on 2010 |
| `state_summary.csv` | V10: per state, 2024, 2025 and 2026 to the last ERA5 day, at two energy levels |
| `validation_summary.csv` | One row per check with its pre-set threshold and whether it passed |
