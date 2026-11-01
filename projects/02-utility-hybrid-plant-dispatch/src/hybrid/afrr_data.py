"""regelleistung.net's aFRR capacity results: the I/O boundary of the aFRR model.

The TSOs publish one RESULT_OVERVIEW_CAPACITY_MARKET_aFRR file per year, an xlsx workbook with
one row per day and product. It is read here with the standard library, by column name, so no
dependency is added: pandas.read_excel would need openpyxl, which the project does not install.
Whatever the reader would otherwise have to guess at, it refuses.

The workbook comes from the web, so it is untrusted input. No zip member is unpacked beyond
_MAX_MEMBER_BYTES, and ElementTree, on the expat that ships with Python 3.11 and 3.12, resolves
no external entities and bounds entity expansion.
"""
from __future__ import annotations

import datetime
import io
import math
import re
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd

# The German prices of a product, EUR/MW/h: the volume-weighted average of the accepted bids,
# and the marginal, the highest accepted bid. Never the TOTAL_* columns: Austria since 2020
# and the Czech Republic since 3 September 2025 procure jointly with Germany, so those mix in
# the other countries' bids.
PRICE_COLUMNS = {"average": "GERMANY_AVERAGE_CAPACITY_PRICE_[(EUR/MW)/h]",
                 "marginal": "GERMANY_MARGINAL_CAPACITY_PRICE_[(EUR/MW)/h]"}
_REQUIRED_COLUMNS = ("DATE_FROM", "PRODUCT", *PRICE_COLUMNS.values())

# The 12 daily products: six 4-hour slices of the Berlin day per direction, POS for positive
# (upward) and NEG for negative (downward) reserve (MfRRA § 20 (1), (2), (4)). Any other
# name, such as a 15-minute product of BK6-25-212, is refused rather than mapped.
_DIRECTIONS = {"POS": "up", "NEG": "down"}
_PRODUCTS = frozenset(f"{prefix}_{h:02d}_{h + 4:02d}" for prefix in _DIRECTIONS
                      for h in range(0, 24, 4))

# DATE_FROM is an Excel serial day of the 1900 date system, whose day 0 is 30 December 1899.
_EXCEL_EPOCH = datetime.date(1899, 12, 30)
# The guard against a zip bomb. The largest member, the 2025 sheet, unpacks to 3.5 MB.
_MAX_MEMBER_BYTES = 50 * 2 ** 20

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_DOCUMENT_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_TABLE_COLUMNS = ["date", "direction", "start_h", "end_h", "average", "marginal"]


def parse_results(data: bytes) -> pd.DataFrame:
    """The German capacity prices of every product in a yearly aFRR results file.

    data: the bytes of RESULT_OVERVIEW_CAPACITY_MARKET_aFRR_<year>-01-01_<year>-12-31.xlsx, in
        the 16-column layout of 2024 or the 23-column one of 2025; columns are found by name.
    Returns one row per day and product, sorted by date, direction and start_h, with columns
        date (datetime.date, the Berlin delivery day), direction ("up" for POS, "down" for
        NEG), start_h and end_h (the product's Berlin clock hours, e.g. 8 and 12), and
        average and marginal (EUR/MW/h).
    Raises ValueError, naming the day and the product, when the file lacks a column the model
    reads, a German price is blank, negative or not finite, a product is unknown, or a day
    from the first to the last lacks one of the 12 products or repeats one.
    """
    rows = _sheet_rows(data)
    column = _column_positions(rows[0] if rows else [])
    records = [_product_row(row, column) for row in rows[1:] if any(row)]
    if not records:
        raise ValueError("the aFRR results file holds no product rows")
    table = pd.DataFrame(records)
    _check_days(table)
    table = table.sort_values(["date", "direction", "start_h"], ignore_index=True)
    return table[_TABLE_COLUMNS]


def _column_positions(header: list[str | None]) -> dict[str, int]:
    """Each column's position by its name in the header row; refuses a file that lacks a
    column the model reads."""
    position = {name: i for i, name in enumerate(header) if name}
    missing = [name for name in _REQUIRED_COLUMNS if name not in position]
    if missing:
        raise ValueError(f"the aFRR results file lacks the column(s) {missing}")
    return position


def _product_row(row: list[str | None], column: dict[str, int]) -> dict:
    """One product row of the file as a record: day, product, direction, hours and prices."""
    def cell(name: str) -> str | None:
        return row[column[name]] if column[name] < len(row) else None

    day = _delivery_day(cell("DATE_FROM"))
    product = cell("PRODUCT")
    if product not in _PRODUCTS:
        raise ValueError(f"unknown aFRR product {product!r} on {day}: only the 12 daily "
                         "4-hour products are known")
    prefix, start, end = product.split("_")
    prices = {basis: _price(cell(name), name, day, product)
              for basis, name in PRICE_COLUMNS.items()}
    return {"date": day, "product": product, "direction": _DIRECTIONS[prefix],
            "start_h": int(start), "end_h": int(end), **prices}


