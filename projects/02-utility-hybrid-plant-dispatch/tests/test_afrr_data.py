"""The aFRR data layer: regelleistung.net's yearly capacity results, read offline.

The workbooks are written in memory by tests/afrr_fixtures.py, with the real header names and
invented prices, so no test reads TSO data or the network. Every test here fails on a802748,
which has no aFRR data layer: nothing there reads the results file, maps its products onto a
window or pays a bid.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import io
import os
import re
import urllib.error
from pathlib import Path

import datakit
import numpy as np
import pandas as pd
import pytest

from hybrid import afrr_data
from hybrid.afrr_data import PRICE_COLUMNS, capacity_prices, parse_results
from hybrid.reserve import CapacityPrices, paid_prices


def _load_fixtures():
    """tests/afrr_fixtures.py, loaded by its path: the root pyproject.toml runs the tests in
    importlib mode, which puts no test folder on sys.path."""
    path = Path(__file__).with_name("afrr_fixtures.py")
    spec = importlib.util.spec_from_file_location("afrr_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _days(first: datetime.date, count: int) -> list[datetime.date]:
    """`count` consecutive days from `first`."""
    return [first + datetime.timedelta(days=k) for k in range(count)]


fixtures = _load_fixtures()
results_xlsx = fixtures.results_xlsx
DAYS = _days(datetime.date(2025, 1, 1), 3)


def _berlin_window(first: datetime.date, count: int) -> pd.DatetimeIndex:
    """The UTC quarter-hours of `count` Berlin days from `first`, as load_year's index holds
    them: a window from Berlin midnight."""
    start, end = (pd.Timestamp(day.isoformat(), tz="Europe/Berlin")
                  for day in (first, first + datetime.timedelta(days=count)))
    return pd.date_range(start, end, freq="15min", inclusive="left").tz_convert("UTC")


def _window(first: datetime.date, count: int) -> tuple[pd.DatetimeIndex, CapacityPrices]:
    """A window of `count` Berlin days from `first`, and its prices from a fixture file."""
    index = _berlin_window(first, count)
    return index, capacity_prices(index, parse_results(results_xlsx(_days(first, count))))


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


def test_parser_refuses_a_price_that_is_negative_or_not_finite():
    """A German price is a finite number of at least 0. A negative, NaN or infinite price, or
    text, is refused with its day and product, never read."""
    for column in PRICE_COLUMNS.values():
        for value, shown in ((-1.0, "is -1.0"), (float("nan"), "is nan"),
                             (float("inf"), "is inf"), ("n/a", "reads 'n/a'")):
            data = results_xlsx(DAYS, cells=[(DAYS[1], "NEG_08_12", column, value)])
            with pytest.raises(ValueError, match=rf"{re.escape(column)} {re.escape(shown)} on "
                                                 r"2025-01-02 for NEG_08_12"):
                parse_results(data)


def test_parser_refuses_a_date_that_is_not_a_whole_excel_day():
    """DATE_FROM is a whole Excel serial day. A fraction of a day, or a date written as text,
    is refused rather than truncated or guessed at."""
    serial = float((DAYS[1] - datetime.date(1899, 12, 30)).days)
    for value, text, refusal in ((serial + 0.5, repr(serial + 0.5), "is not a whole day"),
                                 ("2025-01-02", "2025-01-02", "is not an Excel serial day")):
        data = results_xlsx(DAYS, cells=[(DAYS[1], "POS_00_04", "DATE_FROM", value)])
        with pytest.raises(ValueError, match=rf"DATE_FROM '{re.escape(text)}' {refusal}"):
            parse_results(data)


def test_parser_sorts_its_table_by_day_direction_and_hours():
    """The table comes sorted by date, direction and start hour, whatever order the file's
    rows are in: here the days run backwards and, within a day, POS comes before NEG."""
    table = parse_results(results_xlsx(list(reversed(DAYS))))

    keys = list(zip(table["date"], table["direction"], table["start_h"]))
    assert keys == sorted(keys)
    pd.testing.assert_frame_equal(table, parse_results(results_xlsx(DAYS)))


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


def test_the_reader_refuses_a_shared_string_the_workbook_lacks():
    """A cell that names a shared string the workbook does not hold is refused. Read as a
    list index, -1 would quietly give the last string instead."""
    for index in (-1, 99):
        data = fixtures.rewrite_member(results_xlsx(DAYS), "xl/worksheets/sheet1.xml",
                                       '<c r="A1" t="s"><v>0</v></c>',
                                       f'<c r="A1" t="s"><v>{index}</v></c>')
        with pytest.raises(ValueError, match=f"cell A1 names shared string {index} of 16"):
            parse_results(data)


# ------------------------------------------------------------------ onto the window
def test_products_follow_berlin_clock_hours_across_daylight_saving():
    """A product spans its Berlin clock hours. On 30 March 2025 the 00_04 product has 12
    quarter-hours, on 26 October 2025 it has 20, and 04_08 starts at 04:00 Berlin: 02:00 UTC
    on the first day, 03:00 UTC on the second.

    Negative control: a mapper that cuts the window into blocks of 16 steps fails both
    checks on both days, so the checks tell a right mapping from a wrong one.
    """
    def checks(index, block, steps, first_04_08):
        # each window starts at Berlin midnight on the day before, so the day's 00_04 is
        # block 6 and its 04_08 block 7
        return bool(np.sum(block == 6) == steps), bool(index[block == 7][0] == first_04_08)

    for day, steps, first_04_08 in ((datetime.date(2025, 3, 30), 12, "2025-03-30 02:00"),
                                    (datetime.date(2025, 10, 26), 20, "2025-10-26 03:00")):
        index, prices = _window(day - datetime.timedelta(days=1), 3)
        first_04_08 = pd.Timestamp(first_04_08, tz="UTC")

        assert checks(index, prices.block, steps, first_04_08) == (True, True), day
        every_16 = np.arange(len(index)) // 16
        assert checks(index, every_16, steps, first_04_08) == (False, False), day


def test_the_gate_and_the_award_are_nine_and_half_past_nine_berlin():
    """B3 bids at 09:00 Berlin on D-1 and learns its award at 09:30 (MfRRA § 20 (3)): at 08:00
    and 08:30 UTC in January, 07:00 and 07:30 UTC in July. The window's last day has neither,
    as its next day lies outside the window. The gate before the 23-hour 30 March 2025 bids
    for 92 steps, the one before the 25-hour 26 October 2025 for 100."""
    for first, gate_utc in ((datetime.date(2025, 1, 14), "08:00"),
                            (datetime.date(2025, 7, 14), "07:00")):
        index, prices = _window(first, 3)
        gates = [pd.Timestamp(f"{day} {gate_utc}", tz="UTC") for day in _days(first, 2)]

        assert list(index[prices.gate]) == gates
        assert list(index[prices.award]) == [gate + pd.Timedelta(minutes=30) for gate in gates]
    for first, steps in ((datetime.date(2025, 3, 29), 92), (datetime.date(2025, 10, 25), 100)):
        _, prices = _window(first, 3)
        bids_for = prices.day[prices.gate][0] + 1
        assert np.sum(prices.day == bids_for) == steps, first


def test_the_bid_is_the_same_product_on_the_previous_day():
    """B3 bids each product at its German average of the previous Berlin day, to whole cents
    (MfRRA § 4 (3)(h)), matched by product and not by step offset: on 26 October 2025 the
    00_04 product lasts 5 hours, and each of its 20 steps bids the 00_04 of the day before.
    On the window's first day, which has no gate inside the window, there is no bid."""
    days = _days(datetime.date(2025, 10, 25), 3)
    table = parse_results(results_xlsx(days))

    def product(day, direction, start_h):
        return ((table["date"] == day) & (table["direction"] == direction)
                & (table["start_h"] == start_h))

    table.loc[product(days[0], "up", 0), "average"] = 12.347
    table.loc[product(days[1], "down", 20), "average"] = 7.231
    prices = capacity_prices(_berlin_window(days[0], 3), table)

    first_of_day_1 = prices.block == prices.block[prices.day == 1][0]
    last_of_day_2 = prices.block == prices.block[prices.day == 2][-1]
    assert prices.bid_up[first_of_day_1].tolist() == [12.35] * 20
    assert prices.bid_down[last_of_day_2].tolist() == [7.23] * 16
    assert np.isnan(prices.bid_up[prices.day == 0]).all()
    assert np.isnan(prices.bid_down[prices.day == 0]).all()
    assert np.isfinite(prices.bid_up[prices.day > 0]).all()


