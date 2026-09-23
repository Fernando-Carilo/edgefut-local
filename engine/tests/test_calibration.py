"""Calibration Engine: PAV monotônico, limiar N>=300, RAW ≠ CALIBRATED só quando confiável."""

import numpy as np

from edgefut.models.calibration import (
    MIN_CALIBRATION_N,
    Calibrator,
    apply_isotonic,
    brier,
    pav,
    pick,
)


def test_pav_is_monotone_and_reduces_brier():
    rng = np.random.default_rng(7)
    n = 2000
    p = rng.uniform(0.05, 0.95, n)
    # modelo sistematicamente superconfiante: verdadeira prob = 0.5 + 0.6*(p-0.5)
    true = 0.5 + 0.6 * (p - 0.5)
    y = (rng.uniform(size=n) < true).astype(float)
    thresholds, values = pav(p, y)
    assert all(values[i] <= values[i + 1] + 1e-12 for i in range(len(values) - 1))
    assert all(thresholds[i] <= thresholds[i + 1] + 1e-12 for i in range(len(thresholds) - 1))
    cal = np.array([apply_isotonic(thresholds, values, v) for v in p])
    assert brier(cal, y) < brier(p, y)
    assert 0.0 <= cal.min() and cal.max() <= 1.0
    # superconfiança corrigida: p alto vira menor, p baixo vira maior
    assert apply_isotonic(thresholds, values, 0.9) < 0.9
    assert apply_isotonic(thresholds, values, 0.1) > 0.1


def test_pav_perfect_calibration_is_near_identity():
    rng = np.random.default_rng(1)
    p = rng.uniform(0, 1, 20000)
    y = (rng.uniform(size=20000) < p).astype(float)
    t, v = pav(p, y)
    for x in (0.2, 0.5, 0.8):
        assert abs(apply_isotonic(t, v, x) - x) < 0.04


def test_unreliable_calibrator_returns_raw_probability():
    c = Calibrator("1X2|GLOBAL", n=50, reliable=False, thresholds=[0.0, 1.0], values=[0.3, 0.3], brier_raw=None, brier_calibrated=None)
    assert c(0.62) == 0.62
    ok = Calibrator("1X2|GLOBAL", n=MIN_CALIBRATION_N, reliable=True, thresholds=[0.0, 1.0], values=[0.0, 0.8], brier_raw=None, brier_calibrated=None)
    assert abs(ok(0.5) - 0.4) < 1e-9


def test_pick_prefers_competition_then_global_then_none():
    comp = Calibrator("1X2|Premier League", 400, True, [0, 1], [0, 1], None, None)
    glob = Calibrator("1X2|GLOBAL", 900, True, [0, 1], [0, 1], None, None)
    weak = Calibrator("1X2|Serie A", 20, False, [], [], None, None)
    cals = {c.group_key: c for c in (comp, glob, weak)}
    assert pick(cals, "1X2", "Premier League") is comp
    assert pick(cals, "1X2", "Serie A") is glob
    assert pick({"1X2|Serie A": weak}, "1X2", "Serie A") is None
    assert pick(cals, "BTTS", None) is None


def test_apply_without_fit_is_identity():
    assert apply_isotonic([], [], 0.37) == 0.37


def test_engine_uses_calibrated_probability_only_when_reliable():
    from edgefut.domain.analysis import ConfidenceBreakdown, CountDistribution, DataQuality, GoalsModelOutput, MarketOdds, SelectionOdds
    from edgefut.recommendations.engine import evaluate
    from edgefut.simulation.monte_carlo import simulate

    sim = simulate(GoalsModelOutput(model_version="t", lambda_home=1.8, lambda_away=0.8, rho=-0.05, fit_matches=400), 10_000, seed=7)
    sels = [
        SelectionOdds(key="HOME", name="1", price=1.9, implied=0.5263, fair=0.5),
        SelectionOdds(key="DRAW", name="X", price=4.0, implied=0.25, fair=0.2375),
        SelectionOdds(key="AWAY", name="2", price=6.0, implied=0.1667, fair=0.1583),
    ]
    market = MarketOdds(market_key="1X2", label="1X2", line=None, selections=sels, overround=0.05, margin_removed=True)
    empty = CountDistribution(model_version="t", available=False)
    common = dict(
        markets=[market], sim=sim, corners=empty, cards=empty, confidence=ConfidenceBreakdown(model_version="t", score=85, grade="A", components=[]),
        data_quality=DataQuality(score=90, checks=[]), supported=True, teams_resolved=True, min_sample=30, model_disagreement_pp=1.0, unreliable_source=False,
    )
    # calibrador que "encolhe" tudo para 0.5·p + 0.25 (superconfiança corrigida)
    shrink = Calibrator("1X2|GLOBAL", 500, True, [0.0, 1.0], [0.25, 0.75], None, None)
    raw_recs, _ = evaluate(**common)
    cal_recs, _ = evaluate(**common, calibrators={"1X2|GLOBAL": shrink}, competition="Qualquer")
    raw_home = next(r for r in raw_recs if r.selection_key == "HOME")
    cal_home = next(r for r in cal_recs if r.selection_key == "HOME")
    assert raw_home.model_prob_raw == raw_home.model_prob and raw_home.model_prob_calibrated is None and not raw_home.calibration_reliable
    assert cal_home.model_prob_raw == raw_home.model_prob_raw
    assert cal_home.calibration_reliable and cal_home.calibration_group == "1X2|GLOBAL"
    assert abs(cal_home.model_prob_calibrated - (0.25 + 0.5 * cal_home.model_prob_raw)) < 1e-3
    assert cal_home.model_prob == cal_home.model_prob_calibrated
    # edge é recalculado com a probabilidade calibrada
    assert abs(cal_home.edge_pp - (cal_home.model_prob - cal_home.market_prob) * 100) < 0.02
    # calibrador NÃO confiável → probabilidade crua, mesmo que exista
    weak = Calibrator("1X2|GLOBAL", 20, False, [0.0, 1.0], [0.25, 0.75], None, None)
    weak_recs, _ = evaluate(**common, calibrators={"1X2|GLOBAL": weak})
    weak_home = next(r for r in weak_recs if r.selection_key == "HOME")
    assert weak_home.model_prob == raw_home.model_prob and weak_home.model_prob_calibrated is None
