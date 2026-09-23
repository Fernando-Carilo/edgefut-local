from edgefut.backtesting.settlement import MatchResult, settle_selection
from edgefut.domain.analysis import (
    ConfidenceBreakdown,
    CountDistribution,
    DataQuality,
    GoalsModelOutput,
    MarketOdds,
    SelectionOdds,
)
from edgefut.recommendations.confidence import grade_for
from edgefut.recommendations.edge import edge_and_ev
from edgefut.recommendations.engine import evaluate, opportunity_label
from edgefut.recommendations.gate import GateContext, quality_gate
from edgefut.recommendations.opportunity import COMPONENT_LABELS, OpportunityInputs, compute_opportunity, normalized_weights
from edgefut.recommendations.why import assert_no_guarantee_language
from edgefut.simulation.monte_carlo import simulate

import pytest
from edgefut.db.migrations import run_migrations
from edgefut.db.session import get_engine


@pytest.fixture(scope="module", autouse=True)
def _db():
    # evidence_level lê a tabela `setting`; garante o esquema quando o módulo roda isolado
    run_migrations(get_engine())


def test_edge_and_ev_formulas():
    edge, ev = edge_and_ev(0.55, 0.50, 2.0)
    assert edge == 5.0
    assert ev == 10.0
    edge, ev = edge_and_ev(0.40, 0.50, 2.0)
    assert edge == -10.0 and ev == -20.0


def test_grades():
    assert grade_for(80) == "A"
    assert grade_for(79.9) == "B"
    assert grade_for(65) == "B"
    assert grade_for(50) == "C"
    assert grade_for(49) == "D"


def _opp_inputs(**kw) -> OpportunityInputs:
    base = dict(
        confidence_score=90, data_quality=95, edge_pp=8, ev_pct=12, odds_freshness="FRESH", odds_age_seconds=120,
        model_disagreement_pp=1.5, n_models=3, sample_size=40, calibration_quality=0.8, calibration_reliable=True,
        historical_roi=6.0, historical_n=80,
    )
    base.update(kw)
    return OpportunityInputs(**base)


def test_opportunity_v2_is_bounded_has_all_components_and_is_monotonic():
    hi = compute_opportunity(_opp_inputs())
    lo = compute_opportunity(_opp_inputs(confidence_score=40, data_quality=30, edge_pp=0, ev_pct=0, odds_freshness="STALE", odds_age_seconds=7200, model_disagreement_pp=9, sample_size=8, calibration_quality=None, calibration_reliable=False, historical_roi=None, historical_n=0))
    assert hi.model_version == "opportunity-v3"
    assert 0 <= lo.score < hi.score <= 100
    assert {c.key for c in hi.components} == set(COMPONENT_LABELS)
    assert abs(sum(c.weight for c in hi.components) - 100) < 0.5
    assert all(0 <= c.value <= 1 for c in lo.components)
    # sem amostra histórica: componente neutro e explicitamente rotulado
    hist = next(c for c in lo.components if c.key == "historical_performance")
    assert hist.value == 0.5 and "INSUFFICIENT SAMPLE" in (hist.note or "")


def test_opportunity_v2_weights_are_configurable_and_normalized():
    w = normalized_weights({"edge": 1, "ev": 1})  # demais zerados
    assert abs(w["edge"] - 0.5) < 1e-9 and w["model_confidence"] == 0
    only_edge = compute_opportunity(_opp_inputs(edge_pp=10, ev_pct=0), weights={"edge": 1})
    assert only_edge.score == 100.0
    no_edge = compute_opportunity(_opp_inputs(edge_pp=0), weights={"edge": 1})
    assert no_edge.score == 0.0
    # pesos inválidos (tudo zero) caem para iguais, nunca explodem
    assert abs(sum(normalized_weights({}).values()) - 1) < 1e-9


def test_labels_separate_high_probability_from_value():
    assert opportunity_label(0.80, 0.5, 0.5) == "HIGH_PROBABILITY"  # provável, sem valor
    assert opportunity_label(0.35, 6.0, 12.0) == "VALUE"  # improvável, com valor
    assert opportunity_label(0.70, 6.0, 12.0) == "HIGH_PROBABILITY_VALUE"
    assert opportunity_label(0.40, 1.0, 1.0) is None


def _gate_ctx(**kw) -> GateContext:
    base = dict(
        data_quality=85, confidence_score=80, min_sample=30, model_disagreement_pp=2.0, n_models=3,
        odds_freshness="FRESH", provider_status="HEALTHY", edge_pp=5.0, ev_pct=8.0, odd=2.1,
    )
    base.update(kw)
    return GateContext(**base)


