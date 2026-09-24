"""Utilidades comuns aos modelos de gols (matriz de placares → mercados)."""

from __future__ import annotations

import numpy as np
from scipy.stats import poisson

MAX_GOALS = 10  # grade 0..9


def poisson_vector(lam: float, n: int = MAX_GOALS) -> np.ndarray:
    v = poisson.pmf(np.arange(n), lam)
    v[-1] += max(0.0, 1.0 - v.sum())  # massa residual no último bucket
    return v


def score_matrix(lam_home: float, lam_away: float, rho: float | None = None) -> np.ndarray:
    ph = poisson_vector(lam_home)
    pa = poisson_vector(lam_away)
    m = np.outer(ph, pa)
    if rho is not None and rho != 0:
        m = apply_dixon_coles_tau(m, lam_home, lam_away, rho)
    m /= m.sum()
    return m


def apply_dixon_coles_tau(m: np.ndarray, lam: float, mu: float, rho: float) -> np.ndarray:
    m = m.copy()
    m[0, 0] *= 1 - lam * mu * rho
    m[0, 1] *= 1 + lam * rho
    m[1, 0] *= 1 + mu * rho
    m[1, 1] *= 1 - rho
    return np.clip(m, 1e-12, None)


def markets_from_matrix(m: np.ndarray) -> dict:
    n = m.shape[0]
    idx = np.arange(n)
    home_goals = idx[:, None]
    away_goals = idx[None, :]
    total = home_goals + away_goals
    out: dict = {
        "p_home": float(m[home_goals > away_goals].sum()),
        "p_draw": float(np.trace(m)),
        "p_away": float(m[home_goals < away_goals].sum()),
        "btts": float(m[1:, 1:].sum()),
        "over": {},
        "under": {},
        "dist_home": [],
        "dist_away": [],
        "total_dist": [],
        "top_scores": [],
        "team_home_over": {},
        "team_away_over": {},
        "handicap_home": {},
        "expected_home": float((m.sum(axis=1) * idx).sum()),
        "expected_away": float((m.sum(axis=0) * idx).sum()),
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
        over = float(m[total > line].sum())
        out["over"][f"{line:.1f}"] = over
        out["under"][f"{line:.1f}"] = 1 - over
    dh, da = m.sum(axis=1), m.sum(axis=0)
    out["dist_home"] = [float(x) for x in dh[:5]] + [float(dh[5:].sum())]
    out["dist_away"] = [float(x) for x in da[:5]] + [float(da[5:].sum())]
    tot = np.array([float(m[total == k].sum()) for k in range(6)])
    out["total_dist"] = [float(x) for x in tot] + [float(1 - tot.sum())]
    for line in (0.5, 1.5, 2.5):
        out["team_home_over"][f"{line:.1f}"] = float(dh[idx > line].sum())
        out["team_away_over"][f"{line:.1f}"] = float(da[idx > line].sum())
    diff = home_goals - away_goals
    for line in (-2.5, -1.5, -0.5, 0.5, 1.5, 2.5):
        out["handicap_home"][f"{line:+.1f}"] = float(m[(diff + line) > 0].sum())
    flat = [((i, j), float(m[i, j])) for i in range(n) for j in range(n)]
    flat.sort(key=lambda t: -t[1])
    out["top_scores"] = [(f"{i}-{j}", p) for (i, j), p in flat[:8]]
    return out
