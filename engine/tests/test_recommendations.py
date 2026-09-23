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
from edgefut.recommendations.engine import evaluate, opportunity_score
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


def test_opportunity_score_ignores_odd_and_is_bounded():
    hi = opportunity_score(data_quality=95, confidence=90, edge_pp=8, movement_pct=0.0, calibration=0.8)
    lo = opportunity_score(data_quality=30, confidence=40, edge_pp=0, movement_pct=12.0, calibration=0.5)
    assert 0 <= lo < hi <= 100


def _conf(score: float) -> ConfidenceBreakdown:
    return ConfidenceBreakdown(model_version="confidence-v1", score=score, grade=grade_for(score), components=[])


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
        unreliable_source=False,
    )
    kwargs.update(overrides)
    return evaluate(**kwargs)


def test_recommends_when_edge_exists():
    recs, verdict = _evaluate()
    assert verdict.no_bet is False
    home = next(r for r in recs if r.selection_key == "HOME")
    assert home.status == "RECOMMENDED"
    assert home.edge_pp > 3
    assert home.confidence_grade == "A"


def test_no_edge_when_market_agrees():
    # odd 1.55 -> mercado ~0.62 para casa, modelo ~0.62 → sem edge
    recs, verdict = _evaluate(markets=[_market_1x2(1.55)])
    home = next(r for r in recs if r.selection_key == "HOME")
    assert home.status != "RECOMMENDED"
    assert "NO_EDGE" in home.reasons or verdict.reason == "NO_EDGE"


def test_no_bet_reasons_have_priority():
    _, v = _evaluate(supported=False)
    assert v.reason == "UNSUPPORTED_COMPETITION"
    _, v = _evaluate(teams_resolved=False)
    assert v.reason == "LOW_DATA"
    _, v = _evaluate(unreliable_source=True)
    assert v.reason == "UNRELIABLE_SOURCE"
    _, v = _evaluate(min_sample=3)
    assert v.reason == "SMALL_SAMPLE"
    _, v = _evaluate(model_disagreement_pp=12.0)
    assert v.reason == "MODEL_DISAGREEMENT"
    _, v = _evaluate(confidence=_conf(40))
    assert v.reason == "LOW_CONFIDENCE"


def test_event_no_bet_blocks_every_recommendation():
    recs, verdict = _evaluate(model_disagreement_pp=15.0)
    assert verdict.no_bet
    assert all(r.status == "NO_BET" for r in recs)
    assert all("MODEL_DISAGREEMENT" in r.reasons for r in recs)


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