def test_quality_gate_treats_huge_uncalibrated_edge_as_model_error():
    g = quality_gate(_gate_ctx(edge_pp=22.0, calibration_reliable=False))
    assert not g.passed and g.failed == ["edge_plausible"]
    assert "erro do modelo" in next(c.detail for c in g.checks if c.key == "edge_plausible")
    assert quality_gate(_gate_ctx(edge_pp=22.0, calibration_reliable=True)).passed
    assert quality_gate(_gate_ctx(edge_pp=14.9, calibration_reliable=False)).passed


def test_quality_gate_requires_every_check():
    assert quality_gate(_gate_ctx()).passed
    for bad in (
        dict(data_quality=50), dict(confidence_score=60), dict(min_sample=12), dict(model_disagreement_pp=11),
        dict(n_models=1), dict(odds_freshness="STALE"), dict(odds_freshness=None), dict(provider_status="UNAVAILABLE"),
        dict(edge_pp=2.0), dict(ev_pct=1.0), dict(odd=8.0),
    ):
        g = quality_gate(_gate_ctx(**bad))
        assert not g.passed, bad
        assert len(g.failed) == 1, bad
        assert all(c.detail for c in g.checks)


def _conf(score: float) -> ConfidenceBreakdown:
    return ConfidenceBreakdown(model_version="confidence-v2", score=score, grade=grade_for(score), components=[])


def _dq(score: float) -> DataQuality:
    return DataQuality(score=score, checks=[])


def _sim():
    return simulate(GoalsModelOutput(model_version="t", lambda_home=1.8, lambda_away=0.8, rho=-0.05, fit_matches=400), 10_000, seed=7)


def _market_1x2(home_price: float) -> MarketOdds:
    sels = [
        SelectionOdds(key="HOME", name="1", price=home_price, implied=round(1 / home_price, 4), fair=None),
        SelectionOdds(key="DRAW", name="X", price=4.0, implied=0.25, fair=None),
        SelectionOdds(key="AWAY", name="2", price=6.0, implied=round(1 / 6, 4), fair=None),
    ]
    total = sum(s.implied for s in sels)
    for s in sels:
        s.fair = round(s.implied / total, 4)
    return MarketOdds(market_key="1X2", label="Resultado Final", line=None, selections=sels, overround=total - 1, margin_removed=True)


def _empty_count() -> CountDistribution:
    return CountDistribution(model_version="x", available=False)


def _evaluate(**overrides):
    kwargs = dict(
        markets=[_market_1x2(2.4)], sim=_sim(), corners=_empty_count(), cards=_empty_count(), confidence=_conf(85),
        data_quality=_dq(90), supported=True, teams_resolved=True, min_sample=40, model_disagreement_pp=2.0,
        unreliable_source=False, odds_freshness="FRESH", odds_age_seconds=90, provider_status="HEALTHY", n_models=3,
        model_source="consenso de 3 modelos (teste)", evidence="BACKTEST_ODDS",
    )
    kwargs.update(overrides)
    return evaluate(**kwargs)


def test_recommends_when_edge_exists():
    recs, verdict, why_not = _evaluate()
    assert verdict.no_bet is False and why_not == []
    home = next(r for r in recs if r.selection_key == "HOME")
    assert home.status == "RECOMMENDED"
    assert home.edge_pp > 3
    assert home.confidence_grade == "A"
    assert home.quality_gate is not None and home.quality_gate.passed
    # Opportunity V3: breakdown (V2) + penalidade de incerteza por falta de prova OOS
    assert home.opportunity is not None and home.opportunity.score - 5.0 == home.opportunity_score
    assert home.opportunity_adjustments == {"uncertainty_penalty": -5.0}
    assert home.state == "VALUE_CANDIDATE" and home.label == "VALUE_CANDIDATE"  # sem prova OOS → candidato
    assert home.price and home.price["min_acceptable_odd"] < 2.4 and home.price["price_gap_pct"] > 0
    assert home.cluster_id == "HOME_TEAM_POSITIVE" and home.is_primary
    assert len(home.why) >= 4 and home.why_not == []
    assert any("edge" in w for w in home.why)
    assert_no_guarantee_language(home.why)


