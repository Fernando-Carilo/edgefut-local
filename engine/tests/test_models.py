import numpy as np

from edgefut.domain.analysis import GoalsModelOutput
from edgefut.models.goals_common import apply_dixon_coles_tau, markets_from_matrix, score_matrix
from edgefut.simulation.monte_carlo import ALLOWED_SIMULATIONS, simulate


def test_score_matrix_is_probability_distribution():
    m = score_matrix(1.5, 1.1)
    assert m.shape[0] == m.shape[1]
    assert abs(m.sum() - 1.0) < 1e-6
    assert m[1, 1] > m[4, 4]


def test_dixon_coles_tau_keeps_mass_and_moves_low_scores():
    base = score_matrix(1.4, 1.0)
    adj = apply_dixon_coles_tau(base.copy(), 1.4, 1.0, rho=-0.1)
    assert abs(adj.sum() - 1.0) < 1e-6
    # rho negativo aumenta 0-0 e 1-1, reduz 1-0 e 0-1
    assert adj[0, 0] > base[0, 0]
    assert adj[1, 1] > base[1, 1]
    assert adj[1, 0] < base[1, 0]


def test_markets_from_matrix_consistency():
    mk = markets_from_matrix(score_matrix(1.6, 1.2))
    assert abs(mk["p_home"] + mk["p_draw"] + mk["p_away"] - 1.0) < 1e-6
    assert abs(mk["over"]["2.5"] + mk["under"]["2.5"] - 1.0) < 1e-6
    assert mk["over"]["0.5"] > mk["over"]["2.5"] > mk["over"]["4.5"]
    assert 0 < mk["btts"] < 1
    assert mk["top_scores"][0][1] >= mk["top_scores"][1][1]


def test_monte_carlo_matches_analytic_and_is_reproducible():
    model = GoalsModelOutput(model_version="test", lambda_home=1.7, lambda_away=0.9, rho=-0.05, fit_matches=500)
    a = simulate(model, 50_000, seed=42)
    b = simulate(model, 50_000, seed=42)
    assert a is not None and b is not None
    assert a.p_home == b.p_home and a.top_scores == b.top_scores
    analytic = markets_from_matrix(apply_dixon_coles_tau(score_matrix(1.7, 0.9), 1.7, 0.9, -0.05))
    assert abs(a.p_home - analytic["p_home"]) < 0.01
    assert abs(a.over["2.5"] - analytic["over"]["2.5"]) < 0.01
    assert a.most_likely_score is not None


def test_monte_carlo_rejects_unavailable_model():
    model = GoalsModelOutput(model_version="test", available=False)
    assert simulate(model, ALLOWED_SIMULATIONS[0], seed=1) is None


def test_allowed_simulation_sizes():
    assert ALLOWED_SIMULATIONS == (10_000, 25_000, 50_000, 100_000)
    assert 50_000 in ALLOWED_SIMULATIONS
    assert np.all(np.diff(ALLOWED_SIMULATIONS) > 0)
