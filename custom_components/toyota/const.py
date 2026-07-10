"""Constants for the Toyota Connected Services integration."""

from homeassistant.const import Platform

# PLATFORMS SUPPORTED
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.CLIMATE,
]

# INTEGRATION ATTRIBUTES
DOMAIN = "toyota"
NAME = "Toyota Connected Services"
ISSUES_URL = "https://github.com/pytoyoda/ha_toyota/issues"

# CONF
CONF_BRAND = "Brand"
CONF_BRAND_MAPPING = {"T": "Toyota", "L": "Lexus"}
CONF_METRIC_VALUES = "use_metric_values"
# When True, per-vehicle cached data is returned on transient coordinator
# failures (Toyota 429, connection timeouts, read timeouts) instead of
# flipping entities to unavailable. Off by default for backward compatibility.
CONF_RETAIN_ON_TRANSIENT_FAILURE = "retain_on_transient_failure"
DEFAULT_RETAIN_ON_TRANSIENT_FAILURE = False

# Smart status refresh strategy. POSTs /v1/global/remote/refresh-status to
# wake the car's modem before reading /status, mimicking the Toyota mobile
# app's two-stage protocol. Reduces stuck-stale lock/door state and 429s.
# Off = stop the automatic cadence; explicit refresh_vehicle_status service
# calls still go through (per HA polling-toggle convention). See
# rate-limit-remediation-plan.md Addendum 4.
CONF_ENABLE_STATUS_REFRESH = "enable_status_refresh"
DEFAULT_ENABLE_STATUS_REFRESH = True
# Set automatically when the gateway repeatedly rejects the POST (vehicle
# does not support refresh-status). Cleared by either: (a) a successful
# service-call POST proving the gateway works, or (b) the user toggling
# CONF_ENABLE_STATUS_REFRESH OFF then ON. Hidden in the UI.
CONF_AUTO_DISABLED_STATUS_REFRESH = "auto_disabled_status_refresh"
DEFAULT_AUTO_DISABLED_STATUS_REFRESH = False
CONF_IDLE_WAKE_HOURS = "idle_wake_hours"
# Hours between idle-wake POSTs. 0 disables the feature entirely (off by
# default); 1-72 = wake every N hours when the car has not moved. Combines
# what was previously a separate boolean toggle plus a sub-interval.
DEFAULT_IDLE_WAKE_HOURS = 0
CONF_FAILED_WAKE_THRESHOLD = "failed_wake_threshold"
DEFAULT_FAILED_WAKE_THRESHOLD = 3
CONF_MAX_CACHE_AGE_MINUTES = "max_cache_age_minutes"
DEFAULT_MAX_CACHE_AGE_MINUTES = 30
CONF_POLLING_INTERVAL_MINUTES = "polling_interval_minutes"
DEFAULT_POLLING_INTERVAL_MINUTES = 6
# How many wake POSTs to fire when a stop event is detected. Cycle-count based
# (one POST per cycle), independent of polling interval. 1 = single POST on
# the just-stopped cycle. 2 (default) = an additional POST on the next cycle,
# which typically catches state the user changes shortly after stopping
# (locking the doors, opening the trunk). Those post-park events trigger
# fresh modem reports; the followup POST's poll loop picks them up.
CONF_POST_COUNT_PER_STOP = "post_count_per_stop"
DEFAULT_POST_COUNT_PER_STOP = 2

# Recent-trips sensor: rolling cache of the most recent N trips per vehicle,
# fetched on-demand when the smart-strategy detects a stop event. Surfaces as
# `sensor.<alias>_recent_trips` with attributes.trips[] in the
# journey-viewer-card data contract shape. 0 (default) disables the feature
# entirely (no extra API calls). 1-20 enables auto-fetch + caches that many
# most-recent trips per VIN. The service `toyota.refresh_recent_trips` works
# regardless of this setting.
CONF_MAX_RECENT_TRIPS = "max_recent_trips"
DEFAULT_MAX_RECENT_TRIPS = 0
MAX_RECENT_TRIPS_LIMIT = 20

# DEFAULTS
DEFAULT_LOCALE = "en-gb"

# DATA COORDINATOR ATTRIBUTES
BUCKET = "bucket"
DATA = "data"
ENGINE = "engine"
FUEL_TYPE = "fuel"
HYBRID = "hybrid"
LAST_UPDATED = "last_updated"
VIN = "vin"
PERIODE_START = "periode_start"
STATISTICS = "statistics"
WARNING = "warning"

# ICONS
ICON_BATTERY = "mdi:car-battery"
ICON_CAR = "mdi:car-info"
ICON_CAR_DOOR = "mdi:car-door"
ICON_CAR_DOOR_LOCK = "mdi:car-door-lock"
ICON_CAR_LIGHTS = "mdi:car-parking-lights"
ICON_EV = "mdi:car-electric"
ICON_FRONT_DEFOGGER = "mdi:car-defrost-front"
ICON_FUEL = "mdi:gas-station"
ICON_HISTORY = "mdi:history"
ICON_KEY = "mdi:car-key"
ICON_ODOMETER = "mdi:counter"
ICON_PARKING = "mdi:map-marker"
ICON_RANGE = "mdi:map-marker-distance"
ICON_REAR_DEFOGGER = "mdi:car-defrost-rear"

# STARTUP LOG MESSAGE
STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUES_URL}
-------------------------------------------------------------------
"""
