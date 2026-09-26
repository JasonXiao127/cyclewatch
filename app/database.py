"""SQLite persistence: devices, uploads and battery readings."""

from __future__ import annotations

import logging
import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_data_dir() -> str:
    """Resolve DATA_DIR live so tests can monkeypatch env without reload."""
    return os.environ.get("DATA_DIR", os.path.join(_BASE_DIR, "data"))


def get_db_path() -> str:
    return os.path.join(get_data_dir(), "battery.db")


def get_uploads_dir() -> str:
    return os.path.join(get_data_dir(), "uploads")


# Import-time snapshots kept for backwards compat (tests reload the module).
# New code should use the getters above.
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(_BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "battery.db")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")


def _valid_sha(sha256: str) -> bool:
    return isinstance(sha256, str) and len(sha256) == 64 and all(c in "0123456789abcdef" for c in sha256.lower())


def upload_path_for_sha(sha256: str) -> str:
    return os.path.join(get_uploads_dir(), f"{sha256.lower()}.ips")


def legacy_upload_path_for_sha(sha256: str) -> str:
    return os.path.join(get_uploads_dir(), f"{sha256.lower()[:16]}.ips")


def delete_upload_files_if_orphaned(conn: sqlite3.Connection, sha256: str) -> None:
    """Remove stored .ips file(s) if no upload row references the sha anymore."""
    if not _valid_sha(sha256):
        return
    still_used = conn.execute(
        "SELECT 1 FROM uploads WHERE sha256 = ? LIMIT 1", (sha256,)
    ).fetchone()
    if still_used:
        return
    for path in (upload_path_for_sha(sha256), legacy_upload_path_for_sha(sha256)):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_excluded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    readings_found INTEGER NOT NULL DEFAULT 0,
    UNIQUE(device_id, sha256)
);

CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    upload_id INTEGER REFERENCES uploads(id) ON DELETE SET NULL,
    timestamp TEXT NOT NULL,
    date_source TEXT NOT NULL DEFAULT 'file',
    cycle_count INTEGER,
    nominal_capacity_mah INTEGER,
    full_charge_capacity_mah INTEGER,
    design_capacity_mah INTEGER,
    health_pct REAL,
    battery_level_pct INTEGER,
    min_soc_pct INTEGER,
    max_soc_pct INTEGER,
    is_excluded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(device_id, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_readings_device_ts ON readings(device_id, timestamp);
"""


@contextmanager
def get_connection():
    os.makedirs(get_data_dir(), exist_ok=True)
    conn = sqlite3.connect(get_db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(readings)")}
    for column, ddl in (
        ("cycle_count", "ALTER TABLE readings ADD COLUMN cycle_count INTEGER"),
        ("nominal_capacity_mah", "ALTER TABLE readings ADD COLUMN nominal_capacity_mah INTEGER"),
        ("full_charge_capacity_mah", "ALTER TABLE readings ADD COLUMN full_charge_capacity_mah INTEGER"),
        ("design_capacity_mah", "ALTER TABLE readings ADD COLUMN design_capacity_mah INTEGER"),
        ("health_pct", "ALTER TABLE readings ADD COLUMN health_pct REAL"),
        ("battery_level_pct", "ALTER TABLE readings ADD COLUMN battery_level_pct INTEGER"),
        ("min_soc_pct", "ALTER TABLE readings ADD COLUMN min_soc_pct INTEGER"),
        ("max_soc_pct", "ALTER TABLE readings ADD COLUMN max_soc_pct INTEGER"),
        ("is_excluded", "ALTER TABLE readings ADD COLUMN is_excluded INTEGER NOT NULL DEFAULT 0"),
        ("date_source", "ALTER TABLE readings ADD COLUMN date_source TEXT NOT NULL DEFAULT 'file'"),
        ("upload_id", "ALTER TABLE readings ADD COLUMN upload_id INTEGER REFERENCES uploads(id) ON DELETE SET NULL"),
    ):
        if column not in existing:
            conn.execute(ddl)
    # Devices: ensure is_excluded exists on very old DBs.
    dev_cols = {row[1] for row in conn.execute("PRAGMA table_info(devices)")}
    if "is_excluded" not in dev_cols:
        conn.execute("ALTER TABLE devices ADD COLUMN is_excluded INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_device_ts ON readings(device_id, timestamp)")
    # Backfill NULLs left by older code before defaults existed.
    log = logging.getLogger("cyclewatch.migrate")
    try:
        conn.execute("UPDATE readings SET date_source = 'file' WHERE date_source IS NULL")
        conn.execute("UPDATE readings SET is_excluded = 0 WHERE is_excluded IS NULL")
    except sqlite3.Error as exc:
        log.warning("migrate backfill readings failed: %s", exc)
    try:
        conn.execute("UPDATE devices SET is_excluded = 0 WHERE is_excluded IS NULL")
    except sqlite3.Error as exc:
        log.warning("migrate backfill devices failed: %s", exc)


def upsert_reading(
    conn: sqlite3.Connection,
    device_id: int,
    upload_id: int | None,
    reading: dict,
    fallback_date,
) -> bool:
    """Insert or merge one parsed reading. Returns True if it was new."""
    timestamp = reading.get("timestamp")
    if timestamp is not None and not isinstance(timestamp, datetime):
        raise TypeError(f"timestamp must be datetime|None, got {type(timestamp).__name__}")
    date_source = reading.get("date_source") or "file"
    needs_unique_fallback = timestamp is None
    if timestamp is None:
        # Fallback: neither the entry nor the header had a usable timestamp.
        timestamp = datetime.combine(fallback_date, datetime.min.time())
        date_source = "fallback"

    def _nonneg_int(v):
        if v is None or isinstance(v, bool):
            return None
        try:
            iv = int(v)
        except (ValueError, TypeError, OverflowError):
            return None
        return iv if iv >= 0 else None

    def _pct(v):
        if v is None or isinstance(v, bool):
            return None
        try:
            fv = float(v)
        except (ValueError, TypeError, OverflowError):
            return None
        if not math.isfinite(fv):
            return None
        if fv < 0 or fv > 100.0:
            return None
        return fv

    nominal = _nonneg_int(reading.get("nominal_capacity_mah"))
    full_charge = _nonneg_int(reading.get("full_charge_capacity_mah"))
    design = _nonneg_int(reading.get("design_capacity_mah"))
    cycle = _nonneg_int(reading.get("cycle_count"))
    # Health: prefer Apple's own reported percent, then compute from
    # capacities (full-charge vs design, then nominal vs design).
    health = reading.get("health_pct")
    if isinstance(health, bool):
        health = None
    else:
        try:
            hf = float(health) if health is not None else None
            if hf is None or not math.isfinite(hf) or hf < 0:
                health = None
            else:
                health = min(hf, 200.0)
        except (ValueError, TypeError, OverflowError):
            health = None
    if health is None:
        if full_charge is not None and design not in (None, 0):
            health = round(100.0 * full_charge / design, 2)
        elif nominal is not None and design not in (None, 0):
            health = round(100.0 * nominal / design, 2)
        if health is not None:
            health = min(max(health, 0.0), 200.0)
    level = _pct(reading.get("battery_level_pct"))
    min_soc = _pct(reading.get("min_soc_pct"))
    max_soc = _pct(reading.get("max_soc_pct"))
    # is_excluded: None = preserve (upload path), 0/1/bool = enforce (import path).
    raw_excluded = reading.get("is_excluded", None)
    if raw_excluded is None:
        excluded: int | None = None
    else:
        excluded = 1 if bool(raw_excluded) else 0
    if needs_unique_fallback:
        # Dateless readings would otherwise all share today's midnight and
        # collapse into one row via UNIQUE(device_id, timestamp). Bump by
        # seconds until free so each fallback reading is preserved.
        # Invariant: MAX_READINGS (50000) < 86400, so same-day fallback batches
        # always fit. Cross-connection races retry below on IntegrityError.
        ts_text = timestamp.isoformat()
        bumps = 0
        while conn.execute(
            "SELECT 1 FROM readings WHERE device_id = ? AND timestamp = ?",
            (device_id, ts_text),
        ).fetchone():
            if bumps >= 86400:
                # Full day exhausted; merge into last slot rather than loop forever.
                break
            timestamp = timestamp + timedelta(seconds=1)
            ts_text = timestamp.isoformat()
            bumps += 1
    else:
        ts_text = timestamp.isoformat()

    if excluded is None:
        # Retry fallback bumps across concurrent transactions (SELECT-then-INSERT race).
        for attempt in range(6 if needs_unique_fallback else 1):
            try:
                conn.execute(
                    """INSERT INTO readings
                           (device_id, upload_id, timestamp, date_source, cycle_count,
                            nominal_capacity_mah, full_charge_capacity_mah,
                            design_capacity_mah, health_pct,
                            battery_level_pct, min_soc_pct, max_soc_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (device_id, upload_id, ts_text, date_source, cycle, nominal, full_charge, design, health, level, min_soc, max_soc),
                )
                return True
            except sqlite3.IntegrityError:
                if needs_unique_fallback and attempt < 5:
                    timestamp = timestamp + timedelta(seconds=1)
                    ts_text = timestamp.isoformat()
                    continue
                conn.execute(
                    """UPDATE readings SET
                           upload_id = ?,
                           date_source = ?,
                           cycle_count = COALESCE(?, cycle_count),
                           nominal_capacity_mah = COALESCE(?, nominal_capacity_mah),
                           full_charge_capacity_mah = COALESCE(?, full_charge_capacity_mah),
                           design_capacity_mah = COALESCE(?, design_capacity_mah),
                           health_pct = COALESCE(?, health_pct),
                           battery_level_pct = COALESCE(?, battery_level_pct),
                           min_soc_pct = COALESCE(?, min_soc_pct),
                           max_soc_pct = COALESCE(?, max_soc_pct)
                       WHERE device_id = ? AND timestamp = ?""",
                    (
                        upload_id,
                        date_source,
                        cycle,
                        nominal,
                        full_charge,
                        design,
                        health,
                        level,
                        min_soc,
                        max_soc,
                        device_id,
                        ts_text,
                    ),
                )
                return False
    else:
        for attempt in range(6 if needs_unique_fallback else 1):
            try:
                conn.execute(
                    """INSERT INTO readings
                           (device_id, upload_id, timestamp, date_source, cycle_count,
                            nominal_capacity_mah, full_charge_capacity_mah,
                            design_capacity_mah, health_pct,
                            battery_level_pct, min_soc_pct, max_soc_pct, is_excluded)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        device_id,
                        upload_id,
                        ts_text,
                        date_source,
                        cycle,
                        nominal,
                        full_charge,
                        design,
                        health,
                        level,
                        min_soc,
                        max_soc,
                        excluded,
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                if needs_unique_fallback and attempt < 5:
                    timestamp = timestamp + timedelta(seconds=1)
                    ts_text = timestamp.isoformat()
                    continue
                conn.execute(
                    """UPDATE readings SET
                           upload_id = ?,
                           date_source = ?,
                           cycle_count = COALESCE(?, cycle_count),
                           nominal_capacity_mah = COALESCE(?, nominal_capacity_mah),
                           full_charge_capacity_mah = COALESCE(?, full_charge_capacity_mah),
                           design_capacity_mah = COALESCE(?, design_capacity_mah),
                           health_pct = COALESCE(?, health_pct),
                           battery_level_pct = COALESCE(?, battery_level_pct),
                           min_soc_pct = COALESCE(?, min_soc_pct),
                           max_soc_pct = COALESCE(?, max_soc_pct),
                           is_excluded = ?
                       WHERE device_id = ? AND timestamp = ?""",
                    (
                        upload_id,
                        date_source,
                        cycle,
                        nominal,
                        full_charge,
                        design,
                        health,
                        level,
                        min_soc,
                        max_soc,
                        excluded,
                        device_id,
                        ts_text,
                    ),
                )
                return False
