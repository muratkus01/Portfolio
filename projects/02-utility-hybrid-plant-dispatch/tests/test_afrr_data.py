"""The aFRR data layer: regelleistung.net's yearly capacity results, read offline.

The workbooks are written in memory by tests/afrr_fixtures.py, with the real header names and
invented prices, so no test reads TSO data or the network. Every test here fails on a802748,
which has no aFRR data layer: nothing there reads the results file.
"""
from __future__ import annotations

import datetime
import importlib.util
import re
from pathlib import Path

import pandas as pd
import pytest

from hybrid import afrr_data
from hybrid.afrr_data import PRICE_COLUMNS, parse_results


def _load_fixtures():
    """tests/afrr_fixtures.py, loaded by its path: the root pyproject.toml runs the tests in
    importlib mode, which puts no test folder on sys.path."""
    path = Path(__file__).with_name("afrr_fixtures.py")
    spec = importlib.util.spec_from_file_location("afrr_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = _load_fixtures()
results_xlsx = fixtures.results_xlsx
DAYS = [datetime.date(2025, 1, 1) + datetime.timedelta(days=k) for k in range(3)]


# ------------------------------------------------------------------ parser
def test_parser_reads_both_layouts_by_column_name():
    """The 2024 file has 16 columns; the 2025 file has 23, whose Czech cells are blank before
    3 September 2025. Read by name, a file in either layout gives the same table of German
    prices, even with the columns in reverse order."""
    table = parse_results(results_xlsx(DAYS, layout="16"))

    pd.testing.assert_frame_equal(table, parse_results(results_xlsx(DAYS, layout="23")))
    assert list(table.columns) == ["date", "direction", "start_h", "end_h", "average",
                                   "marginal"]
    assert len(table) == 3 * 12
    row = table[(table["date"] == DAYS[1]) & (table["direction"] == "down")
                & (table["start_h"] == 8)]
    assert row["end_h"].tolist() == [12]
    assert (row["average"].item(), row["marginal"].item()) == fixtures.invented_prices(
        DAYS[1], "NEG_08_12")


def test_parser_refuses_a_blank_german_price():
    """A blank German price is refused with its day and product, never read as 0."""
    for basis, column in PRICE_COLUMNS.items():
        data = results_xlsx(DAYS, blank=[(DAYS[1], "NEG_08_12", basis)])
        with pytest.raises(ValueError,
                           match=rf"{re.escape(column)} is blank on 2025-01-02 for NEG_08_12"):
            parse_results(data)


def test_parser_refuses_a_day_without_its_twelve_products():
    """Every day carries each of its 12 products once: a day that lacks one, repeats one or
    lacks them all is refused, naming the day."""
    lacking = results_xlsx(DAYS, drop=[(DAYS[1], "POS_16_20")])
    repeating = results_xlsx(DAYS, extra=[(DAYS[1], "POS_16_20")])
    missing_day = results_xlsx([DAYS[0], DAYS[2]])

    with pytest.raises(ValueError,
                       match=r"2025-01-02 lacks the aFRR product\(s\) \['POS_16_20'\]"):
        parse_results(lacking)
    with pytest.raises(ValueError, match="POS_16_20 appears more than once on 2025-01-02"):
        parse_results(repeating)
    with pytest.raises(ValueError, match="no products for 2025-01-02"):
        parse_results(missing_day)


def test_parser_refuses_an_unknown_product():
    """A product other than the 12 daily 4-hour ones, such as a 15-minute product of
    BK6-25-212, is refused rather than mapped onto a 4-hour slice, so a later year's file
    fails loudly."""
    with pytest.raises(ValueError, match="unknown aFRR product 'POS_00_01' on 2025-01-02"):
        parse_results(results_xlsx(DAYS, extra=[(DAYS[1], "POS_00_01")]))


def test_parser_refuses_a_file_without_a_column_it_reads():
    """DATE_FROM, PRODUCT and both German prices are found by name, or the file is refused."""
    for name in ("DATE_FROM", "PRODUCT", *PRICE_COLUMNS.values()):
        layout = tuple(column for column in fixtures.LAYOUT_2025 if column != name)
        with pytest.raises(ValueError, match=rf"lacks the column\(s\) \['{re.escape(name)}'\]"):
            parse_results(results_xlsx(DAYS, layout=layout))


def test_the_reader_refuses_a_zip_member_above_its_cap(monkeypatch):
    """The workbook comes from the web, so no member is unpacked past the cap, whatever size
    the archive declares. The cap is lowered here to below the size of the fixture's sheet."""
    monkeypatch.setattr(afrr_data, "_MAX_MEMBER_BYTES", 5000)
    with pytest.raises(ValueError, match="sheet1.xml unpacks to more than 5000 bytes"):
        parse_results(results_xlsx(DAYS))
