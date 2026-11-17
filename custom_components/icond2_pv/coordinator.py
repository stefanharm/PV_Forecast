"""Datenabruf, Berechnung und automatische Kalibrierung."""
from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import pvmodel
from .const import (
    API_URL, CAL_MIN_FORECAST_KWH_PER_KWP, CAL_RATIO_MAX, CAL_RATIO_MIN, CONF_ALBEDO, CONF_AZIMUTH,
    CONF_CALIBRATION, CONF_CALIBRATION_DAYS, CONF_CALIBRATION_ENTITY, CONF_DAMPING_EVENING,
    CONF_DAMPING_MORNING, CONF_EFFICIENCY, CONF_GLOBAL_FACTOR, CONF_HORIZON, CONF_INVERTER_LIMIT,
    CONF_KWP, CONF_LATITUDE, CONF_LONGITUDE, CONF_STRING_ID, CONF_STRING_NAME, CONF_STRINGS,
    CONF_SYSTEM_LOSS, CONF_TEMP_COEFF, CONF_TILT, DEFAULT_ALBEDO, DEFAULT_CALIBRATION_DAYS,
    DEFAULT_DAMPING, DEFAULT_EFFICIENCY, DEFAULT_GLOBAL_FACTOR, DEFAULT_INVERTER_LIMIT,
    DEFAULT_SYSTEM_LOSS, DEFAULT_TEMP_COEFF, DOMAIN, MODEL, STORAGE_VERSION, UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

RAD = ("shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation")


def strings_from_options(options: dict[str, Any]) -> list[pvmodel.PVString]:
    """Konfigurierte Strings in Modellobjekte umwandeln."""
    result = []
    for s in options.get(CONF_STRINGS, []):
        result.append(pvmodel.PVString(
            id=s[CONF_STRING_ID],
            name=s[CONF_STRING_NAME],
            kwp=float(s[CONF_KWP]),
            tilt=float(s[CONF_TILT]),
            azimuth=float(s[CONF_AZIMUTH]),
            horizon=pvmodel.parse_horizon(s.get(CONF_HORIZON, "")),
            efficiency=float(s.get(CONF_EFFICIENCY, DEFAULT_EFFICIENCY)) / 100,
            damping_morning=float(s.get(CONF_DAMPING_MORNING, DEFAULT_DAMPING)) / 100,
            damping_evening=float(s.get(CONF_DAMPING_EVENING, DEFAULT_DAMPING)) / 100,
            temp_coeff=float(s.get(CONF_TEMP_COEFF, DEFAULT_TEMP_COEFF)) / 100,
            albedo=float(s.get(CONF_ALBEDO, DEFAULT_ALBEDO)),
        ))
    return result


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _interp(times: list[datetime], values: list, when: datetime, default: float) -> float:
    """Lineare Interpolation einer Stundenreihe (Momentanwerte) auf einen Zeitpunkt."""
    best = None
    for i in range(len(times) - 1):
        t0, t1 = times[i], times[i + 1]
        if t0 <= when <= t1:
            v0, v1 = values[i], values[i + 1]
            if v0 is None or v1 is None:
                best = v0 if v0 is not None else v1
                break
            f = (when - t0).total_seconds() / (t1 - t0).total_seconds()
            return v0 + (v1 - v0) * f
    return default if best is None else best


class PVForecastCoordinator(DataUpdateCoordinator[list[pvmodel.Interval]]):
    """Holt ICON-D2-Strahlungsdaten und berechnet die PV-Prognose."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL, config_entry=entry)
        opts = entry.options
        self.strings = strings_from_options(opts)
        self.total_kwp = sum(s.kwp for s in self.strings)
        self.settings = pvmodel.Settings(
            lat=float(entry.data[CONF_LATITUDE]),
            lon=float(entry.data[CONF_LONGITUDE]),
            system_loss=float(opts.get(CONF_SYSTEM_LOSS, DEFAULT_SYSTEM_LOSS)) / 100,
            inverter_limit_w=float(opts.get(CONF_INVERTER_LIMIT, DEFAULT_INVERTER_LIMIT)) * 1000,
            global_factor=float(opts.get(CONF_GLOBAL_FACTOR, DEFAULT_GLOBAL_FACTOR)) / 100,
        )
        self.cal_enabled = bool(opts.get(CONF_CALIBRATION)) and bool(opts.get(CONF_CALIBRATION_ENTITY))
        self.cal_entity: str | None = opts.get(CONF_CALIBRATION_ENTITY)
        self.cal_days = int(opts.get(CONF_CALIBRATION_DAYS, DEFAULT_CALIBRATION_DAYS))
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.cal_ratios: dict[str, float | None] = {}
        self._raw: dict[str, Any] | None = None
        self.last_model_update: datetime | None = None

    # ------------------------------------------------------------------ Speicher
    async def async_load_calibration(self) -> None:
        data = await self._store.async_load() or {}
        self.cal_ratios = data.get("ratios", {})
        self._apply_calibration_factor()

    async def _async_save_calibration(self) -> None:
        await self._store.async_save({"ratios": self.cal_ratios})

    def _apply_calibration_factor(self) -> None:
        if not self.cal_enabled:
            self.settings.calibration_factor = 1.0
            return
        used = self.calibration_used()
        self.settings.calibration_factor = statistics.median(used.values()) if used else 1.0

    def calibration_used(self) -> dict[str, float]:
        """Verhältnisse Ist/Prognose der letzten N Tage, die in den Faktor eingehen."""
        cutoff = (dt_util.now().date() - timedelta(days=self.cal_days)).isoformat()
        return {d: r for d, r in sorted(self.cal_ratios.items()) if r is not None and d >= cutoff}

    async def async_reset_calibration(self) -> None:
        self.cal_ratios = {}
        await self._async_save_calibration()
        self._apply_calibration_factor()
        await self.async_refresh()

    # ------------------------------------------------------------------ Abruf
    async def _async_fetch(self) -> dict[str, Any]:
        params = {
            "latitude": self.settings.lat,
            "longitude": self.settings.lon,
            "models": MODEL,
            "minutely_15": ",".join(RAD),
            "hourly": ",".join(("temperature_2m", "wind_speed_10m", *RAD)),
            "past_days": 1,
            "forecast_days": 3,
            "timezone": "GMT",
            "wind_speed_unit": "ms",
        }
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(API_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    raise UpdateFailed(f"Open-Meteo antwortet mit HTTP {resp.status}: {await resp.text()}")
                return await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Open-Meteo nicht erreichbar: {err}") from err

    def _calculate(self, raw: dict[str, Any], calibration_factor: float | None = None) -> list[pvmodel.Interval]:
        st = self.settings
        if calibration_factor is not None:
            st = pvmodel.Settings(**{**st.__dict__, "calibration_factor": calibration_factor})
        hourly = raw.get("hourly") or {}
        h_times = [_parse_time(t) for t in hourly.get("time", [])]
        h_temp = hourly.get("temperature_2m", [None] * len(h_times))
        h_wind = hourly.get("wind_speed_10m", [None] * len(h_times))

        def weather(ts: list[datetime]) -> tuple[list[float], list[float]]:
            return ([_interp(h_times, h_temp, t, 10.0) for t in ts],
                    [_interp(h_times, h_wind, t, 1.0) for t in ts])

        intervals: list[pvmodel.Interval] = []
        m15 = raw.get("minutely_15") or {}
        if m15.get("time") and all(m15.get(k) for k in RAD):
            ts = [_parse_time(t) for t in m15["time"]]
            temp, wind = weather([t - timedelta(minutes=7, seconds=30) for t in ts])
            intervals = pvmodel.compute(ts, 15, m15.get(RAD[0]), m15.get(RAD[1]), m15.get(RAD[2]),
                                        temp, wind, self.strings, st)
        # Lücken (z. B. ohne 15-Minuten-Daten) mit Stundenwerten auffüllen
        if h_times and all(hourly.get(k) for k in RAD):
            covered = {iv.end for iv in intervals}
            temp, wind = weather([t - timedelta(minutes=30) for t in h_times])
            hour_iv = pvmodel.compute(h_times, 60, hourly.get(RAD[0]), hourly.get(RAD[1]),
                                      hourly.get(RAD[2]), temp, wind, self.strings, st)
            for iv in hour_iv:
                quarter_ends = {iv.start + timedelta(minutes=15 * k) for k in range(1, 5)}
                if not quarter_ends & covered:
                    intervals.append(iv)
        intervals.sort(key=lambda iv: iv.start)
        return intervals

    async def _async_update_data(self) -> list[pvmodel.Interval]:
        raw = await self._async_fetch()
        self._raw = raw
        self.last_model_update = dt_util.utcnow()
        if self.cal_enabled:
            try:
                await self._async_learn_yesterday(raw)
            except Exception:  # Kalibrierung darf die Prognose nie blockieren
                _LOGGER.exception("Kalibrierung fehlgeschlagen")
        intervals = self._calculate(raw)
        if not intervals:
            raise UpdateFailed("Keine verwertbaren Strahlungsdaten von Open-Meteo erhalten")
        return intervals

    # ------------------------------------------------------------------ Kalibrierung
    async def _async_learn_yesterday(self, raw: dict[str, Any]) -> None:
        now = dt_util.now()
        if now.hour < 1:  # Statistik des Vortags ist erst nach Mitternacht vollständig
            return
        day = now.date() - timedelta(days=1)
        key = day.isoformat()
        if key in self.cal_ratios:
            return
        start = dt_util.start_of_local_day(day)
        end = dt_util.start_of_local_day(now.date())

        uncal = self._calculate(raw, calibration_factor=1.0)
        forecast_kwh = energy_between(uncal, start, end) / 1000
        actual_kwh = await self._async_actual_energy(start, end)
        if actual_kwh is None:
            return
        if forecast_kwh < CAL_MIN_FORECAST_KWH_PER_KWP * max(self.total_kwp, 0.1):
            self.cal_ratios[key] = None  # zu trüb, nicht aussagekräftig
            _LOGGER.debug("Kalibrierung %s übersprungen (Prognose %.2f kWh)", key, forecast_kwh)
        else:
            ratio = min(max(actual_kwh / forecast_kwh, CAL_RATIO_MIN), CAL_RATIO_MAX)
            self.cal_ratios[key] = round(ratio, 4)
            _LOGGER.debug("Kalibrierung %s: Ist %.2f kWh / Prognose %.2f kWh = %.3f",
                          key, actual_kwh, forecast_kwh, ratio)
        cutoff = (now.date() - timedelta(days=max(self.cal_days, 60))).isoformat()
        self.cal_ratios = {d: r for d, r in self.cal_ratios.items() if d >= cutoff}
        await self._async_save_calibration()
        self._apply_calibration_factor()

    async def _async_actual_energy(self, start: datetime, end: datetime) -> float | None:
        """Ist-Erzeugung in kWh aus der Langzeitstatistik der Kalibrier-Entität."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, start, end, {self.cal_entity}, "hour",
            {"energy": UnitOfEnergy.KILO_WATT_HOUR}, {"change"},
        )
        rows = stats.get(self.cal_entity, [])
        if not rows:
            _LOGGER.warning("Keine Statistik für %s gefunden. Die Entität braucht state_class "
                            "total_increasing oder total und die Einheit Wh/kWh.", self.cal_entity)
            return None
        return sum(r.get("change") or 0.0 for r in rows)


