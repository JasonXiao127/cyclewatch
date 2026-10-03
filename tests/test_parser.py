import json
from datetime import datetime

from app.parser import parse_ips_file


def make_ips(entries, header=None):
    header = header or {"bug_type": "211", "os_version": "iOS 17.4.1"}
    lines = [json.dumps(header)] + [json.dumps(entry) for entry in entries]
    return "\n".join(lines)


def test_extracts_nested_battery_keys():
    entry = {
        "timestamp": "2024-03-01 10:00:00",
        "log_type": "UserEvent",
        "payload": {
            "device": {"name": "iPhone"},
            "battery": {
                "BatteryCycleCount": 152,
                "NominalChargeCapacity": 3006,
                "DesignCapacity": 3279,
            },
        },
    }
    result = parse_ips_file(make_ips([entry]))
    assert len(result["readings"]) == 1
    reading = result["readings"][0]
    assert reading["cycle_count"] == 152
    assert reading["nominal_capacity_mah"] == 3006
    assert reading["design_capacity_mah"] == 3279
    assert reading["date_source"] == "file"
    assert reading["timestamp"] == datetime(2024, 3, 1, 10, 0, 0)


def test_entries_sharing_timestamp_are_merged():
    a = {"timestamp": "2024-03-01 10:00:00", "p": {"battery": {"BatteryCycleCount": 10}}}
    b = {"timestamp": "2024-03-01 10:00:00", "p": {"battery": {"NominalChargeCapacity": 3000}}}
    c = {"timestamp": "2024-03-02 10:00:00", "p": {"battery": {"BatteryCycleCount": 11}}}
    result = parse_ips_file(make_ips([a, b, c]))
    assert len(result["readings"]) == 2
    merged = next(r for r in result["readings"] if r["cycle_count"] == 10)
    assert merged["nominal_capacity_mah"] == 3000


def test_missing_timestamp_falls_back():
    entry = {"p": {"battery": {"BatteryCycleCount": 5}}}
    result = parse_ips_file(make_ips([entry]))
    reading = result["readings"][0]
    assert reading["timestamp"] is None
    assert reading["date_source"] == "fallback"


def test_header_captured():
    header = {"bug_type": "211", "os_version": "iOS 18.0"}
    result = parse_ips_file(make_ips([], header=header))
    assert result["header"] == header
    assert result["readings"] == []


def test_unrecognized_battery_keys_are_reported():
    entry = {
        "timestamp": "2024-03-01 10:00:00",
        "p": {"battery": {"BatteryNewMysteryMetric": 42, "BatteryCycleCount": 3}},
    }
    result = parse_ips_file(make_ips([entry]))
    assert "BatteryNewMysteryMetric" in result["unrecognized_keys"]


def test_malformed_lines_are_skipped():
    text = (
        "{not json}\n"
        + json.dumps({"timestamp": "2024-03-01 10:00:00", "p": {"BatteryCycleCount": 7}})
        + "\n[broken"
    )
    result = parse_ips_file(text)
    assert len(result["readings"]) == 1
    assert result["readings"][0]["cycle_count"] == 7


def test_string_numbers_coerced():
    entry = {"timestamp": "2024-03-01 10:00:00", "p": {"BatteryCycleCount": "42"}}
    result = parse_ips_file(make_ips([entry]))
    assert result["readings"][0]["cycle_count"] == 42


def test_readings_sorted_by_timestamp():
    entries = [
        {"timestamp": "2024-03-03 10:00:00", "p": {"BatteryCycleCount": 3}},
        {"timestamp": "2024-03-01 10:00:00", "p": {"BatteryCycleCount": 1}},
        {"timestamp": "2024-03-02 10:00:00", "p": {"BatteryCycleCount": 2}},
    ]
    result = parse_ips_file(make_ips(entries))
    assert [r["cycle_count"] for r in result["readings"]] == [1, 2, 3]


