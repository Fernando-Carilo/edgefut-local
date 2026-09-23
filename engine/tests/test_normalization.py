from edgefut.normalization.competitions import classify_competition
from edgefut.normalization.teams import is_womens, resolve_country, resolve_team
from edgefut.providers.superbet.markets import normalize_odd


def test_classify_league_maps_dataset():
    p = classify_competition(106, "Premier League", 1, "Inglaterra")
    assert p.supported and "E0" in p.dataset_codes and not p.is_national_teams


def test_classify_national_teams_and_womens():
    intl = classify_competition(292, "Amistoso Internacional", 102, "Internacional")
    assert intl.is_national_teams and intl.supported
    w = classify_competition(999, "UEFA - Champions League (F)", 31, "Internacional Clubes")
    assert w.is_womens and not w.supported


def test_country_translation_and_fuzzy_team():
    assert resolve_country("Japão") == "Japan"
    assert resolve_country("Coreia do Sul") == "South Korea"
    assert resolve_country("Costa do Marfim") == "Ivory Coast"
    m = resolve_team("Japão", "INTL", ["Japan", "Uruguay", "Jamaica"], is_national=True)
    assert m.canonical == "Japan" and m.confidence >= 0.88
    miss = resolve_team("Time Inexistente FC", "E0", ["Liverpool", "Everton"], is_national=False)
    assert miss.method == "unmatched" and miss.canonical is None


def test_is_womens():
    assert is_womens("Servette (F)")
    assert not is_womens("Servette")


def test_normalize_odd_1x2_and_totals():
    o = normalize_odd({"marketId": 547, "marketName": "Resultado Final", "name": "Japão", "code": "1", "price": 1.92, "status": "active"}, "Japão", "Uruguai")
    assert o is not None and o.market_key == "1X2" and o.selection_key == "HOME" and o.price == 1.92
    t = normalize_odd(
        {"marketId": 200734, "marketName": "Total de Gols", "name": "Mais de 2.5", "code": "+", "price": 2.22, "status": "active", "specifiers": {"total": "2.5"}},
        "Japão", "Uruguai",
    )
    assert t is not None and t.market_key == "TOTAL_GOALS" and t.selection_key == "OVER" and t.line == 2.5


def test_normalize_odd_rejects_unknown_market_and_bad_price():
    assert normalize_odd({"marketId": 231194, "name": "x", "price": 2.0}, "A", "B") is None
    assert normalize_odd({"marketId": 547, "name": "A", "price": 1.0}, "A", "B") is None