def _delivery_day(value: str | None) -> datetime.date:
    """DATE_FROM, an Excel serial day such as "45658.0", as the Berlin delivery day."""
    try:
        serial = float(value)
    except (TypeError, ValueError) as err:
        raise ValueError(f"DATE_FROM {value!r} is not an Excel serial day") from err
    if not serial.is_integer():
        raise ValueError(f"DATE_FROM {value!r} is not a whole day")
    return _EXCEL_EPOCH + datetime.timedelta(days=int(serial))


def _price(value: str | None, name: str, day: datetime.date, product: str) -> float:
    """A German price cell in EUR/MW/h; a blank, negative or non-finite one is refused."""
    if value in (None, ""):
        raise ValueError(f"{name} is blank on {day} for {product}")
    try:
        price = float(value)
    except ValueError as err:
        raise ValueError(f"{name} reads {value!r} on {day} for {product}") from err
    if not math.isfinite(price) or price < 0.0:
        raise ValueError(f"{name} is {price} on {day} for {product}; a capacity price is "
                         "finite and at least 0")
    return price


def _check_days(table: pd.DataFrame) -> None:
    """Refuse a file in which a day from the first to the last lacks one of the 12 products
    or repeats one. A whole day missing would otherwise surface only where a window needs it."""
    repeated = table[table.duplicated(["date", "product"])]
    if len(repeated):
        first = repeated.iloc[0]
        raise ValueError(f"{first['product']} appears more than once on {first['date']}")
    products = table.groupby("date")["product"].agg(set)
    for day, held in products.items():
        if held != _PRODUCTS:
            raise ValueError(f"{day} lacks the aFRR product(s) {sorted(_PRODUCTS - held)}")
    first, last = products.index.min(), products.index.max()
    for offset in range((last - first).days + 1):
        day = first + datetime.timedelta(days=offset)
        if day not in products.index:
            raise ValueError(f"the aFRR results file has no products for {day}")


def _sheet_rows(data: bytes) -> list[list[str | None]]:
    """The cells of an xlsx workbook's first sheet as text, row by row, None where blank.

    Shared strings, inline strings and numbers are read; a number keeps the text of its stored
    value, e.g. "45658.0". A row ends at its last cell that holds a value.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            shared = _shared_strings(book)
            sheet = ET.fromstring(_member(book, _first_sheet(book)))
    except (zipfile.BadZipFile, ET.ParseError) as err:
        raise ValueError(f"the aFRR results file is not a readable xlsx workbook: {err}") from err
    rows = []
    for row in sheet.iter(f"{_MAIN}row"):
        cells = {_column_index(cell.get("r")): _cell_text(cell, shared)
                 for cell in row.iter(f"{_MAIN}c")}
        rows.append([cells.get(i) for i in range(max(cells, default=-1) + 1)])
    return rows


def _first_sheet(book: zipfile.ZipFile) -> str:
    """The zip member of the workbook's first sheet, found through workbook.xml and its
    relationships, e.g. "xl/worksheets/sheet1.xml"."""
    sheet = ET.fromstring(_member(book, "xl/workbook.xml")).find(f"{_MAIN}sheets/{_MAIN}sheet")
    relationships = ET.fromstring(_member(book, "xl/_rels/workbook.xml.rels"))
    target = None if sheet is None else next(
        (rel.get("Target") for rel in relationships.iter(f"{_PACKAGE_REL}Relationship")
         if rel.get("Id") == sheet.get(f"{_DOCUMENT_REL}id")), None)
    if not target:
        raise ValueError("the aFRR results workbook names no first sheet")
    return target.lstrip("/") if target.startswith("/") else f"xl/{target}"


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    """The workbook's shared strings in order; none where it has no shared-strings part."""
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    strings = ET.fromstring(_member(book, "xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in item.iter(f"{_MAIN}t"))
            for item in strings.iter(f"{_MAIN}si")]


def _member(book: zipfile.ZipFile, name: str) -> bytes:
    """One zip member's bytes. Reading stops past _MAX_MEMBER_BYTES and the member is refused,
    whatever size the archive declares for it."""
    try:
        with book.open(name) as member:
            data = member.read(_MAX_MEMBER_BYTES + 1)
    except KeyError as err:
        raise ValueError(f"the aFRR results workbook has no {name}") from err
    if len(data) > _MAX_MEMBER_BYTES:
        raise ValueError(f"{name} unpacks to more than {_MAX_MEMBER_BYTES} bytes; refused")
    return data


def _column_index(ref: str | None) -> int:
    """The 0-based column of a cell reference: 0 for "A2", 25 for "Z9", 26 for "AA1"."""
    match = re.fullmatch(r"([A-Z]{1,3})[0-9]+", ref or "")
    if match is None:
        raise ValueError(f"cell reference {ref!r} is not of the form A1")
    index = 0
    for letter in match.group(1):
        index = 26 * index + ord(letter) - ord("A") + 1
    return index - 1


def _cell_text(cell: ET.Element, shared: list[str]) -> str | None:
    """A cell's text: a shared or inline string, or a number's stored value; None if blank."""
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{_MAIN}t"))
    value = cell.find(f"{_MAIN}v")
    if value is None or value.text is None:
        return None
    if kind != "s":
        return value.text
    index = int(value.text)
    if not 0 <= index < len(shared):
        raise ValueError(f"cell {cell.get('r')} names shared string {index} of {len(shared)}")
    return shared[index]
