"""Regression guard for #412: no two sensor entities may share a ``key``.

``ToyotaBaseEntity`` builds ``unique_id`` as ``f"{entry_id}_{vin}/{key}"``
(see entity.py) - entry_id and vin are constant per vehicle, so a
collision anywhere in the full set of descriptions sensor.py can create
for one vehicle produces a duplicate unique_id. Home Assistant then logs
"Platform toyota does not generate unique IDs" and silently drops one of
the two entities.

This bit us for real in #410/#411: NOTIFICATIONS_ENTITY_DESCRIPTION in
sensor.py (key="notifications") and the "notifications" entry in
sensor_extra.py's _CLASSES/DESCRIPTIONS both resolved to the same key.
This test enumerates every description sensor.py's async_setup_entry can
combine for a single vehicle - independent of any specific vehicle's
capability flags, since the bug is in the static declarations, not in
which subset a given vehicle happens to unlock - and asserts the key set
has no duplicates.
"""

from __future__ import annotations

from collections import Counter

from custom_components.toyota import sensor
from custom_components.toyota import sensor_extra


def _all_declared_sensor_keys() -> list[str]:
    """Every sensor key sensor.py's async_setup_entry can attach to one vehicle."""
    keys = [
        config["description"].key for config in sensor.create_sensor_configurations(True)
    ]
    keys += [
        sensor.LAST_SUCCESSFUL_FETCH_ENTITY_DESCRIPTION.key,
        sensor.LAST_REFRESH_ERROR_TIME_ENTITY_DESCRIPTION.key,
        sensor.LAST_REFRESH_ERROR_CODE_ENTITY_DESCRIPTION.key,
        sensor.STATUS_LAST_REPORTED_ENTITY_DESCRIPTION.key,
        sensor.STATUS_REFRESH_STATE_ENTITY_DESCRIPTION.key,
        sensor.RECENT_TRIPS_ENTITY_DESCRIPTION.key,
    ]
    keys += list(sensor_extra._CLASSES)
    return keys


def test_no_duplicate_sensor_keys() -> None:
    """Every sensor key sensor.py can create for one vehicle must be unique.

    A duplicate here means Home Assistant will silently drop one of the two
    colliding entities at runtime (see #410/#411) instead of failing loudly.
    """
    keys = _all_declared_sensor_keys()
    counts = Counter(keys)
    duplicates = {key: count for key, count in counts.items() if count > 1}
    assert not duplicates, (
        f"Duplicate sensor keys found (each produces a colliding unique_id): "
        f"{duplicates}"
    )


def test_extra_sensor_classes_and_descriptions_have_matching_keys() -> None:
    """sensor_extra's _CLASSES and DESCRIPTIONS dicts must share the same keys.

    sensor.py's async_setup_entry looks up DESCRIPTIONS[key] for every key
    in _CLASSES; a key present in one but not the other would raise
    KeyError at setup time instead of being caught here.
    """
    assert set(sensor_extra._CLASSES) == set(sensor_extra.DESCRIPTIONS)
