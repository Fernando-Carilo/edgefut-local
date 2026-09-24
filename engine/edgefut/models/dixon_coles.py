"""Dixon-Coles (goals-dixon-coles-v1) — MLE com decaimento temporal e gradiente analítico."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from ..core import versions
from ..domain.analysis import GoalsModelOutput
from .goals_common import markets_from_matrix, score_matrix

log = logging.getLogger(__name__)

XI_PER_DAY = 0.0018
MIN_MATCHES = 150


@dataclass
class DCParams:
    teams: list[str]
    attack: dict[str, float] = field(default_factory=dict)  # log-escala
    defense: dict[str, float] = field(default_factory=dict)  # log-escala
    gamma: float = 0.0  # vantagem de mandante (log)
    rho: float = 0.0
    matches: int = 0
    converged: bool = False
    fitted_at: datetime | None = None

    def lambdas(self, home: str, away: str, home_adv_weight: float) -> tuple[float, float]:
        lam = float(np.exp(self.attack[home] + self.defense[away] + self.gamma * home_adv_weight))
        mu = float(np.exp(self.attack[away] + self.defense[home]))
        return lam, mu


def _neg_ll_and_grad(theta: np.ndarray, hi, ai, x, y, w, n_teams):
    a = theta[:n_teams]
    d = theta[n_teams : 2 * n_teams]
    gamma = theta[-2]
    rho = theta[-1]
    lam = np.exp(a[hi] + d[ai] + gamma)
    mu = np.exp(a[ai] + d[hi])

    tau = np.ones_like(lam)
    dtau_dlam = np.zeros_like(lam)
    dtau_dmu = np.zeros_like(lam)
    dtau_drho = np.zeros_like(lam)
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    tau[m00] = 1 - lam[m00] * mu[m00] * rho
    dtau_dlam[m00] = -mu[m00] * rho
    dtau_dmu[m00] = -lam[m00] * rho
    dtau_drho[m00] = -lam[m00] * mu[m00]
    tau[m01] = 1 + lam[m01] * rho
    dtau_dlam[m01] = rho
    dtau_drho[m01] = lam[m01]
    tau[m10] = 1 + mu[m10] * rho
    dtau_dmu[m10] = rho
    dtau_drho[m10] = mu[m10]
    tau[m11] = 1 - rho
    dtau_drho[m11] = -1.0
    tau = np.clip(tau, 1e-6, None)

    ll = w * (np.log(tau) + x * np.log(lam) - lam + y * np.log(mu) - mu)
    penalty = 1000.0 * a.mean() ** 2
    nll = -ll.sum() + penalty

    # derivadas em relação a log-lambda e log-mu
    g_lam = w * (x - lam + dtau_dlam / tau * lam)
    g_mu = w * (y - mu + dtau_dmu / tau * mu)
    grad = np.zeros_like(theta)
    np.add.at(grad, hi, g_lam)  # attack home
    np.add.at(grad, ai, g_mu)  # attack away
    np.add.at(grad, n_teams + ai, g_lam)  # defense away
    np.add.at(grad, n_teams + hi, g_mu)  # defense home
    grad[-2] = g_lam.sum()
    grad[-1] = (w * dtau_drho / tau).sum()
    grad = -grad
    grad[:n_teams] += 2000.0 * a.mean() / n_teams
    return nll, grad


def fit_dixon_coles(df: pd.DataFrame, reference_date: datetime | None = None) -> DCParams | None:
    if df is None or len(df) < MIN_MATCHES:
        return None
    df = df.dropna(subset=["hg", "ag"]).copy()
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
    theta0 = np.concatenate([np.zeros(n), np.zeros(n), [0.25], [-0.05]])
    bounds = [(-3, 3)] * (2 * n) + [(-1, 1), (-0.2, 0.2)]
    res = minimize(
        _neg_ll_and_grad, theta0, args=(hi, ai, x, y, w, n), jac=True, method="L-BFGS-B",
        bounds=bounds, options={"maxiter": 1500, "maxfun": 30000},
    )
    theta = res.x
    params = DCParams(
        teams=teams,
        attack={t: float(theta[i]) for t, i in idx.items()},
        defense={t: float(theta[n + i]) for t, i in idx.items()},
        gamma=float(theta[-2]),
        rho=float(theta[-1]),
        matches=len(df),
        converged=bool(res.success),
        fitted_at=datetime.utcnow(),
    )
    if not res.success:
        log.info("dixon-coles não convergiu totalmente: %s", res.message)
    return params


def dixon_coles_output(params: DCParams | None, home: str | None, away: str | None, home_adv_weight: float) -> GoalsModelOutput:
    if params is None:
        return GoalsModelOutput(
            model_version=versions.DIXON_COLES, available=False,
            note=f"Dixon-Coles não ajustado: menos de {MIN_MATCHES} jogos na competição.",
        )
    if not home or not away or home not in params.attack or away not in params.attack:
        return GoalsModelOutput(
            model_version=versions.DIXON_COLES, available=False, fit_matches=params.matches,
            note="Dixon-Coles indisponível: time ausente do ajuste da competição.",
        )
    lam, mu = params.lambdas(home, away, home_adv_weight)
    lam = max(0.15, min(5.0, lam))
    mu = max(0.15, min(5.0, mu))
    m = score_matrix(lam, mu, params.rho)
    mk = markets_from_matrix(m)
    return GoalsModelOutput(
        model_version=versions.DIXON_COLES,
        available=True,
        lambda_home=round(lam, 3),
        lambda_away=round(mu, 3),
        rho=round(params.rho, 4),
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
        note=None if params.converged else "Otimização não convergiu totalmente; resultado aproximado.",
    )


class DCCache:
    def __init__(self) -> None:
        self._fits: dict[tuple, DCParams | None] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple, builder):
        with self._lock:
            if key not in self._fits:
                self._fits[key] = builder()
            return self._fits[key]

    def clear(self) -> None:
        with self._lock:
            self._fits.clear()


dc_cache = DCCache()
