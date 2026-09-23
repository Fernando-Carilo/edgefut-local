"""Invariantes matemáticos que nunca podem quebrar, independentemente dos dados.

0 <= p <= 1 · 1X2 soma 1 · fair soma 1 · odds > 1 · edge = modelo − mercado · EV = p·odd − 1 ·
Monte Carlo converge para a matriz analítica · seed determinística · matriz bivariada soma 1.
"""

from __future__ import annotations

import numpy as np
import pytest

from edgefut.domain.analysis import GoalsModelOutput
from edgefut.models.bivariate_poisson import bivariate_pmf_matrix
from edgefut.models.dixon_coles import markets_from_matrix, score_matrix
from edgefut.odds.implied import implied_probability, remove_margin, remove_margin_shin
from edgefut.recommendations.edge import edge_and_ev
from edgefut.simulation.monte_carlo import simulate

LAMBDAS = [(0.6, 0.5), (1.3, 1.1), (1.8, 0.9), (2.4, 1.6), (3.1, 0.4)]


@pytest.mark.parametrize("lh,la", LAMBDAS)
@pytest.mark.parametrize("rho", [None, -0.08, 0.05])
def test_score_matrix_is_a_distribution_and_1x2_sums_to_one(lh, la, rho):
    m = score_matrix(lh, la, rho)
    assert np.all(m >= 0)
    assert abs(m.sum() - 1) < 1e-9
    mk = markets_from_matrix(m)
    assert abs(mk["p_home"] + mk["p_draw"] + mk["p_away"] - 1) < 1e-9
    for k in ("p_home", "p_draw", "p_away", "btts"):
        assert 0 <= mk[k] <= 1
    for line, p in mk["over"].items():
        assert 0 <= p <= 1 and abs(p + mk["under"][line] - 1) < 1e-9
    # Over é monótono decrescente na linha
    lines = sorted(mk["over"], key=float)
    assert all(mk["over"][a] >= mk["over"][b] - 1e-12 for a, b in zip(lines, lines[1:]))


@pytest.mark.parametrize("l3", [0.0, 0.05, 0.2])
def test_bivariate_matrix_sums_to_one_with_correct_marginals(l3):
    l1, l2 = 1.4, 0.9
    m = bivariate_pmf_matrix(l1, l2, l3, n=10)
    assert abs(m.sum() - 1) < 1e-9 and np.all(m >= 0)
    rows = np.arange(m.shape[0])
    assert abs(float((m.sum(axis=1) * rows).sum()) - (l1 + l3)) < 2e-3  # E[X] = λ1 + λ3
    assert abs(float((m.sum(axis=0) * rows).sum()) - (l2 + l3)) < 2e-3


@pytest.mark.parametrize("prices", [
    {"HOME": 1.72, "DRAW": 3.60, "AWAY": 5.00},
    {"HOME": 2.10, "DRAW": 3.30, "AWAY": 3.40},
    {"OVER": 1.91, "UNDER": 1.91},
    {"HOME": 1.05, "DRAW": 12.0, "AWAY": 41.0},
])
def test_fair_probabilities_sum_to_one_for_both_margin_methods(prices):
    for odd in prices.values():
        assert odd > 1
        assert 0 < implied_probability(odd) < 1
    fair_m, over_m = remove_margin(prices)
    fair_s, over_s, z = remove_margin_shin(prices)
    assert abs(sum(fair_m.values()) - 1) < 1e-9
    assert abs(sum(fair_s.values()) - 1) < 1e-6
    assert over_m > 0 and abs(over_m - over_s) < 1e-9  # overround é propriedade do mercado, não do método
    assert 0 <= z < 1
    for k in prices:
        assert 0 < fair_m[k] < 1 and 0 < fair_s[k] < 1
        assert fair_m[k] <= implied_probability(prices[k]) + 1e-12  # remover margem nunca aumenta a probabilidade
    # Shin favorece os azarões menos que o multiplicativo (longshot bias): favorito ganha, azarão perde
    fav = min(prices, key=prices.get)
    dog = max(prices, key=prices.get)
    if len(prices) >= 3:
        assert fair_s[fav] >= fair_m[fav] - 1e-12
        assert fair_s[dog] <= fair_m[dog] + 1e-12


@pytest.mark.parametrize("p,q,odd", [(0.55, 0.50, 2.0), (0.30, 0.35, 2.9), (0.80, 0.78, 1.28), (0.10, 0.05, 20.0)])
def test_edge_and_ev_definitions(p, q, odd):
    edge, ev = edge_and_ev(p, q, odd)
    assert edge == pytest.approx(100 * (p - q), abs=0.051)
    assert ev == pytest.approx(100 * (p * odd - 1), abs=0.051)
    # sinal do edge segue p − q; EV positivo ⇔ p > 1/odd
    assert (edge > 0) == (p > q) or abs(p - q) < 5e-4
    assert (ev > 0) == (p * odd > 1) or abs(p * odd - 1) < 5e-4


def test_monte_carlo_is_deterministic_and_converges_to_analytic_matrix():
    model = GoalsModelOutput(model_version="t", lambda_home=1.5, lambda_away=1.1, rho=-0.05, fit_matches=500, available=True)
    a = simulate(model, 20_000, seed=42)
    b = simulate(model, 20_000, seed=42)
    c = simulate(model, 20_000, seed=43)
    assert a is not None and b is not None and c is not None
    assert (a.p_home, a.p_draw, a.p_away, a.top_scores) == (b.p_home, b.p_draw, b.p_away, b.top_scores)
    assert (a.p_home, a.p_draw, a.p_away) != (c.p_home, c.p_draw, c.p_away)
    assert abs(a.p_home + a.p_draw + a.p_away - 1) < 1e-6
    assert abs(sum(a.total_goals_dist) - 1) < 1e-6
    for d in (a.over, a.under, a.team_totals_home_over, a.team_totals_away_over):
        assert all(0 <= v <= 1 for v in d.values())
    mk = markets_from_matrix(score_matrix(1.5, 1.1, -0.05))
    big = simulate(model, 200_000, seed=7)
    assert big is not None
    # erro-padrão em 200k sims ≈ 0.0011; 4σ ≈ 0.0045
    for key in ("p_home", "p_draw", "p_away"):
        assert abs(getattr(big, key) - mk[key]) < 0.006, key
    assert abs(big.over["2.5"] - mk["over"]["2.5"]) < 0.006


def test_monte_carlo_with_explicit_matrix_follows_the_matrix():
    model = GoalsModelOutput(model_version="t", lambda_home=1.5, lambda_away=1.1, rho=None, fit_matches=500, available=True)
    m = bivariate_pmf_matrix(1.2, 0.8, 0.3, n=10)
    sim = simulate(model, 100_000, seed=1, matrix=m)
    mk = markets_from_matrix(m)
    assert sim is not None
    assert abs(sim.p_home - mk["p_home"]) < 0.01 and abs(sim.p_draw - mk["p_draw"]) < 0.01
