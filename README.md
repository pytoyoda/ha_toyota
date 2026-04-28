[![License](https://img.shields.io/github/license/pytoyoda/ha_toyota)](LICENSE)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)
![Version](https://img.shields.io/github/v/release/pytoyoda/ha_toyota)
![Downloads](https://img.shields.io/github/downloads/pytoyoda/ha_toyota/total)
[![CodeQL](https://github.com/pytoyoda/ha_toyota/actions/workflows/codeql.yml/badge.svg)](https://github.com/pytoyoda/ha_toyota/actions/workflows/codeql.yml)

<p align="center">
    <img src="https://brands.home-assistant.io/_/toyota/icon@2x.png" alt="logo" height="200">
</p>

<h2 align="center">Toyota EU community integration</h2>

<p align="center">
    This custom integration aims to provide plug-and-play integration for your Toyota vehicle.
</p>

## Summary

- [About](#about)
- [Features](#features)
  - [Overview](#overview)
  - [Binary sensor(s)](#binary-sensor-s-)
  - [Device tracker(s)](#device-tracker-s-)
  - [Sensor(s)](#sensor-s-)
  - [Button(s)](#button-s-)
  - [Service(s)](#service-s-)
  - [Smart status refresh](#smart-status-refresh)
  - [Statistics sensors](#statistics-sensors)
    - [Important](#important)
    - [Attributes available](#attributes-available)
- [Getting started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [HACS installation (Recommended)](#hacs-installation--recommended-)
  - [Manual Installation](#manual-installation)
    - [Git clone method](#git-clone-method)
    - [Copy method](#copy-method)
  - [Integration Setup](#integration-setup)
  - [Configuration](#configuration)
- [Contribution](#contribution)
  - [License](#license)
- [Credits](#credits)

## About

This is a custom integration the retrieves' data from the
Toyota EU MyToyota ctpa-oneapi API and makes them available in Home Assistant as different types of sensors.
As there is no official API from Toyota, I will try my best to keep
it working, but there are no promises.

With Version 2.0.0 the toyota Custom Component only supports the new Toyota ctpa-oneapi API!
This means that this version is **no longer compatible** with your vehicle **if you are still using the old [MyT](https://play.google.com/store/apps/details?id=app.mytoyota.toyota.com.mytoyota) app**! Before updating, please make sure that you are already using the new [MyToyota](https://play.google.com/store/apps/details?id=com.toyota.oneapp.eu) app and that your vehicle has already been migrated to the new API.

**If you already have an installation of the custom component, make sure when updating to a version >= 2.0 to completely remove the previous installation from your Home Assistant devices and HACS!
You should then perform a reboot and can then reinstall the custom component via HACS again.**

## Features

Only Europe is supported.
See [here](https://github.com/widewing/ha-toyota-na) for North America.

**Disclaimer: Features available depends on your car model and year.**

### Overview

- VIN (Vehicle Identification Number) sensor
- Fuel, battery and odometer information
- Current day, week, month and year statistics.
- Door and door lock sensors, including hood and trunk sensor.
- Diagnostic sensors for fetch health and cache freshness.
- Smart status refresh: wake the vehicle on demand or automatically when it
  has just stopped, mimicking the Toyota app's two-stage protocol so that
  lock/door/window/hood state reflects reality instead of getting stuck stale.
- Per-vehicle button to trigger a manual wake from the dashboard, plus a
  `toyota.refresh_vehicle_status` service for use in automations.

### Binary sensor(s)

| <div style="width:250px">Name</div>      | Description                                           |
| ---------------------------------------- | ----------------------------------------------------- |
| `binary_sensor.<you_car_alias>_hood`     | If the hood is open of not.                           |
| `binary_sensor.<you_car_alias>_*_door`   | Door sensors, one is created for each door and trunk. |
| `binary_sensor.<you_car_alias>_*_lock`   | Lock sensors, one is created for each door and trunk. |
| `binary_sensor.<you_car_alias>_*_window` | Window sensors, one is created for window.            |

When the underlying vehicle status payload is missing a field (cold cache
on first start, vehicle that does not report that field), these sensors
read as `unknown` rather than falsely reporting `open` / `unlocked`.

### Device tracker(s)

| <div style="width:250px">Name</div> | Description                         |
| ----------------------------------- | ----------------------------------- |
| `device_tracker.<you_car_alias>`    | Shows you last parking information. |

### Sensor(s)

| <div style="width:250px">Name</div>                  | Description                                                                                                                                   |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `sensor.<you_car_alias>_vin`                         | Static data about your car.                                                                                                                   |
| `sensor.<you_car_alias>_odometer`                    | Odometer information.                                                                                                                         |
| `sensor.<you_car_alias>_fuel_level`                  | Fuel level information.                                                                                                                       |
| `sensor.<you_car_alias>_fuel_range`                  | Fuel range information.                                                                                                                       |
| `sensor.<you_car_alias>_battery_level`               | Battery level information.                                                                                                                    |
| `sensor.<you_car_alias>_battery_range`               | Battery range information.                                                                                                                    |
| `sensor.<you_car_alias>_battery_range_ac`            | Battery range information when AC is on.                                                                                                      |
| `sensor.<you_car_alias>_total_range`                 | Information about combined fuel and battery range.                                                                                            |
| `sensor.<you_car_alias>_charging_status`             | Charging status\*                                                                                                                             |
| `sensor.<you_car_alias>_remaining_charge_time`       | Remaining minutes until charging is complete.                                                                                                 |
| `sensor.<you_car_alias>_current_day_stats`           | Statistics for current day.                                                                                                                   |
| `sensor.<you_car_alias>_current_week_stats`          | Statistics for current week.                                                                                                                  |
| `sensor.<you_car_alias>_current_month_stats`         | Statistics for current month.                                                                                                                 |
| `sensor.<you_car_alias>_current_year_stats`          | Statistics for current year.                                                                                                                  |
| `sensor.<you_car_alias>_last_successful_fetch`       | Diagnostic: timestamp of the last successful refresh.                                                                                         |
| `sensor.<you_car_alias>_last_error`                  | Diagnostic: timestamp of the last refresh error.                                                                                              |
| `sensor.<you_car_alias>_last_error_code`             | Diagnostic: HTTP status or exception class of the last error.                                                                                 |
| `sensor.<you_car_alias>_status_last_reported_by_car` | Diagnostic: `occurrence_date` of the most recent `/v1/global/remote/status` payload (i.e. when the car last transmitted its lock/door state). |
| `sensor.<you_car_alias>_status_refresh_state`        | Diagnostic: smart-refresh state (`active` / `soft_disabled_unreachable` / `hard_disabled_auto` / `hard_disabled_user`).                       |

\* _Possible charging states_: `Charge complete` | `Charging` | `Not connected` | `Plugged in`

### Button(s)

| <div style="width:250px">Name</div>             | Description                                                                        |
| ----------------------------------------------- | ---------------------------------------------------------------------------------- |
| `button.<you_car_alias>_refresh_vehicle_status` | One-tap wake. Wraps `toyota.refresh_vehicle_status` for the corresponding vehicle. |

### Service(s)

| Service                         | Description                                                                                                                  |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `toyota.refresh_vehicle_status` | Wakes the vehicle's cellular modem and fetches a fresh door / lock / window / hood payload. Targets one or more `device_id`. |

Service fields:

| Field             | Default | Description                                                                      |
| ----------------- | ------- | -------------------------------------------------------------------------------- |
| `timeout_seconds` | `60`    | How long to wait for the car to transmit fresh data before returning (10 - 180). |

Use sparingly: each call uses a small amount of cellular airtime and 12 V
battery. For routine polling, the integration's smart strategy already
picks the right moments (see below).

### Smart status refresh

Lock / door / window / hood data tends to get stuck stale. The Toyota mobile
app issues a `POST /v1/global/remote/refresh-status` to wake the vehicle's
modem before reading `/v1/global/remote/status`; doing the same in this
integration empirically gives fresher payloads and reduces (but does not
eliminate) the `429 APIGW-403` responses on the GET. We do not have a
confirmed model for every 429 - some 429s are unrelated to cache state and
look random - so the integration treats this as a best-effort improvement
rather than a guarantee. When this option is on (default), the integration
fires the wake POST under specific triggers; each wake costs a small amount
of cellular airtime and 12 V battery, so the strategy gates WHEN to fire one:

- **`just_stopped`**: the integration detects via the odometer that the vehicle
  was driving last cycle and is stationary now, and fires a wake POST. A
  configurable number of follow-up POSTs is fired on the immediately
  following coordinator cycles (default 2 total POSTs per stop event) to
  catch state the user changes shortly after stopping - locking the doors,
  opening the trunk to grab bags, etc. - which trigger fresh modem reports
  that the followup POSTs' poll loops pick up. This is the main path that
  aims to address the stuck-stale reports in [#87], [#137], [#157],
  [#190], [#229], [#281] and [#284] without any user action.
- **`service_call`**: the user fires `toyota.refresh_vehicle_status` (via the
  per-vehicle button or an automation). Bypasses soft-disable.
- **`idle_wake`** (opt-in, default off): wakes the vehicle every N hours
  even if it has not moved. Useful for cars that sit unused for days.
- **`cache_stale`**: a regular GET if the cached payload is older than the
  configured `max_cache_age_minutes` (default 30 minutes).

Cars that do not respond to the wake POST (deeply parked Aygo, etc.) are
**soft-disabled per VIN** after a configurable number of failed wakes (default
3). Soft-disable auto-clears as soon as the car shows any sign of life
(driving event, external app refresh, manual service call). Vehicles whose
Toyota account does not support `/refresh-status` at all are **hard-disabled**
automatically; the user clears this by toggling the master switch off then on.

[#87]: https://github.com/pytoyoda/ha_toyota/issues/87
[#137]: https://github.com/pytoyoda/ha_toyota/issues/137
[#157]: https://github.com/pytoyoda/ha_toyota/issues/157
[#190]: https://github.com/pytoyoda/ha_toyota/issues/190
[#229]: https://github.com/pytoyoda/ha_toyota/issues/229
[#281]: https://github.com/pytoyoda/ha_toyota/issues/281
[#284]: https://github.com/pytoyoda/ha_toyota/issues/284

### Statistics sensors

#### Important

When starting a new week, month or year, it will not show any information before your first trip. Even though a new month starts on the 1, you will need to wait for the 2 of the month before it is able to show you current month stats. This due to a limitation in Toyota API. This limitation also applies to weeks.
Due to this, this integration will list sensors as unavailable when no data is available.

#### Attributes available

**Disclaimer: Attributes available depends on your car model and year.**

All values will show `None` if no data is available for the period.

| Attribute               | Description                                                                     |
| ----------------------- | ------------------------------------------------------------------------------- |
| `Distance`              | Distance driven (Displayed as sensor value).                                    |
| `Average_speed`         | The average speed in the respective period (can be km/h or mph).                |
| `Countries`             | The countries travelled through in the respective period.                       |
| `Duration`              | The total driving time in the respective period.                                |
| `Total_fuel_consumed`   | The total fuel consumption in the respective period (can be litres or gallons). |
| `Average_fuel_consumed` | The average fuel consumption in the respective period (can be l/100km or mpg).  |
| `EV_distance`           | The driving distiance in EV mode in the respective period .                     |
| `EV_duration`           | The driving time in EV mode in the respective period .                          |
| `From_date`             | Start date of the calculation period.                                           |
| `To_date`               | End date of the calculation period.                                             |

## Getting started

### Prerequisites

Use Home Assistant build 2023.12 or above.

If you can confirm that it is working as advertised on older version please open a PR.

**Note:** It is **_only_** tested against latest, but should work on older versions too.

**Note:** Future updates may change which version are required.

### HACS installation (Recommended)

We are still waiting for our repository to be included in the current HACS package sources (see: https://github.com/hacs/default/pull/3284).
Therefore you have to add the repository manually as an “Integration” custom repository in HACS for the time being. See also: https://www.hacs.xyz/docs/faq/custom_repositories/?h=add+repos

After that you can open HACS and search for `Toyota EU community integration` under integrations.
You can choose to install a specific version or from main (Not recommended).

### Manual Installation

1. Open the directory with your Home Assistant configuration (where you find `configuration.yaml`,
   usually `~/.homeassistant/`).
2. If you do not have a `custom_components` directory there, you need to create it.

#### Git clone method

This is a preferred method of manual installation, because it allows you to keep the `git` functionality,
allowing you to manually install updates just by running `git pull origin main` from the created directory.

Now you can clone the repository somewhere else and symlink it to Home Assistant like so:

1. Clone the repo.

   ```shell
   git clone https://github.com/pytoyoda/ha_toyota.git
   ```

2. Create the symlink to `toyota` in the configuration directory.
   If you have non-standard directory for configuration, use it instead.

   ```shell
   ln -s ha_toyota/custom_components/toyota ~/.homeassistant/custom_components/toyota
   ```

#### Copy method

1. Download [ZIP](https://github.com/pytoyoda/ha_toyota/archive/main.zip) with the code.
2. Unpack it.
3. Copy the `custom_components/toyota/` from the unpacked archive to `custom_components`
   in your Home Assistant configuration directory.

### Integration Setup

- Browse to your Home Assistant instance.
- In the sidebar click on [Configuration](https://my.home-assistant.io/redirect/config).
- From the configuration menu select: [Integrations](https://my.home-assistant.io/redirect/integrations).
- In the bottom right, click on the [Add Integration](https://my.home-assistant.io/redirect/config_flow_start?domain=toyota) button.
- From the list, search and select “Toyota Connected Services”.
- Follow the instruction on screen to complete the set-up.
- After completing, the Toyota Connected Services integration will be immediately available for use.

### Configuration

After setup, options can be tuned per integration entry from
**Settings → Devices & Services → Toyota Connected Services → Configure**.
Defaults are tuned for a typical daily-driven car.

| Option                                             | Default | Range   | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| -------------------------------------------------- | ------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Polling interval (minutes)**                     | 6       | 5 - 60  | How often the integration polls Toyota for fresh data. Lower values may hit rate limits.                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **Retain last good data on transient failures**    | off     | toggle  | When a refresh fails (HTTP 429, timeout, connection error), keep the last successful per-vehicle data in place instead of flipping to `unavailable`. The diagnostic sensors above still surface the underlying failure.                                                                                                                                                                                                                                                                                   |
| **Refresh vehicle status remotely**                | on      | toggle  | Master switch for the smart status refresh feature. Scope is only the `/v1/global/remote/status` endpoint (door / window / lock / hood); other data is fetched every cycle regardless. Disable for vehicles whose Toyota account does not support `/refresh-status`; the integration also auto-disables this for you when it detects unsupported responses. **When this option is OFF, the four options below (idle wake, failed-wake threshold, status cache age, wake POSTs per stop) have no effect.** |
| **Wake idle vehicle every N hours (0 = disabled)** | 0       | 0 - 72  | Wake the car periodically even if it has not moved. Useful for cars that sit unused for days where you still want fresh lock state. 0 disables the feature; 1-72 fires a wake POST every N hours. Off by default to spare 12 V battery. The wake only refreshes the `/v1/global/remote/status` endpoint (door / window / lock / hood); other data is fetched every cycle regardless.                                                                                                                      |
| **Mark unreachable after N failed wakes**          | 3       | 1 - 10  | A vehicle that fails to respond to this many consecutive wake POSTs is marked unreachable per-VIN. Auto-clears on any sign of life from the car.                                                                                                                                                                                                                                                                                                                                                          |
| **Refresh status cache if older**                  | 30      | 5 - 180 | Maximum acceptable age of the cached `/status` data before issuing a fresh GET. Controls only the `/v1/global/remote/status` endpoint (door / window / lock / hood). Other data (odometer, fuel, location, etc.) is fetched every cycle regardless.                                                                                                                                                                                                                                                       |
| **Wake POSTs per stop event**                      | 2       | 1 - 5   | Number of wake POSTs fired when a stop event is detected, one per coordinator cycle. 1 = single POST. 2 = an additional POST on the next cycle, which typically catches state the user changes shortly after stopping (locking the doors, opening the trunk) - those events trigger fresh modem reports that the second POST's poll loop picks up. Higher rarely helps and burns 12 V battery.                                                                                                            |

## Contribution

Contributions are more the welcome. This project uses `poetry` and `pre-commit` to make sure that
we use a unified coding style throughout the code. Poetry can be installed by running `poetry install`.
Please run `poetry run pre-commit run --all-files` and make sure that all tests passes before
opening a PR or committing to the PR. All PR's must pass all checks for them to get approved.

### License

By contributing, you agree that your contributions will be licensed under its MIT License.

## Credits

Under the hood this integration uses the [pytoyoda](https://github.com/pytoyoda/pytoyoda) python package.
