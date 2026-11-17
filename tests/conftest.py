import math
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.icond2_pv import pvmodel

pytest_plugins = "pytest_homeassistant_custom_component"


async def prep(hass):
    hass.config.language = "de"
    await hass.config.async_set_time_zone("Europe/Vienna")


def fake_api(lat=48.21, lon=16.37, cloud=0.0, drop_m15=False):
    """Open-Meteo-Antwort: gestern 00 UTC + 3 Tage, ab übermorgen null (wie ICON-D2)."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=1)
    horizon_end = today + timedelta(days=2, hours=6)

    def rad(t_end, minutes):
        mid = t_end - timedelta(minutes=minutes / 2)
        elev, _ = pvmodel.sun_position(mid, lat, lon)
        if t_end > horizon_end:
            return None, None, None
        s = max(math.sin(math.radians(elev)), 0)
        dni = 850 * s ** 0.3 * (1 - cloud) if s > 0 else 0
        dhi = (100 + 150 * cloud) * s
        ghi = dni * s + dhi
        return round(ghi, 1), round(dni, 1), round(dhi, 1)

    m15 = {"time": [], "shortwave_radiation": [], "direct_normal_irradiance": [], "diffuse_radiation": []}
    t = start + timedelta(minutes=15)
    while t <= start + timedelta(days=4):
        g, b, d = rad(t, 15)
        m15["time"].append(t.strftime("%Y-%m-%dT%H:%M"))
        m15["shortwave_radiation"].append(g)
        m15["direct_normal_irradiance"].append(b)
        m15["diffuse_radiation"].append(d)
        t += timedelta(minutes=15)
    hourly = {"time": [], "temperature_2m": [], "wind_speed_10m": [], "shortwave_radiation": [],
              "direct_normal_irradiance": [], "diffuse_radiation": []}
    t = start
    while t < start + timedelta(days=4):
        g, b, d = rad(t, 60)
        hourly["time"].append(t.strftime("%Y-%m-%dT%H:%M"))
        hourly["temperature_2m"].append(None if g is None else 5 + 5 * math.sin((t.hour - 9) / 24 * 2 * math.pi))
        hourly["wind_speed_10m"].append(None if g is None else 2.0)
        hourly["shortwave_radiation"].append(g)
        hourly["direct_normal_irradiance"].append(b)
        hourly["diffuse_radiation"].append(d)
        t += timedelta(hours=1)
    out = {"latitude": lat, "longitude": lon, "hourly": hourly}
    if not drop_m15:
        out["minutely_15"] = m15
    return out
