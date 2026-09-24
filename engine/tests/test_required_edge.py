"""Iteração 4 — RequiredEdgeEngine, intervalo de incerteza, regra EDGE_NOT_ROBUST (só mais conservadora),
market_view/selection_lookup e HistoricalQualityGate do replay."""

from __future__ import annotations

from edgefut.core.config import settings
from edgefut.recommendations.required_edge import (
    RequiredEdgeInputs,
    UncertaintyInputs,
    required_edge,
    uncertainty_interval,
)


def test_uncertainty_interval_components():
    u = uncertainty_interval(UncertaintyInputs(model_prob=0.5, member_probs=[0.46, 0.50, 0.54], replay_ece=0.01, min_sample=100))
    assert u["n_members"] == 3
    assert abs(u["components_pp"]["member_spread"] - 4.0) < 1e-6
    assert abs(u["components_pp"]["calibration"] - 1.0) < 1e-6
    assert abs(u["components_pp"]["sample"] - 2.5) < 1e-6  # √(0,25/100)·0,5 = 2,5 pp
    assert u["half_width_pp"] > max(u["components_pp"].values())  # quadratura
    assert u["low"] < 0.5 < u["high"]
    # sem membros e sem replay → ECE por defeito, nunca zero
    u0 = uncertainty_interval(UncertaintyInputs(model_prob=0.9, member_probs=[], replay_ece=None, min_sample=5))
    assert u0["components_pp"]["member_spread"] == 0 and u0["components_pp"]["calibration"] == 2.0 and u0["half_width_pp"] > 2


def test_required_edge_spec_example_is_observation():
    """Spec §24: bruto +5,2 pp, margem 5,1 %, incerteza 2,4 pp → required ≈ 7,8 pp → não robusto."""
    r = required_edge(RequiredEdgeInputs(edge_raw_pp=5.2, market_overround=0.051, uncertainty_half_pp=2.4, calibration_reliable=True, market_oos_n=1000, market_efficiency_status=None))
    assert r["components_pp"]["margin"] == 5.1 and r["components_pp"]["uncertainty"] == 2.4
    assert 7.4 <= r["required_pp"] <= 7.9 and not r["robust"]
    assert abs(r["edge_adjusted_pp"] - 2.8) < 1e-9
    ok = required_edge(RequiredEdgeInputs(edge_raw_pp=9.0, market_overround=0.051, uncertainty_half_pp=2.4, calibration_reliable=True, market_oos_n=1000, market_efficiency_status=None))
    assert ok["robust"]


def test_required_edge_penalties_only_raise_the_bar():
    base = RequiredEdgeInputs(edge_raw_pp=8.0, market_overround=0.05, uncertainty_half_pp=1.0, calibration_reliable=True, market_oos_n=1000, market_efficiency_status=None)
    r0 = required_edge(base)
    r1 = required_edge(RequiredEdgeInputs(8.0, 0.05, 1.0, False, 10, "NO EVIDENCE OF MARKET EDGE"))
    assert r1["required_pp"] > r0["required_pp"]
    assert r1["components_pp"]["calibration"] > 0 and r1["components_pp"]["sample"] > 0 and r1["components_pp"]["market_efficiency"] > 0
    # nunca abaixo do limiar configurado
    r2 = required_edge(RequiredEdgeInputs(1.0, 0.0, 0.0, True, 1000, None))
    assert r2["required_pp"] >= settings.min_edge_pp
    # overround exótico tem teto
    r3 = required_edge(RequiredEdgeInputs(1.0, 0.9, 0.0, True, 1000, None))
    assert r3["components_pp"]["margin"] == 15.0


def test_engine_blocks_value_when_edge_not_robust(monkeypatch):
    """Uma seleção que passaria a VALUE fica em OBSERVATION quando o edge bruto < required edge.
    A regra nunca cria VALUE: sem os novos inputs o resultado é idêntico ao anterior."""
    from edgefut.recommendations import engine as eng
    from tests.test_recommendations import _evaluate  # fixture partilhada dos testes de iteração 3

    # prova OOS suficiente no 1X2 → a seleção HOME (odd 2,4, modelo ≈ 0,56) chega a VALUE
    oos = {"1X2": {"n": settings.value_min_oos_bets + 50, "verdict": "POSITIVE", "source": "test"}}
    before, _, _ = _evaluate(oos=oos)
    value_before = [r for r in before if r.state == "VALUE"]
    assert value_before, "fixture precisa de pelo menos uma seleção VALUE para o teste fazer sentido"
    assert all(r.required_edge is not None and r.required_edge["robust"] and r.uncertainty is not None for r in value_before)
    # força incerteza gigante → required edge impossível
    monkeypatch.setattr(eng, "uncertainty_interval", lambda inp: {"low": 0, "high": 1, "half_width_pp": 60.0, "components_pp": {"member_spread": 60.0, "calibration": 0, "sample": 0}, "n_members": 0})
    after, _, _ = _evaluate(oos=oos)
    assert not [r for r in after if r.state == "VALUE"]
    blocked = [r for r in after if "EDGE_NOT_ROBUST" in r.reasons]
    assert blocked and all(r.state == "OBSERVATION" and r.status == "WATCH" for r in blocked)
    assert all(r.required_edge is not None and not r.required_edge["robust"] for r in blocked)


