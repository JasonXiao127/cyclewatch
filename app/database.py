"""SQLite persistence: devices, uploads and battery readings."""

from __future__ import annotations

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


def upload_path_for_sha(sha256: str) -> str:
    return os.path.join(get_uploads_dir(), f"{sha256}.ips")


def legacy_upload_path_for_sha(sha256: str) -> str:
    return os.path.join(get_uploads_dir(), f"{sha256[:16]}.ips")


def delete_upload_files_if_orphaned(conn: sqlite3.Connection, sha256: str) -> None:
    """Remove stored .ips file(s) if no upload row references the sha anymore."""
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
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
    if "full_charge_capacity_mah" not in existing:
        conn.execute("ALTER TABLE readings ADD COLUMN full_charge_capacity_mah INTEGER")
    for column in ("battery_level_pct", "min_soc_pct", "max_soc_pct"):
        if column not in existing:
            conn.execute(f"ALTER TABLE readings ADD COLUMN {column} INTEGER")


def upsert_reading(
    conn: sqlite3.Connection,
    device_id: int,
    upload_id: int | None,
    reading: dict,
    fallback_date,
) -> bool:
    """Insert or merge one parsed reading. Returns True if it was new."""
    timestamp = reading.get("timestamp")
    date_source = reading.get("date_source", "file")
    needs_unique_fallback = timestamp is None
    if timestamp is None:
        # Fallback: neither the entry nor the header had a usable timestamp.
        timestamp = datetime.combine(fallback_date, datetime.min.time())
        date_source = "fallback"
    nominal = reading.get("nominal_capacity_mah")
    full_charge = reading.get("full_charge_capacity_mah")
    design = reading.get("design_capacity_mah")
    # Health: prefer Apple's own reported percent, then compute from
    # capacities (full-charge vs design, then nominal vs design).
    health = reading.get("health_pct")
    if health is None:
        if full_charge is not None and design not in (None, 0):
            health = round(100.0 * full_charge / design, 2)
        elif nominal is not None and design not in (None, 0):
            health = round(100.0 * nominal / design, 2)
    if needs_unique_fallback:
        # Dateless readings would otherwise all share today's midnight and
        # collapse into one row via UNIQUE(device_id, timestamp). Bump by
        # seconds until free so each fallback reading is preserved.
        ts_text = timestamp.isoformat()
        while conn.execute(
            "SELECT 1 FROM readings WHERE device_id = ? AND timestamp = ?",
            (device_id, ts_text),
        ).fetchone():
            timestamp = timestamp + timedelta(seconds=1)
            ts_text = timestamp.isoformat()
    else:
        ts_text = timestamp.isoformat()

    params = (
        device_id,
        upload_id,
        ts_text,
        date_source,
        reading.get("cycle_count"),
        nominal,
        full_charge,
        design,
        health,
        reading.get("battery_level_pct"),
        reading.get("min_soc_pct"),
        reading.get("max_soc_pct"),
    )
    try:
        conn.execute(
            """INSERT INTO readings
                   (device_id, upload_id, timestamp, date_source, cycle_count,
                    nominal_capacity_mah, full_charge_capacity_mah,
                    design_capacity_mah, health_pct,
                    battery_level_pct, min_soc_pct, max_soc_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            params,
        )
        return True
    except sqlite3.IntegrityError:
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
                reading.get("cycle_count"),
                nominal,
                full_charge,
                design,
                health,
                reading.get("battery_level_pct"),
                reading.get("min_soc_pct"),
                reading.get("max_soc_pct"),
                device_id,
                ts_text,
            ),
        )
        return False
