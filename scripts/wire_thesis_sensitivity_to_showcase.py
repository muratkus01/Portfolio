"""Project 01's thesis sizing stand-in as a CSV, and its values on the showcase page.

    python scripts/wire_thesis_sensitivity_to_showcase.py [--check]

`prosumer.experiments.sensitivity` gives each size of the thesis sizing grid a ROCE without
simulating any dispatch: the yearly profit comes from hand-set formulas, and its Type 1 and
Type 2 functions use different ones. Hence "parametric" in the CSV's name. The script records
what the code returns and puts exactly that on the page:

1. reports/thesis_sensitivity_parametric.csv in the Project 01 folder, one row per feasible
   size (1,016): CAPEX, the Type 1 profit, ROCE and rank (tied sizes share a rank), and,
   where PV is 5, 10 or 15 kWp, the Type 2 profit and ROCE;
2. the THESIS_SENSITIVITY line of showcase.html, built from that CSV as read back: Type 1's
   top five and every Type 2 cell, which the page's script draws.

With --check it writes nothing and exits with an error if the committed CSV or the page line
differs from what the code gives now.
"""
import argparse
import io
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
P01 = ROOT / "projects" / "01-prosumer-pv-bess-mpc-rl"
# This checkout's package first, not an editable install that points at another checkout.
sys.path.insert(0, str(P01 / "src"))

from prosumer.experiments.sensitivity import (  # noqa: E402
    run_type1_sensitivity, run_type2_fixed_pv_sensitivity)

CSV_PATH = P01 / "reports" / "thesis_sensitivity_parametric.csv"
HTML_PATH = ROOT / "showcase.html"
TYPE2_PV_KWP = (5, 10, 15)      # the fixed PV sizes the page has buttons for
TOP_N = 5                       # rows of the page's Type 1 table
CONST = "const THESIS_SENSITIVITY = "


def sensitivity_table() -> pd.DataFrame:
    """Every feasible size with the values of both functions, in Type 1 rank order.

    Columns: config_id, pv_kwp, bess_kwh, inverter_kw, capex_eur, type1_profit_eur,
    type1_roce_pct, type1_rank, type2_profit_eur, type2_roce_pct, e.g. the first row is
    15_8_8, 15, 8, 8, 12205.0, 1115.81, 9.14, 1, 1115.8, 9.14. type1_rank is 1 plus the
    number of sizes with a higher type1_roce_pct, so tied sizes share a rank; rows of equal
    rank run by PV, BESS and inverter. The Type 2 columns are empty unless PV is one of
    TYPE2_PV_KWP.
    """
    t1 = run_type1_sensitivity()
    # The module numbers its rows after a sort that is not stable, so sizes with equal ROCE get
    # distinct ranks in an order that depends on the numpy build and the CPU. Here tied sizes
    # share the best rank and the rows have a fixed order, so every machine writes the same file.
    table = t1[["config_id", "pv_kwp", "bess_kwh", "inverter_kw", "capex_eur"]].assign(
        type1_profit_eur=t1["opt_profit_eur"], type1_roce_pct=t1["roce_pct"],
        type1_rank=t1["roce_pct"].rank(method="min", ascending=False).astype(int))
    table = table.sort_values(["type1_rank", "pv_kwp", "bess_kwh", "inverter_kw"], ignore_index=True)
    t2 = pd.DataFrame([
        {"config_id": f"{pv}_{c['bess_kwh']}_{c['inverter_kw']}", "type2_capex_eur": c["capex_eur"],
         "type2_profit_eur": c["opt_profit_eur"], "type2_roce_pct": round(c["roce"] * 100, 2)}
        for pv in TYPE2_PV_KWP for c in run_type2_fixed_pv_sensitivity(float(pv))["valid_configs"]])
    table = table.merge(t2, on="config_id", how="left", validate="one_to_one")
    # Both functions price a size with compute_capex, so equal CAPEX shows that each Type 2
    # row joined its own size.
    joined = table["type2_capex_eur"].notna()
    if joined.sum() != len(t2) or not (table.loc[joined, "type2_capex_eur"]
                                       == table.loc[joined, "capex_eur"]).all():
        sys.exit("ERROR: the Type 2 sizes do not join Type 1's grid one to one")
    return table.drop(columns="type2_capex_eur")


