import importlib
import os
from datetime import date, datetime


def _fresh_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import database as db

    importlib.reload(db)
    db.init_db()
    from app import main as main_module

    importlib.reload(main_module)
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    return client, db


def _seed(client, db, device_id, rows):
    with db.get_connection() as conn:
        for ts, cycle, soc in rows:
            db.upsert_reading(
                conn,
                device_id,
                None,
                {
                    "timestamp": ts,
                    "date_source": "file",
                    "cycle_count": cycle,
                    "battery_level_pct": soc,
                    "full_charge_capacity_mah": 3000,
                    "design_capacity_mah": 3200,
                    "health_pct": 93.75,
                },
                date.today(),
            )


def test_export_selective_and_import_merge(tmp_path, monkeypatch):
    client, db = _fresh_client(tmp_path, monkeypatch)
    d1 = client.post("/api/devices", json={"name": "iPhone"}).json()
    d2 = client.post("/api/devices", json={"name": "iPad"}).json()
    _seed(
        client,
        db,
        d1["id"],
        [
            (datetime(2024, 3, 1, 10, 0, 0), 100, 10),
            (datetime(2024, 3, 2, 10, 0, 0), 101, 50),
        ],
    )
    _seed(client, db, d2["id"], [(datetime(2024, 3, 1, 10, 0, 0), 5, 80)])

    full = client.get("/api/export").json()
    assert full["version"] == 1
    assert {d["name"] for d in full["devices"]} == {"iPhone", "iPad"}

    one = client.get("/api/export", params={"device_ids": str(d1["id"])}).json()
    assert len(one["devices"]) == 1
    assert one["devices"][0]["name"] == "iPhone"
    assert len(one["devices"][0]["readings"]) == 2

    # Import selective payload into a fresh DB.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    client2, _ = _fresh_client(fresh, monkeypatch)
    res = client2.post("/api/import", json=one).json()
    assert res["results"][0]["new_readings"] == 2
    assert len(client2.get("/api/devices").json()) == 1

    # Re-import merges: 0 new, 2 updated.
    res2 = client2.post("/api/import", json=one).json()
    assert res2["results"][0]["new_readings"] == 0
    assert res2["results"][0]["updated_readings"] == 2


def test_import_rejects_future_version(tmp_path, monkeypatch):
    client, _ = _fresh_client(tmp_path, monkeypatch)
    payload = client.get("/api/export").json()
    payload["version"] = 99
    res = client.post("/api/import", json=payload)
    assert res.status_code == 422
