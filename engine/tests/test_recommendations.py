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
    assert hi.model_version == "opportunity-v2"
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
        model_source="consenso de 3 modelos (teste)",
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
    assert home.opportunity is not None and home.opportunity.score == home.opportunity_score
    assert home.label in ("VALUE", "HIGH_PROBABILITY_VALUE")
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
    assert "NO_EDGE" in home.reasons or verdict.reason == "NO_EDGE"
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