def test_selection_lookup_only_for_1x2_and_ou25():
    from edgefut.analysis.market_view import selection_lookup

    view = {"markets": {"1X2": {"residual_validated": False, "validation_status": "NO EVIDENCE OF MARKET EDGE", "selections": {"HOME": {"market": 0.5, "edgefut": 0.55, "hybrid": 0.5, "disagreement_pp": 5.0, "residual_edge_pp": 0.0}}},
                        "OU25": {"residual_validated": False, "validation_status": "NO EVIDENCE OF MARKET EDGE", "selections": {"OVER": {"market": 0.5, "edgefut": 0.6, "hybrid": 0.5, "disagreement_pp": 10.0, "residual_edge_pp": 0.0}}}}}
    assert selection_lookup(view, "1X2", "HOME", None)["disagreement_pp"] == 5.0
    assert selection_lookup(view, "TOTAL_GOALS", "OVER", 2.5)["validated"] is False
    assert selection_lookup(view, "TOTAL_GOALS", "OVER", 3.5) is None
    assert selection_lookup(view, "BTTS", "YES", None) is None
    assert selection_lookup(None, "1X2", "HOME", None) is None


def test_market_view_uses_frozen_artifact_and_reports_status(tmp_path, monkeypatch):
    import numpy as np

    from edgefut.analysis import market_view as mv
    from edgefut.validation import market_aware as ma

    # artefato mínimo: só blend, α = 1 → híbrido = mercado, residual = 0 (é exatamente o que α=1 significa)
    art = {"version": "market-aware-frozen-v1", "config_hash": "c", "dataset_version": "d", "holdout_start": "2026-01-01", "base_model": "ensemble", "frozen_at": "x", "model_hash": "m",
           "markets": {"1X2": {"alpha": 1.0, "datasets": ["E0"], "selected": "blend", "train_rows": 10, "coefficients": {}}}}
    monkeypatch.setattr(mv, "load_active_artifact", lambda: art)
    monkeypatch.setattr(mv, "validation_status", lambda session, max_age_s=300: {"source": "holdout", "markets": {"1X2": "NO EVIDENCE OF MARKET EDGE"}, "model_hash": "m"})
    monkeypatch.setattr(mv, "predict_frozen", lambda a, market, pm, pe, features=None: {"blend": ma.blend(pm, pe, 1.0)})
    from datetime import datetime
    feats = {"dataset": "E0", "neutral": False, "elo_diff": 10.0, "att_diff": None, "def_diff": None, "home_advantage": 1.2, "form5_home": 1.5, "form5_away": 1.0, "gd5_home": 1.0, "gd5_away": -1.0, "min_team_sample": 30, "agreement_pp": 3.0, "kickoff": datetime(2026, 9, 1)}
    out = mv.market_view(None, p_market_1x2=(0.5, 0.25, 0.25), p_edgefut_1x2=(0.6, 0.2, 0.2), p_market_over25=None, p_edgefut_over25=None, features=feats)
    m = out["markets"]["1X2"]
    assert m["validation_status"] == "NO EVIDENCE OF MARKET EDGE" and m["residual_validated"] is False
    assert abs(m["selections"]["HOME"]["hybrid"] - 0.5) < 1e-6 and m["selections"]["HOME"]["disagreement_pp"] == 10.0 and m["selections"]["HOME"]["residual_edge_pp"] == 0.0
    assert "hipótese" in m["interpretation"]
    assert out["features_imputed"] == ["att_diff", "def_diff"]
    assert np.isfinite(out["markets"]["1X2"]["selections"]["DRAW"]["hybrid"])


def test_replay_reports_historical_quality_gate():
    from datetime import datetime

    from edgefut.validation.replay import HISTORICAL_QUALITY_GATE, ReplayRequest, run_replay
    from tests.test_strength_v2_replay import _league

    rep = run_replay(ReplayRequest(datasets=["SYN"], start=datetime(2023, 8, 1), window_days=90, min_train=50), frames={"SYN": _league(teams=10, seasons=3, seed=8)}, persist=False)
    hq = rep["historical_quality_gate"]
    assert hq["components"] == HISTORICAL_QUALITY_GATE
    assert hq["components"]["odds_freshness"] == "UNAVAILABLE_IN_REPLAY" and hq["components"]["lineups"] == "UNAVAILABLE_IN_REPLAY"
    assert hq["applied"].get("evaluated", 0) > 0
