"""Odds V2: Shin, ambas as probabilidades justas gravadas, movimento de linha, odd bruta preservada."""

from datetime import datetime, timedelta

import pytest

from edgefut.core.config import settings
from edgefut.odds.implied import build_markets, line_movement, remove_margin, remove_margin_shin

T0 = datetime(2026, 9, 23, 8, 0)


def _rows(mk, sels, line=None):
    return [
        {"market_key": mk, "market_name": mk, "selection_key": k, "selection_name": k, "line": line, "price": p}
        for k, p in sels.items()
    ]


def test_shin_sums_to_one_and_favours_favourite():
    prices = {"HOME": 1.72, "DRAW": 3.6, "AWAY": 5.0}
    mult, over_m = remove_margin(prices)
    shin, over_s, z = remove_margin_shin(prices)
    assert abs(sum(shin.values()) - 1.0) < 1e-9
    assert abs(over_m - over_s) < 1e-12  # overround é propriedade das odds, não do método
    assert 0 < z < 0.1
    assert shin["HOME"] > mult["HOME"]  # favorito recebe menos da margem
    assert shin["AWAY"] < mult["AWAY"]  # azarão perde mais (longshot bias)
    for k in prices:
        assert 0 < shin[k] < 1 and shin[k] < 1 / prices[k]  # nunca acima da implícita


def test_shin_two_way_symmetric_equals_multiplicative():
    shin, _, z = remove_margin_shin({"OVER": 1.9, "UNDER": 1.9})
    assert abs(shin["OVER"] - 0.5) < 1e-9 and abs(shin["UNDER"] - 0.5) < 1e-9
    assert abs(z - (2 / 1.9 - 1)) < 1e-6  # z ≈ overround em dois lados simétricos


def test_shin_falls_back_for_double_chance():
    prices = {"HOME_DRAW": 1.24, "DRAW_AWAY": 1.87, "HOME_AWAY": 1.31}
    shin, _, z = remove_margin_shin(prices, fair_sum=2.0)
    mult, _ = remove_margin(prices, fair_sum=2.0)
    assert z == 0.0 and shin == mult


def test_shin_no_margin_is_identity():
    shin, over, z = remove_margin_shin({"A": 2.0, "B": 2.0})
    assert over == 0.0 and z == 0.0 and shin == {"A": 0.5, "B": 0.5}


@pytest.mark.parametrize("method", ["MULTIPLICATIVE", "SHIN"])
def test_build_markets_stores_raw_mult_and_shin(method):
    prices = {"HOME": 1.72, "DRAW": 3.6, "AWAY": 5.0}
    m = build_markets(_rows("1X2", prices), margin_method=method)[0]
    assert m.margin_removed and m.margin_method == method
    assert m.shin_z is not None and m.shin_z > 0
    for s in m.selections:
        assert s.price == prices[s.key]  # odd bruta preservada
        assert s.fair_multiplicative is not None and s.fair_shin is not None
        expected = s.fair_shin if method == "SHIN" else s.fair_multiplicative
        assert s.fair == expected and s.fair_method == method
    assert abs(sum(s.fair_multiplicative for s in m.selections) - 1) < 1e-3
    assert abs(sum(s.fair_shin for s in m.selections) - 1) < 1e-3


def test_build_markets_uses_configured_default():
    prev = settings.margin_method
    try:
        settings.margin_method = "SHIN"
        m = build_markets(_rows("1X2", {"HOME": 1.72, "DRAW": 3.6, "AWAY": 5.0}))[0]
        assert m.margin_method == "SHIN"
        settings.margin_method = "banana"
        assert build_markets(_rows("1X2", {"HOME": 1.72, "DRAW": 3.6, "AWAY": 5.0}))[0].margin_method == "MULTIPLICATIVE"
    finally:
        settings.margin_method = prev


def test_line_movement_summary_matches_spec_example():
    pts = [(T0, 1.72), (T0 + timedelta(hours=1), 1.65), (T0 + timedelta(hours=2), 1.80), (T0 + timedelta(hours=3), 1.54)]
    lm = line_movement(pts)
    assert lm.opening_odd == 1.72 and lm.current_odd == 1.54
    assert lm.lowest_odd == 1.54 and lm.highest_odd == 1.80
    assert lm.absolute_move == -0.18
    assert lm.percentage_move == -10.47
    assert lm.implied_opening == 0.5814 and lm.implied_current == 0.6494
    assert lm.implied_probability_move_pp == 6.8
    assert lm.direction == "down" and lm.points == 4 and not lm.extreme
    assert lm.first_seen_at == T0 and lm.last_seen_at == T0 + timedelta(hours=3)


def test_line_movement_extreme_flag_and_empty():
    assert line_movement([]) is None
    lm = line_movement([(T0, 2.0), (T0 + timedelta(hours=1), 1.6)])
    assert lm.extreme and lm.percentage_move == -20.0


def test_build_markets_attaches_history_movement():
    hist = {("1X2", "HOME", None): [(T0, 1.72), (T0 + timedelta(hours=2), 1.60)]}
    m = build_markets(_rows("1X2", {"HOME": 1.54, "DRAW": 3.6, "AWAY": 5.0}), opening={("1X2", "HOME", None): 1.72}, history=hist)[0]
    home = next(s for s in m.selections if s.key == "HOME")
    assert home.movement is not None and home.movement.current_odd == 1.54 and home.movement.opening_odd == 1.72
    assert home.movement.lowest_odd == 1.54 and home.movement.highest_odd == 1.72
    assert home.movement_pct == -10.47
    draw = next(s for s in m.selections if s.key == "DRAW")
    assert draw.movement is None  # sem histórico → não inventa
