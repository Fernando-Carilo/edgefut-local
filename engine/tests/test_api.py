import pytest
from fastapi.testclient import TestClient

from edgefut.api.app import app
from edgefut.core.config import settings


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_engine_binds_loopback_only():
    assert settings.host == "127.0.0.1"
    assert settings.host_is_loopback
    with pytest.raises(Exception):
        settings.host = "0.0.0.0"  # frozen


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["host"] == "127.0.0.1"
    assert "scheduler" in body


def test_events_empty_db_is_honest(client):
    r = client.get("/events?window=48h")
    assert r.status_code == 200
    assert r.json()["total"] == 0
    assert client.get("/events/123456789").status_code == 404
    assert client.get("/events/123456789/analysis").status_code == 404


def test_live_is_not_faked(client):
    body = client.get("/live").json()
    assert body["available"] is False and body["events"] == []


def test_simulator_and_stake(client):
    r = client.post("/simulator", json={"stake": 100, "odd": 2.5, "model_prob": 0.52})
    assert r.status_code == 200
    b = r.json()
    assert b["gross_return"] == 250 and b["profit"] == 150 and b["implied_probability"] == 0.4
    assert b["ev_pct"] == 30.0 and "não realiza apostas" in b["note"]

    r = client.post("/bankroll/stake", json={"bankroll": 1000, "odd": 2.5, "model_prob": 0.52, "method": "kelly", "kelly_fraction": 0.9})
    b = r.json()
    assert b["kelly_fraction_used"] <= 0.25
    assert b["kelly_full_pct"] == 20.0
    assert b["warning"]


def test_settings_roundtrip_caps_kelly(client):
    r = client.put("/settings", json={"user_name": "Fernando", "default_simulations": 12345, "kelly_fraction_max": 0.9})
    assert r.status_code == 200
    b = r.json()
    assert b["kelly_fraction_max"] == 0.25
    assert b["default_simulations"] == 50_000
    assert client.get("/settings").json()["user_name"] == "Fernando"


def test_models_and_sources(client):
    m = client.get("/models").json()
    assert m["versions"]["dixon_coles"] == "goals-dixon-coles-v1"
    assert len(m["models"]) >= 10
    s = client.get("/sources").json()
    assert {p["key"] for p in s["providers"]} >= {"superbet", "football_data", "international_results"}
    assert all("apostas" not in (p.get("notes") or "").lower() or "não" in p["notes"].lower() for p in s["providers"])


def test_radar_and_dashboard_shape(client):
    r = client.get("/radar").json()
    assert {c["key"] for c in r["cards"]} >= {"top", "high_confidence", "gols", "escanteios", "cartoes", "finalizacoes", "valor", "observacao"}
    d = client.get("/dashboard").json()
    assert d["user_name"] == "Fernando"
    assert d["greeting"] in {"Bom dia", "Boa tarde", "Boa noite"}


def test_chat_requires_analyzed_event(client):
    r = client.post("/chat", json={"event_id": 1, "question": "quem ganha?"})
    assert r.status_code in (404, 400)


def test_live_snapshot_without_poll_is_honest(client):
    body = client.get("/live").json()
    assert body["mode"] == "OBSERVATION_ONLY"
    assert body["available"] is False and body["events"] == []
    assert body["freshness"]["status"] == "UNAVAILABLE"


def test_health_system_components(client):
    r = client.get("/health/system")
    assert r.status_code == 200
    b = r.json()
    assert b["overall"] in {"HEALTHY", "DEGRADED", "UNAVAILABLE", "STALE"}
    keys = {c["key"] for c in b["components"]}
    assert {"superbet", "fixtures", "historical", "results", "database", "duckdb", "scheduler", "ollama", "cache", "player_data"} <= keys
    player = next(c for c in b["components"] if c["key"] == "player_data")
    assert player["status"] == "UNAVAILABLE"


def test_jobs_alerts_calibration_endpoints(client):
    j = client.get("/jobs").json()
    assert j["scheduler_running"] is False and isinstance(j["runs"], list) and "sync_events" in j["labels"]
    assert client.post("/jobs/nao-existe/run").status_code == 404
    a = client.get("/alerts").json()
    assert a["alerts"] == [] and "ODD_MOVEMENT" in a["kinds"]
    c = client.get("/calibration").json()
    assert c["min_n"] == 300 and c["diagram"]["sample"] == 0 and c["diagram"]["reliable"] is False
    assert len(c["diagram"]["buckets"]) == 10 and c["diagram"]["buckets"][0]["bucket"] == "0-10"
    assert client.get("/events/123456789/conflicts").status_code == 404
