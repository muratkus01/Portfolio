"""Minimal regelleistung.net aFRR results workbooks, written in memory for offline tests.

Each workbook carries the real header names of the yearly RESULT_OVERVIEW files and invented
prices, so no TSO data enters the repository. Its header cells are shared strings and its
other text cells inline strings, so a reader must handle both; the real files use inline
strings only.
"""
from __future__ import annotations

import datetime
import io
import zipfile
from xml.sax.saxutils import escape

# The yearly results file's columns, in the order of the 2024 file (16) and the 2025 file (23).
LAYOUT_2024 = (
    "DATE_FROM", "DATE_TO", "TYPE_OF_RESERVES", "PRODUCT",
    "TOTAL_MIN_CAPACITY_PRICE_[(EUR/MW)/h]", "TOTAL_AVERAGE_CAPACITY_PRICE_[(EUR/MW)/h]",
    "TOTAL_MARGINAL_CAPACITY_PRICE_[(EUR/MW)/h]", "GERMANY_MIN_CAPACITY_PRICE_[(EUR/MW)/h]",
    "GERMANY_AVERAGE_CAPACITY_PRICE_[(EUR/MW)/h]", "GERMANY_MARGINAL_CAPACITY_PRICE_[(EUR/MW)/h]",
    "GERMANY_IMPORT(-)_EXPORT(+)_[MW]", "AUSTRIA_MIN_CAPACITY_PRICE_[(EUR/MW)/h]",
    "AUSTRIA_AVERAGE_CAPACITY_PRICE_[(EUR/MW)/h]", "AUSTRIA_MARGINAL_CAPACITY_PRICE_[(EUR/MW)/h]",
    "AUSTRIA_IMPORT(-)_EXPORT(+)_[MW]", "GERMANY_SUM_OF_OFFERED_CAPACITY_[MW]")
LAYOUT_2025 = (
    "DATE_FROM", "DATE_TO", "TYPE_OF_RESERVES", "PRODUCT",
    "TOTAL_MIN_CAPACITY_PRICE_[(EUR/MW)/h]", "TOTAL_AVERAGE_CAPACITY_PRICE_[(EUR/MW)/h]",
    "TOTAL_MARGINAL_CAPACITY_PRICE_[(EUR/MW)/h]", "GERMANY_MIN_CAPACITY_PRICE_[(EUR/MW)/h]",
    "GERMANY_AVERAGE_CAPACITY_PRICE_[(EUR/MW)/h]", "GERMANY_MARGINAL_CAPACITY_PRICE_[(EUR/MW)/h]",
    "GERMANY_IMPORT(-)_EXPORT(+)_[MW]", "GERMANY_ALLOCATED_VOLUME_[MW]",
    "AUSTRIA_MIN_CAPACITY_PRICE_[(EUR/MW)/h]", "AUSTRIA_AVERAGE_CAPACITY_PRICE_[(EUR/MW)/h]",
    "AUSTRIA_MARGINAL_CAPACITY_PRICE_[(EUR/MW)/h]", "AUSTRIA_IMPORT(-)_EXPORT(+)_[MW]",
    "AUSTRIA_ALLOCATED_VOLUME_[MW]", "CZECH_REPUBLIC_MIN_CAPACITY_PRICE_[(EUR/MW)/h]",
    "CZECH_REPUBLIC_AVERAGE_CAPACITY_PRICE_[(EUR/MW)/h]",
    "CZECH_REPUBLIC_MARGINAL_CAPACITY_PRICE_[(EUR/MW)/h]",
    "CZECH_REPUBLIC_IMPORT(-)_EXPORT(+)_[MW]", "CZECH_REPUBLIC_ALLOCATED_VOLUME_[MW]",
    "GERMANY_SUM_OF_OFFERED_CAPACITY_[MW]")
PRODUCTS = tuple(f"{prefix}_{h:02d}_{h + 4:02d}" for prefix in ("POS", "NEG")
                 for h in range(0, 24, 4))
GERMAN = {"average": "GERMANY_AVERAGE_CAPACITY_PRICE_[(EUR/MW)/h]",
          "marginal": "GERMANY_MARGINAL_CAPACITY_PRICE_[(EUR/MW)/h]"}

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML = '<?xml version="1.0" encoding="UTF-8"?>'
_CONTENT_TYPES = (
    f'{_XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>')
_WORKBOOK = (f'{_XML}<workbook xmlns="{_MAIN}" xmlns:r="{_RELATIONSHIPS}"><sheets>'
             '<sheet name="001" sheetId="1" r:id="rId3"/></sheets></workbook>')
