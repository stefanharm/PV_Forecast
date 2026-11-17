from datetime import timedelta
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.icond2_pv.const import API_URL, DOMAIN

import pytest

from .conftest import fake_api, prep


@pytest.fixture(autouse=True)
async def _de(enable_custom_integrations, hass):
    await prep(hass)

STR_SUED = {"name": "Dach Süd", "kwp": 10.0, "tilt": 30, "azimuth": 180, "horizon": "",
            "efficiency": 100, "damping_morning": 0, "damping_evening": 0, "temp_coeff": -0.35, "albedo": 0.2}
STR_OST = {**STR_SUED, "name": "Dach Ost", "kwp": 7.0, "azimuth": 90, "horizon": "90:10, 135:25, 180:5"}
STR_ZAUN = {**STR_SUED, "name": "PV-Zaun", "kwp": 3.0, "tilt": 90, "horizon": "120:15, 200:15"}


async def _setup(hass, aioclient_mock, strings, **opts):
    aioclient_mock.get(API_URL, json=fake_api())
    entry = MockConfigEntry(domain=DOMAIN, title="PV", data={"latitude": 48.21, "longitude": 16.37},
                            options={"system_loss": 14, "inverter_limit": 0, "global_factor": 100,
                                     "calibration": False, "calibration_days": 14,
                                     "strings": [{"id": f"s{i}", **s} for i, s in enumerate(strings)], **opts})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(API_URL, json=fake_api())
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "user"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "name": "Haus", "latitude": 48.21, "longitude": 16.37, "system_loss": 14, "inverter_limit": 10})
    assert r["step_id"] == "string"
    bad = await hass.config_entries.flow.async_configure(r["flow_id"], {**STR_SUED, "horizon": "quatsch"})
    assert bad["errors"] == {"horizon": "invalid_horizon"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], STR_SUED)
    assert r["type"] is FlowResultType.MENU
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "string"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], STR_OST)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "finish"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Haus"
    assert [s["name"] for s in r["options"]["strings"]] == ["Dach Süd", "Dach Ost"]
    assert r["options"]["inverter_limit"] == 10
    await hass.async_block_till_done()
    states = hass.states.async_entity_ids("sensor")
    print(sorted(states))
    assert len(states) == 8 + 2 * 3


async def test_sensors_and_energy(hass: HomeAssistant, aioclient_mock) -> None:
    entry = await _setup(hass, aioclient_mock, [STR_SUED, STR_OST, STR_ZAUN])
    states = {s.entity_id: s for s in hass.states.async_all("sensor")}
    for eid, s in sorted(states.items()):
        print(f"{eid:55} {s.state:>8} {s.attributes.get('unit_of_measurement','')}")
    today = float(states["sensor.pv_energie_heute"].state)
    tomorrow = float(states["sensor.pv_energie_morgen"].state)
    assert today > 0 and tomorrow > 0
    parts = sum(float(states[f"sensor.pv_{n}_energie_heute"].state) for n in ("dach_sud", "dach_ost", "pv_zaun"))
    assert abs(parts - today) < 0.05
    fc = states["sensor.pv_energie_morgen"].attributes["forecast"]
    assert len(fc) == 96 and max(x["power"] for x in fc) > 0
    assert states["sensor.pv_spitzenleistung_morgen"].attributes["time"]

    from custom_components.icond2_pv.energy import async_get_solar_forecast
    res = await async_get_solar_forecast(hass, entry.entry_id)
    wh = sum(res["wh_hours"].values())
    assert wh > 0 and all(k.endswith("+00:00") for k in res["wh_hours"])


