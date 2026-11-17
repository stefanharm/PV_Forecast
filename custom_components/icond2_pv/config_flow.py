"""Einrichtung und Optionen über die Benutzeroberfläche."""
from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector, EntitySelector, EntitySelectorConfig, NumberSelector, NumberSelectorConfig,
    NumberSelectorMode, SelectOptionDict, SelectSelector, SelectSelectorConfig, TextSelector,
)

from .const import (
    CONF_ALBEDO, CONF_AZIMUTH, CONF_CALIBRATION, CONF_CALIBRATION_DAYS, CONF_CALIBRATION_ENTITY,
    CONF_DAMPING_EVENING, CONF_DAMPING_MORNING, CONF_EFFICIENCY, CONF_GLOBAL_FACTOR, CONF_HORIZON,
    CONF_INVERTER_LIMIT, CONF_KWP, CONF_LATITUDE, CONF_LONGITUDE, CONF_STRING_ID, CONF_STRING_NAME,
    CONF_STRINGS, CONF_SYSTEM_LOSS, CONF_TEMP_COEFF, CONF_TILT, DEFAULT_ALBEDO,
    DEFAULT_CALIBRATION_DAYS, DEFAULT_DAMPING, DEFAULT_EFFICIENCY, DEFAULT_GLOBAL_FACTOR,
    DEFAULT_INVERTER_LIMIT, DEFAULT_SYSTEM_LOSS, DEFAULT_TEMP_COEFF, DOMAIN,
)
from .pvmodel import parse_horizon


def _num(min_v: float, max_v: float, step: float | str, unit: str | None = None) -> NumberSelector:
    cfg: dict[str, Any] = {"min": min_v, "max": max_v, "step": step, "mode": NumberSelectorMode.BOX}
    if unit:
        cfg["unit_of_measurement"] = unit
    return NumberSelector(NumberSelectorConfig(**cfg))


def _string_schema(d: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_STRING_NAME, default=d.get(CONF_STRING_NAME, "")): TextSelector(),
        vol.Required(CONF_KWP, default=d.get(CONF_KWP, 5.0)): _num(0.01, 1000, 0.01, "kWp"),
        vol.Required(CONF_TILT, default=d.get(CONF_TILT, 30)): _num(0, 90, 1, "°"),
        vol.Required(CONF_AZIMUTH, default=d.get(CONF_AZIMUTH, 180)): _num(0, 360, 1, "°"),
        vol.Optional(CONF_HORIZON, default=d.get(CONF_HORIZON, "")): TextSelector(),
        vol.Required(CONF_EFFICIENCY, default=d.get(CONF_EFFICIENCY, DEFAULT_EFFICIENCY)): _num(10, 150, 0.5, "%"),
        vol.Required(CONF_DAMPING_MORNING, default=d.get(CONF_DAMPING_MORNING, DEFAULT_DAMPING)): _num(0, 100, 1, "%"),
        vol.Required(CONF_DAMPING_EVENING, default=d.get(CONF_DAMPING_EVENING, DEFAULT_DAMPING)): _num(0, 100, 1, "%"),
        vol.Required(CONF_TEMP_COEFF, default=d.get(CONF_TEMP_COEFF, DEFAULT_TEMP_COEFF)): _num(-1, 0, 0.01, "%/K"),
        vol.Required(CONF_ALBEDO, default=d.get(CONF_ALBEDO, DEFAULT_ALBEDO)): _num(0, 1, 0.01),
    })


