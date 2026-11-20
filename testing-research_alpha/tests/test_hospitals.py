"""医院推荐 / 距离计算测试。"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.db.database import query_all
from app.main import app
from app.modules.geo import haversine_km


@pytest.fixture
def client(monkeypatch):
    # 不依赖真实高德 Key；强制走本地医院表
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(amap_api_key=""))
    with TestClient(app) as c:
        yield c


def test_haversine_same_point_is_zero():
    assert haversine_km(26.074, 119.296, 26.074, 119.296) == 0


def test_haversine_one_degree_latitude():
    d = haversine_km(26.0, 119.0, 27.0, 119.0)
    assert 110 < d < 112


def test_hospital_table_seeded_with_coordinates():
    rows = query_all("SELECT name, latitude, longitude, address FROM hospitals")
    assert len(rows) >= 6
    for row in rows:
        assert row["latitude"] is not None
        assert row["longitude"] is not None
        assert row["address"]


def test_nearby_hospitals_sorted_and_have_map_url(client):
    resp = client.get("/api/v1/hospitals/nearby", params={"lat": 26.074, "lng": 119.296, "limit": 4})
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "local"
    assert len(data["items"]) == 4
    distances = [item["distance_km"] for item in data["items"]]
    assert distances == sorted(distances)
    for item in data["items"]:
        assert item["name"]
        assert item["map_url"].startswith("https://uri.amap.com/marker")
        assert "position=" in item["map_url"]


def test_nearby_hospitals_invalid_coordinates(client):
    assert client.get("/api/v1/hospitals/nearby?lat=91&lng=119").status_code == 422
    assert client.get("/api/v1/hospitals/nearby?lat=26&lng=181").status_code == 422


def test_saved_location_crud(client):
    client.delete("/api/v1/preferences/location")
    assert client.get("/api/v1/preferences/location").status_code == 204

    resp = client.put(
        "/api/v1/preferences/location",
        json={
            "label": "福州大学旗山校区",
            "address": "闽侯县上街镇乌龙江北大道2号",
            "latitude": 26.057439,
            "longitude": 119.193555,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["label"] == "福州大学旗山校区"

    got = client.get("/api/v1/preferences/location")
    assert got.status_code == 200
    assert got.json()["latitude"] == 26.057439

    assert client.delete("/api/v1/preferences/location").status_code == 204
    assert client.get("/api/v1/preferences/location").status_code == 204


def test_geocode_requires_amap_key(client, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(amap_api_key=""))
    assert client.get("/api/v1/hospitals/geocode", params={"address": "旗山校区"}).status_code == 503


def test_geocode_resolves_address(client, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(amap_api_key="test-key"))
    monkeypatch.setattr(
        routes,
        "amap_input_tip",
        lambda address, key: {
            "label": "福州大学旗山校区(西门)",
            "address": "上街镇乌龙江北大道2号",
            "latitude": 26.057439,
            "longitude": 119.193555,
            "source": "amap",
        },
    )
    resp = client.get("/api/v1/hospitals/geocode", params={"address": "旗山校区"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["label"].startswith("福州大学旗山校区")
    assert data["latitude"] == 26.057439
