# Energy-Charts: output, TSO forecasts, capacity and day-ahead prices

Fetched on 2026-10-31 through the shared `datakit` client from https://api.energy-charts.info
(Fraunhofer ISE), licence CC BY 4.0. The day-ahead price responses name their own source in
the field `license_info`: "CC BY 4.0 (creativecommons.org/licenses/by/4.0) from
Bundesnetzagentur | SMARD.de". The responses are cached in `datakit/cache/`, which is not
committed; its own SOURCE.md lists every file with its URL and retrieval time.

| Series | Endpoint, per calendar year | Use |
|---|---|---|
| German public net generation: wind onshore, wind offshore, solar | `public_power?country=de` | the output, scaled to the portfolio |
| TSO forecasts of the same, day-ahead, intraday and current | `public_power_forecast?country=de&production_type=...&forecast_type=...` | F4, the benchmark |
| Installed capacity per month | `installed_power?country=de&time_step=monthly` | turns output and forecasts into capacity factors |
| Day-ahead price, DE-LU | `price?bzn=DE-LU` | the day-ahead leg |

The values are as retrieved. Published actuals can be corrected after the fact, so they may
differ from what was known at the time. The forecasts carry no publication time; README
section 4 says what that means for F4.
