"""Bivariate Poisson (goals-bivariate-poisson-v1) — Karlis & Ntzoufras (2003).

X = X1 + X3 (gols do mandante), Y = X2 + X3 (gols do visitante), com X1, X2, X3
Poisson independentes de médias λ1, λ2, λ3. λ3 é a covariância entre os gols das duas
equipes (jogos "abertos"/"fechados" em conjunto) e substitui a correção τ do Dixon-Coles.

Ajuste (pragmático e reprodutível):
1. Ataque/defesa/vantagem de mando por MLE Poisson ponderado no tempo (ξ = 0.0018/dia),
   reutilizando o otimizador do Dixon-Coles com ρ fixado em 0.
2. λ3 por MLE unidimensional (bounded) na verossimilhança bivariada ponderada, com as
   marginais fixas: λ1 = max(ε, λ_home − λ3), λ2 = max(ε, λ_away − λ3).

Não substitui Poisson/Dixon-Coles: entra na comparação de modelos e no consenso.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from math import lgamma

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar

from ..core import versions
from ..domain.analysis import GoalsModelOutput
from .dixon_coles import MIN_MATCHES, XI_PER_DAY, _neg_ll_and_grad
from .goals_common import MAX_GOALS, markets_from_matrix

log = logging.getLogger(__name__)

EPS = 0.05


@dataclass
class BPParams:
    teams: list[str]
    attack: dict[str, float] = field(default_factory=dict)
    defense: dict[str, float] = field(default_factory=dict)
    gamma: float = 0.0
    lambda3: float = 0.0
    matches: int = 0
    converged: bool = False
    fitted_at: datetime | None = None

    def marginals(self, home: str, away: str, home_adv_weight: float) -> tuple[float, float]:
        lam = float(np.exp(self.attack[home] + self.defense[away] + self.gamma * home_adv_weight))
        mu = float(np.exp(self.attack[away] + self.defense[home]))
        return lam, mu


def _log_factorials(n: int) -> np.ndarray:
    return np.array([lgamma(k + 1) for k in range(n)])


_LF = _log_factorials(MAX_GOALS + 1)


def bivariate_pmf_matrix(l1: float, l2: float, l3: float, n: int = MAX_GOALS) -> np.ndarray:
    """Matriz P(X=x, Y=y) para x, y em 0..n-1 (massa residual no último bucket)."""
    l1, l2, l3 = max(l1, 1e-9), max(l2, 1e-9), max(l3, 0.0)
    m = np.zeros((n, n))
    base = -(l1 + l2 + l3)
    ratio = l3 / (l1 * l2) if l3 > 0 else 0.0
    for x in range(n):
        for y in range(n):
            kmax = min(x, y)
            if ratio == 0.0:
                s = 1.0
            else:
                terms = []
                for k in range(kmax + 1):
                    # C(x,k) C(y,k) k! r^k  em log
                    lt = (_LF[x] - _LF[k] - _LF[x - k]) + (_LF[y] - _LF[k] - _LF[y - k]) + _LF[k] + k * np.log(ratio)
                    terms.append(lt)
                mx = max(terms)
                s = float(np.exp(mx) * np.sum(np.exp(np.array(terms) - mx)))
            m[x, y] = np.exp(base + x * np.log(l1) + y * np.log(l2) - _LF[x] - _LF[y]) * s
    resid = max(0.0, 1.0 - m.sum())
    m[-1, -1] += resid
    return m / m.sum()


def _bp_loglik(l3: float, lam: np.ndarray, mu: np.ndarray, x: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    """Log-verossimilhança ponderada da bivariada com marginais fixas (vetorizada por par único)."""
    l1 = np.clip(lam - l3, EPS, None)
    l2 = np.clip(mu - l3, EPS, None)
    xi = x.astype(int)
    yi = y.astype(int)
    kmax = np.minimum(xi, yi)
    log_base = -(l1 + l2 + l3) + xi * np.log(l1) + yi * np.log(l2) - _LF[np.clip(xi, 0, MAX_GOALS)] - _LF[np.clip(yi, 0, MAX_GOALS)]
    if l3 <= 0:
        return float((w * log_base).sum())
    ratio = l3 / (l1 * l2)
    K = int(kmax.max()) + 1
    ks = np.arange(K)[None, :]
    valid = ks <= kmax[:, None]
    xk = np.clip(xi[:, None] - ks, 0, MAX_GOALS)
    yk = np.clip(yi[:, None] - ks, 0, MAX_GOALS)
    lt = (_LF[np.clip(xi, 0, MAX_GOALS)][:, None] - _LF[ks] - _LF[xk]) + (_LF[np.clip(yi, 0, MAX_GOALS)][:, None] - _LF[ks] - _LF[yk]) + _LF[ks] + ks * np.log(ratio)[:, None]
    lt = np.where(valid, lt, -np.inf)
    mx = lt.max(axis=1)
    log_s = mx + np.log(np.exp(lt - mx[:, None]).sum(axis=1))
    return float((w * (log_base + log_s)).sum())


def fit_bivariate_poisson(df: pd.DataFrame, reference_date: datetime | None = None) -> BPParams | None:
    if df is None or len(df) < MIN_MATCHES:
        return None
    df = df.dropna(subset=["hg", "ag"]).copy()
    df = df[(df["hg"] < MAX_GOALS) & (df["ag"] < MAX_GOALS)]
    teams = sorted(set(df["home"]) | set(df["away"]))
    idx = {t: i for i, t in enumerate(teams)}
    hi = df["home"].map(idx).to_numpy()
    ai = df["away"].map(idx).to_numpy()
    x = df["hg"].astype(float).to_numpy()
    y = df["ag"].astype(float).to_numpy()
    ref = pd.Timestamp(reference_date or datetime.utcnow())
    days = (ref - pd.to_datetime(df["date"])).dt.days.clip(lower=0).to_numpy()
    w = np.exp(-XI_PER_DAY * days)
    n = len(teams)
    theta0 = np.concatenate([np.zeros(n), np.zeros(n), [0.25], [0.0]])
    bounds = [(-3, 3)] * (2 * n) + [(-1, 1), (0.0, 0.0)]  # ρ fixo = 0 → Poisson independente
    res = minimize(
        _neg_ll_and_grad, theta0, args=(hi, ai, x, y, w, n), jac=True, method="L-BFGS-B",
        bounds=bounds, options={"maxiter": 1500, "maxfun": 30000},
    )
    theta = res.x
    attack = theta[:n]
    defense = theta[n : 2 * n]
    gamma = float(theta[-2])
    lam = np.exp(attack[hi] + defense[ai] + gamma)
    mu = np.exp(attack[ai] + defense[hi])
    upper = float(max(EPS, min(lam.min(), mu.min()) - EPS, 0.6))
    r = minimize_scalar(lambda l3: -_bp_loglik(l3, lam, mu, x, y, w), bounds=(0.0, upper), method="bounded", options={"xatol": 1e-4})
    l3 = float(r.x) if r.success else 0.0
    params = BPParams(
        teams=teams,
        attack={t: float(theta[i]) for t, i in idx.items()},
        defense={t: float(theta[n + i]) for t, i in idx.items()},
        gamma=gamma, lambda3=l3, matches=len(df), converged=bool(res.success and r.success), fitted_at=datetime.utcnow(),
    )
    if not res.success:
        log.info("bivariate-poisson: marginais não convergiram totalmente: %s", res.message)
    return params


def bivariate_output(params: BPParams | None, home: str | None, away: str | None, home_adv_weight: float) -> GoalsModelOutput:
    if params is None:
        return GoalsModelOutput(
            model_version=versions.BIVARIATE_POISSON, available=False,
            note=f"Bivariate Poisson não ajustado: menos de {MIN_MATCHES} jogos na competição.",
        )
    if not home or not away or home not in params.attack or away not in params.attack:
        return GoalsModelOutput(
            model_version=versions.BIVARIATE_POISSON, available=False, fit_matches=params.matches,
            note="Bivariate Poisson indisponível: time ausente do ajuste da competição.",
        )
    lam, mu = params.marginals(home, away, home_adv_weight)
    lam, mu = max(0.15, min(5.0, lam)), max(0.15, min(5.0, mu))
    l3 = min(params.lambda3, lam - EPS, mu - EPS)
    l3 = max(0.0, l3)
    m = bivariate_pmf_matrix(lam - l3, mu - l3, l3)
    mk = markets_from_matrix(m)
    return GoalsModelOutput(
        model_version=versions.BIVARIATE_POISSON,
        available=True,
        lambda_home=round(lam, 3),
        lambda_away=round(mu, 3),
        rho=round(l3, 4),  # aqui: λ3 (covariância), não o ρ de Dixon-Coles
        p_home=round(mk["p_home"], 4),
        p_draw=round(mk["p_draw"], 4),
        p_away=round(mk["p_away"], 4),
        dist_home=[round(v, 4) for v in mk["dist_home"]],
        dist_away=[round(v, 4) for v in mk["dist_away"]],
        over={k: round(v, 4) for k, v in mk["over"].items()},
        under={k: round(v, 4) for k, v in mk["under"].items()},
        btts=round(mk["btts"], 4),
        top_scores=[(s, round(p, 4)) for s, p in mk["top_scores"][:6]],
        fit_matches=params.matches,
        note=f"λ3 (covariância) = {l3:.3f}." + ("" if params.converged else " Otimização não convergiu totalmente."),
    )


def bivariate_matrix_for(model: GoalsModelOutput) -> np.ndarray:
    """Matriz de placares a partir de um output bivariado (λ_home, λ_away, λ3 em `rho`)."""
    l3 = model.rho or 0.0
    return bivariate_pmf_matrix((model.lambda_home or 1.0) - l3, (model.lambda_away or 1.0) - l3, l3)


class BPCache:
    def __init__(self) -> None:
        self._fits: dict[tuple, BPParams | None] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple, builder):
        with self._lock:
            if key not in self._fits:
                self._fits[key] = builder()
            return self._fits[key]

    def clear(self) -> None:
        with self._lock:
            self._fits.clear()


bp_cache = BPCache()
