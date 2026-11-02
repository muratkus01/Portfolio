"""regelleistung.net's aFRR capacity results: the I/O boundary of the aFRR model.

The TSOs publish one RESULT_OVERVIEW_CAPACITY_MARKET_aFRR file per year, an xlsx workbook with
one row per day and product. It is read here with the standard library, by column name, so no
dependency is added: pandas.read_excel would need openpyxl, which the project does not install.
Whatever the reader would otherwise have to guess at, it refuses.

The workbook comes from the web, so it is untrusted input. A download stops past
_MAX_DOWNLOAD_BYTES, its SHA-256 must be the pinned one before it is cached and again before
it is parsed, no zip member is unpacked beyond _MAX_MEMBER_BYTES, and ElementTree, on the
expat that ships with Python 3.11 and 3.12, resolves no external entities and bounds entity
expansion.

`load_capacity_results` caches the file under datakit's conventions without changing datakit,
and `capacity_prices` maps its products onto a window's 15-minute steps (`reserve`). Neither
the raw file nor block-level prices are ever committed.
"""
from __future__ import annotations

import datetime
import hashlib
import io
import math
import os
import re
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import NamedTuple
from urllib.request import urlopen

import numpy as np
import pandas as pd

# Imported for its side effect: hybrid.data puts the repository's datakit folder on sys.path.
# datakit itself is imported where it is used, as hybrid.data does.
from . import data as _data  # noqa: F401
from .reserve import CapacityPrices

RESULTS_URL = ("https://www.regelleistung.net/apps/crds/api/v2/tenders/files/"
               "RESULT_OVERVIEW_CAPACITY_MARKET_aFRR_{year}-01-01_{year}-12-31.xlsx")
# The yearly files as retrieved on 1 November 2026, byte-identical on a second download. A
# file whose bytes differ is refused: the TSOs revised it, so inspect it, then re-pin it in a
# commit of its own.
PINNED_SHA256 = {2024: "f74c4abaaeed69c408b13096736595eda01303d1ae698be0553cb3488a440830",
                 2025: "12e81c231c762c2f5580ccd6988b71703511ff97364641773a71c7fbc3e9ad1c"}
# The years whose aFRR rules the model was checked against. Every one of them is pinned above,
# so no unpinned year can load. Adding a year is a commit of its own, after re-checking the
# documents that `_refuse_unchecked_year` names.
RULE_YEARS = (2024, 2025)
# The rule documents the model was checked against, as run_meta records them for each
# experiment that reads a results file: (document and version, its date, the rule years it
# governs). The PQ conditions went from v1.04 to v1.05 during 2024. Both set alike what the
# model reads from them: a storage unit keeps for FRR at least 60 minutes of its marketed power
# and 15 minutes of its PQ power, and only the 60 minutes where its storage management secures
# four hours of delivery.
RULE_DOCUMENTS = (
    ("PQ conditions for FCR, aFRR and mFRR, v1.04", "3 June 2022", (2024,)),
    ("PQ conditions for FCR, aFRR and mFRR, v1.05", "5 July 2024", (2024, 2025)),
    ("MfRRA, reading version", "8 December 2022", (2024, 2025)),
    ("BNetzA BK6-15-158", "13 June 2017", (2024, 2025)),
    ("BNetzA BK6-23-333", "5 November 2024", (2024, 2025)),
)
GATE = datetime.time(9, 0)      # Berlin; bids for day D close at 09:00 on D-1, MfRRA § 20 (3)
AWARD = datetime.time(9, 30)    # Berlin; awards are known by 09:30 on D-1, MfRRA § 20 (3)(c)

# The yearly files are 0.33 to 0.38 MB.
_MAX_DOWNLOAD_BYTES = 20 * 2 ** 20
_LICENCE = "none; the regelleistung.net FAQ allows use without permission; not redistributed"

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


class CapacityResults(NamedTuple):
    """A year's aFRR results file: its table (`parse_results`), the URL it came from, and the
    SHA-256 of its bytes, which run_meta records."""
    table: pd.DataFrame
    url: str
    sha256: str


