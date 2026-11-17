"""Konstanten für die ICON-D2 PV-Prognose."""
from datetime import timedelta

DOMAIN = "icond2_pv"
NAME = "ICON-D2 PV-Prognose"

API_URL = "https://api.open-meteo.com/v1/dwd-icon"
MODEL = "icon_d2"
UPDATE_INTERVAL = timedelta(minutes=30)
ATTRIBUTION = "Wetterdaten: DWD ICON-D2 über Open-Meteo (CC BY 4.0)"

# Anlage (config entry data / options)
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_SYSTEM_LOSS = "system_loss"            # %
CONF_INVERTER_LIMIT = "inverter_limit"      # kW, 0 = kein Limit
CONF_GLOBAL_FACTOR = "global_factor"        # %
CONF_CALIBRATION = "calibration"            # bool
CONF_CALIBRATION_ENTITY = "calibration_entity"
CONF_CALIBRATION_DAYS = "calibration_days"
CONF_STRINGS = "strings"

# String
CONF_STRING_ID = "id"
CONF_STRING_NAME = "name"
CONF_KWP = "kwp"
CONF_TILT = "tilt"
CONF_AZIMUTH = "azimuth"
CONF_HORIZON = "horizon"
CONF_EFFICIENCY = "efficiency"              # %
CONF_DAMPING_MORNING = "damping_morning"    # %
CONF_DAMPING_EVENING = "damping_evening"    # %
CONF_TEMP_COEFF = "temp_coeff"              # %/K
CONF_ALBEDO = "albedo"

DEFAULT_SYSTEM_LOSS = 14.0
DEFAULT_INVERTER_LIMIT = 0.0
DEFAULT_GLOBAL_FACTOR = 100.0
DEFAULT_CALIBRATION_DAYS = 14
DEFAULT_EFFICIENCY = 100.0
DEFAULT_DAMPING = 0.0
DEFAULT_TEMP_COEFF = -0.35
DEFAULT_ALBEDO = 0.2

# Kalibrierung
CAL_MIN_FORECAST_KWH_PER_KWP = 0.3   # sehr trübe Tage werden zum Lernen ignoriert
CAL_RATIO_MIN = 0.5
CAL_RATIO_MAX = 1.5
STORAGE_VERSION = 1
