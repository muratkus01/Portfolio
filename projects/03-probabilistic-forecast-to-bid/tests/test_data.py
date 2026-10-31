"""Reading the netztransparenz exports: German number format, time zones, overlaps."""
from __future__ import annotations

import pandas as pd
import pytest
from f2b.data import read_netztransparenz

HEADER = "Datum von;(Uhrzeit) von;Zeitzone;(Uhrzeit) bis;Zeitzone;ID AEP in EUR/MWh\n"


def write(path, rows: list[str], header: str = HEADER) -> None:
    path.write_text(header + "".join(r + "\n" for r in rows), encoding="utf-8-sig")


def test_reads_decimal_commas_thousands_points_and_utc(tmp_path):
    write(tmp_path / "a.csv", ["31.12.2023;23:00;UTC;23:15;UTC;5,19",
                               "31.12.2023;23:15;UTC;23:30;UTC;-2.345,60"])
    s = read_netztransparenz(tmp_path)["id_aep"]
    assert s.index[0] == pd.Timestamp("2023-12-31 23:00", tz="UTC")
    assert s.tolist() == pytest.approx([5.19, -2345.60])


def test_local_time_zones_become_utc(tmp_path):
    write(tmp_path / "a.csv", ["01.01.2025;00:00;MEZ;00:15;MEZ;10,0",
                               "01.07.2025;00:00;MESZ;00:15;MESZ;20,0"])
    s = read_netztransparenz(tmp_path)["id_aep"]
    assert list(s.index) == [pd.Timestamp("2024-12-31 23:00", tz="UTC"),
                             pd.Timestamp("2025-06-30 22:00", tz="UTC")]


def test_overlapping_files_keep_the_later_one(tmp_path):
    write(tmp_path / "a.csv", ["01.01.2025;00:00;UTC;00:15;UTC;1,0"])
    write(tmp_path / "b.csv", ["01.01.2025;00:00;UTC;00:15;UTC;2,0",
                               "01.01.2025;00:15;UTC;00:30;UTC;3,0"])
    s = read_netztransparenz(tmp_path)["id_aep"]
    assert s.tolist() == [2.0, 3.0]


def test_an_unknown_column_is_an_error_not_a_skip(tmp_path):
    write(tmp_path / "a.csv", ["01.01.2025;00:00;UTC;00:15;UTC;1,0"],
          header=HEADER.replace("ID AEP in EUR/MWh", "Something else"))
    with pytest.raises(ValueError, match="unknown value column"):
        read_netztransparenz(tmp_path)


def test_an_empty_folder_gives_none(tmp_path):
    assert read_netztransparenz(tmp_path) is None


REBAP_HEADER = "Datum;Zeitzone;von;bis;Einheit;reBAP unterdeckt;reBAP ueberdeckt\n"


def test_reads_the_rebap_layout_with_both_directions(tmp_path):
    write(tmp_path / "r.csv", ["31.05.2024;UTC;22:00;22:15;EUR/MWh;150,94;150,94",
                               "31.05.2024;UTC;22:15;22:30;EUR/MWh;1.234,50;99,00"],
          header=REBAP_HEADER)
    write(tmp_path / "a.csv", ["31.05.2024;22:00;UTC;22:15;UTC;140,00"])
    d = read_netztransparenz(tmp_path)
    assert d.loc[pd.Timestamp("2024-05-31 22:15", tz="UTC")].to_dict() == pytest.approx(
        {"rebap": 1234.50, "rebap_long": 99.00, "id_aep": float("nan")}, nan_ok=True)
    assert d["id_aep"].iloc[0] == pytest.approx(140.0)


def test_a_unit_other_than_eur_per_mwh_is_an_error(tmp_path):
    write(tmp_path / "r.csv", ["31.05.2024;UTC;22:00;22:15;ct/kWh;15,09;15,09"],
          header=REBAP_HEADER)
    with pytest.raises(ValueError, match="unexpected units"):
        read_netztransparenz(tmp_path)