def test_quality_gate_moves_recommendation_to_watch_with_reasons():
    recs, verdict, why_not = _evaluate(odds_freshness="STALE", odds_age_seconds=5400)
    home = next(r for r in recs if r.selection_key == "HOME")
    assert home.status == "WATCH"
    assert home.reasons[0] == "QUALITY_GATE"
    assert home.quality_gate is not None and home.quality_gate.failed == ["odds_fresh"]
    assert any("Odds frescas" in w for w in home.why_not)
    assert verdict.no_bet and verdict.reason == "QUALITY_GATE" and "odds_fresh" in (verdict.detail or "")
    assert_no_guarantee_language(home.why + home.why_not + why_not)

    recs, verdict, _ = _evaluate(provider_status="UNAVAILABLE")
    home = next(r for r in recs if r.selection_key == "HOME")
    assert home.status == "WATCH" and home.quality_gate.failed == ["provider"]


def test_no_edge_when_market_agrees():
    # odd 1.55 -> mercado ~0.62 para casa, modelo ~0.62 → sem edge
    recs, verdict, _ = _evaluate(markets=[_market_1x2(1.55)])
    home = next(r for r in recs if r.selection_key == "HOME")
    assert home.status != "RECOMMENDED"
    assert "NO_EDGE" in home.reasons or "WATCHING_PRICE" in home.reasons or verdict.reason == "NO_EDGE"
    assert home.why_not and any("Sem edge" in w for w in home.why_not)


def test_no_bet_reasons_have_priority():
    _, v, _ = _evaluate(supported=False)
    assert v.reason == "UNSUPPORTED_COMPETITION"
    _, v, _ = _evaluate(teams_resolved=False)
    assert v.reason == "LOW_DATA"
    _, v, _ = _evaluate(unreliable_source=True)
    assert v.reason == "UNRELIABLE_SOURCE"
    _, v, _ = _evaluate(min_sample=3)
    assert v.reason == "SMALL_SAMPLE"
    _, v, _ = _evaluate(model_disagreement_pp=12.0)
    assert v.reason == "MODEL_DISAGREEMENT"
    _, v, _ = _evaluate(confidence=_conf(40))
    assert v.reason == "LOW_CONFIDENCE"


def test_event_no_bet_blocks_every_recommendation_and_explains_why_not():
    recs, verdict, why_not = _evaluate(model_disagreement_pp=15.0)
    assert verdict.no_bet
    assert all(r.status == "NO_BET" for r in recs)
    assert all("MODEL_DISAGREEMENT" in r.reasons for r in recs)
    assert len(why_not) >= 2 and any("15.0 pp" in w for w in why_not)
    assert_no_guarantee_language(why_not)


def test_guarantee_language_guard_catches_forbidden_terms():
    import pytest

    with pytest.raises(AssertionError):
        assert_no_guarantee_language(["Aposta garantida."])
    with pytest.raises(AssertionError):
        assert_no_guarantee_language(["Entrada 100% segura"])


def test_settlement_rules():
    r = MatchResult(hg=2, ag=1, corners=9, cards=4)
    assert settle_selection("1X2", "HOME", None, r) is True
    assert settle_selection("1X2", "AWAY", None, r) is False
    assert settle_selection("DOUBLE_CHANCE", "DRAW_AWAY", None, r) is False
    assert settle_selection("TOTAL_GOALS", "OVER", 2.5, r) is True
    assert settle_selection("TOTAL_GOALS", "UNDER", 3.0, r) is None  # push
    assert settle_selection("BTTS", "YES", None, r) is True
    assert settle_selection("HANDICAP", "AWAY", -1.0, r) is None  # linha na ótica do mandante: -1 → 2-1 vira 1-1 → void
    assert settle_selection("HANDICAP", "HOME", 1.0, r) is True
    assert settle_selection("CORRECT_SCORE", "2:1", None, r) is True
    assert settle_selection("TOTAL_CORNERS", "OVER", 8.5, r) is True
    assert settle_selection("TOTAL_CARDS", "UNDER", 3.5, r) is False
    assert settle_selection("PLAYER_TO_SCORE", "X", None, r) is None
    assert settle_selection("DRAW_NO_BET", "HOME", None, MatchResult(hg=1, ag=1)) is None


def test_evidence_level_never_claims_more_than_the_data_supports():
    from edgefut.analysis import pipeline
    from edgefut.db.session import session_scope

    class FakeStore:
        def datasets_with_odds(self):
            return {"E0", "SP1"}

    pipeline.invalidate_cache()
    with session_scope() as s:
        assert pipeline.evidence_level(s, FakeStore(), ["E0"], "Premier League") == "BACKTEST_ODDS"
        assert pipeline.evidence_level(s, FakeStore(), ["INTL"], "Amistoso Internacional") == "MODEL_ONLY"
        assert pipeline.evidence_level(s, FakeStore(), ["E0", "INTL"], "Copa") == "MODEL_ONLY"  # mistura: o elo mais fraco manda
        assert pipeline.evidence_level(s, FakeStore(), [], None) == "MODEL_ONLY"
    pipeline.invalidate_cache()


