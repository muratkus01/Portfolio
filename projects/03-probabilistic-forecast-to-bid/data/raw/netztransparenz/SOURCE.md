# netztransparenz.de: reBAP and ID AEP

Published by the four German transmission system operators. Downloaded by hand on
2026-10-31; the raw CSVs stay out of the repository, only results derived from them are
committed.

| Series | Page | Settings | Files |
|---|---|---|---|
| reBAP (quality-assured), short and long | Regelenergie > Ausgleichsenergiepreis > reBAP, "CSV-Datei herunterladen" | time zone UTC; at most about seven months per file, so six files with overlapping edges | `reBAP unterdeckt [2026-10-31 16-10-26].csv` ... `[2026-10-31 16-17-13].csv` |
| ID AEP | Regelenergie > Ausgleichsenergiepreis > Index Ausgleichsenergiepreis | time zone UTC, 01.01.2024 to 31.10.2026 in one file | `Index Ausgleichsenergiepreis [2026-10-31 15-46-18].csv` |

Layouts, both semicolon-separated with decimal commas:

- reBAP: `Datum;Zeitzone;von;bis;Einheit;reBAP unterdeckt;reBAP ueberdeckt`
- ID AEP: `Datum von;(Uhrzeit) von;Zeitzone;(Uhrzeit) bis;Zeitzone;ID AEP in EUR/MWh`

Coverage as read by `f2b.data.read_netztransparenz`: ID AEP from 2023-12-31 23:00 to
2026-10-30 22:45 UTC without a gap; reBAP from 2023-12-31 23:00 to 2026-10-19 21:45 UTC with
15, 16 and 18 October 2026 (local days) not yet published. The two reBAP columns are equal in
every quarter-hour of this period.