def _validate_string(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not str(user_input.get(CONF_STRING_NAME, "")).strip():
        errors[CONF_STRING_NAME] = "name_required"
    try:
        parse_horizon(user_input.get(CONF_HORIZON, ""))
    except ValueError:
        errors[CONF_HORIZON] = "invalid_horizon"
    return errors


def _settings_schema(d: dict[str, Any]) -> dict:
    return {
        vol.Required(CONF_SYSTEM_LOSS, default=d.get(CONF_SYSTEM_LOSS, DEFAULT_SYSTEM_LOSS)): _num(0, 50, 0.5, "%"),
        vol.Required(CONF_INVERTER_LIMIT, default=d.get(CONF_INVERTER_LIMIT, DEFAULT_INVERTER_LIMIT)): _num(0, 1000, 0.1, "kW"),
    }


class PVForecastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Erstinstallation: Standort, Anlage, erster String."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._options: dict[str, Any] = {}
        self._title = "PV-Prognose"

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PVForecastOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._title = user_input[CONF_NAME]
            self._data = {CONF_LATITUDE: user_input[CONF_LATITUDE], CONF_LONGITUDE: user_input[CONF_LONGITUDE]}
            self._options = {
                CONF_SYSTEM_LOSS: user_input[CONF_SYSTEM_LOSS],
                CONF_INVERTER_LIMIT: user_input[CONF_INVERTER_LIMIT],
                CONF_GLOBAL_FACTOR: DEFAULT_GLOBAL_FACTOR,
                CONF_CALIBRATION: False,
                CONF_CALIBRATION_DAYS: DEFAULT_CALIBRATION_DAYS,
                CONF_STRINGS: [],
            }
            return await self.async_step_string()

        schema = vol.Schema({
            vol.Required(CONF_NAME, default="PV-Prognose"): TextSelector(),
            vol.Required(CONF_LATITUDE, default=round(self.hass.config.latitude, 4)): _num(-90, 90, "any", "°"),
            vol.Required(CONF_LONGITUDE, default=round(self.hass.config.longitude, 4)): _num(-180, 180, "any", "°"),
            **_settings_schema({}),
        })
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_string(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_string(user_input)
            if not errors:
                self._options[CONF_STRINGS].append({CONF_STRING_ID: uuid.uuid4().hex[:8], **user_input})
                return await self.async_step_more()
        default = {CONF_STRING_NAME: f"String {len(self._options[CONF_STRINGS]) + 1}"}
        return self.async_show_form(step_id="string", data_schema=_string_schema(user_input or default),
                                    errors=errors)

    async def async_step_more(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="more", menu_options=["string", "finish"])

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_create_entry(title=self._title, data=self._data, options=self._options)


class PVForecastOptionsFlow(OptionsFlow):
    """Nachträgliche Änderungen: Einstellungen, Kalibrierung, Strings."""

    def __init__(self) -> None:
        self._edit_id: str | None = None

    @property
    def _opts(self) -> dict[str, Any]:
        return dict(self.config_entry.options)

    def _strings(self) -> list[dict[str, Any]]:
        return [dict(s) for s in self.config_entry.options.get(CONF_STRINGS, [])]

    def _string_choice(self) -> SelectSelector:
        return SelectSelector(SelectSelectorConfig(options=[
            SelectOptionDict(value=s[CONF_STRING_ID], label=f"{s[CONF_STRING_NAME]} ({s[CONF_KWP]} kWp)")
            for s in self._strings()]))

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["settings", "add_string"]
        if self._strings():
            menu += ["edit_string", "remove_string"]
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_CALIBRATION) and not user_input.get(CONF_CALIBRATION_ENTITY):
                errors[CONF_CALIBRATION_ENTITY] = "calibration_entity_required"
            else:
                opts = self._opts
                opts.update(user_input)
                if not user_input.get(CONF_CALIBRATION_ENTITY):
                    opts.pop(CONF_CALIBRATION_ENTITY, None)
                return self.async_create_entry(data=opts)
        d = {**self._opts, **(user_input or {})}
        entity_key = (vol.Optional(CONF_CALIBRATION_ENTITY, default=d[CONF_CALIBRATION_ENTITY])
                      if d.get(CONF_CALIBRATION_ENTITY) else vol.Optional(CONF_CALIBRATION_ENTITY))
        schema = vol.Schema({
            **_settings_schema(d),
            vol.Required(CONF_GLOBAL_FACTOR, default=d.get(CONF_GLOBAL_FACTOR, DEFAULT_GLOBAL_FACTOR)): _num(10, 200, 0.5, "%"),
            vol.Required(CONF_CALIBRATION, default=d.get(CONF_CALIBRATION, False)): BooleanSelector(),
            entity_key: EntitySelector(EntitySelectorConfig(domain="sensor", device_class="energy")),
            vol.Required(CONF_CALIBRATION_DAYS, default=d.get(CONF_CALIBRATION_DAYS, DEFAULT_CALIBRATION_DAYS)): _num(3, 60, 1, "Tage"),
        })
        return self.async_show_form(step_id="settings", data_schema=schema, errors=errors)

    async def async_step_add_string(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_string(user_input)
            if not errors:
                opts = self._opts
                opts[CONF_STRINGS] = [*self._strings(), {CONF_STRING_ID: uuid.uuid4().hex[:8], **user_input}]
                return self.async_create_entry(data=opts)
        default = {CONF_STRING_NAME: f"String {len(self._strings()) + 1}"}
        return self.async_show_form(step_id="add_string", data_schema=_string_schema(user_input or default),
                                    errors=errors)

    async def async_step_edit_string(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._edit_id = user_input[CONF_STRING_ID]
            return await self.async_step_edit_string_values()
        return self.async_show_form(step_id="edit_string", data_schema=vol.Schema(
            {vol.Required(CONF_STRING_ID): self._string_choice()}))

    async def async_step_edit_string_values(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = next(s for s in self._strings() if s[CONF_STRING_ID] == self._edit_id)
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_string(user_input)
            if not errors:
                opts = self._opts
                opts[CONF_STRINGS] = [
                    {CONF_STRING_ID: s[CONF_STRING_ID], **user_input} if s[CONF_STRING_ID] == self._edit_id else s
                    for s in self._strings()]
                return self.async_create_entry(data=opts)
        return self.async_show_form(step_id="edit_string_values",
                                    data_schema=_string_schema(user_input or current), errors=errors,
                                    description_placeholders={"name": current[CONF_STRING_NAME]})

    async def async_step_remove_string(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            opts = self._opts
            opts[CONF_STRINGS] = [s for s in self._strings() if s[CONF_STRING_ID] != user_input[CONF_STRING_ID]]
            return self.async_create_entry(data=opts)
        return self.async_show_form(step_id="remove_string", data_schema=vol.Schema(
            {vol.Required(CONF_STRING_ID): self._string_choice()}))