def load_capacity_results(year: int, cache_dir: Path | None = None) -> CapacityResults:
    """The yearly aFRR capacity results of `year`, from the cache or regelleistung.net.

    year: a year whose rules the model was checked against (RULE_YEARS), e.g. 2025.
    cache_dir: where the file is cached, by default datakit's gitignored cache. A cached file
        is never downloaded again, as datakit's are not.
    Returns the file's table, URL and SHA-256.
    Raises ValueError for a year outside RULE_YEARS, before any download; for a year
    regelleistung.net has no file for yet; for a download past _MAX_DOWNLOAD_BYTES; for a
    download or a cached file whose SHA-256 is not the pinned one; and for content that
    parse_results refuses.
    """
    _refuse_unchecked_year(year)
    import datakit

    cache_dir = datakit.DEFAULT_CACHE if cache_dir is None else Path(cache_dir)
    url = RESULTS_URL.format(year=year)
    tag = f"regelleistung_aFRR_capacity_results_{year}"
    path = cache_dir / f"{urllib.parse.quote(tag, safe='')}.xlsx"
    if not path.exists():
        try:
            _download(url, path, tag, PINNED_SHA256[year])
        except urllib.error.HTTPError as err:
            if err.code != 404:
                raise
            raise ValueError(f"regelleistung.net has no yearly results file for {year} at "
                             f"{url}; it publishes it on 2 January of the next year") from err
    content = path.read_bytes()
    sha256 = hashlib.sha256(content).hexdigest()
    if sha256 != PINNED_SHA256[year]:
        # _download caches only pinned bytes, so these changed in the cache since
        raise ValueError(f"{path} has SHA-256 {sha256}, not the pinned {PINNED_SHA256[year]}. "
                         "A download is cached only once it matches its pin, so the cached "
                         "file changed since: delete it, and the next load downloads it again")
    return CapacityResults(parse_results(content), url, sha256)


def rule_documents(year: int) -> list[tuple[str, str]]:
    """The rule documents the model was checked against for `year`, from RULE_DOCUMENTS.

    year: a rule year, e.g. 2024.
    Returns (document and version, its date) pairs in RULE_DOCUMENTS' order, e.g.
    [("PQ conditions for FCR, aFRR and mFRR, v1.05", "5 July 2024"), ...].
    Raises ValueError for a year outside RULE_YEARS, as `load_capacity_results` does.
    """
    _refuse_unchecked_year(year)
    return [(name, date) for name, date, years in RULE_DOCUMENTS if year in years]


def _refuse_unchecked_year(year: int) -> None:
    """Refuse a year whose aFRR rules the model was not checked against, and name what to
    re-check before RULE_YEARS may grow. No clock is read: results do not depend on today."""
    if year not in RULE_YEARS:
        raise ValueError(
            f"the aFRR rules were checked for {' and '.join(map(str, RULE_YEARS))} only, not "
            f"for {year}. Before adding a year to RULE_YEARS, re-check the PQ conditions (v1.06 "
            "of 7 October 2026 changes the energy band), the MfRRA and BK6-25-212 (15-minute "
            "products), the aFRR settlement model that regelleistung.net lists from 1 "
            "September 2026, and § 19 (3) EEG with the BNetzA's MiSpeL determination")


def _download(url: str, path: Path, tag: str, pinned: str) -> None:
    """Fetch `url` into `path` and record it in the cache's SOURCE.md, in datakit's format.

    Only bytes whose SHA-256 is `pinned` reach `path`. A cached file is never fetched again,
    so a bad download, such as a proxy's error page sent with HTTP 200 or a truncated body,
    would otherwise stay in the cache and be refused on every later load as if the TSOs had
    revised the file. Such bytes are kept as <path>.rejected for inspection instead. Pinned
    bytes reach `path` through <path>.partial and os.replace, so an interrupted download
    never leaves a cached file behind.
    url: the file's address (RESULTS_URL). path: where the file is cached. tag: its name in
    SOURCE.md, e.g. "regelleistung_aFRR_capacity_results_2025". pinned: its SHA-256.
    Raises ValueError for a response past _MAX_DOWNLOAD_BYTES or bytes that are not the
    pinned ones, and urllib.error.HTTPError and URLError as urlopen does.
    """
    import datakit

    path.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=datakit.TIMEOUT) as response:
        content = response.read(_MAX_DOWNLOAD_BYTES + 1)
    if len(content) > _MAX_DOWNLOAD_BYTES:
        raise ValueError(f"{url} sent more than {_MAX_DOWNLOAD_BYTES} bytes; refused")
    sha256 = hashlib.sha256(content).hexdigest()
    if sha256 != pinned:
        rejected = path.with_name(f"{path.name}.rejected")
        rejected.write_bytes(content)
        raise ValueError(f"{url} sent bytes with SHA-256 {sha256}, not the pinned {pinned}, so "
                         f"nothing was cached; they are kept as {rejected}. Either the TSOs "
                         "revised the file: inspect it, then re-pin it in a commit of its own. "
                         "Or the download is not the file, e.g. a proxy's error page: try again")
    partial = path.with_name(f"{path.name}.partial")
    partial.write_bytes(content)
    os.replace(partial, path)
    source = path.parent / "SOURCE.md"
    if not source.exists():
        source.write_text("# Data provenance\n\nEvery file in this directory, with its "
                          "origin.\n\n", encoding="utf-8")
    retrieved = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with source.open("a", encoding="utf-8") as fh:
        fh.write(f"- `{tag}` | {url} | retrieved {retrieved} | licence: {_LICENCE}\n")


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


