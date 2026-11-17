"""Reines PV-Rechenmodell ohne Home-Assistant-Abhängigkeiten.

Ablauf je Zeitintervall und String:
  Sonnenstand (NOAA) -> Einstrahlung auf die Modulebene (Hay-Davies, transponiert)
  -> Horizontverschattung (Direktanteil + Himmelssichtfaktor für Diffusanteil)
  -> Zelltemperatur (Faiman) -> DC-Leistung -> Feinjustierung -> Systemverluste
  -> Wechselrichter-Limit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

SOLAR_CONSTANT = 1361.0  # W/m²


# ---------------------------------------------------------------------------
# Sonnenstand (NOAA, Genauigkeit < 0,1° im relevanten Zeitraum)
# ---------------------------------------------------------------------------
def sun_position(when: datetime, lat: float, lon: float) -> tuple[float, float]:
    """Sonnenhöhe und -azimut in Grad (Azimut: 0 = Nord, 90 = Ost, 180 = Süd)."""
    when = when.astimezone(timezone.utc)
    jd = (when - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400.0 + 2451545.0
    jc = (jd - 2451545.0) / 36525.0

    geom_mean_long = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    geom_mean_anom = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    ecc = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    m = math.radians(geom_mean_anom)
    eq_ctr = (math.sin(m) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
              + math.sin(2 * m) * (0.019993 - 0.000101 * jc) + math.sin(3 * m) * 0.000289)
    true_long = geom_mean_long + eq_ctr
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    mean_obliq = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    obliq = mean_obliq + 0.00256 * math.cos(math.radians(omega))
    decl = math.degrees(math.asin(math.sin(math.radians(obliq)) * math.sin(math.radians(app_long))))

    y = math.tan(math.radians(obliq / 2)) ** 2
    l0 = math.radians(geom_mean_long)
    eq_time = 4 * math.degrees(
        y * math.sin(2 * l0) - 2 * ecc * math.sin(m) + 4 * ecc * y * math.sin(m) * math.cos(2 * l0)
        - 0.5 * y * y * math.sin(4 * l0) - 1.25 * ecc * ecc * math.sin(2 * m))

    minutes = when.hour * 60 + when.minute + when.second / 60
    true_solar_time = (minutes + eq_time + 4 * lon) % 1440
    hour_angle = true_solar_time / 4 - 180

    lat_r, decl_r, ha_r = math.radians(lat), math.radians(decl), math.radians(hour_angle)
    cos_zen = math.sin(lat_r) * math.sin(decl_r) + math.cos(lat_r) * math.cos(decl_r) * math.cos(ha_r)
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zen = math.degrees(math.acos(cos_zen))
    elevation = 90 - zen

    # Atmosphärische Refraktion (NOAA)
    if elevation > 85:
        refr = 0.0
    elif elevation > 5:
        te = math.tan(math.radians(elevation))
        refr = 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5
    elif elevation > -0.575:
        refr = 1735 + elevation * (-518.2 + elevation * (103.4 + elevation * (-12.79 + elevation * 0.711)))
    else:
        refr = -20.772 / math.tan(math.radians(elevation))
    elevation += refr / 3600

    sin_zen = math.sin(math.radians(zen))
    if sin_zen < 1e-9:
        azimuth = 180.0
    else:
        cos_az = (math.sin(lat_r) * cos_zen - math.sin(decl_r)) / (math.cos(lat_r) * sin_zen)
        cos_az = max(-1.0, min(1.0, cos_az))
        az = math.degrees(math.acos(cos_az))
        azimuth = (az + 180) % 360 if hour_angle > 0 else (540 - az) % 360
    return elevation, azimuth


# ---------------------------------------------------------------------------
# Horizontprofil
# ---------------------------------------------------------------------------
def parse_horizon(text: str | None) -> list[tuple[float, float]]:
    """'90:5, 135:25, 180:10' -> [(90, 5), (135, 25), (180, 10)].

    Leerer Text = freier Horizont. Wirft ValueError bei ungültiger Eingabe.
    """
    points: list[tuple[float, float]] = []
    if not text or not text.strip():
        return points
    for part in text.replace(';', ',').split(','):
        part = part.strip()
        if not part:
            continue
        if ':' not in part:
            raise ValueError(part)
        az_s, el_s = part.split(':', 1)
        az, el = float(az_s.replace(' ', '')), float(el_s.replace(' ', ''))
        if not 0 <= az <= 360 or not 0 <= el < 90:
            raise ValueError(part)
        points.append((az % 360, el))
    points.sort()
    return points


def horizon_elevation(points: list[tuple[float, float]], azimuth: float) -> float:
    """Horizonthöhe in Grad bei gegebenem Azimut (lineare Interpolation, umlaufend)."""
    if not points:
        return 0.0
    if len(points) == 1:
        return points[0][1]
    az = azimuth % 360
    ext = [(points[-1][0] - 360, points[-1][1]), *points, (points[0][0] + 360, points[0][1])]
    for (a0, e0), (a1, e1) in zip(ext, ext[1:]):
        if a0 <= az <= a1:
            return e0 if a1 == a0 else e0 + (e1 - e0) * (az - a0) / (a1 - a0)
    return 0.0


def sky_view_factor(points: list[tuple[float, float]]) -> float:
    """Anteil des isotropen Himmelsdiffuslichts, der nicht vom Horizont verdeckt wird."""
    if not points:
        return 1.0
    blocked = sum(math.sin(math.radians(horizon_elevation(points, a + 0.5))) ** 2 for a in range(360)) / 360
    return 1.0 - blocked


# ---------------------------------------------------------------------------
# Strings und Einstellungen
# ---------------------------------------------------------------------------
@dataclass
class PVString:
    """Ein PV-String bzw. eine Modulfläche mit einheitlicher Ausrichtung."""

    id: str
    name: str
    kwp: float
    tilt: float                      # Neigung: 0 = waagrecht, 90 = senkrecht
    azimuth: float                   # 0 = Nord, 90 = Ost, 180 = Süd, 270 = West
    horizon: list[tuple[float, float]] = field(default_factory=list)
    efficiency: float = 1.0          # Feinjustierung des Strings (1.0 = 100 %)
    damping_morning: float = 0.0     # 0..1, wirkt bei tiefer Sonne vor Mittag
    damping_evening: float = 0.0     # 0..1, wirkt bei tiefer Sonne nach Mittag
    temp_coeff: float = -0.0035      # Leistungs-Temperaturkoeffizient pro Kelvin
    albedo: float = 0.2              # Bodenreflexion
    _svf: float | None = field(default=None, repr=False)

    @property
    def svf(self) -> float:
        if self._svf is None:
            self._svf = sky_view_factor(self.horizon)
        return self._svf


@dataclass
class Settings:
    """Anlagenweite Einstellungen."""

    lat: float
    lon: float
    system_loss: float = 0.14         # Kabel, Wechselrichter, Verschmutzung, Mismatch
    inverter_limit_w: float = 0.0     # 0 = kein Limit
    global_factor: float = 1.0        # manuelle Feinjustierung der Gesamtanlage
    calibration_factor: float = 1.0   # automatisch gelernter Faktor


@dataclass
class Interval:
    """Ergebnis eines Zeitintervalls. Leistungen in W (AC), Mittelwert über das Intervall."""

    start: datetime
    end: datetime
    power: float
    strings: dict[str, float]

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    @property
    def energy_wh(self) -> float:
        return self.power * self.hours


# ---------------------------------------------------------------------------
# Physik
# ---------------------------------------------------------------------------
def poa_irradiance(s: PVString, elev: float, sun_az: float, ghi: float, dni: float, dhi: float,
                   doy: int) -> float:
    """Einstrahlung auf die Modulebene (W/m²) nach Hay-Davies inkl. Horizontverschattung."""
    if ghi <= 0 and dni <= 0 and dhi <= 0:
        return 0.0
    tilt_r = math.radians(s.tilt)
    zen_r = math.radians(90 - max(elev, -5))
    cos_zen = math.cos(zen_r)
    cos_aoi = (cos_zen * math.cos(tilt_r)
               + math.sin(zen_r) * math.sin(tilt_r) * math.cos(math.radians(sun_az - s.azimuth)))

    sun_visible = elev > 0 and elev > horizon_elevation(s.horizon, sun_az)
    beam = dni * max(cos_aoi, 0.0) if sun_visible else 0.0

    # Hay-Davies: zirkumsolarer Anteil folgt der Direktstrahlung, Rest isotrop
    e0 = SOLAR_CONSTANT * (1 + 0.033 * math.cos(2 * math.pi * doy / 365))
    ai = min(max(dni / e0, 0.0), 1.0) if elev > 0 else 0.0
    rb = max(cos_aoi, 0.0) / max(cos_zen, 0.0872)  # cos(85°) als Untergrenze
    circum = dhi * ai * rb if sun_visible else 0.0
    iso = dhi * (1 - ai) * (1 + math.cos(tilt_r)) / 2 * s.svf

    ground = ghi * s.albedo * (1 - math.cos(tilt_r)) / 2
    return max(beam + circum + iso + ground, 0.0)


def string_dc_power(s: PVString, poa: float, temp_air: float, wind_ms: float, elev: float,
                    sun_az: float) -> float:
    """DC-Leistung eines Strings in W."""
    if poa <= 0:
        return 0.0
    # Faiman-Modell für die Zelltemperatur
    t_cell = temp_air + poa / (25.0 + 6.84 * max(wind_ms, 0.0))
    p = s.kwp * 1000 * poa / 1000 * (1 + s.temp_coeff * (t_cell - 25))
    p *= s.efficiency
    # Dämpfung bei tiefer Sonne (voll am Horizont, keine ab 30° Sonnenhöhe)
    weight = max(0.0, 1 - max(elev, 0) / 30)
    damping = s.damping_morning if sun_az < 180 else s.damping_evening
    p *= 1 - damping * weight
    return max(p, 0.0)


def compute(times_end: list[datetime], minutes: int, ghi: list, dni: list, dhi: list,
            temp: list, wind_ms: list, strings: list[PVString], st: Settings) -> list[Interval]:
    """Prognose für alle Intervalle. times_end = Intervallende (Open-Meteo-Konvention)."""
    out: list[Interval] = []
    step = timedelta(minutes=minutes)
    total_factor = st.global_factor * st.calibration_factor * (1 - st.system_loss)
    for i, end in enumerate(times_end):
        g, b, d = ghi[i], dni[i], dhi[i]
        if g is None or b is None or d is None:
            continue
        ta = temp[i] if temp[i] is not None else 10.0
        ws = wind_ms[i] if wind_ms[i] is not None else 1.0
        start = end - step
        mid = start + step / 2
        elev, az = sun_position(mid, st.lat, st.lon)
        doy = mid.timetuple().tm_yday
        per: dict[str, float] = {}
        for s in strings:
            poa = poa_irradiance(s, elev, az, g, b, d, doy)
            per[s.id] = string_dc_power(s, poa, ta, ws, elev, az) * total_factor
        total = sum(per.values())
        if st.inverter_limit_w > 0 and total > st.inverter_limit_w:
            scale = st.inverter_limit_w / total
            per = {k: v * scale for k, v in per.items()}
            total = st.inverter_limit_w
        out.append(Interval(start, end, total, per))
    return out