def test_a_bid_is_paid_only_where_it_was_at_or_below_the_marginal():
    """Pay-as-bid: a bid at or below the realised marginal, a tie included, is accepted and
    paid the bid; a bid above it, and the first day, which has no bid, are paid nothing. The
    "average" and "marginal" bases pay the realised values."""
    prices = CapacityPrices(
        block=np.arange(4), day=np.array([0, 1, 1, 1]), gate=np.zeros(4, dtype=bool),
        award=np.zeros(4, dtype=bool),
        average_up=np.array([5.0, 6.0, 7.0, 8.0]), average_down=np.array([1.0, 2.0, 3.0, 4.0]),
        marginal_up=np.array([9.0, 10.0, 10.0, 10.0]), marginal_down=np.full(4, 5.0),
        bid_up=np.array([np.nan, 9.5, 10.0, 10.5]), bid_down=np.array([np.nan, 6.0, 4.0, 5.0]))

    up, down = paid_prices(prices, "bid")

    assert up.tolist() == [0.0, 9.5, 10.0, 0.0]
    assert down.tolist() == [0.0, 0.0, 4.0, 5.0]
    assert [a.tolist() for a in paid_prices(prices, "average")] == [[5.0, 6.0, 7.0, 8.0],
                                                                    [1.0, 2.0, 3.0, 4.0]]
    assert [a.tolist() for a in paid_prices(prices, "marginal")] == [[9.0, 10.0, 10.0, 10.0],
                                                                     [5.0] * 4]
    with pytest.raises(ValueError, match="unknown aFRR price basis 'minimum'"):
        paid_prices(prices, "minimum")