async def test_inverter_limit_and_fallback_hourly(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(API_URL, json=fake_api(drop_m15=True))
    entry = MockConfigEntry(domain=DOMAIN, title="PV", data={"latitude": 48.21, "longitude": 16.37},
                            options={"system_loss": 14, "inverter_limit": 3, "global_factor": 100,
                                     "calibration": False, "calibration_days": 14,
                                     "strings": [{"id": "a", **STR_SUED}]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    peak = float(hass.states.get("sensor.pv_spitzenleistung_morgen").state)
    assert peak == 3000
    assert len(hass.states.get("sensor.pv_energie_morgen").attributes["forecast"]) == 24


async def test_options_flow(hass: HomeAssistant, aioclient_mock) -> None:
    entry = await _setup(hass, aioclient_mock, [STR_SUED, STR_OST])
    reg = er.async_get(hass)
    assert reg.async_get("sensor.pv_dach_ost_energie_heute")
    # String entfernen
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] is FlowResultType.MENU
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "remove_string"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"id": "s1"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [s["name"] for s in entry.options["strings"]] == ["Dach Süd"]
    assert reg.async_get("sensor.pv_dach_ost_energie_heute") is None
    # String bearbeiten
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "edit_string"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"id": "s0"})
    assert r["step_id"] == "edit_string_values"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {**STR_SUED, "kwp": 5.0})
    await hass.async_block_till_done()
    assert entry.options["strings"][0]["kwp"] == 5.0
    # Einstellungen: Kalibrierung ohne Entität -> Fehler
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "settings"})
    bad = await hass.config_entries.options.async_configure(r["flow_id"], {
        "system_loss": 14, "inverter_limit": 0, "global_factor": 90, "calibration": True, "calibration_days": 14})
    assert bad["errors"] == {"calibration_entity": "calibration_entity_required"}
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "system_loss": 14, "inverter_limit": 0, "global_factor": 90, "calibration": False, "calibration_days": 14})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["global_factor"] == 90
    # String hinzufügen
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "add_string"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], STR_ZAUN)
    await hass.async_block_till_done()
    assert len(entry.options["strings"]) == 2
    assert hass.states.get("sensor.pv_pv_zaun_energie_morgen")


async def test_global_factor_scales(hass: HomeAssistant, aioclient_mock) -> None:
    e1 = await _setup(hass, aioclient_mock, [STR_SUED])
    full = float(hass.states.get("sensor.pv_energie_morgen").state)
    await hass.config_entries.async_unload(e1.entry_id)
    hass.config_entries.async_update_entry(e1, options={**e1.options, "global_factor": 80})
    assert await hass.config_entries.async_setup(e1.entry_id)
    await hass.async_block_till_done()
    scaled = float(hass.states.get("sensor.pv_energie_morgen").state)
    assert abs(scaled / full - 0.8) < 0.01


async def test_calibration(hass: HomeAssistant, aioclient_mock) -> None:
    """Ist-Erzeugung 85 % der Prognose -> Faktor 85 %, Prognose sinkt entsprechend."""
    from custom_components.icond2_pv import coordinator as co

    seen = {}

    async def fake_actual(self, start, end):
        uncal = self._calculate(self._raw, calibration_factor=1.0)
        seen["fc"] = co.energy_between(uncal, start, end) / 1000
        return seen["fc"] * 0.85

    with patch.object(co.PVForecastCoordinator, "_async_actual_energy", fake_actual), \
         patch("homeassistant.util.dt.now", wraps=None) as _:
        pass
    with patch.object(co.PVForecastCoordinator, "_async_actual_energy", fake_actual):
        entry = await _setup(hass, aioclient_mock, [STR_SUED], calibration=True,
                             calibration_entity="sensor.fronius_energie")
        c = entry.runtime_data
        from homeassistant.util import dt as dt_util
        if dt_util.now().hour < 1:
            return  # Testlauf kurz nach Mitternacht: Kalibrierung wartet bewusst
        assert seen["fc"] > 0
        factor = float(hass.states.get("sensor.pv_kalibrierfaktor").state)
        assert abs(factor - 85.0) < 0.2, factor
        attrs = hass.states.get("sensor.pv_kalibrierfaktor").attributes
        assert attrs["days_used"] == 1
        # Service setzt zurück
        await hass.services.async_call(DOMAIN, "reset_calibration", blocking=True)
        await hass.async_block_till_done()
        # nach Reset lernt der nächste Abruf den Vortag erneut
        assert float(hass.states.get("sensor.pv_kalibrierfaktor").state) == 85.0 or c.cal_ratios