def test_api_roundtrip(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import database as db

    importlib.reload(db)
    db.init_db()
    from app import main as main_module

    importlib.reload(main_module)
    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as client:
        device = client.post("/api/devices", json={"name": "Test iPhone"}).json()
        ips = make_ips(
            [
                {
                    "timestamp": "2024-03-01 10:00:00",
                    "p": {
                        "battery": {
                            "BatteryCycleCount": 100,
                            "NominalChargeCapacity": 3000,
                            "DesignCapacity": 3279,
                        }
                    },
                }
            ]
        )
        res = client.post(
            "/api/upload",
            data={"device_id": str(device["id"])},
            files={"files": ("Analytics-2024-03-01.ips", ips.encode(), "text/plain")},
        )
        assert res.status_code == 200
        body = res.json()["results"][0]
        assert body["status"] == "ok"
        assert body["new_readings"] == 1

        readings = client.get("/api/readings").json()
        assert len(readings) == 1
        assert readings[0]["device_name"] == "Test iPhone"
        assert readings[0]["health_pct"] == round(100 * 3000 / 3279, 2)

        assert readings[0]["cycle_count"] == 100

        # Same file again -> duplicate, no new readings.
        res2 = client.post(
            "/api/upload",
            data={"device_id": str(device["id"])},
            files={"files": ("Analytics-2024-03-01.ips", ips.encode(), "text/plain")},
        )
        assert res2.json()["results"][0]["status"] == "duplicate"
        assert len(client.get("/api/readings").json()) == 1

        # Exclude toggle + delete.
        reading_id = readings[0]["id"]
        client.patch(f"/api/readings/{reading_id}", json={"is_excluded": True})
        assert client.get("/api/readings").json()[0]["is_excluded"] == 1
        client.delete(f"/api/readings/{reading_id}")
        assert client.get("/api/readings").json() == []

        # Delete device cascades.
        client.delete(f"/api/devices/{device['id']}")
        assert client.get("/api/devices").json() == []

REAL_STYLE_HEADER = {
    "bug_type": "211",
    "timestamp": "2026-07-18 20:00:00.00 -0400",
    "os_version": "iPhone OS 26.5.2 (23F84)",
}


def test_last_value_prefixed_keys_merge_into_one_reading():
    e1 = {
        "aggregationPeriod": "Daily",
        "name": "BatteryConfigValueHistogramFinal_V2",
        "message": {
            "last_value_CycleCount": 1413,
            "last_value_NominalChargeCapacity": 3240,
            "last_value_AppleRawMaxCapacity": 3482,
            "last_value_MaximumCapacityPercent": 74,
            "last_value_UISOC": 3,
            "last_value_BatterySerialChanged": False,
        },
    }
    e2 = {
        "aggregationPeriod": "Daily",
        "name": "BatteryShutdownHistogram",
        "message": {
            "last_value_FullChargeCapacity": 3406,
            "last_value_DailyMinSoc": 5,
            "last_value_DailyMaxSoc": 45,
            "last_value_NominalChargeCapacityPrevious": 3240,
        },
    }
    result = parse_ips_file(make_ips([e1, e2], header=REAL_STYLE_HEADER))
    assert len(result["readings"]) == 1
    reading = result["readings"][0]
    assert reading["cycle_count"] == 1413
    assert reading["nominal_capacity_mah"] == 3240
    assert reading["full_charge_capacity_mah"] == 3482
    assert reading["health_pct"] == 74
    assert reading["battery_level_pct"] == 3
    assert reading["min_soc_pct"] == 5
    assert reading["max_soc_pct"] == 45
    assert reading["date_source"] == "file"


def test_header_timestamp_used_when_entry_has_none():
    entry = {"message": {"last_value_CycleCount": 5}}
    result = parse_ips_file(make_ips([entry], header=REAL_STYLE_HEADER))
    reading = result["readings"][0]
    assert reading["timestamp"] == datetime(2026, 7, 18, 20, 0, 0)
    assert reading["date_source"] == "file"


def test_fallback_when_neither_entry_nor_header_has_timestamp():
    entry = {"message": {"last_value_CycleCount": 9}}
    result = parse_ips_file(make_ips([entry], header={"bug_type": "211"}))
    reading = result["readings"][0]
    assert reading["timestamp"] is None
    assert reading["date_source"] == "fallback"


def test_plain_keys_still_supported():
    entry = {
        "timestamp": "2024-03-01 10:00:00",
        "p": {"BatteryCycleCount": 42, "NominalChargeCapacity": 3000, "DesignCapacity": 3279},
    }
    result = parse_ips_file(make_ips([entry]))
    reading = result["readings"][0]
    assert reading["cycle_count"] == 42
    assert reading["nominal_capacity_mah"] == 3000
    assert reading["design_capacity_mah"] == 3279


def test_unrecognized_report_includes_prefixed_keys():
    entry = {
        "message": {
            "last_value_BatteryNewMysteryMetric": 42,
            "last_value_CycleCount": 3,
        }
    }
    result = parse_ips_file(make_ips([entry], header=REAL_STYLE_HEADER))
    assert "last_value_BatteryNewMysteryMetric" in result["unrecognized_keys"]


def test_charge_level_extracted_old_and_new_formats():
    # Newer format: UISOC + daily min/max SOC.
    new_entry = {"message": {"last_value_UISOC": 41, "last_value_DailyMinSoc": 12, "last_value_DailyMaxSoc": 80}}
    reading = parse_ips_file(make_ips([new_entry], header=REAL_STYLE_HEADER))["readings"][0]
    assert reading["battery_level_pct"] == 41
    assert reading["min_soc_pct"] == 12
    assert reading["max_soc_pct"] == 80

    # Older format: plain BatteryLevel key.
    old_entry = {"timestamp": "2024-03-01 10:00:00", "p": {"BatteryLevel": 55, "BatteryCycleCount": 9}}
    reading = parse_ips_file(make_ips([old_entry]))["readings"][0]
    assert reading["battery_level_pct"] == 55
