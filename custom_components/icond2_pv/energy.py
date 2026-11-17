"""Prognose für das Energie-Dashboard von Home Assistant."""
from __future__ import annotations

from collections import defaultdict

from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_solar_forecast(hass: HomeAssistant, config_entry_id: str) -> dict | None:
    """Stündliche Prognose in Wh, Schlüssel = Stundenbeginn (ISO, UTC)."""
    entry = hass.config_entries.async_get_entry(config_entry_id)
    if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
        return None
    coordinator = entry.runtime_data
    if not coordinator.data:
        return None
    wh_hours: dict[str, float] = defaultdict(float)
    for iv in coordinator.data:
        hour = iv.start.replace(minute=0, second=0, microsecond=0)
        wh_hours[hour.isoformat()] += iv.energy_wh
    return {"wh_hours": {k: round(v, 1) for k, v in sorted(wh_hours.items())}}