def test_states_value_requires_oos_and_model_only_never_value():
    # prova OOS suficiente e não negativa → VALUE
    recs, verdict, _ = _evaluate(oos={"1X2": {"n": 250, "verdict": "INCONCLUSIVE", "source": "replay"}})
    home = next(r for r in recs if r.selection_key == "HOME")
    assert home.status == "RECOMMENDED" and home.state == "VALUE" and home.label == "VALUE" and home.oos["n"] == 250
    # OOS negativa → OBSERVATION
    recs, verdict, _ = _evaluate(oos={"1X2": {"n": 250, "verdict": "NEGATIVE", "roi_low": -12.0, "roi_high": -2.0}})
    home = next(r for r in recs if r.selection_key == "HOME")
    assert home.status == "WATCH" and home.state == "OBSERVATION" and "OOS_NEGATIVE" in home.reasons
    # evidência MODEL_ONLY: existe preço e edge, mas a competição nunca foi validada → nunca VALUE
    recs, verdict, _ = _evaluate(evidence="MODEL_ONLY", oos={"1X2": {"n": 999, "verdict": "POSITIVE"}})
    home = next(r for r in recs if r.selection_key == "HOME")
    assert home.status != "RECOMMENDED" and home.state == "MODEL_ONLY" and home.label == "MODEL_ONLY"
    assert home.opportunity_score == 0.0 and "MODEL_ONLY" in home.reasons
    assert home.state_text == "Probabilidade calculada, mas sem preço de mercado válido para determinar valor."
    assert verdict.no_bet and verdict.reason == "MODEL_ONLY"
    assert all(r.state != "VALUE" and r.label != "VALUE" for r in recs)
    # §29: em competição MODEL_ONLY, TODAS as seleções com preço são MODEL_ONLY — inclusive as sem edge
    assert all(r.state == "MODEL_ONLY" for r in recs if r.market_key != "PLAYER_TO_SCORE")
    assert not any("WATCHING_PRICE" in r.reasons for r in recs)
    # sem edge: mercado observado; favorito do modelo vira rótulo de probabilidade, não de valor
    recs, _, _ = _evaluate(markets=[_market_1x2(1.55)])
    home = next(r for r in recs if r.selection_key == "HOME")
    assert home.state in ("MARKET_OBSERVED", "OBSERVATION")
    assert home.label in ("MODEL_FAVORITE", "WATCH", None)


def test_clusters_pick_one_primary_per_thesis_and_penalize_alternatives():
    from edgefut.domain.analysis import MarketOdds, SelectionOdds

    dnb = MarketOdds(market_key="DRAW_NO_BET", label="DNB", line=None, margin_removed=True, overround=0.04, selections=[
        SelectionOdds(key="HOME", name="1", price=1.6, implied=0.625, fair=0.60), SelectionOdds(key="AWAY", name="2", price=2.5, implied=0.4, fair=0.40),
    ])
    dc = MarketOdds(market_key="DOUBLE_CHANCE", label="DC", line=None, margin_removed=True, overround=0.04, selections=[
        SelectionOdds(key="HOME_DRAW", name="1X", price=1.3, implied=0.769, fair=0.74), SelectionOdds(key="DRAW_AWAY", name="X2", price=2.2, implied=0.4545, fair=0.44), SelectionOdds(key="HOME_AWAY", name="12", price=1.35, implied=0.74, fair=0.71),
    ])
    recs, verdict, _ = _evaluate(markets=[_market_1x2(2.4), dnb, dc])
    home_cluster = [r for r in recs if r.cluster_id == "HOME_TEAM_POSITIVE"]
    assert len(home_cluster) == 3
    primaries = [r for r in home_cluster if r.is_primary]
    assert len(primaries) == 1
    for alt in home_cluster:
        if not alt.is_primary:
            assert alt.primary_of is not None and any(x.startswith("ALTERNATIVE_OF:") for x in alt.reasons) if alt.status != "NO_BET" else True
    # todas as seleções do evento anotadas com cluster; nunca duas primárias no mesmo cluster
    by = {}
    for r in recs:
        by.setdefault(r.cluster_id, []).append(r.is_primary)
    assert all(sum(v) == 1 for v in by.values())
