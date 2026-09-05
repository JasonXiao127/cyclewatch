import importlib
import json


def _fresh_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import database as db

    importlib.reload(db)
    db.init_db()
    from app import main as main_module

    importlib.reload(main_module)
    from fastapi.testclient import TestClient

    return TestClient(main_module.app)


def _make_ips():
    header = {"bug_type": "211", "timestamp": "2026-07-18 20:00:00.00 -0400"}
    e1 = {"message": {"last_value_CycleCount": 1413,
                      "last_value_NominalChargeCapacity": 3240,
                      "last_value_AppleRawMaxCapacity": 3482,
                      "last_value_MaximumCapacityPercent": 74}}
    lines = [json.dumps(header), json.dumps(e1)]
    return "\n".join(lines)


def test_paste_full_file_ok(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    device = client.post("/api/devices", json={"name": "iPhone"}).json()
    res = client.post("/api/upload-text", json={
        "device_id": device["id"],
        "filename": "Analytics-2026-07-18.ips.synced",
        "content": _make_ips(),
    })
    assert res.status_code == 200
    body = res.json()["results"][0]
    assert body["status"] == "ok"
    assert body["new_readings"] == 1
    assert len(client.get("/api/readings").json()) == 1


def test_paste_duplicate(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    device = client.post("/api/devices", json={"name": "iPhone"}).json()
    payload = {"device_id": device["id"], "filename": "a.ips", "content": _make_ips()}
    assert client.post("/api/upload-text", json=payload).status_code == 200
    res2 = client.post("/api/upload-text", json=payload)
    assert res2.json()["results"][0]["status"] == "duplicate"


def test_paste_empty_rejected(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    device = client.post("/api/devices", json={"name": "iPhone"}).json()
    res = client.post("/api/upload-text", json={
        "device_id": device["id"], "filename": "x.ips", "content": "   "})
    assert res.status_code == 422


def test_paste_unknown_device(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    res = client.post("/api/upload-text", json={
        "device_id": 9999, "filename": "x.ips", "content": _make_ips()})
    assert res.status_code == 404
