"""Sensoren der ICON-D2 PV-Prognose."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import PVConfigEntry
from .const import ATTRIBUTION, DOMAIN, NAME
from .coordinator import (
    PVForecastCoordinator, day_bounds, day_complete, day_intervals, energy_between, power_at,
)


def _forecast_attr(c: PVForecastCoordinator, offset: int, sid: str | None) -> list[dict[str, Any]]:
    day = dt_util.now().date() + timedelta(days=offset)
    return [
        {"datetime": dt_util.as_local(iv.start).isoformat(),
         "power": round(iv.power if sid is None else iv.strings.get(sid, 0.0))}
        for iv in day_intervals(c.data, day)
    ]


def _day_energy(c: PVForecastCoordinator, offset: int, sid: str | None) -> float | None:
    day = dt_util.now().date() + timedelta(days=offset)
    if not day_complete(c.data, day):
        return None
    start, end = day_bounds(day)
    return round(energy_between(c.data, start, end, sid) / 1000, 2)


def _remaining_today(c: PVForecastCoordinator, sid: str | None) -> float | None:
    _, end = day_bounds(dt_util.now().date())
    return round(energy_between(c.data, dt_util.utcnow(), end, sid) / 1000, 2)


def _next_hour(c: PVForecastCoordinator, sid: str | None) -> float:
    now = dt_util.utcnow()
    return round(energy_between(c.data, now, now + timedelta(hours=1), sid) / 1000, 2)


def _power_now(c: PVForecastCoordinator, sid: str | None) -> float | None:
    p = power_at(c.data, dt_util.utcnow(), sid)
    return None if p is None else round(p)


def _peak(c: PVForecastCoordinator, offset: int) -> tuple[float | None, str | None]:
    ivs = day_intervals(c.data, dt_util.now().date() + timedelta(days=offset))
    if not ivs:
        return None, None
    best = max(ivs, key=lambda iv: iv.power)
    return round(best.power), dt_util.as_local(best.start).isoformat()


@dataclass(frozen=True, kw_only=True)
class PVSensorDescription(SensorEntityDescription):
    value_fn: Callable[[PVForecastCoordinator, str | None], Any]
    attr_fn: Callable[[PVForecastCoordinator, str | None], dict[str, Any]] | None = None


ENERGY = {"device_class": SensorDeviceClass.ENERGY,
          "native_unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR, "suggested_display_precision": 1}
POWER = {"device_class": SensorDeviceClass.POWER, "native_unit_of_measurement": UnitOfPower.WATT,
         "suggested_display_precision": 0}

SYSTEM_SENSORS: tuple[PVSensorDescription, ...] = (
    PVSensorDescription(key="power_now", translation_key="power_now", **POWER,
                        state_class=SensorStateClass.MEASUREMENT, value_fn=_power_now),
    PVSensorDescription(key="energy_today", translation_key="energy_today", **ENERGY,
                        value_fn=lambda c, s: _day_energy(c, 0, s),
                        attr_fn=lambda c, s: {"forecast": _forecast_attr(c, 0, s)}),
    PVSensorDescription(key="energy_today_remaining", translation_key="energy_today_remaining", **ENERGY,
                        value_fn=_remaining_today),
    PVSensorDescription(key="energy_tomorrow", translation_key="energy_tomorrow", **ENERGY,
                        value_fn=lambda c, s: _day_energy(c, 1, s),
                        attr_fn=lambda c, s: {"forecast": _forecast_attr(c, 1, s)}),
    PVSensorDescription(key="energy_next_hour", translation_key="energy_next_hour", **ENERGY,
                        value_fn=_next_hour),
    PVSensorDescription(key="peak_today", translation_key="peak_today", **POWER,
                        value_fn=lambda c, s: _peak(c, 0)[0],
                        attr_fn=lambda c, s: {"time": _peak(c, 0)[1]}),
    PVSensorDescription(key="peak_tomorrow", translation_key="peak_tomorrow", **POWER,
                        value_fn=lambda c, s: _peak(c, 1)[0],
                        attr_fn=lambda c, s: {"time": _peak(c, 1)[1]}),
    PVSensorDescription(
        key="calibration_factor", translation_key="calibration_factor",
        native_unit_of_measurement=PERCENTAGE, suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c, s: round(c.settings.calibration_factor * 100, 1),
        attr_fn=lambda c, s: {
            "enabled": c.cal_enabled,
            "source_entity": c.cal_entity,
            "days_used": len(c.calibration_used()),
            "ratios": {d: round(r * 100, 1) for d, r in c.calibration_used().items()},
        }),
)

STRING_SENSORS: tuple[PVSensorDescription, ...] = (
    PVSensorDescription(key="power_now", translation_key="string_power_now", **POWER,
                        state_class=SensorStateClass.MEASUREMENT, value_fn=_power_now),
    PVSensorDescription(key="energy_today", translation_key="string_energy_today", **ENERGY,
                        value_fn=lambda c, s: _day_energy(c, 0, s),
                        attr_fn=lambda c, s: {"forecast": _forecast_attr(c, 0, s)}),
    PVSensorDescription(key="energy_tomorrow", translation_key="string_energy_tomorrow", **ENERGY,
                        value_fn=lambda c, s: _day_energy(c, 1, s),
                        attr_fn=lambda c, s: {"forecast": _forecast_attr(c, 1, s)}),
)


async def async_setup_entry(hass: HomeAssistant, entry: PVConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    c = entry.runtime_data
    entities: list[PVSensor] = [PVSensor(c, d, None) for d in SYSTEM_SENSORS]
    for s in c.strings:
        entities += [PVSensor(c, d, s.id, s.name) for d in STRING_SENSORS]
    async_add_entities(entities)


class PVSensor(CoordinatorEntity[PVForecastCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: PVSensorDescription

    def __init__(self, coordinator: PVForecastCoordinator, description: PVSensorDescription,
                 string_id: str | None, string_name: str | None = None) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._sid = string_id
        entry = coordinator.config_entry
        if string_id is None:
            self._attr_unique_id = f"{entry.entry_id}_{description.key}"
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
                manufacturer="DWD ICON-D2 / Open-Meteo", model=NAME,
                entry_type=DeviceEntryType.SERVICE)
        else:
            self._attr_unique_id = f"{entry.entry_id}_string_{string_id}_{description.key}"
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{entry.entry_id}_{string_id}")},
                name=f"{entry.title} {string_name}", manufacturer="DWD ICON-D2 / Open-Meteo",
                model="PV-String", via_device=(DOMAIN, entry.entry_id),
                entry_type=DeviceEntryType.SERVICE)

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator, self._sid)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.coordinator.data or self.entity_description.attr_fn is None:
            return None
        return self.entity_description.attr_fn(self.coordinator, self._sid)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Leistung "jetzt" ändert sich alle 15 Minuten, auch ohne neuen Abruf
        if self.entity_description.key in ("power_now", "energy_today_remaining", "energy_next_hour"):
            self.async_on_remove(async_track_time_interval(
                self.hass, self._async_tick, timedelta(minutes=5)))

    @callback
    def _async_tick(self, _now: Any) -> None:
        self.async_write_ha_state()
