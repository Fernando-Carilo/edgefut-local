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
    assert {c["key"] for c in r["cards"]} >= {"top", "high_probability", "high_confidence", "gols", "escanteios", "cartoes", "finalizacoes", "valor", "observacao", "no_bet"}
    s = r["summary"]
    assert {"events_found", "with_sufficient_data", "analyzed", "quality_gate_passed", "confidence_a", "confidence_b", "high_probability", "value", "watch", "no_bet", "stale", "alerts_unread"} <= set(s)
    assert s["analyzed"] == r["analyzed_events"] == 0  # banco vazio: contadores honestos
    assert r["thresholds"]["gate_min_confidence"] >= 50
    d = client.get("/dashboard").json()
    assert d["user_name"] == "Fernando"
    assert d["greeting"] in {"Bom dia", "Boa tarde", "Boa noite"}
    assert d["cta"] == "VER RADAR"
    assert "Fernando" in d["morning_summary"] and ("ainda não retornou" in d["morning_summary"] or "primeira análise" in d["morning_summary"])
    from edgefut.recommendations.why import assert_no_guarantee_language

    assert_no_guarantee_language([d["morning_summary"]])


def test_settings_floors_block_threshold_hunting(client):
    r = client.put("/settings", json={"min_edge_pp": 0.1, "min_ev_pct": 0.0, "gate_min_confidence": 10, "gate_min_data_quality": 5, "gate_max_disagreement_pp": 80, "opportunity_weights": {"edge": 5, "bogus": 9, "ev": -3}})
    assert r.status_code == 200
    b = r.json()
    assert b["min_edge_pp"] >= 1.0 and b["min_ev_pct"] >= 1.0
    assert b["gate_min_confidence"] >= 50 and b["gate_min_data_quality"] >= 40
    assert b["gate_max_disagreement_pp"] <= 15
    assert b["opportunity_weights"] == {"edge": 5.0, "ev": 0.0}
    # volta ao padrão para não contaminar os outros testes
    client.put("/settings", json={"user_name": "Fernando"})


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


def test_sources_v2_cards_are_honest_about_player_data(client):
    s = client.get("/sources").json()
    cards = {c["key"]: c for c in s["cards"]}
    assert {"superbet", "superbet_live", "fixtures", "football_data", "international_results", "results", "player_data", "ollama"} <= set(cards)
    assert cards["player_data"]["status"] == "UNAVAILABLE" and "PLAYER DATA UNAVAILABLE" in cards["player_data"]["summary"]
    assert "odds históricas" in " ".join(cards["international_results"]["does_not_provide"])
    for c in cards.values():
        assert c["status"] in {"HEALTHY", "DEGRADED", "STALE", "UNAVAILABLE"}


def test_player_engine_is_architecture_only():
    from edgefut.providers.player import PlayerProvider, player_market_verdict, registry

    assert registry().available is False and registry().status == "PLAYER DATA UNAVAILABLE"
    reason, detail = player_market_verdict()
    assert reason == "LINEUP_UNCERTAINTY" and "PLAYER DATA UNAVAILABLE" in detail
    import pytest

    with pytest.raises(TypeError):
        PlayerProvider()  # abstrato: não pode ser instanciado sem implementar o contrato


def test_cache_key_changes_with_thresholds_and_pipeline_version():
    from edgefut.analysis.pipeline import thresholds_hash
    from edgefut.core.config import settings

    a = thresholds_hash()
    old = settings.min_edge_pp
    settings.min_edge_pp = old + 1
    try:
        assert thresholds_hash() != a
    finally:
        settings.min_edge_pp = old
    assert thresholds_hash() == a
