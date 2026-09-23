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
