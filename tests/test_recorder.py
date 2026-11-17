from datetime import timedelta

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.icond2_pv.const import API_URL, DOMAIN

from .conftest import fake_api, prep


async def test_actual_energy_from_statistics(recorder_mock, enable_custom_integrations, hass, aioclient_mock):
    """Ist-Energie gestern aus Langzeitstatistik (Wh-Zähler) korrekt als kWh lesen."""
    await prep(hass)
    aioclient_mock.get(API_URL, json=fake_api())
    yesterday = dt_util.now().date() - timedelta(days=1)
    start = dt_util.start_of_local_day(yesterday)
    meta = StatisticMetaData(has_mean=False, has_sum=True, name="PV", source="recorder",
                             statistic_id="sensor.pv_total", unit_of_measurement="Wh",
                             mean_type=0, unit_class="energy")
    rows, total = [], 1_000_000.0
    for h in range(-2, 26):
        t = start + timedelta(hours=h)
        add = 1500.0 if 8 <= h < 16 and h >= 0 and h < 24 else 0.0  # 8 h x 1,5 kWh = 12 kWh
        total += add
        rows.append(StatisticData(start=t, state=total, sum=total))
    async_import_statistics(hass, meta, rows)
    await async_wait_recording_done(hass)

    entry = MockConfigEntry(domain=DOMAIN, title="PV", data={"latitude": 48.21, "longitude": 16.37},
                            options={"system_loss": 14, "inverter_limit": 0, "global_factor": 100,
                                     "calibration": True, "calibration_entity": "sensor.pv_total",
                                     "calibration_days": 14,
                                     "strings": [{"id": "a", "name": "Süd", "kwp": 10, "tilt": 30,
                                                  "azimuth": 180, "horizon": ""}]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    c = entry.runtime_data
    kwh = await c._async_actual_energy(start, dt_util.start_of_local_day(dt_util.now().date()))
    print("Ist gestern:", kwh, "Ratios:", c.cal_ratios, "Faktor:", c.settings.calibration_factor)
    assert abs(kwh - 12.0) < 0.01
    if dt_util.now().hour >= 1:
        assert yesterday.isoformat() in c.cal_ratios