# ---------------------------------------------------------------------- Auswertung
def energy_between(intervals: list[pvmodel.Interval], start: datetime, end: datetime,
                   string_id: str | None = None) -> float:
    """Energie in Wh zwischen start und end (anteilig für angeschnittene Intervalle)."""
    total = 0.0
    for iv in intervals:
        overlap = (min(iv.end, end) - max(iv.start, start)).total_seconds()
        if overlap <= 0:
            continue
        p = iv.power if string_id is None else iv.strings.get(string_id, 0.0)
        total += p * overlap / 3600
    return total


def power_at(intervals: list[pvmodel.Interval], when: datetime, string_id: str | None = None) -> float | None:
    for iv in intervals:
        if iv.start <= when < iv.end:
            return iv.power if string_id is None else iv.strings.get(string_id, 0.0)
    return None


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = dt_util.start_of_local_day(day)
    return start, dt_util.start_of_local_day(day + timedelta(days=1))


def day_intervals(intervals: list[pvmodel.Interval], day: date) -> list[pvmodel.Interval]:
    start, end = day_bounds(day)
    return [iv for iv in intervals if iv.start >= start and iv.end <= end]


def day_complete(intervals: list[pvmodel.Interval], day: date) -> bool:
    """True, wenn die Daten den Tag bis Sonnenuntergang abdecken (letztes Intervall nach 20 Uhr)."""
    _, end = day_bounds(day)
    return any(iv.end >= end - timedelta(hours=4) for iv in intervals)
