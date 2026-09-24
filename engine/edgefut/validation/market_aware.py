"""Market-aware challengers — o mercado como prior forte (iteração 4).

Pergunta: *o EdgeFut contém informação incremental além do preço do mercado?*

Entrada: o **frame por partida** do replay (features em T + probabilidades dos modelos + mercado
+ resultado; `replay.save_frame`). Nenhuma coluna `close_*` é usada como feature — só para CLV
(`test_market_aware_never_uses_closing_line`).

Challengers (ordem obrigatória — simples primeiro):

* **A · `market-model-blend-v1`**  p ∝ p_mercado^α · p_edgefut^(1−α), normalizado. α escolhido
  **só no bloco de validação** (nunca no teste); α = 1,0 está no grid como referência — se vencer,
  o EdgeFut não acrescentou nada, e isso é registrado.
* **B · `market-logistic-stack-v1`**  regressão logística multinomial (1X2) / binária (OU 2,5) com
  L2 sobre logit(mercado), logit(modelo), divergência, concordância entre modelos, força, ELO,
  forma, mando, competição e tempo. Poucas features, regularização obrigatória.
* **C · `market-residual-v1`**  log p_mercado entra como *offset* (coeficiente fixo = 1) e o modelo
  aprende só o desvio: "quando a divergência historicamente favoreceu o EdgeFut?".

Validação aninhada no tempo (§15): para cada janela de teste [t, t+90d): validação = 180 d
anteriores; treino = tudo antes da validação. Hiperparâmetros escolhidos na validação; refit em
treino+validação; previsão no teste. **Nunca** split aleatório. O último período é **HOLDOUT
congelado** (§16), só avaliado com arquitetura e hiperparâmetros fixos e registrado com
`model_hash / config_hash / dataset_version / run_timestamp`; uma execução por
(config_hash, dataset_version).

Também aqui: ablation (§14), buckets de divergência (§44), teste contrário (§45), decomposição de
Brier (§43), segmentos com Benjamini-Hochberg (§18) e IC por cluster de evento (§20).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import logsumexp

from ..core.config import settings
from .bootstrap import (
    benjamini_hochberg,
    bootstrap_ci,
    bootstrap_p_value,
    brier_decomposition,
    cluster_bootstrap_ci,
    effective_sample_size,
    lift_pct,
    model_significance,
    paired_bootstrap_diff,
    sample_quality,
)

log = logging.getLogger(__name__)

BLEND = "market-model-blend-v1"
LOGISTIC = "market-logistic-stack-v1"
RESIDUAL = "market-residual-v1"
CHALLENGERS = ("blend", "logistic", "residual")
CHALLENGER_VERSIONS = {"blend": BLEND, "logistic": LOGISTIC, "residual": RESIDUAL}
ALPHAS = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0)
L2_GRID = (0.03, 0.1, 0.3, 1.0, 3.0)
MARKETS = ("1X2", "OU25")
EPS = 1e-6
# grupos de features aprovados (§4) — nada além disto
FEATURE_GROUPS = ("market", "model", "diff", "agreement", "strength", "elo", "form", "home_adv", "competition", "time", "sample")
FULL_FEATURES = FEATURE_GROUPS
RESIDUAL_FEATURES = ("diff", "absdiff", "agreement", "strength", "elo", "home_adv", "competition", "time", "sample")
ABLATION = {
    "market_only": ("market",),
    "market_edgefut": ("market", "model"),
    "market_strength": ("market", "strength"),
    "market_elo": ("market", "elo"),
    "market_form": ("market", "form"),
    "market_home_adv": ("market", "home_adv"),
    "market_all": FULL_FEATURES,
}
FORBIDDEN_COLUMNS = ("close_h", "close_d", "close_a", "close_o25", "close_u25")
DISAGREEMENT_BUCKETS = ((0, 2), (2, 5), (5, 10), (10, 100))
ODDS_BANDS = ((1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 100.0))


@dataclass
class MarketAwareRequest:
    frame_path: str | None = None
    base_model: str = "ensemble"
    markets: tuple[str, ...] = MARKETS
    test_window_days: int = 90
    validation_days: int = 180
    min_train: int = 1000
    min_validation: int = 100
    holdout_days: int = 240            # último período do frame, congelado
    alphas: tuple[float, ...] = ALPHAS
    l2_grid: tuple[float, ...] = L2_GRID
    ablation: bool = True
    max_rows: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["markets"] = list(self.markets)
        d["alphas"] = list(self.alphas)
        d["l2_grid"] = list(self.l2_grid)
        return d

    def config_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# preparação
# ---------------------------------------------------------------------------
def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def prepare(df: pd.DataFrame, market: str, base_model: str) -> pd.DataFrame:
    """Linhas utilizáveis para o mercado: precisam de preço (mercado) e de probabilidade do modelo base."""
    for c in FORBIDDEN_COLUMNS:
        # colunas de fechamento continuam no frame para CLV, mas nunca viram feature
        pass
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    if market == "1X2":
        need = [f"p_market_{k}" for k in "hda"] + [f"p_{base_model}_{k}" for k in "hda"]
        d = d.dropna(subset=need)
        d["y"] = d["outcome"].astype(int)
        for k in "hda":
            d[f"pm_{k}"] = d[f"p_market_{k}"].astype(float)
            d[f"pe_{k}"] = d[f"p_{base_model}_{k}"].astype(float)
        d["fav_odd"] = d[["odds_h", "odds_d", "odds_a"]].min(axis=1)
        d["disagreement_pp"] = (d[[f"pe_{k}" for k in "hda"]].to_numpy() - d[[f"pm_{k}" for k in "hda"]].to_numpy()).__abs__().max(axis=1) * 100
    else:
        need = ["p_market_ou", f"p_{base_model}_ou"]
        d = d.dropna(subset=need)
        d["y"] = d["over25"].astype(int)
        d["pm_o"] = d["p_market_ou"].astype(float)
        d["pe_o"] = d[f"p_{base_model}_ou"].astype(float)
        d["fav_odd"] = d[["odds_o25", "odds_u25"]].min(axis=1)
        d["disagreement_pp"] = (d["pe_o"] - d["pm_o"]).abs() * 100
    return d.sort_values(["date", "dataset", "mid"]).reset_index(drop=True)


@dataclass
class Scaler:
    medians: dict[str, float]
    means: dict[str, float]
    stds: dict[str, float]
    datasets: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _raw_features(d: pd.DataFrame, market: str, groups: tuple[str, ...], datasets: list[str]) -> pd.DataFrame:
    cols: dict[str, np.ndarray] = {}
    n = len(d)
    if market == "1X2":
        pm = d[[f"pm_{k}" for k in "hda"]].to_numpy(float)
        pe = d[[f"pe_{k}" for k in "hda"]].to_numpy(float)
        if "market" in groups:
            for i, k in enumerate("hda"):
                cols[f"log_pm_{k}"] = np.log(np.clip(pm[:, i], EPS, 1))
        if "model" in groups:
            for i, k in enumerate("hda"):
                cols[f"log_pe_{k}"] = np.log(np.clip(pe[:, i], EPS, 1))
        if "diff" in groups:
            for i, k in enumerate("hda"):
                cols[f"diff_{k}"] = pe[:, i] - pm[:, i]
        if "absdiff" in groups:
            cols["absdiff_max"] = np.abs(pe - pm).max(axis=1)
    else:
        pm = d["pm_o"].to_numpy(float)
        pe = d["pe_o"].to_numpy(float)
        if "market" in groups:
            cols["logit_pm"] = _logit(pm)
        if "model" in groups:
            cols["logit_pe"] = _logit(pe)
        if "diff" in groups:
            cols["diff_o"] = pe - pm
        if "absdiff" in groups:
            cols["absdiff_o"] = np.abs(pe - pm)
    if "agreement" in groups:
        cols["agreement"] = d.get("agreement_pp", pd.Series(np.nan, index=d.index)).to_numpy(float) / 10.0
    if "strength" in groups:
        cols["att_diff"] = d.get("att_diff", pd.Series(np.nan, index=d.index)).to_numpy(float)
        cols["def_diff"] = d.get("def_diff", pd.Series(np.nan, index=d.index)).to_numpy(float)
    if "elo" in groups:
        cols["elo_diff"] = d.get("elo_diff", pd.Series(np.nan, index=d.index)).to_numpy(float) / 400.0
    if "form" in groups:
        cols["form_diff"] = (d.get("form5_home", pd.Series(np.nan, index=d.index)) - d.get("form5_away", pd.Series(np.nan, index=d.index))).to_numpy(float)
        cols["gd_diff"] = (d.get("gd5_home", pd.Series(np.nan, index=d.index)) - d.get("gd5_away", pd.Series(np.nan, index=d.index))).to_numpy(float)
    if "home_adv" in groups:
        cols["home_adv"] = d.get("home_advantage", pd.Series(np.nan, index=d.index)).to_numpy(float) - 1.0
        cols["neutral"] = d.get("neutral", pd.Series(False, index=d.index)).astype(float).to_numpy()
    if "competition" in groups:
        ds = d["dataset"].astype(str).to_numpy()
        for code in datasets:
            cols[f"ds_{code}"] = (ds == code).astype(float)
    if "time" in groups:
        month = d.get("month", pd.Series(6, index=d.index)).to_numpy(float)
        cols["month_sin"] = np.sin(2 * np.pi * month / 12.0)
        cols["month_cos"] = np.cos(2 * np.pi * month / 12.0)
        cols["season_progress"] = d.get("days_into_group", pd.Series(np.nan, index=d.index)).to_numpy(float) / 365.0
    if "sample" in groups:
        cols["log_min_sample"] = np.log1p(d.get("min_team_sample", pd.Series(np.nan, index=d.index)).to_numpy(float))
    if not cols:
        return pd.DataFrame(index=range(n))
    return pd.DataFrame(cols)


def fit_scaler(X: pd.DataFrame, datasets: list[str]) -> Scaler:
    med = {c: float(X[c].median()) if X[c].notna().any() else 0.0 for c in X.columns}
    Xf = X.fillna(pd.Series(med))
    means = {c: float(Xf[c].mean()) for c in X.columns}
    stds = {c: float(Xf[c].std(ddof=0)) or 1.0 for c in X.columns}
    for c in X.columns:
        if c.startswith("ds_"):  # one-hot: não escalar
            means[c], stds[c] = 0.0, 1.0
    return Scaler(med, means, stds, datasets)


def transform(X: pd.DataFrame, sc: Scaler) -> np.ndarray:
    Xf = X.fillna(pd.Series(sc.medians))
    out = np.empty(Xf.shape, dtype=float)
    for j, c in enumerate(Xf.columns):
        out[:, j] = (Xf[c].to_numpy(float) - sc.means.get(c, 0.0)) / (sc.stds.get(c, 1.0) or 1.0)
    return out


# ---------------------------------------------------------------------------
# modelos
# ---------------------------------------------------------------------------
def blend(pm: np.ndarray, pe: np.ndarray, alpha: float) -> np.ndarray:
    """p ∝ pm^α · pe^(1−α), normalizado por linha. `pm`/`pe` são matrizes n×K."""
    lp = alpha * np.log(np.clip(pm, EPS, 1)) + (1 - alpha) * np.log(np.clip(pe, EPS, 1))
    lp -= logsumexp(lp, axis=1, keepdims=True)
    return np.exp(lp)


@dataclass
class Multinomial:
    W: np.ndarray            # K × d
    b: np.ndarray            # K
    lam: float
    feature_names: list[str]
    scaler: Scaler
    groups: tuple[str, ...]
    offset: bool = False     # True → log p_mercado entra como offset (residual)
    converged: bool = True

    def predict(self, X: np.ndarray, offset: np.ndarray | None = None) -> np.ndarray:
        Z = X @ self.W.T + self.b
        if self.offset and offset is not None:
            Z = Z + offset
        Z -= logsumexp(Z, axis=1, keepdims=True)
        return np.exp(Z)

    def to_dict(self) -> dict:
        return {"W": self.W.round(6).tolist(), "b": self.b.round(6).tolist(), "lam": self.lam, "features": self.feature_names,
                "scaler": self.scaler.to_dict(), "groups": list(self.groups), "offset": self.offset, "converged": self.converged}

    def coefficients(self) -> dict:
        """Coeficientes por classe (interpretabilidade — §41: nada de caixa-preta)."""
        K = self.W.shape[0]
        labels = ["home", "draw", "away"] if K == 3 else ["under", "over"]
        return {labels[k]: {f: round(float(self.W[k, j]), 4) for j, f in enumerate(self.feature_names)} | {"_intercept": round(float(self.b[k]), 4)} for k in range(K)}


def fit_multinomial(X: np.ndarray, y: np.ndarray, K: int, lam: float, *, offset: np.ndarray | None = None, names: list[str], scaler: Scaler, groups: tuple[str, ...]) -> Multinomial:
    n, d = X.shape
    Y = np.zeros((n, K))
    Y[np.arange(n), y] = 1.0
    off = offset if offset is not None else np.zeros((n, K))

    def unpack(theta):
        return theta[: K * d].reshape(K, d), theta[K * d:]

    def f(theta):
        W, b = unpack(theta)
        Z = X @ W.T + b + off
        lse = logsumexp(Z, axis=1, keepdims=True)
        logp = Z - lse
        loss = -float(np.sum(logp[np.arange(n), y])) / n + lam * float(np.sum(W * W)) / 2.0
        P = np.exp(logp)
        G = (P - Y) / n
        gW = G.T @ X + lam * W
        gb = G.sum(axis=0)
        return loss, np.concatenate([gW.ravel(), gb])

    theta0 = np.zeros(K * d + K)
    res = minimize(f, theta0, jac=True, method="L-BFGS-B", options={"maxiter": 500})
    W, b = unpack(res.x)
    # identificabilidade: centrar interceptos (softmax é invariante a deslocamento)
    b = b - b.mean()
    return Multinomial(W, b, lam, names, scaler, groups, offset=offset is not None, converged=bool(res.success))


# ---------------------------------------------------------------------------
# métricas
# ---------------------------------------------------------------------------
def _brier_rows(P: np.ndarray, y: np.ndarray) -> np.ndarray:
    Y = np.zeros_like(P)
    Y[np.arange(len(y)), y] = 1.0
    return ((P - Y) ** 2).sum(axis=1)


def _ll_rows(P: np.ndarray, y: np.ndarray) -> np.ndarray:
    return -np.log(np.clip(P[np.arange(len(y)), y], EPS, 1))


def _ece(P: np.ndarray, y: np.ndarray, bins: int = 10) -> float | None:
    K = P.shape[1]
    probs = P.ravel()
    outs = np.zeros_like(P)
    outs[np.arange(len(y)), y] = 1.0
    outs = outs.ravel()
    if probs.size < 50:
        return None
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(probs, edges) - 1, 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(probs[m].mean() - outs[m].mean())
    return round(float(ece), 4)


def _metrics(P: np.ndarray, y: np.ndarray, clusters: np.ndarray | None = None) -> dict:
    n = len(y)
    if n == 0:
        return {"n": 0, "sample_quality": sample_quality(0)}
    br, ll = _brier_rows(P, y), _ll_rows(P, y)
    out = {
        "n": n, "sample_quality": sample_quality(n),
        "brier": (cluster_bootstrap_ci(br, clusters) if clusters is not None else bootstrap_ci(br)).to_dict(),
        "log_loss": (cluster_bootstrap_ci(ll, clusters) if clusters is not None else bootstrap_ci(ll)).to_dict(),
        "ece": _ece(P, y), "hit_rate": round(float(np.mean(P.argmax(axis=1) == y)), 4),
    }
    # decomposição de Murphy: soma das decomposições binárias por classe (multiclasse) ou da classe positiva (binário)
    if P.shape[1] == 2:
        out["decomposition"] = brier_decomposition(P[:, 1], (y == 1).astype(float))
    else:
        parts = [brier_decomposition(P[:, k], (y == k).astype(float)) for k in range(P.shape[1])]
        if all(parts):
            out["decomposition"] = {"n": n, "brier": round(sum(p["brier"] for p in parts), 5), "reliability": round(sum(p["reliability"] for p in parts), 5),
                                    "resolution": round(sum(p["resolution"] for p in parts), 5), "uncertainty": round(sum(p["uncertainty"] for p in parts), 5), "bins": 10}
    return out


def _paired(Pa: np.ndarray, Pb: np.ndarray, y: np.ndarray, windows: np.ndarray | None, clusters: np.ndarray | None = None) -> dict:
    """a − b em Brier/LogLoss (negativo = a melhor), IC por cluster de evento, janelas vencidas."""
    ba, bb = _brier_rows(Pa, y), _brier_rows(Pb, y)
    la, lb = _ll_rows(Pa, y), _ll_rows(Pb, y)
    n = len(y)
    if n < 30:
        return {"n": n, "significance": "INSUFFICIENT DATA"}
    d_br = cluster_bootstrap_ci(ba - bb, clusters, zero_test=True) if clusters is not None else paired_bootstrap_diff(ba, bb)
    d_ll = cluster_bootstrap_ci(la - lb, clusters, zero_test=True) if clusters is not None else paired_bootstrap_diff(la, lb)
    wins = total = 0
    if windows is not None:
        for w in np.unique(windows):
            m = windows == w
            total += 1
            wins += int(np.mean(ba[m] - bb[m]) < 0)
    return {
        "n": n, "brier_a": round(float(ba.mean()), 5), "brier_b": round(float(bb.mean()), 5),
        "lift_pct": lift_pct(float(ba.mean()), float(bb.mean())),
        "delta_brier_ci": d_br.to_dict(), "delta_logloss_ci": d_ll.to_dict(),
        "windows_better": wins, "windows_total": total,
        "p_value": bootstrap_p_value(ba - bb, clusters),
        "significance": model_significance(n, d_br, windows_positive=wins if total else None, windows_total=total or None),
    }


# ---------------------------------------------------------------------------
# validação aninhada
# ---------------------------------------------------------------------------
def _probs(d: pd.DataFrame, market: str, prefix: str) -> np.ndarray:
    if market == "1X2":
        return d[[f"{prefix}_{k}" for k in "hda"]].to_numpy(float)
    # OU: classe 0 = under, classe 1 = over (alinhado a y = over25)
    p = d[f"{prefix}_o"].to_numpy(float)
    return np.column_stack([1 - p, p])


def _fit_logistic(train: pd.DataFrame, market: str, groups: tuple[str, ...], lam: float, datasets: list[str], residual: bool) -> Multinomial:
    Xdf = _raw_features(train, market, groups, datasets)
    sc = fit_scaler(Xdf, datasets)
    X = transform(Xdf, sc)
    y = train["y"].to_numpy(int)
    K = 3 if market == "1X2" else 2
    off = np.log(np.clip(_probs(train, market, "pm"), EPS, 1)) if residual else None
    return fit_multinomial(X, y, K, lam, offset=off, names=list(Xdf.columns), scaler=sc, groups=groups)


def _predict_logistic(model: Multinomial, d: pd.DataFrame, market: str, datasets: list[str]) -> np.ndarray:
    Xdf = _raw_features(d, market, model.groups, datasets)
    Xdf = Xdf.reindex(columns=model.feature_names)
    X = transform(Xdf, model.scaler)
    off = np.log(np.clip(_probs(d, market, "pm"), EPS, 1)) if model.offset else None
    return model.predict(X, off)


def _choose_alpha(train: pd.DataFrame, val: pd.DataFrame, market: str, alphas: tuple[float, ...]) -> tuple[float, dict]:
    """α por LogLoss no bloco de validação (o treino não é usado: o blend não tem parâmetros além de α)."""
    pm, pe, y = _probs(val, market, "pm"), _probs(val, market, "pe"), val["y"].to_numpy(int)
    scores = {a: float(_ll_rows(blend(pm, pe, a), y).mean()) for a in alphas}
    best = min(scores, key=scores.get)
    return best, {str(a): round(v, 5) for a, v in scores.items()}


def _choose_lambda(train: pd.DataFrame, val: pd.DataFrame, market: str, groups: tuple[str, ...], grid: tuple[float, ...], datasets: list[str], residual: bool) -> tuple[float, dict]:
    y = val["y"].to_numpy(int)
    scores = {}
    for lam in grid:
        m = _fit_logistic(train, market, groups, lam, datasets, residual)
        scores[lam] = float(_ll_rows(_predict_logistic(m, val, market, datasets), y).mean())
    best = min(scores, key=scores.get)
    return best, {str(k): round(v, 5) for k, v in scores.items()}


def nested_walk_forward(d: pd.DataFrame, market: str, req: MarketAwareRequest, *, end: pd.Timestamp) -> tuple[pd.DataFrame, list[dict]]:
    """Devolve previsões OOS (só janelas de teste) e o log de hiperparâmetros por janela."""
    datasets = sorted(d["dataset"].astype(str).unique().tolist())
    first = d["date"].min()
    # primeira janela de teste: quando já há min_train linhas antes da validação
    cutoff_idx = min(len(d) - 1, req.min_train)
    t0 = max(d["date"].iloc[cutoff_idx] + timedelta(days=req.validation_days), first + timedelta(days=req.validation_days + 30))
    t0 = pd.Timestamp(t0.date())
    rows: list[pd.DataFrame] = []
    windows: list[dict] = []
    w = 0
    ts = t0
    K = 3 if market == "1X2" else 2
    while ts < end:
        te = min(ts + timedelta(days=req.test_window_days), end)
        vs = ts - timedelta(days=req.validation_days)
        train = d[d["date"] < vs]
        val = d[(d["date"] >= vs) & (d["date"] < ts)]
        test = d[(d["date"] >= ts) & (d["date"] < te)]
        if len(train) < req.min_train or len(val) < req.min_validation or test.empty:
            ts = te
            continue
        w += 1
        fit_all = pd.concat([train, val])
        alpha, alpha_scores = _choose_alpha(train, val, market, req.alphas)
        lam_logit, logit_scores = _choose_lambda(train, val, market, FULL_FEATURES, req.l2_grid, datasets, False)
        lam_res, res_scores = _choose_lambda(train, val, market, RESIDUAL_FEATURES, req.l2_grid, datasets, True)
        m_logit = _fit_logistic(fit_all, market, FULL_FEATURES, lam_logit, datasets, False)
        m_res = _fit_logistic(fit_all, market, RESIDUAL_FEATURES, lam_res, datasets, True)
        pm, pe = _probs(test, market, "pm"), _probs(test, market, "pe")
        out = test[["dataset", "mid", "date", "y", "fav_odd", "disagreement_pp"]].copy()
        out["window"] = w
        out["alpha"] = alpha
        P = {"market": pm, "edgefut": pe, "blend": blend(pm, pe, alpha), "logistic": _predict_logistic(m_logit, test, market, datasets), "residual": _predict_logistic(m_res, test, market, datasets)}
        abl_lams: dict[str, float] = {}
        if req.ablation:
            for name, groups in ABLATION.items():
                if name == "market_all":
                    P[f"abl_{name}"] = P["logistic"]
                    abl_lams[name] = lam_logit
                    continue
                lam_a, _ = _choose_lambda(train, val, market, groups, req.l2_grid, datasets, False)
                m_a = _fit_logistic(fit_all, market, groups, lam_a, datasets, False)
                P[f"abl_{name}"] = _predict_logistic(m_a, test, market, datasets)
                abl_lams[name] = lam_a
        for name, mat in P.items():
            for k in range(K):
                out[f"{name}_{k}"] = mat[:, k]
        rows.append(out)
        windows.append({"window": w, "test_start": ts.date().isoformat(), "test_end": te.date().isoformat(), "train": len(train), "validation": len(val), "test": len(test),
                        "alpha": alpha, "alpha_scores": alpha_scores, "lambda_logistic": lam_logit, "logistic_scores": logit_scores,
                        "lambda_residual": lam_res, "residual_scores": res_scores, "ablation_lambdas": abl_lams,
                        "logistic_converged": m_logit.converged, "residual_converged": m_res.converged})
        ts = te
    oos = pd.concat(rows).reset_index(drop=True) if rows else pd.DataFrame()
    return oos, windows


def _P(oos: pd.DataFrame, name: str, K: int) -> np.ndarray:
    return oos[[f"{name}_{k}" for k in range(K)]].to_numpy(float)


def evaluate_oos(oos: pd.DataFrame, market: str, ablation: bool) -> dict:
    if oos.empty:
        return {"n": 0, "note": "sem janelas de teste suficientes"}
    K = 3 if market == "1X2" else 2
    y = oos["y"].to_numpy(int)
    clusters = (oos["dataset"].astype(str) + ":" + oos["mid"].astype(str)).to_numpy()
    windows = oos["window"].to_numpy()
    names = ["market", "edgefut", "blend", "logistic", "residual"] + ([f"abl_{k}" for k in ABLATION] if ablation else [])
    mats = {n: _P(oos, n, K) for n in names if f"{n}_0" in oos.columns}
    overall = {n: _metrics(P, y, clusters) for n, P in mats.items()}
    vs_market = {n: _paired(P, mats["market"], y, windows, clusters) for n, P in mats.items() if n != "market"}
    vs_edgefut = {n: _paired(P, mats["edgefut"], y, windows, clusters) for n, P in mats.items() if n not in ("market", "edgefut")}
    ranking = sorted(((n, overall[n]["brier"]["point"]) for n in mats), key=lambda kv: kv[1])
    alphas = oos.groupby("window")["alpha"].first()
    alpha_summary = {"chosen_per_window": {int(w): float(a) for w, a in alphas.items()}, "mean": round(float(alphas.mean()), 3),
                     "share_alpha_1": round(float((alphas >= 0.999).mean()), 3), "note": None}
    if alpha_summary["share_alpha_1"] >= 0.5:
        alpha_summary["note"] = "α = 1,0 (mercado puro) venceu na validação na maioria das janelas: o EdgeFut não acrescentou informação ao mercado neste mercado."
    # ablation resumo
    ablation_table = None
    if ablation:
        ablation_table = []
        for k in ABLATION:
            n = f"abl_{k}"
            if n in overall:
                ablation_table.append({"variant": k, "features": list(ABLATION[k]), "brier": overall[n]["brier"]["point"], "log_loss": overall[n]["log_loss"]["point"],
                                       "delta_brier_vs_market": vs_market[n]["delta_brier_ci"] if "delta_brier_ci" in vs_market[n] else None,
                                       "significance": vs_market[n]["significance"]})
    return {
        "market": market, "n": int(len(y)), "windows": int(len(np.unique(windows))),
        "effective_sample": effective_sample_size(clusters, rho=1.0), "overall": overall, "vs_market": vs_market, "vs_edgefut": vs_edgefut,
        "ranking_brier": ranking, "alpha": alpha_summary, "ablation": ablation_table,
        "disagreement_buckets": disagreement_buckets(oos, market), "contrarian": contrarian_test(oos, market),
        "segments": segment_analysis(oos, market), "by_dataset": by_dataset(oos, market),
    }


# ---------------------------------------------------------------------------
# análises exploratórias (com FDR)
# ---------------------------------------------------------------------------
def disagreement_buckets(oos: pd.DataFrame, market: str) -> list[dict]:
    """§44: abs(modelo − mercado) por faixa → quem acertou mais? O Brier de cada um por bucket."""
    K = 3 if market == "1X2" else 2
    y = oos["y"].to_numpy(int)
    dis = oos["disagreement_pp"].to_numpy(float)
    out = []
    for lo, hi in DISAGREEMENT_BUCKETS:
        m = (dis >= lo) & (dis < hi)
        n = int(m.sum())
        row: dict = {"bucket": f"{lo}-{hi if hi < 100 else '+'} pp", "n": n, "sample_quality": sample_quality(n)}
        if n >= 30:
            for name in ("market", "edgefut", "blend", "logistic", "residual"):
                if f"{name}_0" in oos.columns:
                    row[f"brier_{name}"] = round(float(_brier_rows(_P(oos[m], name, K), y[m]).mean()), 5)
            pm, pe = _P(oos[m], "market", K), _P(oos[m], "edgefut", K)
            d = _paired(pe, pm, y[m], None, (oos[m]["dataset"].astype(str) + ":" + oos[m]["mid"].astype(str)).to_numpy())
            row["edgefut_vs_market"] = {"delta_brier_ci": d.get("delta_brier_ci"), "significance": d.get("significance")}
            # lado que o modelo favorece mais que o mercado: acertou com que frequência vs o que cada um previa?
            side = np.argmax(pe - pm, axis=1)
            hit = (y[m] == side).astype(float)
            row["favored_side"] = {"observed_rate": round(float(hit.mean()), 4), "market_prob": round(float(pm[np.arange(n), side].mean()), 4), "model_prob": round(float(pe[np.arange(n), side].mean()), 4)}
        out.append(row)
    return out


def contrarian_test(oos: pd.DataFrame, market: str, threshold_pp: float = 5.0) -> dict:
    """§45: quando o EdgeFut diverge ≥ 5 pp do mercado, o lado favorecido pelo modelo acontece com a
    frequência do modelo (edge real) ou a do mercado (mercado certo)?"""
    K = 3 if market == "1X2" else 2
    y = oos["y"].to_numpy(int)
    pm, pe = _P(oos, "market", K), _P(oos, "edgefut", K)
    diff = pe - pm
    side = np.argmax(diff, axis=1)
    strong = diff[np.arange(len(y)), side] * 100 >= threshold_pp
    n = int(strong.sum())
    if n < 30:
        return {"n": n, "verdict": "INSUFFICIENT DATA", "threshold_pp": threshold_pp}
    hit = (y[strong] == side[strong]).astype(float)
    obs = float(hit.mean())
    m_prob = float(pm[strong, side[strong]].mean())
    e_prob = float(pe[strong, side[strong]].mean())
    clusters = (oos[strong]["dataset"].astype(str) + ":" + oos[strong]["mid"].astype(str)).to_numpy()
    ci = cluster_bootstrap_ci(hit, clusters)
    closer = "MARKET" if abs(obs - m_prob) <= abs(obs - e_prob) else "EDGEFUT"
    # ROI hipotético de apostar sempre o lado favorecido pelo modelo a odd justa do mercado (sem margem)
    verdict = "MARKET CORRECT" if closer == "MARKET" else "MODEL CORRECT"
    if ci.low is not None and ci.low <= m_prob <= ci.high and ci.low <= e_prob <= ci.high:
        verdict = "INCONCLUSIVE"
    return {"n": n, "threshold_pp": threshold_pp, "observed_rate": round(obs, 4), "observed_ci": ci.to_dict(), "market_prob": round(m_prob, 4), "edgefut_prob": round(e_prob, 4),
            "closer_to_observed": closer, "verdict": verdict,
            "note": "Quando o EdgeFut discorda forte do mercado, a frequência observada do lado favorecido diz quem estava calibrado. Divergência ≠ edge."}


def _odds_band(o: float) -> str:
    for lo, hi in ODDS_BANDS:
        if lo <= o < hi:
            return f"{lo:.2f}-{hi:.2f}" if hi < 100 else f"{lo:.2f}+"
    return "n/a"


def segment_analysis(oos: pd.DataFrame, market: str, challenger: str = "blend") -> dict:
    """§18/§38: por competição e faixa de odd do favorito — quem vence? Com Benjamini-Hochberg sobre os
    p-valores do bootstrap pareado (challenger − mercado). Só segmentos com N ≥ 100 são testados."""
    K = 3 if market == "1X2" else 2
    y = oos["y"].to_numpy(int)
    cl = (oos["dataset"].astype(str) + ":" + oos["mid"].astype(str)).to_numpy()
    keys: dict[str, np.ndarray] = {}
    for ds in sorted(oos["dataset"].astype(str).unique()):
        keys[f"competition:{ds}"] = (oos["dataset"].astype(str) == ds).to_numpy()
    bands = oos["fav_odd"].apply(lambda v: _odds_band(float(v)) if pd.notna(v) else "n/a").to_numpy()
    for b in sorted(set(bands)):
        if b != "n/a":
            keys[f"odds_band:{b}"] = bands == b
    rows = []
    pvals = {}
    for key, m in keys.items():
        n = int(m.sum())
        row: dict = {"segment": key, "n": n, "sample_quality": sample_quality(n)}
        if n >= 100:
            pm, pc, pe = _P(oos[m], "market", K), _P(oos[m], challenger, K), _P(oos[m], "edgefut", K)
            bm, bc, be = _brier_rows(pm, y[m]), _brier_rows(pc, y[m]), _brier_rows(pe, y[m])
            ci = cluster_bootstrap_ci(bc - bm, cl[m], zero_test=True)
            row.update({"brier_market": round(float(bm.mean()), 5), f"brier_{challenger}": round(float(bc.mean()), 5), "brier_edgefut": round(float(be.mean()), 5),
                        "delta_ci": ci.to_dict(), "p_value": bootstrap_p_value(bc - bm, cl[m])})
            row["winner"] = "INCONCLUSIVE" if not ci.conclusive else ("MODEL" if ci.high < 0 else "MARKET")
            if row["p_value"] is not None:
                pvals[key] = row["p_value"]
        else:
            row["winner"] = "INSUFFICIENT"
        rows.append(row)
    fdr = benjamini_hochberg(pvals, q=0.10)
    for r in rows:
        r["fdr_adjusted_p"] = fdr["adjusted"].get(r["segment"])
        r["survives_fdr"] = r["segment"] in fdr["survivors"]
        if r.get("winner") == "MODEL" and not r["survives_fdr"]:
            r["winner"] = "INCONCLUSIVE (FDR)"
    return {"challenger": challenger, "rows": rows, "fdr": fdr,
            "note": "Segmentos onde o challenger vence o mercado só viram HIPÓTESE (fase de confirmação em período futuro); nunca regra."}


def by_dataset(oos: pd.DataFrame, market: str) -> dict:
    K = 3 if market == "1X2" else 2
    out = {}
    for ds in sorted(oos["dataset"].astype(str).unique()):
        m = (oos["dataset"].astype(str) == ds).to_numpy()
        y = oos[m]["y"].to_numpy(int)
        out[ds] = {"n": int(m.sum()), **{name: round(float(_brier_rows(_P(oos[m], name, K), y).mean()), 5) for name in ("market", "edgefut", "blend", "logistic", "residual") if f"{name}_0" in oos.columns}}
    return out


# ---------------------------------------------------------------------------
# execução: discovery (nested) e frozen holdout
# ---------------------------------------------------------------------------
def load_frame(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df


def frame_version(path: str) -> str:
    return "replay-frame-v1:" + hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def run_discovery(frame: pd.DataFrame, req: MarketAwareRequest, *, dataset_version: str) -> dict:
    """Fase DISCOVERY (§17): validação aninhada em tudo que **não** é holdout. Nunca toca o holdout."""
    t0 = time.perf_counter()
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    last = frame["date"].max()
    holdout_start = pd.Timestamp((last - timedelta(days=req.holdout_days)).date())
    work = frame[frame["date"] < holdout_start]
    if req.max_rows:
        work = work.tail(req.max_rows)
    report: dict = {"phase": "DISCOVERY", "request": req.to_dict(), "config_hash": req.config_hash(), "dataset_version": dataset_version,
                    "holdout_start": holdout_start.isoformat(), "holdout_rows": int((frame["date"] >= holdout_start).sum()), "discovery_rows": int(len(work)),
                    "period": {"start": work["date"].min().isoformat() if len(work) else None, "end": work["date"].max().isoformat() if len(work) else None},
                    "markets": {}, "classification": "RESEARCH MARKET BENCHMARK",
                    "features": {"approved_groups": list(FEATURE_GROUPS), "residual_groups": list(RESIDUAL_FEATURES), "forbidden": list(FORBIDDEN_COLUMNS), "data_quality": "UNAVAILABLE_IN_REPLAY"}}
    frozen_spec: dict = {}
    for market in req.markets:
        d = prepare(work, market, req.base_model)
        if len(d) < req.min_train + 200:
            report["markets"][market] = {"n": int(len(d)), "note": "linhas insuficientes"}
            continue
        oos, windows = nested_walk_forward(d, market, req, end=holdout_start)
        ev = evaluate_oos(oos, market, req.ablation)
        ev["windows_log"] = windows
        report["markets"][market] = ev
        # especificação congelável: hiperparâmetros escolhidos na ÚLTIMA validação (nunca no holdout)
        if windows:
            lastw = windows[-1]
            frozen_spec[market] = {"alpha": lastw["alpha"], "lambda_logistic": lastw["lambda_logistic"], "lambda_residual": lastw["lambda_residual"]}
    report["frozen_spec_candidate"] = frozen_spec
    report["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    report["generated_at"] = datetime.utcnow().isoformat()
    report["verdict"] = _verdict(report)
    return report


MIN_EFFECTIVE_N = 300          # §29: N efetivo adequado (= sample_moderate_min da governança)
MIN_WINDOW_SHARE = 0.60        # §29: estabilidade temporal
ECE_TOLERANCE = 0.005          # §29: calibração não pior


def promotion_check(ev: dict, challenger: str) -> dict:
    """Critérios do §29 para um challenger vs mercado, todos obrigatórios:
    Brier OOS melhor (IC do delta inteiro < 0), LogLoss não pior, calibração não pior, estável entre
    janelas, IC bootstrap por cluster, N efetivo adequado. Devolve cada critério com valor e pass/fail."""
    vm = (ev.get("vs_market") or {}).get(challenger) or {}
    ov = ev.get("overall") or {}
    br = vm.get("delta_brier_ci") or {}
    ll = vm.get("delta_logloss_ci") or {}
    eff = (ev.get("effective_sample") or {}).get("effective_n") or 0
    ece_c, ece_m = (ov.get(challenger) or {}).get("ece"), (ov.get("market") or {}).get("ece")
    wt, wb = vm.get("windows_total") or 0, vm.get("windows_better") or 0
    crit = {
        "brier_better": {"value": br.get("high"), "pass": br.get("high") is not None and br["high"] < 0},
        "logloss_not_worse": {"value": ll.get("point"), "pass": ll.get("point") is not None and ll["point"] <= 0},
        "calibration_not_worse": {"value": None if ece_c is None or ece_m is None else round(ece_c - ece_m, 4), "pass": ece_c is not None and ece_m is not None and ece_c <= ece_m + ECE_TOLERANCE},
        "stable_windows": {"value": f"{wb}/{wt}", "pass": wt > 0 and wb / wt >= MIN_WINDOW_SHARE},
        "effective_n": {"value": eff, "pass": eff >= MIN_EFFECTIVE_N},
    }
    return {"challenger": challenger, "criteria": crit, "pass": all(c["pass"] for c in crit.values()), "delta_brier": br.get("point"), "significance": vm.get("significance")}


def _verdict(report: dict) -> dict:
    """Resposta objetiva por mercado (§29/§40): algum challenger bate o mercado OOS com TODOS os critérios?"""
    out = {}
    for market, ev in report.get("markets", {}).items():
        if not ev.get("vs_market"):
            out[market] = {"status": "INSUFFICIENT DATA", "challenger": None, "checks": {}}
            continue
        checks = {ch: promotion_check(ev, ch) for ch in CHALLENGERS if ch in ev["vs_market"]}
        passing = [c for c in checks.values() if c["pass"]]
        if passing:
            best = min(passing, key=lambda c: c["delta_brier"])
            out[market] = {"status": "CHALLENGER BEATS MARKET (OOS)", "challenger": best["challenger"], "delta_brier": best["delta_brier"], "significance": best["significance"], "checks": checks}
            continue
        promising = [c for c in checks.values() if c["criteria"]["brier_better"]["pass"]]
        if promising:
            best = min(promising, key=lambda c: c["delta_brier"])
            failed = [k for k, v in best["criteria"].items() if not v["pass"]]
            out[market] = {"status": "PROMISING · NOT CONFIRMED", "challenger": best["challenger"], "delta_brier": best["delta_brier"], "significance": best["significance"], "failed": failed, "checks": checks}
            continue
        n = ev.get("n") or 0
        status = "INSUFFICIENT DATA" if n < settings.sample_early_min else "NO EVIDENCE OF MARKET EDGE"
        out[market] = {"status": status, "challenger": None, "checks": checks}
    return out


def freeze(report: dict, frame: pd.DataFrame, req: MarketAwareRequest) -> dict:
    """Congela arquitetura + hiperparâmetros do discovery e ajusta os modelos finais em TODO o período
    de discovery (sem holdout). Devolve artefato serializável com `model_hash`."""
    spec = report.get("frozen_spec_candidate") or {}
    holdout_start = pd.Timestamp(report["holdout_start"])
    work = frame[pd.to_datetime(frame["date"]) < holdout_start]
    artifact: dict = {"version": "market-aware-frozen-v1", "config_hash": report["config_hash"], "dataset_version": report["dataset_version"],
                      "holdout_start": report["holdout_start"], "base_model": req.base_model, "frozen_at": datetime.utcnow().isoformat(), "markets": {}}
    for market, hp in spec.items():
        d = prepare(work, market, req.base_model)
        datasets = sorted(d["dataset"].astype(str).unique().tolist())
        m_logit = _fit_logistic(d, market, FULL_FEATURES, hp["lambda_logistic"], datasets, False)
        m_res = _fit_logistic(d, market, RESIDUAL_FEATURES, hp["lambda_residual"], datasets, True)
        # challenger "selecionado" para a UI: o melhor OOS no discovery entre os que bateram o mercado; senão blend
        vm = (report["markets"].get(market) or {}).get("vs_market") or {}
        ranked = sorted(((ch, (vm.get(ch) or {}).get("brier_a") or 9) for ch in CHALLENGERS), key=lambda kv: kv[1])
        artifact["markets"][market] = {"alpha": hp["alpha"], "datasets": datasets, "logistic": m_logit.to_dict(), "residual": m_res.to_dict(),
                                       "selected": ranked[0][0] if ranked else "blend", "train_rows": int(len(d)),
                                       "coefficients": {"logistic": m_logit.coefficients(), "residual": m_res.coefficients()}}
    payload = json.dumps({k: v for k, v in artifact.items() if k != "frozen_at"}, sort_keys=True, default=str)
    artifact["model_hash"] = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return artifact


def _model_from_dict(dct: dict) -> Multinomial:
    sc = dct["scaler"]
    return Multinomial(np.array(dct["W"]), np.array(dct["b"]), dct["lam"], list(dct["features"]), Scaler(sc["medians"], sc["means"], sc["stds"], sc["datasets"]), tuple(dct["groups"]), offset=bool(dct["offset"]), converged=bool(dct.get("converged", True)))


def predict_frozen(artifact: dict, market: str, pm: np.ndarray, pe: np.ndarray, features: pd.DataFrame | None = None) -> dict[str, np.ndarray]:
    """Aplica o artefato congelado a novas linhas. `features` = frame com as colunas de contexto; se
    ausente, só o blend é calculado (o logístico precisa das features)."""
    spec = artifact["markets"].get(market)
    if not spec:
        return {}
    out = {"blend": blend(pm, pe, float(spec["alpha"]))}
    if features is not None:
        d = features.copy()
        if market == "1X2":
            for i, k in enumerate("hda"):
                d[f"pm_{k}"], d[f"pe_{k}"] = pm[:, i], pe[:, i]
        else:
            d["pm_o"], d["pe_o"] = pm[:, 1], pe[:, 1]
        for name in ("logistic", "residual"):
            m = _model_from_dict(spec[name])
            out[name] = _predict_logistic(m, d, market, spec["datasets"])
    return out


def run_holdout(artifact: dict, frame: pd.DataFrame, req: MarketAwareRequest) -> dict:
    """Fase CONFIRMATION (§16/§17): avalia o artefato congelado no holdout. Uma vez por
    (config_hash, dataset_version) — quem chama garante isso (router). Nada é reajustado aqui."""
    t0 = time.perf_counter()
    holdout_start = pd.Timestamp(artifact["holdout_start"])
    hold = frame[pd.to_datetime(frame["date"]) >= holdout_start]
    report: dict = {"phase": "CONFIRMATION (FROZEN HOLDOUT)", "config_hash": artifact["config_hash"], "model_hash": artifact["model_hash"], "dataset_version": artifact["dataset_version"],
                    "run_timestamp": datetime.utcnow().isoformat(), "holdout_start": artifact["holdout_start"], "holdout_rows": int(len(hold)), "markets": {}, "classification": "RESEARCH MARKET BENCHMARK"}
    for market, spec in artifact["markets"].items():
        d = prepare(hold, market, artifact["base_model"])
        if d.empty:
            report["markets"][market] = {"n": 0}
            continue
        K = 3 if market == "1X2" else 2
        pm, pe, y = _probs(d, market, "pm"), _probs(d, market, "pe"), d["y"].to_numpy(int)
        preds = predict_frozen(artifact, market, pm, pe, d)
        mats = {"market": pm, "edgefut": pe, **preds}
        clusters = (d["dataset"].astype(str) + ":" + d["mid"].astype(str)).to_numpy()
        # janelas do holdout: blocos de 30 dias para a estabilidade
        windows = ((d["date"] - holdout_start).dt.days // 30).to_numpy()
        ev = {"n": int(len(y)), "effective_sample": effective_sample_size(clusters, rho=1.0), "overall": {n: _metrics(P, y, clusters) for n, P in mats.items()},
              "vs_market": {n: _paired(P, pm, y, windows, clusters) for n, P in mats.items() if n != "market"},
              "ranking_brier": sorted(((n, round(float(_brier_rows(P, y).mean()), 5)) for n, P in mats.items()), key=lambda kv: kv[1]),
              "alpha_frozen": spec["alpha"], "selected": spec["selected"]}
        # buckets e contrário também no holdout (confirmação das hipóteses do discovery)
        oos = d[["dataset", "mid", "date", "y", "fav_odd", "disagreement_pp"]].copy()
        for n, P in mats.items():
            for k in range(K):
                oos[f"{n}_{k}"] = P[:, k]
        ev["disagreement_buckets"] = disagreement_buckets(oos, market)
        ev["contrarian"] = contrarian_test(oos, market)
        ev["segments"] = segment_analysis(oos, market)
        report["markets"][market] = ev
    report["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    report["verdict"] = _verdict(report)
    return report


__all__ = [
    "MarketAwareRequest", "prepare", "blend", "fit_multinomial", "nested_walk_forward", "evaluate_oos", "run_discovery", "freeze", "run_holdout",
    "predict_frozen", "load_frame", "frame_version", "disagreement_buckets", "contrarian_test", "segment_analysis",
    "CHALLENGERS", "CHALLENGER_VERSIONS", "FEATURE_GROUPS", "FORBIDDEN_COLUMNS", "ABLATION", "BLEND", "LOGISTIC", "RESIDUAL",
]
