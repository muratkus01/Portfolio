# README rows of the aFRR tables, for scripts/publish_p02.py

The rows the driver adds when its run of the publish set writes the aFRR tables: one per new
experiment to the README's table of commands, one per new window to its table of windows, and
the new tables' rows to the file table of reports/SOURCE.md. The format is the one the
driver's docstring gives. The window rows print window_stats.csv's values for the two 2024
windows, computed on 2 November 2026 from the cached Energy-Charts data the committed tables
were computed from; the P02 suite checks them against the fresh window_stats.csv before the
driver commits. The README's results section on aFRR is written after the run, from its tables.

## How each table is made

| [`afrr_2024_fast.csv`](reports/afrr_2024_fast.csv) | calendar year 2024 | `python -m hybrid.cli afrr --year 2024 --full-year --fast` |
| [`afrr_2025_fast.csv`](reports/afrr_2025_fast.csv) | calendar year 2025 | `python -m hybrid.cli afrr --year 2025 --full-year --fast` |
| [`afrr_2024_fast_up-hours-1.csv`](reports/afrr_2024_fast_up-hours-1.csv) | calendar year 2024 | `python -m hybrid.cli afrr --year 2024 --full-year --fast --up-hours 1` |
| [`afrr_2025_fast_up-hours-1.csv`](reports/afrr_2025_fast_up-hours-1.csv) | calendar year 2025 | `python -m hybrid.cli afrr --year 2025 --full-year --fast --up-hours 1` |
| [`afrr_2025_fast_basis-average.csv`](reports/afrr_2025_fast_basis-average.csv) | calendar year 2025 | `python -m hybrid.cli afrr --year 2025 --full-year --fast --basis average` |
| [`afrr_2025_fast_basis-marginal.csv`](reports/afrr_2025_fast_basis-marginal.csv) | calendar year 2025 | `python -m hybrid.cli afrr --year 2025 --full-year --fast --basis marginal` |
| [`afrr_2025-01-01_30d.csv`](reports/afrr_2025-01-01_30d.csv) | 1 to 30 January 2025 | `python -m hybrid.cli afrr --year 2025 --start 01-01 --days 30` |
| [`afrr_2025-07-01_30d.csv`](reports/afrr_2025-07-01_30d.csv) | 1 to 30 July 2025 | `python -m hybrid.cli afrr --year 2025 --start 07-01 --days 30` |
| [`afrr_2024-01-01_30d.csv`](reports/afrr_2024-01-01_30d.csv) | 1 to 30 January 2024 | `python -m hybrid.cli afrr --year 2024 --start 01-01 --days 30` |
| [`afrr_2024-07-01_30d.csv`](reports/afrr_2024-07-01_30d.csv) | 1 to 30 July 2024 | `python -m hybrid.cli afrr --year 2024 --start 07-01 --days 30` |

## Windows of the tables

| 1 to 30 January 2024 | 2,880 | 76.59 | 64 | 40.0 | 0.500 | 60 | 0 |
| 1 to 30 July 2024 | 2,880 | 67.31 | 324 | 28.5 | 0.356 | 60 | 0 |

## Files

| `afrr_2024_fast.csv`, `afrr_2025_fast.csv` | {rows} | one rung (B0, B1, B2) at one connection (20, 30 or 60 MW) without aFRR capacity or with it, B2 paid its accepted bids and keeping 4 hours of energy for its positive reserve, with the window's aFRR capacity prices and B2's aFRR value at that connection |
| `afrr_2024_fast_up-hours-1.csv`, `afrr_2025_fast_up-hours-1.csv` | {rows} | the same, keeping 1 hour of energy for positive reserve |
| `afrr_2025_fast_basis-average.csv`, `afrr_2025_fast_basis-marginal.csv` | {rows} | the same as `afrr_2025_fast.csv`, with B2 paid the realised German average or marginal capacity price instead of its bids |
| `afrr_2025-01-01_30d.csv`, `afrr_2025-07-01_30d.csv`, `afrr_2024-01-01_30d.csv`, `afrr_2024-07-01_30d.csv` | {rows} | one rung, B0 to B3, at one connection without aFRR capacity or with it; B3 bids each product at the German average price it cleared at the day before and is paid its accepted bids, and its rows add its aFRR value and its share of B2's |
