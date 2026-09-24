"""Iteração 3 — fase L: guard de probabilidade extrema (§37), WHY MODEL CHANGED (§39) e
performance por cluster/primárias (§27)."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from edgefut.analysis.changes import why_model_changed
from edgefut.backtesting.performance import performance_report
from edgefut.db.migrations import run_migrations
from edgefut.db.models import Event, PredictionSnapshot
from edgefut.db.session import get_engine, session_scope
from edgefut.domain.analysis import GoalsModelOutput, Recommendation
from edgefut.recommendations.engine import extreme_probability_guard
from edgefut.simulation.monte_carlo import simulate

from test_recommendations import _evaluate, _market_1x2


@pytest.fixture(scope="module", autouse=True)
def _db():
    run_migrations(get_engine())


def test_extreme_probability_penalizes_confidence_but_never_truncates():
    sim = simulate(GoalsModelOutput(model_version="t", lambda_home=4.8, lambda_away=0.25, rho=-0.05, fit_matches=400), 20_000, seed=3)
    market = _market_1x2(1.12)
    weak, _, _ = _evaluate(sim=sim, markets=[market], min_sample=12)
    home = next(r for r in weak if r.selection_key == "HOME")
    assert home.model_prob > 0.90  # não truncada
    assert "EXTREME_PROBABILITY" in home.reasons
    assert home.confidence_score < 85 * 0.75  # amostra fraca em time E mercado → fator 0,7

    strong, _, _ = _evaluate(sim=sim, markets=[_market_1x2(1.12)], min_sample=60, historical={"1X2": {"roi": 1.0, "n": 400}})
    home_s = next(r for r in strong if r.selection_key == "HOME")
    assert "EXTREME_PROBABILITY" not in home_s.reasons
    assert home_s.confidence_score > home.confidence_score

    assert extreme_probability_guard(0.85, 5, None) is None
    g = extreme_probability_guard(0.95, 60, {"n": 10})
    assert g is not None and g["confidence_factor"] == 0.85  # só um dos dois critérios


def _rec(prob: float, odd: float, state: str = "VALUE_CANDIDATE", calibrated: bool = False) -> Recommendation:
    return Recommendation(
        market_key="1X2", market_label="1X2", selection_key="HOME", selection_name="1", line=None, odd=odd, model_prob=prob,
        market_prob=0.5, market_prob_is_fair=True, edge_pp=100 * (prob - 0.5), ev_pct=0.0, confidence_score=70, confidence_grade="B", opportunity_score=50,
        status="RECOMMENDED", reasons=[], category="RESULTADO", state=state, calibration_reliable=calibrated,
    )


def _analysis(prob: float, odd: float, *, home_n: int = 30, attack: float = 1.1, elo: float = 1500.0, versions: dict | None = None, champion: str = "ensemble"):
    team = lambda n: SimpleNamespace(sample_size=n, ratings_v2={"attack": attack, "defense": 0.9}, elo=elo)  # noqa: E731
    return SimpleNamespace(
        recommendations=[_rec(prob, odd)], home=team(home_n), away=team(30),
        model_versions=versions or {"poisson": "v1"}, pipeline_version="pipeline-v3", champion=champion,
    )


def test_why_model_changed_detects_drivers():
    eid = 990001
    now = datetime.utcnow()
    with session_scope() as s:
        if s.get(Event, eid) is None:
            s.add(Event(id=eid, home_name="H", away_name="A", kickoff_utc=now + timedelta(days=1), status="prematch", market_count=1))
        s.flush()
        first = why_model_changed(s, eid, _analysis(0.55, 2.0))
        assert first["status"] == "FIRST_ANALYSIS" and first["drivers"] == []
        s.add(PredictionSnapshot(
            event_id=eid, kickoff_utc=now + timedelta(days=1), home_name="H", away_name="A", competition_name="T",
            model_version="pipeline-v3", model_versions={"poisson": "v1"},
            features={"home": {"sample_size": 30, "ratings_v2": {"attack": 1.1, "defense": 0.9}, "elo": 1500.0}, "away": {"sample_size": 30, "ratings_v2": {"attack": 1.1, "defense": 0.9}, "elo": 1500.0}},
            probabilities={"model_comparison": {"champion": "ensemble"}},
            odds=[{"market_key": "1X2", "line": None, "selections": [{"key": "HOME", "price": 2.0}]}],
            recommendations=[_rec(0.55, 2.0).model_dump(mode="json")],
        ))
        s.flush()
        same = why_model_changed(s, eid, _analysis(0.552, 2.0))
        assert same["status"] == "COMPARED" and same["drivers"] == ["NONE"] and same["selections"] == []

        moved = why_model_changed(s, eid, _analysis(0.55, 2.3))
        assert moved["drivers"] == ["ODDS_MOVED"] and moved["selections"][0]["odd_after"] == 2.3

        new_match = why_model_changed(s, eid, _analysis(0.60, 2.0, home_n=31))
        assert "NEW_MATCHES" in new_match["drivers"] and new_match["selections"][0]["delta_pp"] == 5.0

        ratings = why_model_changed(s, eid, _analysis(0.58, 2.0, attack=1.25))
        assert "RATINGS_CHANGED" in ratings["drivers"] and "NEW_MATCHES" not in ratings["drivers"]

        gov = why_model_changed(s, eid, _analysis(0.55, 2.0, versions={"poisson": "v2"}, champion="ensemble_v2"))
        assert {"MODEL_VERSION", "CHAMPION_CHANGED"} <= set(gov["drivers"])
        assert "Maior mudança" not in gov["text"] or gov["selections"]
        s.rollback()


def test_performance_report_separates_primaries_from_alternatives():
    eid = 990002
    now = datetime.utcnow()
    with session_scope() as s:
        if s.get(Event, eid) is None:
            s.add(Event(id=eid, home_name="H", away_name="A", kickoff_utc=now - timedelta(days=2), status="finished", market_count=1))
        recs = [
            {"market_key": "1X2", "selection_key": "HOME", "line": None, "model_prob": 0.6, "odd": 2.0, "market_label": "1X2", "status": "RECOMMENDED", "is_primary": True, "cluster_id": "HOME_TEAM_POSITIVE", "state": "VALUE_CANDIDATE"},
            {"market_key": "DNB", "selection_key": "HOME", "line": None, "model_prob": 0.7, "odd": 1.5, "market_label": "DNB", "status": "RECOMMENDED", "is_primary": False, "cluster_id": "HOME_TEAM_POSITIVE", "state": "VALUE_CANDIDATE"},
            {"market_key": "TOTAL_CORNERS", "selection_key": "OVER", "line": 9.5, "model_prob": 0.55, "odd": 1.9, "market_label": "Escanteios", "status": "WATCH", "is_primary": True, "cluster_id": "CORNERS_HIGH", "state": "OBSERVATION"},
        ]
        s.add(PredictionSnapshot(
            event_id=eid, kickoff_utc=now - timedelta(days=2), home_name="H", away_name="A", competition_name="T",
            model_version="pipeline-v3", model_versions={}, features={}, probabilities={}, odds=[], recommendations=recs,
            result={"outcomes": {"1X2|HOME|None": True, "DNB|HOME|None": True, "TOTAL_CORNERS|OVER|9.5": False}},
        ))
        s.flush()
        rep = performance_report(s)
        svc = rep["selection_vs_cluster"]
        assert svc["primaries_only"]["bets"] >= 1 and svc["alternatives_only"]["bets"] >= 1
        assert svc["all_selections"]["bets"] == svc["primaries_only"]["bets"] + svc["alternatives_only"]["bets"]
        assert "HOME_TEAM_POSITIVE" in rep["by_cluster"]
        corners = rep["secondary_markets"]["TOTAL_CORNERS"]
        assert corners["bets"] >= 1 and corners["verdict"] == "INSUFFICIENT"
        assert rep["overall"]["roi_ci"] is None or {"low", "high"} <= set(rep["overall"]["roi_ci"])
        s.rollback()