_WORKBOOK_RELS = (
    f'{_XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rId1" Target="sharedStrings.xml" Type="{_RELATIONSHIPS}/sharedStrings"/>'
    f'<Relationship Id="rId3" Target="worksheets/sheet1.xml" Type="{_RELATIONSHIPS}/worksheet"/>'
    '</Relationships>')


def invented_prices(day: datetime.date, product: str) -> tuple[float, float]:
    """The German average and marginal that results_xlsx writes for a day and a product,
    EUR/MW/h, e.g. (6.5, 13.5): invented, and distinct per product and day of the week."""
    k = PRODUCTS.index(product) if product in PRODUCTS else len(PRODUCTS)
    average = 1.0 + k + (day.toordinal() % 7) / 4
    return average, 2 * average + 0.5


def results_xlsx(days, layout="16", blank=(), drop=(), extra=()) -> bytes:
    """A yearly results workbook holding the 12 products of each day, as bytes.

    days: the delivery days, e.g. [datetime.date(2025, 3, 29), datetime.date(2025, 3, 30)].
    layout: "16", the 2024 file's columns in its order; "23", the 2025 file's columns in
        reverse order, so that only a reader that finds columns by name reads both; or a
        tuple of column names. The Czech cells stay blank, as before 3 September 2025.
    blank: (day, product, basis) triples whose German price, basis "average" or "marginal",
        stays blank.
    drop: (day, product) rows left out.
    extra: (day, product) rows added after the others, e.g. a duplicate, or "POS_00_01", a
        15-minute product.
    Returns the bytes of the xlsx file; the same arguments give the same bytes.
    """
    header = {"16": LAYOUT_2024, "23": tuple(reversed(LAYOUT_2025))}.get(layout, layout)
    blanks = {(day, product, GERMAN[basis]) for day, product, basis in blank}
    dropped = set(drop)
    rows = [(day, product) for day in days for product in PRODUCTS
            if (day, product) not in dropped] + list(extra)
    sheet = [_row(1, [("shared", i) for i in range(len(header))])]
    for number, (day, product) in enumerate(rows, start=2):
        sheet.append(_row(number, [None if (day, product, name) in blanks
                                   else _value(name, day, product) for name in header]))
    parts = {"[Content_Types].xml": _CONTENT_TYPES, "xl/workbook.xml": _WORKBOOK,
             "xl/_rels/workbook.xml.rels": _WORKBOOK_RELS,
             "xl/sharedStrings.xml": _shared_strings(header),
             "xl/worksheets/sheet1.xml": (f'{_XML}<worksheet xmlns="{_MAIN}"><sheetData>'
                                          f'{"".join(sheet)}</sheetData></worksheet>')}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as book:
        for name, text in parts.items():
            # a fixed time stamp, so that the same arguments give the same bytes
            book.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), text,
                          compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def _value(name: str, day: datetime.date, product: str):
    """What column `name` holds on the row of a day and a product: text, a number, or None
    for a blank cell. Other countries' prices differ from the German ones, so a reader that
    took the wrong column would read wrong prices."""
    average, marginal = invented_prices(day, product)
    if name in ("DATE_FROM", "DATE_TO"):
        return float((day - datetime.date(1899, 12, 30)).days)
    if name.startswith("CZECH_REPUBLIC_"):
        return None
    fixed = {"TYPE_OF_RESERVES": "aFRR", "PRODUCT": product,
             GERMAN["average"]: average, GERMAN["marginal"]: marginal}
    return fixed.get(name, 500.0 + average)


def _row(number: int, values: list) -> str:
    """One sheet row: ("shared", i) as shared string i, text inline, floats as numbers, and
    None as no cell at all, as a blank cell is written."""
    cells = []
    for i, value in enumerate(values):
        ref = f"{chr(ord('A') + i)}{number}"          # the layouts have at most 26 columns
        if isinstance(value, tuple):
            cells.append(f'<c r="{ref}" t="s"><v>{value[1]}</v></c>')
        elif isinstance(value, str):
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        elif value is not None:
            cells.append(f'<c r="{ref}" t="n"><v>{value!r}</v></c>')
    return f'<row r="{number}">{"".join(cells)}</row>'


def _shared_strings(strings) -> str:
    """The shared-strings part holding `strings` in order."""
    items = "".join(f"<si><t>{escape(text)}</t></si>" for text in strings)
    return (f'{_XML}<sst xmlns="{_MAIN}" count="{len(strings)}" '
            f'uniqueCount="{len(strings)}">{items}</sst>')
