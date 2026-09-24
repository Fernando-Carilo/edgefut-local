"""Iteração 4 — shadow report v2 (Superbet fair vs EdgeFut vs Híbrido com cluster bootstrap e N efetivo),
validação OOS de grau/opportunity, regra de promoção market-aware (CLV ≥ 0) e Model Health V2."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import text

from edgefut.db.migrations import run_migrations
from edgefut.db.models import ShadowPrediction
from edgefut.db.session import get_engine
from edgefut.validation import shadow as sh
from edgefut.validation.governance import evaluate_market_aware_promotion


@pytest.fixture(scope="module", autouse=True)
def _db():
    run_migrations(get_engine())


def _rows(n_events: int, per_event: int = 3, *, seed: int = 1, hybrid: bool = True) -> list[ShadowPrediction]:
    """Linhas liquidadas sintéticas: mercado calibrado, EdgeFut = mercado + ruído (pior), híbrido = mercado."""
    rng = np.random.default_rng(seed)
    out = []
    for e in range(n_events):
        for k in range(per_event):
            pm = float(rng.uniform(0.25, 0.7))
            won = bool(rng.uniform() < pm)
            pe = float(np.clip(pm + rng.normal(0, 0.08), 0.02, 0.98))
            r = ShadowPrediction(
                event_id=1000 + e, kickoff_utc=datetime(2026, 1, 1) + timedelta(days=e), market_key="1X2" if k < 2 else "TOTAL_GOALS", selection_key=f"S{k}", line=None if k < 2 else 2.5,
                odd=round(1 / pm * 1.05, 3), model_prob=pe, market_prob=pm, edge_pp=(pe - pm) * 100, state="OBSERVATION" if k == 0 else "NO_BET",
                confidence_score=float(rng.choice([85, 70, 55, 30])), opportunity_score=float(rng.uniform(10, 90)), model_version="t",
                won=won, settled_at=datetime(2026, 1, 2) + timedelta(days=e), closing_odd=round(1 / pm, 3),
                hybrid_prob=pm if hybrid else None,
            )
            out.append(r)
    return out


def test_migration_v4_adds_shadow_market_aware_columns():
    with get_engine().connect() as c:
        cols = {r[1] for r in c.execute(text("PRAGMA table_info(shadow_prediction)"))}
        assert {"hybrid_prob", "residual_edge_pp", "required_edge_pp", "adjusted_edge_pp", "minutes_to_kickoff"} <= cols
        assert int(c.execute(text("SELECT MAX(version) FROM schema_version")).scalar()) >= 4


def test_market_aware_panel_uses_event_clusters_and_effective_n():
    rows = _rows(120)
    p = sh.market_aware_panel(rows)
    assert p["raw_n"] == 360 and p["events"] == 120
    assert p["effective"]["clusters"] == 120 and p["effective"]["effective_n"] < p["raw_n"]
    assert p["superbet_fair"]["brier"]["n"] == 120  # n do IC = clusters, não observações
    # mercado calibrado vs EdgeFut ruidoso → EdgeFut não pode aparecer como melhor
    assert p["verdict"] in ("SUPERBET BETTER (SHADOW)", "NO CLEAR ADVANTAGE")
    assert p["edgefut_vs_superbet"]["delta_brier"]["point"] > 0
    assert p["hybrid"]["n"] == 360 and abs(p["hybrid"]["delta_brier_vs_superbet"]["point"]) < 1e-9
    assert p["clv"]["n"] == 360 and p["clv"]["pct"]["point"] > 0
    assert p["value_count"] == 0 and p["no_bet_count"] == 240


def test_market_aware_panel_small_sample_is_insufficient_and_hybrid_never_reconstructed():
    p = sh.market_aware_panel(_rows(3, hybrid=False))
    assert p["verdict"] == "INSUFFICIENT DATA" and p["hybrid"]["n"] == 0 and "reconstru" in p["hybrid"]["note"]
    assert sh.market_aware_panel([])["verdict"] == "INSUFFICIENT DATA"


def test_confidence_and_opportunity_validation_report_honestly():
    rows = _rows(150, seed=3)
    cv = sh.confidence_validation(rows)
    assert cv["n"] == 450 and set(cv["grades"]) <= {"A", "B", "C", "D"}
    assert cv["verdict"] in ("GRADE SEPARATES OOS", "GRADE DOES NOT SEPARATE OOS")  # grau aleatório: não deve prometer
    ov = sh.opportunity_validation(rows)
    assert set(ov["bins"]) <= {"0-40", "40-55", "55-70", "70+"}
    assert all(v["roi"]["n"] >= 1 for v in ov["bins"].values())
    assert ov["verdict"] in ("SCORE RELATES TO ROI (OOS)", "NO RELATION SCORE→ROI (OOS)")
    tiny = sh.confidence_validation(_rows(2))
    assert tiny["verdict"] == "INSUFFICIENT DATA"


def _holdout(pass_all: bool) -> dict:
    hi = -0.001 if pass_all else 0.002
    return {"classification": "RESEARCH MARKET BENCHMARK", "model_hash": "m", "config_hash": "c", "dataset_version": "d", "markets": {"1X2": {
        "effective_sample": {"effective_n": 2000},
        "overall": {"residual": {"ece": 0.008}, "market": {"ece": 0.012}},
        "vs_market": {"residual": {"delta_brier_ci": {"point": -0.002, "low": -0.003, "high": hi}, "delta_logloss_ci": {"point": -0.001}, "windows_total": 8, "windows_better": 7 if pass_all else 3, "significance": "x"}},
    }}}


def test_market_aware_promotion_requires_all_criteria_and_non_negative_clv():
    assert evaluate_market_aware_promotion(None, "1X2")["verdict"] == "NOT EVALUATED"
    assert evaluate_market_aware_promotion(_holdout(False), "1X2")["verdict"] == "NO EVIDENCE OF MARKET EDGE"
    r = evaluate_market_aware_promotion(_holdout(True), "1X2", clv={"point": 0.4, "low": -0.2, "high": 1.0})
    assert r["verdict"] == "PROMOTE VALUE LAYER" and r["value_layer_allowed"] and r["passing"] == ["residual"]
    neg = evaluate_market_aware_promotion(_holdout(True), "1X2", clv={"point": -1.0, "low": -2.0, "high": -0.1})
    assert neg["verdict"] == "CHALLENGER BEATS MARKET · CLV NEGATIVE" and not neg["value_layer_allowed"]
    unk = evaluate_market_aware_promotion(_holdout(True), "1X2")
    assert unk["verdict"] == "CHALLENGER BEATS MARKET · CLV UNKNOWN" and not unk["value_layer_allowed"]
    assert evaluate_market_aware_promotion(_holdout(True), "OU25")["verdict"] == "INSUFFICIENT DATA"


def test_model_health_v2_is_unproven_without_experiments():
    from edgefut.db.session import session_scope
    from edgefut.validation.model_health import model_health_summary

    with session_scope() as s:
        h = model_health_summary(s)
    v2 = h["v2"]
    assert set(v2) == {"football_model", "market_model", "superbet_evidence", "shadow_settled", "market_edge"}
    assert v2["market_edge"]["status"] == "UNPROVEN" and v2["market_model"]["status"] == "UNPROVEN"
    assert v2["shadow_settled"]["status"] == "INSUFFICIENT" and "effective_n" in v2["shadow_settled"]
    assert any("MARKET EDGE UNPROVEN" in n for n in h["notes"])
