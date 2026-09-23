from edgefut.odds.implied import build_markets, implied_probability, movement, remove_margin


def test_implied_probability():
    assert implied_probability(2.0) == 0.5
    assert abs(implied_probability(1.25) - 0.8) < 1e-9
    assert implied_probability(0) == 0.0


def test_remove_margin_multiplicative_sums_to_one():
    fair, overround = remove_margin({"HOME": 1.9, "DRAW": 3.4, "AWAY": 4.2})
    assert abs(sum(fair.values()) - 1.0) < 1e-9
    assert overround > 0
    assert fair["HOME"] > fair["DRAW"] > fair["AWAY"]


def test_remove_margin_double_chance_sums_to_two():
    fair, overround = remove_margin({"HOME_DRAW": 1.24, "DRAW_AWAY": 1.87, "HOME_AWAY": 1.31}, fair_sum=2.0)
    assert abs(sum(fair.values()) - 2.0) < 1e-9
    assert 0 < overround < 0.15
    assert 0.7 < fair["HOME_DRAW"] < 0.85


def test_movement_direction():
    assert movement(None, 2.0) == (None, None)
    pct, direction = movement(2.0, 1.8)
    assert pct == -10.0 and direction == "down"
    assert movement(2.0, 2.0)[1] == "flat"


def _rows(mk, sels, line=None):
    return [
        {"market_key": mk, "market_name": mk, "selection_key": k, "selection_name": k, "line": line, "price": p}
        for k, p in sels.items()
    ]


def test_build_markets_only_removes_margin_when_complete():
    rows = _rows("1X2", {"HOME": 1.9, "DRAW": 3.4, "AWAY": 4.2}) + _rows("TOTAL_GOALS", {"OVER": 1.9}, line=2.5)
    markets = build_markets(rows)
    by_key = {m.market_key: m for m in markets}
    assert by_key["1X2"].margin_removed is True
    assert all(s.fair is not None for s in by_key["1X2"].selections)
    assert by_key["TOTAL_GOALS"].margin_removed is False
    assert by_key["TOTAL_GOALS"].selections[0].fair is None
    assert by_key["TOTAL_GOALS"].selections[0].implied == round(1 / 1.9, 4)


def test_build_markets_double_chance_fair_is_sane():
    m = build_markets(_rows("DOUBLE_CHANCE", {"HOME_DRAW": 1.24, "DRAW_AWAY": 1.87, "HOME_AWAY": 1.31}))[0]
    fair = {s.key: s.fair for s in m.selections}
    assert abs(sum(fair.values()) - 2.0) < 1e-3
    assert fair["HOME_DRAW"] < 1 / 1.24
