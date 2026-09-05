"""Extract battery health readings from Apple .ips analytics files.

.ips files (Settings > Privacy & Security > Analytics & Improvements >
Analytics Data, e.g. "Analytics-2024-03-01-080000.ips") are JSON-lines:
the first line is a JSON header object and every following line is an
independent JSON log entry.

Battery metrics vary a lot between iOS versions:
- Older formats nest plain keys such as "BatteryCycleCount" or
  "NominalChargeCapacity" somewhere inside the entry.
- Newer formats (observed on recent iOS) put daily aggregates inside
  "message" with "last_value_"-prefixed keys such as
  "last_value_CycleCount", "last_value_NominalChargeCapacity",
  "last_value_AppleRawMaxCapacity" and "last_value_MaximumCapacityPercent",
  and carry no per-entry timestamp -- only the header line is dated.

So we walk every entry recursively, strip known prefixes ("last_value_",
"daily_total_") and match the remaining key name against the alias table
below. Dates come from the entry's own timestamp, falling back to the
header's timestamp, then to today's date (flagged as "fallback").
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterator

# Field name -> key names seen inside iOS/iPadOS analytics log entries
# (after stripping known prefixes such as "last_value_"). Add new aliases
# here when a future iOS version renames or relocates a key.
BATTERY_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "cycle_count": ("CycleCount", "BatteryCycleCount"),
    "nominal_capacity_mah": ("NominalChargeCapacity",),
    "full_charge_capacity_mah": ("FullChargeCapacity", "AppleRawMaxCapacity"),
    "design_capacity_mah": ("DesignCapacity", "BatteryDesignCapacity"),
    "health_pct": ("MaximumCapacityPercent", "BatteryHealthMaxCapacity"),
    # Charge level (state of charge, %) when the log was taken. UISOC is
    # the user-visible charge percentage in newer iOS formats.
    "battery_level_pct": ("UISOC", "BatteryLevel", "StateOfCharge"),
    # Day's min/max charge % (daily aggregates) -- context for the charge
    # range the sample was exposed to.
    "min_soc_pct": ("DailyMinSoc",),
    "max_soc_pct": ("DailyMaxSoc",),
}

_KEY_TO_FIELD = {
    key: field
    for field, keys in BATTERY_KEY_ALIASES.items()
    for key in keys
}
_ALL_BATTERY_KEYS = frozenset(_KEY_TO_FIELD)

# Known prefixes that iOS prepends to battery metric keys.
_KEY_PREFIXES = ("last_value_", "daily_total_")

_TIMESTAMP_KEYS = ("timestamp", "TimeStamp", "time", "log_time")
_SCALAR_TYPES = (str, int, float, bool)


def _walk(obj: Any) -> Iterator[tuple[str, Any]]:
    """Recursively yield (key, value) pairs for every scalar leaf."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if value is None or isinstance(value, _SCALAR_TYPES):
                yield key, value
            else:
                yield from _walk(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _strip_prefix(key: str) -> str:
    for prefix in _KEY_PREFIXES:
        if key.startswith(prefix):
            return key[len(prefix):]
    return key


def _match_field(key: str) -> str | None:
    if key in _KEY_TO_FIELD:
        return _KEY_TO_FIELD[key]
    return _KEY_TO_FIELD.get(_strip_prefix(key))


def _to_number(value: Any) -> float | int | None:
    """Coerce a scalar into a number; returns None for bools/garbage."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return None
    return None


def parse_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f %z",
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            # Keep the wall-clock time, drop the timezone offset.
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


# Backwards-compat alias (main.py previously imported the private name).
_parse_timestamp = parse_timestamp


def parse_ips_file(text: str) -> dict:
    """Parse an .ips analytics file and extract battery readings.

    Returns a dict with:
      header: the JSON header object from line 1 (or {})
      readings: list of dicts with keys timestamp (datetime | None),
                date_source ("file" | "fallback"), cycle_count,
                nominal_capacity_mah, full_charge_capacity_mah,
                design_capacity_mah, health_pct
      unrecognized_keys: battery-ish keys that were seen but not matched
    """
    grouped: dict[str, dict] = {}
    header: dict = {}
    header_ts: datetime | None = None
    unrecognized: set[str] = set()
    seen_first_entry = False
    fallback_counter = 0

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        if not seen_first_entry:
            seen_first_entry = True
            if "bug_type" in entry:
                header = entry
                header_ts = parse_timestamp(entry.get("timestamp"))

        found: dict[str, float | int] = {}
        timestamp: datetime | None = None
        # Single walk: collect battery fields and first usable timestamp.
        for key, value in _walk(entry):
            if timestamp is None and key in _TIMESTAMP_KEYS:
                timestamp = parse_timestamp(value)
            field = _match_field(key)
            if field is not None:
                number = _to_number(value)
                if number is not None:
                    found.setdefault(field, number)
            elif (
                isinstance(value, _SCALAR_TYPES)
                and not isinstance(value, bool)
                and any(
                    word in _strip_prefix(key).lower()
                    for word in ("battery", "capacity", "cyclecount")
                )
            ):
                unrecognized.add(key)

        if not found:
            continue

        # Date cascade: entry timestamp -> header timestamp -> fallback.
        if timestamp is None:
            timestamp = header_ts

        if timestamp is not None:
            stamp = timestamp.isoformat()
        else:
            # Each dateless entry gets its own bucket; merging them all
            # under one "@fallback" key silently dropped data.
            stamp = f"@fallback-{fallback_counter}"
            fallback_counter += 1
        bucket = grouped.setdefault(
            stamp,
            {
                "timestamp": timestamp,
                "date_source": "file" if timestamp else "fallback",
                "values": {},
            },
        )
        for field, number in found.items():
            bucket["values"].setdefault(field, number)

    readings = []
    for bucket in grouped.values():
        reading = {
            "timestamp": bucket["timestamp"],
            "date_source": bucket["date_source"],
        }
        reading.update(bucket["values"])
        readings.append(reading)
    readings.sort(key=lambda r: r["timestamp"] or datetime.min)
    return {"header": header, "readings": readings, "unrecognized_keys": sorted(unrecognized)}
