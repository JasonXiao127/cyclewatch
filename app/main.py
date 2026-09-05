"""FastAPI app: file upload, device/reading management, dashboard API."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import database as db
from .parser import parse_ips_file, parse_timestamp

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_FILES = 20
MAX_READINGS = 50000
MAX_FILENAME_LEN = 255

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Cyclewatch", lifespan=lifespan)


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class DevicePatch(BaseModel):
    name: str | None = None
    is_excluded: bool | None = None


class ReadingPatch(BaseModel):
    is_excluded: bool


class BackupReading(BaseModel):
    timestamp: str | None = None
    date_source: str = "file"
    cycle_count: int | None = Field(default=None, ge=0)
    nominal_capacity_mah: int | None = Field(default=None, ge=0)
    full_charge_capacity_mah: int | None = Field(default=None, ge=0)
    design_capacity_mah: int | None = Field(default=None, ge=0)
    health_pct: float | None = Field(default=None, ge=0, le=100)
    battery_level_pct: int | None = Field(default=None, ge=0, le=100)
    min_soc_pct: int | None = Field(default=None, ge=0, le=100)
    max_soc_pct: int | None = Field(default=None, ge=0, le=100)
    is_excluded: bool = False


class BackupDevice(BaseModel):
    name: str
    is_excluded: bool = False
    readings: list[BackupReading] = Field(default_factory=list)


class BackupFile(BaseModel):
    # Accepts the legacy "battery-tracker" value from pre-rename exports.
    app: str = "cyclewatch"
    version: int = 1
    devices: list[BackupDevice] = Field(default_factory=list)


class PasteIn(BaseModel):
    device_id: int
    filename: str | None = None
    content: str


# ---------------- devices ----------------


@app.get("/api/devices")
def list_devices():
    with db.get_connection() as conn:
        rows = conn.execute(
            """SELECT d.id, d.name, d.is_excluded, d.created_at,
                      COUNT(r.id) AS readings_count,
                      MAX(r.timestamp) AS latest_reading
               FROM devices d
               LEFT JOIN readings r ON r.device_id = d.id
               GROUP BY d.id
               ORDER BY d.name COLLATE NOCASE"""
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/devices", status_code=201)
def create_device(payload: DeviceIn):
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Device name cannot be empty")
    with db.get_connection() as conn:
        try:
            cur = conn.execute("INSERT INTO devices (name) VALUES (?)", (name,))
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"Device '{name}' already exists")
        device = conn.execute("SELECT * FROM devices WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(device)


@app.patch("/api/devices/{device_id}")
def update_device(device_id: int, payload: DevicePatch):
    updates, params = [], []
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(422, "Device name cannot be empty")
        updates.append("name = ?")
        params.append(name)
    if payload.is_excluded is not None:
        updates.append("is_excluded = ?")
        params.append(1 if payload.is_excluded else 0)
    if not updates:
        raise HTTPException(422, "Nothing to update")
    with db.get_connection() as conn:
        if not conn.execute("SELECT 1 FROM devices WHERE id = ?", (device_id,)).fetchone():
            raise HTTPException(404, "Device not found")
        try:
            conn.execute(
                f"UPDATE devices SET {', '.join(updates)} WHERE id = ?",
                (*params, device_id),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "A device with that name already exists")
        device = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    return dict(device)


@app.delete("/api/devices/{device_id}")
def delete_device(device_id: int):
    with db.get_connection() as conn:
        if not conn.execute("SELECT 1 FROM devices WHERE id = ?", (device_id,)).fetchone():
            raise HTTPException(404, "Device not found")
        obsolete = conn.execute(
            "SELECT DISTINCT sha256 FROM uploads WHERE device_id = ?", (device_id,)
        ).fetchall()
        conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        for row in obsolete:
            db.delete_upload_files_if_orphaned(conn, row[0])
    return {"deleted": device_id}


# ---------------- uploads ----------------


def _sanitize_filename(filename: str | None, fallback: str) -> str:
    name = (filename or "").strip() or fallback
    name = os.path.basename(name)
    if len(name) > MAX_FILENAME_LEN:
        name = name[-MAX_FILENAME_LEN:]
    return name or fallback


def _store_parsed_upload(
    conn: sqlite3.Connection,
    device_id: int,
    filename: str,
    raw: bytes,
    parsed: dict,
    today: date,
) -> dict:
    """Shared persist path for /api/upload and /api/upload-text."""
    if len(parsed["readings"]) > MAX_READINGS:
        raise HTTPException(413, f"Too many readings (max {MAX_READINGS})")
    sha256 = hashlib.sha256(raw).hexdigest()
    duplicate = conn.execute(
        "SELECT id FROM uploads WHERE device_id = ? AND sha256 = ?",
        (device_id, sha256),
    ).fetchone()
    if duplicate:
        return {"filename": filename, "status": "duplicate", "readings_found": 0, "new_readings": 0}
    os.makedirs(db.get_uploads_dir(), exist_ok=True)
    with open(db.upload_path_for_sha(sha256), "wb") as fh:
        fh.write(raw)
    cur = conn.execute(
        "INSERT INTO uploads (device_id, filename, sha256, readings_found) VALUES (?, ?, ?, ?)",
        (device_id, filename, sha256, len(parsed["readings"])),
    )
    upload_id = cur.lastrowid
    new_count = 0
    for reading in parsed["readings"]:
        if db.upsert_reading(conn, device_id, upload_id, reading, today):
            new_count += 1
    others = conn.execute(
        """SELECT d.name FROM uploads u
           JOIN devices d ON d.id = u.device_id
           WHERE u.sha256 = ? AND u.device_id != ?
           GROUP BY d.name ORDER BY d.name COLLATE NOCASE""",
        (sha256, device_id),
    ).fetchall()
    return {
        "filename": filename,
        "status": "ok",
        "readings_found": len(parsed["readings"]),
        "new_readings": new_count,
        "updated_readings": len(parsed["readings"]) - new_count,
        "unrecognized_keys": parsed["unrecognized_keys"],
        "cross_device_duplicate": [row[0] for row in others],
    }


@app.post("/api/upload")
async def upload_files(device_id: int = Form(...), files: list[UploadFile] = File(...)):
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(413, f"Too many files (max {MAX_UPLOAD_FILES})")
    with db.get_connection() as conn:
        device = conn.execute("SELECT id FROM devices WHERE id = ?", (device_id,)).fetchone()
        if device is None:
            raise HTTPException(404, "Device not found")
        results = []
        today = date.today()
        for upload_file in files:
            raw = await upload_file.read()
            if len(raw) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"File too large (max {MAX_UPLOAD_BYTES // 1024 // 1024} MB)")
            filename = _sanitize_filename(upload_file.filename, "unnamed.ips")
            parsed = parse_ips_file(raw.decode("utf-8", errors="replace"))
            results.append(_store_parsed_upload(conn, device_id, filename, raw, parsed, today))
    return {"results": results}


@app.post("/api/upload-text")
def upload_text(payload: PasteIn):
    """Paste fallback for iOS where the file picker greys out .ips files.

    Same parsing, sha256 duplicate detection and upsert logic as /api/upload.
    """
    content = payload.content or ""
    if not content.strip():
        raise HTTPException(422, "Pasted content is empty")
    if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Pasted content too large (max {MAX_UPLOAD_BYTES // 1024 // 1024} MB)")
    filename = _sanitize_filename(
        payload.filename, f"pasted-{datetime.now().strftime('%Y%m%d-%H%M%S')}.ips"
    )
    raw = content.encode("utf-8")
    with db.get_connection() as conn:
        if not conn.execute("SELECT 1 FROM devices WHERE id = ?", (payload.device_id,)).fetchone():
            raise HTTPException(404, "Device not found")
        parsed = parse_ips_file(content)
        result = _store_parsed_upload(conn, payload.device_id, filename, raw, parsed, date.today())
        return {
            "results": [
                {
                    "filename": result["filename"],
                    "status": result["status"],
                    "readings_found": result.get("readings_found", 0),
                    "new_readings": result.get("new_readings", 0),
                    "updated_readings": result.get("updated_readings", 0),
                    "unrecognized_keys": result.get("unrecognized_keys", []),
                    "cross_device_duplicate": result.get("cross_device_duplicate", []),
                }
            ]
        }


@app.get("/api/uploads")
def list_uploads(device_id: int | None = None):
    query = """SELECT u.id, u.device_id, u.filename, u.uploaded_at, u.readings_found,
                      d.name AS device_name,
                      (SELECT COUNT(*) FROM readings r WHERE r.upload_id = u.id) AS readings_count
               FROM uploads u JOIN devices d ON d.id = u.device_id"""
    params: list = []
    if device_id is not None:
        query += " WHERE u.device_id = ?"
        params.append(device_id)
    query += " ORDER BY u.uploaded_at DESC, u.id DESC"
    with db.get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@app.delete("/api/uploads/{upload_id}")
def delete_upload(upload_id: int):
    with db.get_connection() as conn:
        row = conn.execute("SELECT sha256 FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Upload not found")
        sha256 = row[0]
        # Only readings last touched by this upload are removed; readings
        # previously overwritten by a newer upload are left intact.
        conn.execute("DELETE FROM readings WHERE upload_id = ?", (upload_id,))
        conn.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
        db.delete_upload_files_if_orphaned(conn, sha256)
    return {"deleted": upload_id}


# ---------------- readings ----------------


@app.get("/api/readings")
def list_readings(
    device_id: int | None = None,
    limit: int = Query(default=2000, ge=1, le=50000),
):
    query = """SELECT r.*, d.name AS device_name, d.is_excluded AS device_excluded
               FROM readings r JOIN devices d ON d.id = r.device_id"""
    params: list = []
    if device_id is not None:
        query += " WHERE r.device_id = ?"
        params.append(device_id)
    query += " ORDER BY r.timestamp ASC LIMIT ?"
    params.append(limit)
    with db.get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@app.patch("/api/readings/{reading_id}")
def update_reading(reading_id: int, payload: ReadingPatch):
    with db.get_connection() as conn:
        cur = conn.execute(
            "UPDATE readings SET is_excluded = ? WHERE id = ?",
            (1 if payload.is_excluded else 0, reading_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Reading not found")
    return {"id": reading_id, "is_excluded": payload.is_excluded}


@app.delete("/api/readings/{reading_id}")
def delete_reading(reading_id: int):
    with db.get_connection() as conn:
        cur = conn.execute("DELETE FROM readings WHERE id = ?", (reading_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Reading not found")
    return {"deleted": reading_id}


# ---------------- backup (export / import) ----------------


@app.get("/api/export")
def export_backup(device_ids: str | None = Query(default=None)):
    """Export devices + readings as JSON. device_ids=1,2 selects a subset."""
    wanted: set[int] | None = None
    if device_ids is not None:
        try:
            wanted = {int(p) for p in device_ids.split(",") if p.strip()}
        except ValueError:
            raise HTTPException(422, "device_ids must be comma-separated integers")
        if not wanted:
            return {
                "app": "cyclewatch",
                "version": 1,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "devices": [],
            }
    with db.get_connection() as conn:
        if wanted is not None:
            placeholders = ",".join("?" for _ in wanted)
            devices = conn.execute(
                f"SELECT id, name, is_excluded FROM devices WHERE id IN ({placeholders})"
                " ORDER BY name COLLATE NOCASE",
                tuple(wanted),
            ).fetchall()
        else:
            devices = conn.execute(
                "SELECT id, name, is_excluded FROM devices ORDER BY name COLLATE NOCASE"
            ).fetchall()
        out_devices = []
        for dev in devices:
            readings = conn.execute(
                """SELECT timestamp, date_source, cycle_count, nominal_capacity_mah,
                          full_charge_capacity_mah, design_capacity_mah, health_pct,
                          battery_level_pct, min_soc_pct, max_soc_pct, is_excluded
                   FROM readings WHERE device_id = ? ORDER BY timestamp ASC LIMIT 50000""",
                (dev["id"],),
            ).fetchall()
            out_devices.append(
                {
                    "name": dev["name"],
                    "is_excluded": bool(dev["is_excluded"]),
                    "readings": [
                        {
                            "timestamp": r["timestamp"],
                            "date_source": r["date_source"],
                            "cycle_count": r["cycle_count"],
                            "nominal_capacity_mah": r["nominal_capacity_mah"],
                            "full_charge_capacity_mah": r["full_charge_capacity_mah"],
                            "design_capacity_mah": r["design_capacity_mah"],
                            "health_pct": r["health_pct"],
                            "battery_level_pct": r["battery_level_pct"],
                            "min_soc_pct": r["min_soc_pct"],
                            "max_soc_pct": r["max_soc_pct"],
                            "is_excluded": bool(r["is_excluded"]),
                        }
                        for r in readings
                    ],
                }
            )
    return {
        "app": "cyclewatch",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "devices": out_devices,
    }


@app.post("/api/import")
def import_backup(payload: BackupFile):
    """Merge a backup file. Matches devices by name, upserts readings by timestamp."""
    if payload.version > 1:
        raise HTTPException(422, f"Unsupported backup version {payload.version}")
    if payload.app not in ("cyclewatch", "battery-tracker"):
        raise HTTPException(422, f"Not a Cyclewatch backup file (app={payload.app!r})")
    total = sum(len(d.readings) for d in payload.devices)
    if total > MAX_READINGS:
        raise HTTPException(413, f"Backup too large ({total} readings, max {MAX_READINGS})")
    results = []
    with db.get_connection() as conn:
        for dev in payload.devices:
            name = dev.name.strip()
            if not name:
                results.append({"name": dev.name, "status": "skipped", "reason": "empty name"})
                continue
            row = conn.execute("SELECT id FROM devices WHERE name = ?", (name,)).fetchone()
            if row is None:
                try:
                    cur = conn.execute(
                        "INSERT INTO devices (name, is_excluded) VALUES (?, ?)",
                        (name, 1 if dev.is_excluded else 0),
                    )
                    device_id = cur.lastrowid
                    status = "created"
                except sqlite3.IntegrityError:
                    row2 = conn.execute(
                        "SELECT id FROM devices WHERE name = ?", (name,)
                    ).fetchone()
                    device_id = row2["id"]
                    status = "merged"
            else:
                device_id = row["id"]
                status = "merged"
                if dev.is_excluded:
                    conn.execute(
                        "UPDATE devices SET is_excluded = 1 WHERE id = ?", (device_id,)
                    )
            new_count = 0
            updated_count = 0
            skipped = 0
            for r in dev.readings:
                ts = parse_timestamp(r.timestamp) if r.timestamp else None
                reading = {
                    "timestamp": ts,
                    "date_source": r.date_source or "file",
                    "cycle_count": r.cycle_count,
                    "nominal_capacity_mah": r.nominal_capacity_mah,
                    "full_charge_capacity_mah": r.full_charge_capacity_mah,
                    "design_capacity_mah": r.design_capacity_mah,
                    "health_pct": r.health_pct,
                    "battery_level_pct": r.battery_level_pct,
                    "min_soc_pct": r.min_soc_pct,
                    "max_soc_pct": r.max_soc_pct,
                }
                try:
                    is_new = db.upsert_reading(conn, device_id, None, reading, date.today())
                except (ValueError, TypeError, sqlite3.Error):
                    skipped += 1
                    continue
                if is_new:
                    new_count += 1
                else:
                    updated_count += 1
                if r.is_excluded:
                    # upsert preserves is_excluded; enforce backup flag.
                    # Resolve the effective timestamp text the row was stored under.
                    eff = ts.isoformat() if ts else None
                    if eff is None:
                        # fallback rows use today's date at midnight
                        eff = datetime.combine(date.today(), datetime.min.time()).isoformat()
                    conn.execute(
                        "UPDATE readings SET is_excluded = 1 WHERE device_id = ? AND timestamp = ?",
                        (device_id, eff),
                    )
            results.append(
                {
                    "name": name,
                    "status": status,
                    "new_readings": new_count,
                    "updated_readings": updated_count,
                    "skipped": skipped,
                }
            )
    return {"results": results}


# ---------------- UI ----------------


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
