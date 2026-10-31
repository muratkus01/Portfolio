"""Per-state PV model: a fallback loss is visible to the caller, and the sites are the capitals.

No reference export, no calibration file and no network: the weather is a synthetic clear
day and the calibration path points into a temporary folder. The calibrated loss below is a
made-up value, not a fitted one.
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd
import pytest

from prosumer.data import pv_states


def _clear_june_day() -> pd.DataFrame:
    idx = pd.date_range("2024-06-21", periods=96, freq="15min", tz="UTC")
    hour = idx.hour + idx.minute / 60
    sun = np.clip(np.sin(np.pi * (hour - 4) / 16), 0.0, None)
    return pd.DataFrame({"ghi": 850 * sun, "dni": 700 * sun, "dhi": 120 * sun,
                         "temp_air": 22.0, "wind_speed": 7.2}, index=idx)


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """Synthetic weather instead of Open-Meteo, and a calibration file that is not there yet."""
    pytest.importorskip("pvlib")
    weather = _clear_june_day()
    monkeypatch.setattr(pv_states, "fetch_weather_actual", lambda *args, **kwargs: weather)
    calibration = tmp_path / "pv_state_calibration.json"
    monkeypatch.setattr(pv_states, "calibration_path", lambda: calibration)
    return calibration


def test_generate_warns_and_tags_the_series_when_it_falls_back(offline):
    """Regression: generate() dropped the 'fallback' tag, so 11 % passed as calibrated."""
    with pytest.warns(UserWarning, match="fallback"):
        out = pv_states.generate("BY", "2024-06-21", "2024-06-22")
    assert out.attrs.get("loss_source") == "fallback"
    assert out.attrs.get("system_loss") == pv_states.FALLBACK_SYSTEM_LOSS
    assert out.sum() > 0


def test_generate_tags_a_calibrated_loss_and_does_not_warn(offline):
    offline.write_text(json.dumps({"states": {"BY": {"system_loss": 0.2}}}), encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = pv_states.generate("BY", "2024-06-21", "2024-06-22")
    # pvlib raises unrelated pandas deprecation warnings, so look only for ours
    assert not [w for w in caught if "fallback" in str(w.message)]
    assert out.attrs.get("loss_source") == "calibrated"
    assert out.attrs.get("system_loss") == 0.2


def test_site_system_warns_when_it_substitutes_the_fallback_loss():
    """Regression: site_system() put in 11 % without a word when no loss was given."""
    with pytest.warns(UserWarning, match="fallback"):
        system = pv_states.site_system("BE")
    assert system.system_loss == pv_states.FALLBACK_SYSTEM_LOSS


def test_site_system_warning_names_the_missing_argument_not_the_file(offline):
    """Regression: site_system() blamed the calibration file, which it never reads.

    With BE calibrated in the file, a call without a loss still falls back, and the warning
    must say that no loss was passed, not that the file lacks one.
    """
    offline.write_text(json.dumps({"states": {"BE": {"system_loss": 0.2}}}), encoding="utf-8")
    with pytest.warns(pv_states.FallbackLossWarning) as caught:
        pv_states.site_system("BE")
    message = str(caught[0].message)
    assert "no system loss passed" in message
    assert "calibrat" not in message


def test_generate_warning_names_the_calibration_file(offline):
    with pytest.warns(pv_states.FallbackLossWarning, match="no calibrated system loss in"):
        pv_states.generate("BY", "2024-06-21", "2024-06-22")


def test_state_sites_are_the_capitals_of_the_fleet_table():
    """The comment on STATE_SITES says so; this keeps the two tables from drifting apart."""
    from prosumer.experiments.national_impact import _GEO

    sites = {code: (s.latitude, s.longitude) for code, s in pv_states.STATE_SITES.items()}
    assert sites == {code: (geo[2], geo[3]) for code, geo in _GEO.items()}


def test_site_altitude_barely_moves_the_yield():
    """The comment on STATE_SITES: altitude enters only the solar position.

    At the BY site on a clear June day, 520 m against sea level changes the yield by less
    than 0.01 %.
    """
    pytest.importorskip("pvlib")
    from prosumer.data.pv import PVSystem, pv_power

    weather, site = _clear_june_day(), pv_states.STATE_SITES["BY"]
    high, low = (pv_power(weather, PVSystem(kwp=1.0, latitude=site.latitude,
                                            longitude=site.longitude, altitude=altitude)).sum()
                 for altitude in (520.0, 0.0))
    assert low > 0
    assert abs(high / low - 1.0) < 1e-4