def test_capacity_prices_refuses_what_it_cannot_map():
    """A step whose product the table lacks is refused, naming the day and the hours, rather
    than paid nothing; so is an index whose steps are not 15 minutes apart, on which the
    09:00 gate and the 09:30 award may not fall."""
    index = _berlin_window(DAYS[0], 3)
    with pytest.raises(ValueError, match="no product for 2025-01-03, 00-04 h Berlin"):
        capacity_prices(index, parse_results(results_xlsx(DAYS[:2])))
    with pytest.raises(ValueError, match="15 minutes apart"):
        capacity_prices(index[::4], parse_results(results_xlsx(DAYS)))


# ------------------------------------------------------------------ the yearly file
def _cache_file(cache_dir: Path, year: int) -> Path:
    """Where load_capacity_results caches the file of `year`."""
    return cache_dir / f"regelleistung_aFRR_capacity_results_{year}.xlsx"


def _never_download(url, timeout):
    """urlopen for tests that must not download."""
    raise AssertionError(f"downloaded {url}")


def test_a_pinned_file_whose_bytes_changed_is_refused(tmp_path, monkeypatch):
    """A cached file whose SHA-256 is not the pinned one changed in the cache, as only a
    download that matches its pin is cached. It is refused, naming both hashes, to be
    deleted and so fetched again."""
    monkeypatch.setattr(afrr_data, "urlopen", _never_download)
    content = results_xlsx(DAYS)
    _cache_file(tmp_path, 2025).write_bytes(content)

    with pytest.raises(ValueError, match=f"SHA-256 {hashlib.sha256(content).hexdigest()}, not "
                                         f"the pinned {afrr_data.PINNED_SHA256[2025]}.*"
                                         "delete it"):
        afrr_data.load_capacity_results(2025, cache_dir=tmp_path)


def test_a_download_that_does_not_match_its_pin_is_not_cached(tmp_path, monkeypatch):
    """Only pinned bytes reach the cache, which is never fetched again. A download with
    another SHA-256, such as a proxy's error page sent with HTTP 200, a truncated body or a
    file the TSOs revised, is refused naming both hashes and logged nowhere; its bytes are
    kept beside the cache for inspection, and the next load downloads again."""
    content = results_xlsx(DAYS)
    rejected = tmp_path / f"{_cache_file(tmp_path, 2025).name}.rejected"
    requests = []

    def serve(url, timeout):
        requests.append(url)
        return io.BytesIO(content)

    monkeypatch.setattr(afrr_data, "urlopen", serve)
    for attempt in (1, 2):
        with pytest.raises(ValueError, match=f"SHA-256 {hashlib.sha256(content).hexdigest()}, "
                                             f"not the pinned {afrr_data.PINNED_SHA256[2025]}"
                                             f".*kept as {re.escape(str(rejected))}"):
            afrr_data.load_capacity_results(2025, cache_dir=tmp_path)
        assert len(requests) == attempt
    assert [path.name for path in tmp_path.iterdir()] == [rejected.name]
    assert rejected.read_bytes() == content