# ------------------------------------------------------------------ onto the window
def capacity_prices(index: pd.DatetimeIndex, table: pd.DataFrame) -> CapacityPrices:
    """The aFRR products, B3's gates, awards and bids, and the prices on a window's steps.

    A step belongs to the product of its Berlin date and wall-clock hour. On the October day
    02:00-03:00 occurs twice and both fall in 00_04, which then holds 20 quarter-hours; on the
    March day that hour does not exist, and 00_04 holds 12. Never step // 16: after 30 March
    2025 that puts Berlin midnight 12 steps into a block.
    index: the window's tz-aware timestamps, 15 minutes apart, from Berlin midnight to Berlin
        midnight, e.g. a load_year frame's index.
    table: `parse_results` of the year the window lies in. load_window keeps a window inside
        its calendar year, so one yearly file covers it, and the first day's previous day is
        never needed, as nothing is bid for the first day.
    Returns the window's CapacityPrices, one value per step.
    Raises ValueError for a window `_berlin_steps` refuses, and when the table lacks a step's
    product.
    """
    local = _berlin_steps(index)
    dates = np.asarray(local.date)
    start_h = 4 * (np.asarray(local.hour, dtype=np.int64) // 4)
    ordinal = np.array([date.toordinal() for date in dates])
    day = ordinal - ordinal[0]
    clock = 60 * np.asarray(local.hour) + np.asarray(local.minute)
    next_day_in_window = day < day[-1]
    realised = _product_prices(table, dates, start_h)
    unpriced = realised.isna().any(axis=1).to_numpy()
    if unpriced.any():
        t = int(np.argmax(unpriced))
        raise ValueError(f"the aFRR results hold no product for {dates[t]}, "
                         f"{start_h[t]:02d}-{start_h[t] + 4:02d} h Berlin")
    # Matched by product, not by step offset: 00_04 lasts 3 or 5 hours on a daylight-saving day.
    yesterday = _product_prices(table, dates - datetime.timedelta(days=1), start_h)
    bid = np.where((day > 0)[:, None],
                   np.round(yesterday["average"][["up", "down"]].to_numpy(), 2), np.nan)
    return CapacityPrices(
        block=np.unique(6 * ordinal + start_h // 4, return_inverse=True)[1], day=day,
        gate=(clock == 60 * GATE.hour + GATE.minute) & next_day_in_window,
        award=(clock == 60 * AWARD.hour + AWARD.minute) & next_day_in_window,
        average_up=realised[("average", "up")].to_numpy(),
        average_down=realised[("average", "down")].to_numpy(),
        marginal_up=realised[("marginal", "up")].to_numpy(),
        marginal_down=realised[("marginal", "down")].to_numpy(),
        bid_up=bid[:, 0], bid_down=bid[:, 1])


def _berlin_steps(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The window's steps in Berlin time, once they are known to be steps capacity_prices can
    map: 15 minutes apart, so that the 09:00 gate and the 09:30 award fall on steps, and whole
    Berlin days from midnight to midnight. B3 bids at each gate for all of the next day, and
    for nothing on the first day, which no gate inside the window precedes. A window that
    starts later would be paid on "bid" on its second day, which B3 cannot bid for, and one
    that ends off midnight would bid at its last gate for part of a day.
    Raises ValueError otherwise, e.g. for 30 days of 24 hours across a daylight-saving change,
    which end at 01:00 or 23:00 Berlin.
    """
    if not ((index[1:] - index[:-1]) == pd.Timedelta(minutes=15)).all():
        raise ValueError("capacity_prices needs steps 15 minutes apart, so that the 09:00 "
                         "gate and the 09:30 award fall on steps")
    local = index.tz_convert("Europe/Berlin")
    end = local[-1] + pd.Timedelta(minutes=15)
    if local[0] != local[0].normalize() or end != end.normalize():
        raise ValueError(f"capacity_prices needs whole Berlin days, from midnight to midnight, "
                         f"as B3 bids for whole days; this window runs from {local[0]} to "
                         f"{end}. Days of 24 hours that span a daylight-saving change end "
                         "at 01:00 or 23:00 Berlin")
    return local


def _product_prices(table: pd.DataFrame, dates: np.ndarray,
                    start_h: np.ndarray) -> pd.DataFrame:
    """Per step, the average and the marginal of its product in each direction, as columns
    (basis, direction), e.g. ("average", "up"); NaN where `table` lacks the product."""
    wide = (table.set_index(["date", "start_h", "direction"])[["average", "marginal"]]
            .unstack("direction"))
    return wide.reindex(pd.MultiIndex.from_arrays([dates, start_h]))
