"""Iteração 3 — strength-v2, international-strength-v1, replay histórico, decay e governança."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from edgefut.models.international_strength import fit_international, international_output, tournament_type
from edgefut.models.strength_v2 import fit_strength_v2, strength_v2_output
from edgefut.validation.governance import evaluate_promotion
from edgefut.validation.replay import BASELINES, ReplayRequest, run_replay


def _league(seed: int = 7, teams: int = 12, seasons: int = 3, start: datetime = datetime(2022, 8, 1), with_odds: bool = True) -> pd.DataFrame:
    """Liga sintética com forças verdadeiras conhecidas (att_i, def_i) e HA = 1,25."""
    rng = np.random.default_rng(seed)
    names = [f"T{i:02d}" for i in range(teams)]
    att = np.exp(rng.normal(0, 0.25, teams))
    de = np.exp(rng.normal(0, 0.25, teams))
    mu, ha = 1.35, 1.25
    rows = []
    day = start
    for s in range(seasons):
        pairs = [(i, j) for i in range(teams) for j in range(teams) if i != j]
        rng.shuffle(pairs)
        for k, (i, j) in enumerate(pairs):
            lam_h, lam_a = mu * att[i] * de[j] * ha, mu * att[j] * de[i]
            hg, ag = rng.poisson(lam_h), rng.poisson(lam_a)
            d = day + timedelta(days=k * 2)
            row = {"dataset_code": "SYN", "competition": "Synthetic", "season": f"S{s}", "date": d, "home": names[i], "away": names[j], "hg": hg, "ag": ag, "neutral": False}
            if with_odds:
                # "mercado" = verdade com ruído e margem 5 %
                from edgefut.models.goals_common import markets_from_matrix, score_matrix

                mk = markets_from_matrix(score_matrix(lam_h, lam_a))
                p = np.array([mk["p_home"], mk["p_draw"], mk["p_away"]]) * np.exp(rng.normal(0, 0.05, 3))
                p = p / p.sum() * 1.05
                row.update({"odds_h": 1 / p[0], "odds_d": 1 / p[1], "odds_a": 1 / p[2], "odds_close_h": 1 / p[0], "odds_close_d": 1 / p[1], "odds_close_a": 1 / p[2]})
                po = mk["over"]["2.5"] * 1.05
                row.update({"odds_o25": 1 / po, "odds_u25": 1 / ((1 - mk["over"]["2.5"]) * 1.05)})
            rows.append(row)
        day = day + timedelta(days=len(pairs) * 2 + 60)
    df = pd.DataFrame(rows)
    df.attrs["truth"] = {"att": dict(zip(names, att, strict=True)), "def": dict(zip(names, de, strict=True)), "ha": ha, "mu": mu}
    return df


def test_strength_v2_recovers_true_ratings_and_home_advantage():
    df = _league(teams=20, seasons=6)  # 2.280 jogos: ruído Poisson já não domina
    truth = df.attrs["truth"]
    params = fit_strength_v2(df, reference_date=datetime(2060, 1, 1), half_life_days=None, group_col="season")
    assert params is not None and params.converged
    est_att = np.array([params.ratings[t].attack for t in truth["att"]])
    true_att = np.array(list(truth["att"].values()))
    true_att = true_att / np.exp(np.log(true_att).mean())
    assert np.corrcoef(est_att, true_att)[0, 1] > 0.9
    est_def = np.array([params.ratings[t].defense for t in truth["def"]])
    true_def = np.array(list(truth["def"].values()))
    true_def = true_def / np.exp(np.log(true_def).mean())
    assert np.corrcoef(est_def, true_def)[0, 1] > 0.9
    assert abs(params.home_advantage - truth["ha"]) < 0.1
    assert set(params.home_advantage_by_group) == {f"S{i}" for i in range(6)}
    # ratings por condição ficam perto do pooled (a liga sintética não tem efeito casa/fora por equipe)
    for r in params.ratings.values():
        assert 0.6 < r.home_attack / r.attack < 1.5
        assert r.sos is not None and 0.5 < r.sos < 2.0
    assert 0.5 < sum(r.attack for r in params.ratings.values()) / len(params.ratings) < 1.5


def test_strength_v2_output_respects_neutral_venue_and_bounds():
    df = _league()
    params = fit_strength_v2(df, reference_date=datetime(2030, 1, 1), half_life_days=None)
    home, away = "T00", "T01"
    full = strength_v2_output(params, home, away, 1.0)
    neutral = strength_v2_output(params, home, away, 0.0)
    assert full.available and neutral.available
    assert abs(full.p_home + full.p_draw + full.p_away - 1) < 2e-4  # saídas arredondadas a 4 casas
    # sem mando, λ do mandante cai pela HA inteira
    assert full.lambda_home > neutral.lambda_home
    assert strength_v2_output(params, "NOPE", away, 1.0).available is False
    assert strength_v2_output(None, home, away, 1.0).available is False
    assert fit_strength_v2(df.head(30)) is None


def test_strength_v2_time_decay_never_sees_future_and_weights_recent():
    df = _league()
    cut = datetime(2023, 6, 1)
    params = fit_strength_v2(df, reference_date=cut, half_life_days=120)
    assert params is not None
    assert params.matches == int((pd.to_datetime(df["date"]) < pd.Timestamp(cut)).sum())
    assert all(r.weight <= r.matches for r in params.ratings.values())


def test_international_strength_tournament_types_and_friendly_note():
    assert tournament_type("Friendly") == "FRIENDLY"
    assert tournament_type("FIFA World Cup qualification") == "QUALIFIER"
    assert tournament_type("UEFA Nations League") == "NATIONS_LEAGUE"
    assert tournament_type("FIFA World Cup") == "TOURNAMENT"
    assert tournament_type("Copa América") == "CONTINENTAL"
    df = _league(seed=3, teams=16, seasons=2, with_odds=False)
    df["competition"] = np.where(np.arange(len(df)) % 3 == 0, "Friendly", "FIFA World Cup qualification")
    df.loc[df.index % 5 == 0, "neutral"] = True
    params = fit_international(df, reference_date=datetime(2030, 1, 1), half_life_days=None)
    assert params is not None
    assert {"FRIENDLY", "QUALIFIER"} <= set(params.home_advantage_by_group)
    out = international_output(params, "T00", "T01", 1.0, "Friendly")
    assert out.available and "Amistoso" in (out.note or "")
    assert out.model_version == "international-strength-v1"
    out_q = international_output(params, "T00", "T01", 0.0, "FIFA World Cup qualification")
    assert "Amistoso" not in (out_q.note or "")


def test_replay_is_leak_free_and_reports_baselines_with_ci():
    df = _league(seed=11, teams=14, seasons=3)
    req = ReplayRequest(datasets=["SYN"], start=datetime(2023, 8, 1), window_days=45, min_train=100, models=("poisson", "dixon_coles", "poisson_v2", "ensemble_v2"), half_life_days=None)
    rep = run_replay(req, frames={"SYN": df}, persist=False)
    assert rep["matches"] > 200 and rep["windows"] >= 4
    assert set(rep["baselines"]) == set(BASELINES)
    for m in ("poisson", "poisson_v2", "market", "naive", "simple_poisson", "elo", "favorite"):
        o = rep["overall"][m]
        assert o["n"] > 0 and o["brier"]["low"] is not None and o["brier"]["low"] <= o["brier"]["point"] <= o["brier"]["high"]
    # mercado sintético é (quase) a verdade: nenhum modelo o bate de forma conclusiva
    for m in rep["models"]:
        vs = rep["vs_baseline"][m]["market"]
        assert vs["significance"] in ("NO CLEAR ADVANTAGE", "PROMISING", "CONSISTENT", "INSUFFICIENT DATA")
        assert vs["windows_total"] == rep["windows"]
    # modelos com força por equipe batem a frequência ingênua nesta liga com forças heterogêneas
    assert rep["vs_baseline"]["poisson_v2"]["naive"]["lift_pct"] > 0
    # apostas: 1 seleção no máximo por mercado por partida
    bets = rep["bets"]["poisson_v2"]["all"]["n"]
    assert bets <= 2 * rep["matches"]
    assert "pairwise" in rep and "poisson_v2|poisson" in rep["pairwise"]
    # per_window traz Brier por modelo para estabilidade
    assert all("brier" in w and "poisson_v2" in w["brier"] for w in rep["per_window"])


def test_replay_refuses_to_use_future_rows(monkeypatch):
    """Se o TemporalFeatureStore devolvesse linhas futuras, o replay levanta LeakageError."""
    from edgefut.features import temporal
    from edgefut.validation import replay as rp

    df = _league(seed=2, teams=10, seasons=2)
    orig = temporal.TemporalFeatureStore.competition_matches

    def leaky(self, codes, as_of, since=None):
        out = orig(self, codes, as_of, since)
        fut = self._frame[self._frame["date"] >= pd.Timestamp(as_of)].head(1)
        return pd.concat([out, fut])

    monkeypatch.setattr(temporal.TemporalFeatureStore, "competition_matches", leaky)
    with pytest.raises(temporal.LeakageError):
        rp.run_replay(ReplayRequest(datasets=["SYN"], start=datetime(2023, 8, 1), window_days=60, min_train=50, models=("poisson_v2",), half_life_days=None), frames={"SYN": df}, persist=False)


def test_promotion_rule_requires_all_checks():
    rep = {
        "overall": {
            "ensemble": {"n": 500, "brier": {"point": 0.60, "low": 0.59, "high": 0.61}, "log_loss": {"point": 1.0, "low": 0.98, "high": 1.02}, "ece": 0.02},
            "ensemble_v2": {"n": 500, "brier": {"point": 0.58, "low": 0.57, "high": 0.585}, "log_loss": {"point": 0.97, "low": 0.95, "high": 0.99}, "ece": 0.021},
        },
        "per_window": [{"brier": {"ensemble": 0.6, "ensemble_v2": 0.58}}] * 7 + [{"brier": {"ensemble": 0.6, "ensemble_v2": 0.62}}] * 3,
        "pairwise": {"ensemble_v2|ensemble": {"delta_brier_ci": {"point": -0.02, "low": -0.03, "high": -0.01, "n": 500, "conclusive": True}, "delta_logloss_ci": {"point": -0.03, "low": -0.05, "high": -0.01, "n": 500, "conclusive": True}}},
    }
    ev = evaluate_promotion(rep, "ensemble_v2", "ensemble")
    assert ev["eligible"] and ev["verdict"] == "PROMOTE" and ev["roi_is_not_a_criterion"]
    rep["overall"]["ensemble_v2"]["n"] = 120
    assert evaluate_promotion(rep, "ensemble_v2", "ensemble")["verdict"] == "INSUFFICIENT DATA"
    rep["overall"]["ensemble_v2"]["n"] = 500
    rep["per_window"] = [{"brier": {"ensemble": 0.6, "ensemble_v2": 0.58}}] * 4 + [{"brier": {"ensemble": 0.6, "ensemble_v2": 0.62}}] * 6
    assert evaluate_promotion(rep, "ensemble_v2", "ensemble")["verdict"] == "PROMISING · UNSTABLE"
    rep["pairwise"]["ensemble_v2|ensemble"]["delta_brier_ci"]["high"] = 0.001
    assert evaluate_promotion(rep, "ensemble_v2", "ensemble")["verdict"] == "KEEP CHAMPION"
