"""Market-aware challengers (iteração 4): closing line nunca é feature, split temporal, blend, verdict."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from edgefut.validation import market_aware as ma
from edgefut.validation.bootstrap import benjamini_hochberg, brier_decomposition, cluster_bootstrap_ci, effective_sample_size


def _frame(n: int = 900, seed: int = 1, start: datetime = datetime(2022, 1, 1), edge: float = 0.0) -> pd.DataFrame:
    """Frame sintético no formato do replay: mercado ≈ verdade + ruído; modelo = verdade + ruído maior
    (+ `edge` de informação real quando > 0). As colunas `close_*` são ruído puro para provar que não
    influenciam nada."""
    rng = np.random.default_rng(seed)
    base = rng.dirichlet([3, 2.2, 2.6], n)
    # sinal latente que o mercado NÃO vê (edge = desvio-padrão em log-odds); a verdade incorpora-o
    signal = rng.normal(0, edge, base.shape) if edge > 0 else np.zeros_like(base)
    truth = base * np.exp(signal)
    truth /= truth.sum(axis=1, keepdims=True)
    y = np.array([rng.choice(3, p=t) for t in truth])
    pm = base * np.exp(rng.normal(0, 0.08, base.shape))
    pm /= pm.sum(axis=1, keepdims=True)
    # modelo: vê o sinal (informação incremental calibrada) mas com mais ruído que o mercado
    pe = base * np.exp(signal + rng.normal(0, 0.18, base.shape))
    pe /= pe.sum(axis=1, keepdims=True)
    po_true = np.clip(rng.beta(4, 3.5, n), 0.05, 0.95)
    over = rng.random(n) < po_true
    pm_o = np.clip(po_true + rng.normal(0, 0.04, n), 0.05, 0.95)
    pe_o = np.clip(po_true + rng.normal(0, 0.09, n), 0.05, 0.95)
    dates = [start + timedelta(days=i // 3) for i in range(n)]  # 3 jogos por dia
    ds = np.where(rng.random(n) < 0.5, "E0", "SP1")
    df = pd.DataFrame({
        "dataset": ds, "mid": np.arange(n), "window": 1, "date": dates, "group": "S", "home": "H", "away": "A", "neutral": False,
        "hg": 1, "ag": 1, "outcome": y, "over25": over,
        "odds_h": 1 / (pm[:, 0] * 1.05), "odds_d": 1 / (pm[:, 1] * 1.05), "odds_a": 1 / (pm[:, 2] * 1.05),
        "odds_o25": 1 / (pm_o * 1.05), "odds_u25": 1 / ((1 - pm_o) * 1.05),
        "close_h": rng.uniform(1.2, 6, n), "close_d": rng.uniform(2.5, 5, n), "close_a": rng.uniform(1.2, 6, n),
        "close_o25": rng.uniform(1.4, 3, n), "close_u25": rng.uniform(1.4, 3, n),
        "p_market_h": pm[:, 0], "p_market_d": pm[:, 1], "p_market_a": pm[:, 2], "p_market_ou": pm_o,
        "p_ensemble_h": pe[:, 0], "p_ensemble_d": pe[:, 1], "p_ensemble_a": pe[:, 2], "p_ensemble_ou": pe_o,
        "elo_diff": rng.normal(0, 120, n), "att_diff": rng.normal(0, 0.3, n), "def_diff": rng.normal(0, 0.3, n), "home_advantage": 1.25,
        "form5_home": rng.uniform(0, 3, n), "form5_away": rng.uniform(0, 3, n), "gd5_home": rng.normal(0, 1, n), "gd5_away": rng.normal(0, 1, n),
        "n_train_home": 40, "n_train_away": 40, "min_team_sample": rng.integers(10, 60, n), "month": [d.month for d in dates], "dow": 5,
        "days_into_group": [(d - start).days % 300 for d in dates], "agreement_pp": rng.uniform(0, 8, n),
    })
    return df


REQ = ma.MarketAwareRequest(min_train=200, min_validation=60, validation_days=60, test_window_days=45, holdout_days=60, alphas=(0.5, 0.8, 1.0), l2_grid=(0.1, 1.0), ablation=False)


# ---------------------------------------------------------------------------
# §6 — REGRA ABSOLUTA: closing line nunca é feature / input de treino
# ---------------------------------------------------------------------------
def test_market_aware_never_uses_closing_line():
    df = _frame(900)
    datasets = ["E0", "SP1"]
    # 1) nenhuma combinação de grupos aprovados produz uma coluna de fechamento
    all_groups = ma.FEATURE_GROUPS + ("absdiff",)
    for market in ("1X2", "OU25"):
        d = ma.prepare(df, market, "ensemble")
        for r in range(1, 4):
            for groups in itertools.combinations(all_groups, r):
                X = ma._raw_features(d, market, groups, datasets)
                assert not any(c in ma.FORBIDDEN_COLUMNS or "close" in c.lower() for c in X.columns), (groups, list(X.columns))
        X = ma._raw_features(d, market, all_groups, datasets)
        assert not any("close" in c.lower() for c in X.columns)
    # 2) prova por perturbação: alterar TODAS as colunas close_* não muda uma única previsão
    frame_a = df.copy()
    frame_b = df.copy()
    rng = np.random.default_rng(99)
    for c in ma.FORBIDDEN_COLUMNS:
        frame_b[c] = rng.uniform(1.01, 20, len(frame_b))
    rep_a = ma.run_discovery(frame_a, REQ, dataset_version="t")
    rep_b = ma.run_discovery(frame_b, REQ, dataset_version="t")
    for market in REQ.markets:
        ea, eb = rep_a["markets"][market], rep_b["markets"][market]
        assert ea["n"] > 0
        for name in ("blend", "logistic", "residual"):
            assert ea["overall"][name]["brier"]["point"] == eb["overall"][name]["brier"]["point"], (market, name)
            assert ea["overall"][name]["log_loss"]["point"] == eb["overall"][name]["log_loss"]["point"]
    art_a, art_b = ma.freeze(rep_a, frame_a, REQ), ma.freeze(rep_b, frame_b, REQ)
    assert art_a["model_hash"] == art_b["model_hash"], "o artefato congelado mudou com a closing line"
    for spec in art_a["markets"].values():
        for name in ("logistic", "residual"):
            assert not any("close" in f.lower() for f in spec[name]["features"])
    # 3) o relatório declara o proibido
    assert set(ma.FORBIDDEN_COLUMNS) <= set(rep_a["features"]["forbidden"])


# ---------------------------------------------------------------------------
# blend
# ---------------------------------------------------------------------------
def test_blend_alpha_extremes_and_normalisation():
    rng = np.random.default_rng(0)
    pm = rng.dirichlet([2, 2, 2], 50)
    pe = rng.dirichlet([2, 2, 2], 50)
    assert np.allclose(ma.blend(pm, pe, 1.0), pm, atol=1e-6)
    assert np.allclose(ma.blend(pm, pe, 0.0), pe, atol=1e-6)
    mid = ma.blend(pm, pe, 0.7)
    assert np.allclose(mid.sum(axis=1), 1.0)
    assert ((mid >= np.minimum(pm, pe) - 1e-9) | (mid <= np.maximum(pm, pe) + 1e-9)).all()


def test_ou_classes_align_with_outcome():
    """Classe 1 = over; um mercado calibrado tem Brier < 0,5 (aleatório)."""
    df = _frame(600)
    d = ma.prepare(df, "OU25", "ensemble")
    P = ma._probs(d, "OU25", "pm")
    y = d["y"].to_numpy(int)
    assert np.allclose(P[:, 1], d["pm_o"].to_numpy())
    assert ma._brier_rows(P, y).mean() < 0.45


# ---------------------------------------------------------------------------
# §15/§16 — temporal, aninhado, holdout intocado
# ---------------------------------------------------------------------------
def test_nested_walk_forward_is_temporal_and_disjoint():
    df = _frame(900)
    d = ma.prepare(df, "1X2", "ensemble")
    end = d["date"].max() - timedelta(days=REQ.holdout_days)
    oos, windows = ma.nested_walk_forward(d, "1X2", REQ, end=pd.Timestamp(end))
    assert len(windows) >= 2
    prev_end = None
    for w in windows:
        ts, te = pd.Timestamp(w["test_start"]), pd.Timestamp(w["test_end"])
        assert w["train"] >= REQ.min_train and w["validation"] >= REQ.min_validation
        # validação termina onde o teste começa; treino termina onde a validação começa
        val_start = ts - timedelta(days=REQ.validation_days)
        assert (d[d["date"] < val_start].shape[0]) == w["train"]
        assert (d[(d["date"] >= val_start) & (d["date"] < ts)].shape[0]) == w["validation"]
        if prev_end is not None:
            assert ts >= prev_end
        prev_end = te
        assert te <= pd.Timestamp(end)
    # linhas OOS: cada mid aparece uma vez e só dentro da sua janela de teste
    assert oos["mid"].is_unique
    assert (oos["date"] < pd.Timestamp(end)).all()
    # α escolhido pertence ao grid
    assert set(oos["alpha"].unique()) <= set(REQ.alphas)


def test_discovery_never_touches_holdout_and_holdout_is_frozen():
    df = _frame(900)
    rep = ma.run_discovery(df, REQ, dataset_version="v")
    hs = pd.Timestamp(rep["holdout_start"])
    assert pd.Timestamp(rep["period"]["end"]) < hs
    assert rep["holdout_rows"] == int((pd.to_datetime(df["date"]) >= hs).sum())
    assert rep["config_hash"] == REQ.config_hash()
    art = ma.freeze(rep, df, REQ)
    assert art["version"] == "market-aware-frozen-v1" and len(art["model_hash"]) == 16
    for spec in art["markets"].values():
        assert spec["train_rows"] == int((pd.to_datetime(df["date"]) < hs).sum())
        assert spec["alpha"] in REQ.alphas
    hold = ma.run_holdout(art, df, REQ)
    assert hold["phase"].startswith("CONFIRMATION")
    assert hold["model_hash"] == art["model_hash"] and hold["dataset_version"] == "v"
    for ev in hold["markets"].values():
        assert ev["n"] == rep["holdout_rows"]
        assert ev["alpha_frozen"] in REQ.alphas
    # a mesma entrada dá o mesmo artefato (determinístico) — pré-condição para "uma execução por hash"
    assert ma.freeze(rep, df, REQ)["model_hash"] == art["model_hash"]


# ---------------------------------------------------------------------------
# verdict honesto
# ---------------------------------------------------------------------------
def test_verdict_no_edge_when_model_is_noise_and_alpha_one_is_reported():
    df = _frame(900, edge=0.0)
    rep = ma.run_discovery(df, REQ, dataset_version="v")
    for market in REQ.markets:
        ev = rep["markets"][market]
        assert rep["verdict"][market]["status"] in ("NO EVIDENCE OF MARKET EDGE", "PROMISING · NOT CONFIRMED", "INSUFFICIENT DATA")
        assert rep["verdict"][market]["status"] != "CHALLENGER BEATS MARKET (OOS)"
        # nenhum challenger bate o mercado com IC inteiro < 0
        for ch in ("blend", "logistic", "residual"):
            assert not (ev["vs_market"][ch]["delta_brier_ci"]["high"] < 0), (market, ch)
        assert "share_alpha_1" in ev["alpha"]


def test_verdict_detects_real_incremental_information():
    """Se o modelo tiver informação real além do mercado, o blend (α<1) e o residual devem detectá-la."""
    df = _frame(1500, edge=0.8)
    req = ma.MarketAwareRequest(min_train=200, min_validation=60, validation_days=60, test_window_days=45, holdout_days=30, alphas=(0.3, 0.5, 0.8, 1.0), l2_grid=(0.1, 1.0), ablation=False, markets=("1X2",))
    rep = ma.run_discovery(df, req, dataset_version="v")
    ev = rep["markets"]["1X2"]
    assert ev["alpha"]["share_alpha_1"] < 0.5 and ev["alpha"]["note"] is None
    for ch in ("blend", "residual"):
        assert ev["vs_market"][ch]["delta_brier_ci"]["high"] < 0
    v = rep["verdict"]["1X2"]
    assert v["status"] in ("CHALLENGER BEATS MARKET (OOS)", "PROMISING · NOT CONFIRMED")
    crit = v["checks"][v["challenger"]]["criteria"]
    for k in ("brier_better", "logloss_not_worse", "stable_windows", "effective_n"):
        assert crit[k]["pass"], k
    # o mesmo pipeline sem sinal não conclui edge (o detector não é enviesado a favor do modelo)
    rep0 = ma.run_discovery(_frame(1500, edge=0.0), req, dataset_version="v")
    assert rep0["verdict"]["1X2"]["status"] != "CHALLENGER BEATS MARKET (OOS)"


def test_promotion_check_requires_effective_sample():
    ev = {"vs_market": {"blend": {"delta_brier_ci": {"point": -0.01, "low": -0.02, "high": -0.001}, "delta_logloss_ci": {"point": -0.01}, "windows_better": 5, "windows_total": 6}},
          "overall": {"blend": {"ece": 0.02}, "market": {"ece": 0.02}}, "effective_sample": {"effective_n": 120}}
    chk = ma.promotion_check(ev, "blend")
    assert chk["criteria"]["brier_better"]["pass"] and not chk["criteria"]["effective_n"]["pass"] and not chk["pass"]


# ---------------------------------------------------------------------------
# frame do replay (fonte do market-aware)
# ---------------------------------------------------------------------------
def test_replay_frame_has_features_at_T_and_versioned_parquet(tmp_path):
    from edgefut.validation.replay import ReplayRequest, run_replay, save_frame
    from test_strength_v2_replay import _league

    df = _league(teams=10, seasons=3, seed=5)
    rep = run_replay(ReplayRequest(datasets=["SYN"], start=datetime(2023, 8, 1), window_days=90, min_train=50), frames={"SYN": df}, persist=False, return_frame=True)
    frame = rep["_frame_df"]
    assert rep["classification"] == "RESEARCH MARKET BENCHMARK"
    assert len(frame) > 100
    need = {"dataset", "mid", "window", "date", "outcome", "over25", "odds_h", "odds_d", "odds_a", "odds_o25", "odds_u25", "overround_1x2",
            "p_market_h", "p_market_d", "p_market_a", "p_market_ou", "p_ensemble_h", "p_ensemble_ou", "p_poisson_h", "p_elo_h",
            "elo_diff", "att_diff", "def_diff", "home_advantage", "form5_home", "form5_away", "min_team_sample", "month", "days_into_group", "agreement_pp",
            "close_h", "close_d", "close_a"}
    assert need <= set(frame.columns), need - set(frame.columns)
    assert frame["mid"].is_unique
    assert frame["form5_home"].dropna().between(0, 3).all()
    assert frame["p_market_h"].dropna().between(0, 1).all()
    assert (frame[["p_market_h", "p_market_d", "p_market_a"]].dropna().sum(axis=1).round(6) == 1.0).all()
    # persistência versionada
    meta = save_frame(frame, "test", out_dir=tmp_path)
    assert meta["dataset_version"].startswith("replay-frame-v1:") and meta["rows"] == len(frame)
    back = ma.load_frame(meta["path"])
    assert len(back) == len(frame) and ma.frame_version(meta["path"]) == meta["dataset_version"]


# ---------------------------------------------------------------------------
# estatística (bootstrap.py)
# ---------------------------------------------------------------------------
def test_cluster_bootstrap_and_effective_n():
    rng = np.random.default_rng(3)
    clusters = np.repeat(np.arange(60), 4)          # 60 eventos × 4 seleções correlacionadas
    shared = rng.normal(0, 1, 60)
    vals = np.repeat(shared, 4) + rng.normal(0, 0.1, 240)
    ci_c = cluster_bootstrap_ci(vals, clusters, zero_test=True)
    from edgefut.validation.bootstrap import bootstrap_ci
    ci_i = bootstrap_ci(vals, zero_test=True)
    assert ci_c.n == 60
    assert (ci_c.high - ci_c.low) > (ci_i.high - ci_i.low) * 1.3   # ignorar clusters subestima a incerteza
    eff = effective_sample_size(clusters, rho=1.0)
    assert eff["raw_n"] == 240 and eff["clusters"] == 60 and eff["effective_n"] == 60
    eff_half = effective_sample_size(clusters, rho=0.5)
    assert 60 < eff_half["effective_n"] < 240


def test_benjamini_hochberg_controls_fdr():
    p = {"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.3, "e": 0.8}
    out = benjamini_hochberg(p, q=0.10)
    assert "a" in out["survivors"] and "e" not in out["survivors"]
    assert out["adjusted"]["e"] >= out["adjusted"]["a"]
    assert benjamini_hochberg({}, q=0.10)["survivors"] == []


def test_brier_decomposition_sums():
    rng = np.random.default_rng(4)
    p = rng.uniform(0.05, 0.95, 500)
    y = (rng.random(500) < p).astype(float)
    dec = brier_decomposition(p, y)
    assert dec is not None
    assert abs(dec["brier"] - (dec["reliability"] - dec["resolution"] + dec["uncertainty"])) < 0.01
    assert dec["reliability"] < 0.02  # calibrado por construção
    assert brier_decomposition(p[:20], y[:20]) is None