def test_a_download_is_cached_with_a_source_line(tmp_path, monkeypatch):
    """A downloaded file is cached and never fetched again, as datakit's are not. It reaches
    the cache through <file>.partial and os.replace, so an interrupted download leaves no
    cached file. SOURCE.md gets one line per file in datakit's format, below the lines
    already there. Both years are loaded, with their pins moved to the fixture's bytes."""
    content = results_xlsx(DAYS)
    sha256 = hashlib.sha256(content).hexdigest()
    years = (2024, 2025)
    requests, replaced = [], []
    real_replace = os.replace

    def serve(url, timeout):
        requests.append((url, timeout))
        return io.BytesIO(content)

    def replace(source, target):
        replaced.append((Path(source).name, Path(target).name))
        real_replace(source, target)

    monkeypatch.setattr(afrr_data, "urlopen", serve)
    monkeypatch.setattr(afrr_data.os, "replace", replace)
    for year in years:
        monkeypatch.setitem(afrr_data.PINNED_SHA256, year, sha256)
    first = [afrr_data.load_capacity_results(year, cache_dir=tmp_path) for year in years]
    second = afrr_data.load_capacity_results(2025, cache_dir=tmp_path)

    urls = [afrr_data.RESULTS_URL.format(year=year) for year in years]
    names = [_cache_file(tmp_path, year).name for year in years]
    assert requests == [(url, datakit.TIMEOUT) for url in urls]
    assert replaced == [(f"{name}.partial", name) for name in names]
    assert [result.url for result in first] == urls
    assert second.sha256 == sha256
    pd.testing.assert_frame_equal(second.table, parse_results(content))
    assert sorted(path.name for path in tmp_path.iterdir()) == ["SOURCE.md", *names]
    lines = (tmp_path / "SOURCE.md").read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["# Data provenance", ""]
    entries = [line for line in lines if line.startswith("- ")]
    assert len(entries) == 2
    for year, url, entry in zip(years, urls, entries):
        assert re.fullmatch(rf"- `regelleistung_aFRR_capacity_results_{year}` \| "
                            + re.escape(url) + r" \| retrieved \d{4}-\d\d-\d\dT\S+\+00:00 \| "
                            r"licence: none; the regelleistung\.net FAQ allows use without "
                            r"permission; not redistributed", entry)


def test_a_failed_download_other_than_404_is_raised_as_is(tmp_path, monkeypatch):
    """Only a 404 says that the file is not published yet. Any other HTTP error, or no
    network at all, is raised as urlopen raised it, never reworded as the publication rule,
    and nothing is cached."""
    url = afrr_data.RESULTS_URL.format(year=2025)
    for error in (urllib.error.HTTPError(url, 500, "Internal Server Error", None, None),
                  urllib.error.URLError("no network")):
        def fail(url, timeout, error=error):
            raise error

        monkeypatch.setattr(afrr_data, "urlopen", fail)
        with pytest.raises(type(error)) as raised:
            afrr_data.load_capacity_results(2025, cache_dir=tmp_path)
        assert raised.value is error
    assert list(tmp_path.iterdir()) == []


def test_the_cache_is_datakits_unless_another_is_named(tmp_path, monkeypatch):
    """Without cache_dir, the file is cached where datakit caches: datakit.DEFAULT_CACHE, as
    it reads when the load runs. It is moved to tmp_path here, and the pin to the fixture."""
    content = results_xlsx(DAYS)
    monkeypatch.setitem(afrr_data.PINNED_SHA256, 2025, hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(datakit, "DEFAULT_CACHE", tmp_path)
    monkeypatch.setattr(afrr_data, "urlopen", lambda url, timeout: io.BytesIO(content))

    afrr_data.load_capacity_results(2025)

    assert _cache_file(tmp_path, 2025).read_bytes() == content


def test_a_missing_yearly_file_is_refused_with_the_publication_rule(tmp_path, monkeypatch):
    """regelleistung.net answers 404 for a year whose file it has not published. The refusal
    states the rule, 2 January of the next year, and reads no clock to state it; nothing is
    cached."""
    class NoClock:
        def __getattr__(self, name):
            raise AssertionError(f"read the clock through datetime.{name}")

    def not_found(url, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    monkeypatch.setattr(afrr_data, "urlopen", not_found)
    monkeypatch.setattr(afrr_data, "datetime", NoClock())

    with pytest.raises(ValueError, match="no yearly results file for 2025 at .*; it publishes "
                                         "it on 2 January of the next year"):
        afrr_data.load_capacity_results(2025, cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_year_outside_the_checked_rules_is_refused(tmp_path, monkeypatch):
    """The aFRR rules were checked for 2024 and 2025 only. Another year is refused before any
    download, and the refusal names the documents to re-check first."""
    monkeypatch.setattr(afrr_data, "urlopen", _never_download)
    for year in (2023, 2026):
        with pytest.raises(ValueError, match=rf"checked for 2024 and 2025 only, not for {year}"
                                             r".*PQ conditions.*MfRRA and BK6-25-212.*"
                                             r"1 September 2026.*MiSpeL"):
            afrr_data.load_capacity_results(year, cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_an_oversized_download_is_refused_and_not_cached(tmp_path, monkeypatch):
    """The file comes from the web: a response past the cap is refused and nothing is cached.
    The cap is lowered here to below the fixture's size."""
    monkeypatch.setattr(afrr_data, "_MAX_DOWNLOAD_BYTES", 1000)
    monkeypatch.setattr(afrr_data, "urlopen", lambda url, timeout: io.BytesIO(results_xlsx(DAYS)))

    with pytest.raises(ValueError, match="sent more than 1000 bytes; refused"):
        afrr_data.load_capacity_results(2025, cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []
