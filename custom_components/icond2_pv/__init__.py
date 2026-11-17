"""ICON-D2 PV-Prognose für Home Assistant."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er

from .const import CONF_STRING_ID, CONF_STRINGS, DOMAIN
from .coordinator import PVForecastCoordinator

PLATFORMS = [Platform.SENSOR]

type PVConfigEntry = ConfigEntry[PVForecastCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: PVConfigEntry) -> bool:
    coordinator = PVForecastCoordinator(hass, entry)
    await coordinator.async_load_calibration()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _remove_orphaned_string_entities(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    if not hass.services.has_service(DOMAIN, "reset_calibration"):
        async def _reset(call: ServiceCall) -> None:
            for e in hass.config_entries.async_loaded_entries(DOMAIN):
                await e.runtime_data.async_reset_calibration()

        hass.services.async_register(DOMAIN, "reset_calibration", _reset)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PVConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: PVConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_orphaned_string_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Entitäten gelöschter Strings aus der Registry entfernen."""
    ids = {s[CONF_STRING_ID] for s in entry.options.get(CONF_STRINGS, [])}
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        parts = ent.unique_id.split("_string_")
        if len(parts) == 2 and parts[1].split("_")[0] not in ids:
            registry.async_remove(ent.entity_id)