def page_data(table: pd.DataFrame) -> dict:
    """What the page draws, taken from `table` (the CSV as read back).

    e.g. {"type1_top": [[1, 15, 8, 8, 12205.0, 1115.81, 9.14], ...],
          "type2": {"5": [[3, 3, 1.79], ...], "10": [...], "15": [...]}}.
    A Type 1 row is rank, PV kWp, BESS kWh, inverter kW, CAPEX EUR, profit EUR and ROCE %;
    a Type 2 cell is BESS kWh, inverter kW and ROCE %, sorted by BESS, then inverter.
    """
    type1_top = [[int(r.type1_rank), int(r.pv_kwp), int(r.bess_kwh), int(r.inverter_kw),
                  float(r.capex_eur), float(r.type1_profit_eur), float(r.type1_roce_pct)]
                 for r in table.nsmallest(TOP_N, "type1_rank").itertuples()]
    type2 = {}
    for pv in TYPE2_PV_KWP:
        cells = table[(table["pv_kwp"] == pv) & table["type2_roce_pct"].notna()]
        type2[str(pv)] = [[int(r.bess_kwh), int(r.inverter_kw), float(r.type2_roce_pct)]
                          for r in cells.sort_values(["bess_kwh", "inverter_kw"]).itertuples()]
    return {"type1_top": type1_top, "type2": type2}


def wire(html: str, data: dict) -> str:
    """`html` with its one THESIS_SENSITIVITY line set to `data`; exits unless there is one.

    The line keeps its indentation and its line ending, so a CRLF checkout stays CRLF.
    """
    lines = html.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if line.lstrip().startswith(CONST)]
    if len(hits) != 1:
        sys.exit(f"ERROR: showcase.html has {len(hits)} lines starting with {CONST!r}, expected 1")
    line = lines[hits[0]]
    indent = line[:len(line) - len(line.lstrip())]
    ending = line[len(line.rstrip("\r\n")):]
    lines[hits[0]] = f"{indent}{CONST}{json.dumps(data, separators=(',', ':'))};{ending}"
    return "".join(lines)


def check_up_to_date(csv_text: str, html: str, wired: str) -> None:
    """Exit with an error unless the committed CSV and the page line equal what the code gives.

    `csv_text` and `wired` are the CSV and the page as this run would write them, `html` the
    page as it is. The CSV is read with universal newlines, so a checkout whose line endings
    Git turned into CRLF (core.autocrlf) still compares equal.
    """
    stale = []
    if CSV_PATH.read_text(encoding="utf-8") != csv_text:
        stale.append(CSV_PATH.relative_to(ROOT).as_posix())
    if wired != html:
        stale.append("the THESIS_SENSITIVITY line of showcase.html")
    if stale:
        sys.exit(f"ERROR: out of date with prosumer.experiments.sensitivity: {' and '.join(stale)}."
                 " Rerun python scripts/wire_thesis_sensitivity_to_showcase.py.")
    print("OK: the CSV and the page's THESIS_SENSITIVITY line equal what the code gives.")


def main() -> None:
    """Write the CSV, then set the page's THESIS_SENSITIVITY line from the CSV as written.

    With --check, write nothing and exit with an error if either is out of date.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="write nothing; fail if the CSV or the page line is out of date")
    args = ap.parse_args()
    table = sensitivity_table()
    csv_text = table.to_csv(index=False, lineterminator="\n")
    # The page reads the CSV text back rather than the frame, so it shows exactly what is committed.
    data = page_data(pd.read_csv(io.StringIO(csv_text)))
    # newline="" keeps the page's own line endings on every platform.
    with open(HTML_PATH, encoding="utf-8", newline="") as f:
        html = f.read()
    wired = wire(html, data)
    if args.check:
        check_up_to_date(csv_text, html, wired)
        return
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(csv_text)
    with open(HTML_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(wired)
    cells = sum(len(v) for v in data["type2"].values())
    print(f"Wrote {CSV_PATH.relative_to(ROOT).as_posix()} ({len(table)} sizes) and set "
          f"THESIS_SENSITIVITY in showcase.html (top {TOP_N}, {cells} Type 2 cells).")


if __name__ == "__main__":
    main()
