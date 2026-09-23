"""Bivariate Poisson, comparação/consenso de modelos, imutabilidade de snapshots e model_registry."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from edgefut.core import versions
from edgefut.domain.analysis import GoalsModelOutput
from edgefut.models.bivariate_poisson import bivariate_output, bivariate_pmf_matrix, fit_bivariate_poisson
from edgefut.models.ensemble import MODEL_KEYS, WeightSet, compare, weights_from_scores
from edgefut.models.goals_common import markets_from_matrix, score_matrix


def test_bivariate_pmf_is_a_distribution_with_correct_marginals():
    l1, l2, l3 = 1.2, 0.8, 0.2
    m = bivariate_pmf_matrix(l1, l2, l3)
    assert abs(m.sum() - 1) < 1e-9 and (m >= 0).all()
    idx = np.arange(m.shape[0])
    assert abs((m.sum(1) * idx).sum() - (l1 + l3)) < 0.01  # E[X] = λ1 + λ3
    assert abs((m.sum(0) * idx).sum() - (l2 + l3)) < 0.01  # E[Y] = λ2 + λ3
    # covariância positiva → mais empates que Poisson independente com as mesmas marginais
    ind = score_matrix(l1 + l3, l2 + l3)
    assert markets_from_matrix(m)["p_draw"] > markets_from_matrix(ind)["p_draw"]


def test_bivariate_with_zero_covariance_equals_independent_poisson():
    m = bivariate_pmf_matrix(1.4, 1.1, 0.0)
    ind = score_matrix(1.4, 1.1)
    # única diferença: massa residual da cauda (>9 gols) alocada no último bucket
    assert np.allclose(m, ind, atol=1e-5)
    assert abs(markets_from_matrix(m)["p_home"] - markets_from_matrix(ind)["p_home"]) < 1e-5


def _league(seed=11, n_teams=14, seasons=2, cov=0.15):
    rng = np.random.default_rng(seed)
    teams = [f"C{i}" for i in range(n_teams)]
    att = {t: rng.normal(0, 0.2) for t in teams}
    rows = []
    d0 = datetime(2024, 8, 1)
    k = 0
    for s in range(seasons):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                shared = rng.poisson(cov)
                hg = rng.poisson(1.3 * np.exp(att[h] - att[a])) + shared
                ag = rng.poisson(1.0 * np.exp(att[a] - att[h])) + shared
                rows.append({"date": pd.Timestamp(d0 + timedelta(days=k % 300 + 365 * s)), "home": h, "away": a, "hg": hg, "ag": ag})
                k += 1
    return pd.DataFrame(rows)


def test_fit_bivariate_recovers_positive_covariance_and_outputs_probabilities():
    df = _league()
    p = fit_bivariate_poisson(df, reference_date=datetime(2026, 8, 1))
    assert p is not None and p.matches == len(df)
    assert 0.03 < p.lambda3 < 0.5
    out = bivariate_output(p, "C0", "C1", 1.0)
    assert out.available and out.model_version == "goals-bivariate-poisson-v1"
    assert abs(out.p_home + out.p_draw + out.p_away - 1) < 1e-3
    assert 0 < out.p_home < 1 and 0 < out.p_draw < 1 and 0 < out.p_away < 1
    assert abs(out.over["2.5"] + out.under["2.5"] - 1) < 1e-3
    assert bivariate_output(p, "C0", "NAO_EXISTE", 1.0).available is False
    assert fit_bivariate_poisson(df.head(50)) is None  # abaixo de MIN_MATCHES


def _out(version, lh, la, rho=None):
    m = score_matrix(lh, la, rho)
    mk = markets_from_matrix(m)
    return GoalsModelOutput(model_version=version, available=True, lambda_home=lh, lambda_away=la, rho=rho, p_home=mk["p_home"], p_draw=mk["p_draw"], p_away=mk["p_away"], over={k: v for k, v in mk["over"].items()}, btts=mk["btts"], fit_matches=300)


def test_compare_consensus_is_weighted_and_reports_disagreement():
    po = _out(versions.POISSON, 1.6, 1.0)
    dc = _out(versions.DIXON_COLES, 1.4, 1.1, rho=-0.05)
    bp = GoalsModelOutput(model_version=versions.BIVARIATE_POISSON, available=False, note="sem ajuste")
    cmp_, cons, matrix = compare(poisson=po, dixon_coles=dc, bivariate=bp, weights=WeightSet({"poisson": 1, "dixon_coles": 3, "bivariate_poisson": 1}, "walk-forward:E0", "E0", 500))
    assert cons is not None and cons.model_version == "ensemble-v1"
    assert abs(matrix.sum() - 1) < 1e-9
    w = {r.key: r.weight for r in cmp_.rows}
    assert w["poisson"] == 0.25 and w["dixon_coles"] == 0.75 and w["bivariate_poisson"] is None
    # consenso fica entre os dois modelos e mais perto do Dixon-Coles (peso 3)
    assert min(po.p_home, dc.p_home) <= cons.p_home <= max(po.p_home, dc.p_home)
    assert abs(cons.p_home - dc.p_home) < abs(cons.p_home - po.p_home)
    assert cmp_.max_disagreement_pp == cmp_.disagreement_pairs["poisson|dixon_coles"]
    assert cmp_.max_disagreement_pp > 0
    assert abs(cons.p_home + cons.p_draw + cons.p_away - 1) < 1e-3


def test_low_weight_model_is_shown_but_does_not_veto():
    po = _out(versions.POISSON, 1.0, 2.0)  # muito diferente dos outros dois
    dc = _out(versions.DIXON_COLES, 1.9, 0.8, rho=-0.05)
    bp = _out(versions.BIVARIATE_POISSON, 1.9, 0.78)
    # com evidência walk-forward: poisson com peso 3% fica fora do veto
    cmp_, cons, _ = compare(poisson=po, dixon_coles=dc, bivariate=bp, weights=WeightSet({"poisson": 0.03, "dixon_coles": 0.57, "bivariate_poisson": 0.40}, "walk-forward:INTL", "INTL", 857))
    assert cmp_.excluded_low_weight == ["poisson"]
    assert set(cmp_.disagreement_scope) == {"dixon_coles", "bivariate_poisson"}
    assert cmp_.max_disagreement_pp < 5
    assert cmp_.disagreement_pairs["poisson|dixon_coles"] > 20  # ainda reportado
    assert next(r for r in cmp_.rows if r.key == "poisson").weight == 0.03
    assert "fora do veto" in (cmp_.note or "")
    # sem evidência (pesos iguais): todos contam e a divergência veta
    cmp_eq, _, _ = compare(poisson=po, dixon_coles=dc, bivariate=bp, weights=WeightSet({k: 1.0 for k in MODEL_KEYS}, "equal", None, None))
    assert cmp_eq.excluded_low_weight == []
    assert cmp_eq.max_disagreement_pp > 20


def test_compare_with_no_model_is_honest():
    none = GoalsModelOutput(model_version="x", available=False)
    cmp_, cons, matrix = compare(poisson=none, dixon_coles=none, bivariate=none, weights=WeightSet({}, "equal", None, None))
    assert cons is None and matrix is None and cmp_.max_disagreement_pp is None


def test_weights_from_scores_prefer_lower_logloss_but_never_zero():
    w = weights_from_scores({"poisson": {"logloss": 1.05, "n": 100}, "dixon_coles": {"logloss": 1.00, "n": 100}, "bivariate_poisson": {"logloss": 1.01, "n": 100}})
    assert abs(sum(w.values()) - 1) < 1e-6
    assert w["dixon_coles"] > w["bivariate_poisson"] > w["poisson"] > 0
    assert weights_from_scores({}) == {}


def test_snapshot_prediction_fields_are_immutable_but_result_is_settable():
    from sqlalchemy import select

    from edgefut.db.immutability import ImmutableSnapshotError, correct_result, install_snapshot_guard
    from edgefut.db.migrations import run_migrations
    from edgefut.db.models import Event, PredictionSnapshot, SnapshotCorrection
    from edgefut.db.session import get_engine, session_scope

    run_migrations(get_engine())
    install_snapshot_guard()
    with session_scope() as s:
        ev = s.get(Event, 990002) or Event(id=990002, home_name="A", away_name="B", kickoff_utc=datetime(2026, 1, 1), status="prematch", market_count=1)
        s.add(ev)
        snap = PredictionSnapshot(
            event_id=990002, kickoff_utc=datetime(2026, 1, 1), home_name="A", away_name="B", competition_name="T", model_version="pipeline-v2",
            model_versions={}, features={}, probabilities={"p": 0.5}, odds=[], recommendations=[{"market_key": "1X2", "selection_key": "HOME", "line": None, "model_prob": 0.5, "odd": 2.0}],
        )
        s.add(snap)
        s.commit()
        sid = snap.id
    with session_scope() as s:
        snap = s.get(PredictionSnapshot, sid)
        snap.probabilities = {"p": 0.9}
        with pytest.raises(ImmutableSnapshotError):
            s.flush()
        s.rollback()
    with session_scope() as s:
        snap = s.get(PredictionSnapshot, sid)
        assert snap.probabilities == {"p": 0.5}
        snap.result = {"hg": 1, "ag": 0, "outcomes": {"1X2|HOME|None": True}}
        snap.closing_odds = {"1X2|HOME|None": {"price": 1.9}}
        s.flush()  # permitido
        correct_result(s, snap, {"hg": 1, "ag": 1, "outcomes": {"1X2|HOME|None": False}}, reason="placar revisado", source="football-data.co.uk")
        s.commit()
    with session_scope() as s:
        snap = s.get(PredictionSnapshot, sid)
        assert snap.result["hg"] == 1 and snap.result["ag"] == 1
        assert snap.recommendations[0]["model_prob"] == 0.5  # previsão intacta
        corr = s.execute(select(SnapshotCorrection).where(SnapshotCorrection.snapshot_id == sid)).scalars().all()
        assert len(corr) == 1 and corr[0].old_value["ag"] == 0 and corr[0].new_value["ag"] == 1


def test_model_registry_sync_is_idempotent_and_marks_deprecated():
    from edgefut.db.migrations import run_migrations
    from edgefut.db.models import ModelRegistry
    from edgefut.db.session import get_engine, session_scope
    from edgefut.models.registry import registry_rows, sync_registry

    run_migrations(get_engine())
    with session_scope() as s:
        r1 = sync_registry(s)
        r2 = sync_registry(s)
        assert r2["created"] == 0 and r1["total"] == len(versions.ALL_MODELS)
        rows = registry_rows(s)
        ids = {(r["model_id"], r["version"]) for r in rows}
        assert ("bivariate_poisson", "goals-bivariate-poisson-v1") in ids
        assert ("dixon_coles", "goals-dixon-coles-v1") in ids
        assert all(r["active"] for r in rows if (r["model_id"], r["version"]) in versions.ALL_MODELS.items())
        s.add(ModelRegistry(model_id="pipeline", version="pipeline-v0", active=True))
        s.commit()
        sync_registry(s)
        old = next(r for r in registry_rows(s) if r["version"] == "pipeline-v0")
        assert old["deprecated"] and not old["active"]
