"""Engines de contagem: escanteios (corners-v1), cartões (cards-v1), finalizações (shots-v1).

Todos usam: média ponderada do time + média ponderada sofrida pelo adversário,
ajuste por força relativa (ELO) e distribuição Binomial Negativa (ou Poisson
quando não há sobredispersão estimável).
"""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import nbinom, poisson

from ..core import versions
from ..domain.analysis import CountDistribution, WindowStats
from ..domain.provenance import Provenance
from ..features.strength import LeagueAverages

MIN_N = 8


def _pick(windows: dict[str, WindowStats], attr: str) -> tuple[float | None, int]:
    """Combina janelas 5/10/20 (pesos 0.5/0.3/0.2) para o atributo."""
    vals = []
    n_max = 0
    for name, w in (("all_5", 0.5), ("all_10", 0.3), ("all_20", 0.2)):
        ws = windows.get(name)
        if ws is None:
            continue
        v = getattr(ws, attr)
        if v is None:
            continue
        vals.append((v, w))
        n_max = max(n_max, ws.n)
    if not vals:
        return None, 0
    tw = sum(w for _, w in vals)
    return sum(v * w for v, w in vals) / tw, n_max


def _dist(mean: float, k: float | None):
    """Retorna (pmf array 0..N, cdf function)."""
    n = int(max(20, mean * 3 + 15))
    xs = np.arange(n)
    if k is None or k <= 0:
        pmf = poisson.pmf(xs, mean)
    else:
        p = k / (k + mean)
        pmf = nbinom.pmf(xs, k, p)
    pmf = pmf / pmf.sum()
    return xs, pmf


def _over_under(xs, pmf, lines: list[float]) -> tuple[dict[str, float], dict[str, float]]:
    over, under = {}, {}
    for line in lines:
        o = float(pmf[xs > line].sum())
        over[f"{line:.1f}"] = round(o, 4)
        under[f"{line:.1f}"] = round(1 - o, 4)
    return over, under


def _likely_range(xs, pmf) -> tuple[float, float]:
    cdf = np.cumsum(pmf)
    lo = float(xs[np.searchsorted(cdf, 0.2)])
    hi = float(xs[min(len(xs) - 1, np.searchsorted(cdf, 0.8))])
    return lo, hi


def _elo_adjust(elo_diff: float | None, scale: float, strength: float) -> tuple[float, float]:
    if elo_diff is None:
        return 1.0, 1.0
    t = math.tanh(elo_diff / scale)
    return 1 + strength * t, 1 - strength * t


def _p_more(mean_a: float, mean_b: float, k: float | None) -> tuple[float, float]:
    xa, pa = _dist(mean_a, k)
    xb, pb = _dist(mean_b, k)
    n = min(len(pa), len(pb))
    pa, pb = pa[:n], pb[:n]
    joint = np.outer(pa, pb)
    idx = np.arange(n)
    a_more = float(joint[idx[:, None] > idx[None, :]].sum())
    b_more = float(joint[idx[:, None] < idx[None, :]].sum())
    return a_more, b_more


def corners_engine(
    home_windows: dict[str, WindowStats],
    away_windows: dict[str, WindowStats],
    la: LeagueAverages | None,
    elo_diff: float | None,
    provenance: Provenance | None,
) -> CountDistribution:
    hf, n1 = _pick(home_windows, "corners_for")
    ha, n2 = _pick(home_windows, "corners_against")
    af, n3 = _pick(away_windows, "corners_for")
    aa, n4 = _pick(away_windows, "corners_against")
    n = min(n1, n2, n3, n4)
    if None in (hf, ha, af, aa) or n < MIN_N:
        return CountDistribution(
            model_version=versions.CORNERS, available=False, sample_size=n,
            note="Escanteios indisponíveis: histórico sem escanteios (HC/AC) para estas equipes.",
        )
    adj_h, adj_a = _elo_adjust(elo_diff, 200.0, 0.15)
    exp_h = 0.5 * (hf + aa) * adj_h  # type: ignore[operator]
    exp_a = 0.5 * (af + ha) * adj_a  # type: ignore[operator]
    if la and la.corners_home and la.corners_away:
        # leve regressão à média da liga (casa/fora)
        exp_h = 0.8 * exp_h + 0.2 * la.corners_home
        exp_a = 0.8 * exp_a + 0.2 * la.corners_away
    total = exp_h + exp_a
    k = la.corners_dispersion_k if la else None
    xs, pmf = _dist(total, k)
    over, under = _over_under(xs, pmf, [6.5, 7.5, 8.5, 9.5, 10.5, 11.5])
    p_hm, p_am = _p_more(exp_h, exp_a, k)
    return CountDistribution(
        model_version=versions.CORNERS,
        available=True,
        expected_home=round(exp_h, 2),
        expected_away=round(exp_a, 2),
        expected_total=round(total, 2),
        likely_range=_likely_range(xs, pmf),
        over=over,
        under=under,
        p_home_more=round(p_hm, 4),
        p_away_more=round(p_am, 4),
        sample_size=n,
        provenance=provenance,
    )


def cards_engine(
    home_windows: dict[str, WindowStats],
    away_windows: dict[str, WindowStats],
    la: LeagueAverages | None,
    provenance: Provenance | None,
) -> CountDistribution:
    hc, n1 = _pick(home_windows, "cards")
    hca, n2 = _pick(home_windows, "cards_against")
    ac, n3 = _pick(away_windows, "cards")
    aca, n4 = _pick(away_windows, "cards_against")
    n = min(n1, n2, n3, n4)
    if None in (hc, hca, ac, aca) or n < MIN_N:
        return CountDistribution(
            model_version=versions.CARDS, available=False, sample_size=n,
            note="Cartões indisponíveis: histórico sem cartões (HY/AY) para estas equipes.",
        )
    # cartões de um time correlacionam com a agressividade do jogo do adversário (cards_against)
    exp_h = 0.6 * hc + 0.4 * aca  # type: ignore[operator]
    exp_a = 0.6 * ac + 0.4 * hca  # type: ignore[operator]
    total = exp_h + exp_a
    if la and la.cards_total:
        total = 0.8 * total + 0.2 * la.cards_total
    k = la.cards_dispersion_k if la else None
    xs, pmf = _dist(total, k)
    over, under = _over_under(xs, pmf, [2.5, 3.5, 4.5, 5.5, 6.5])
    return CountDistribution(
        model_version=versions.CARDS,
        available=True,
        expected_home=round(exp_h, 2),
        expected_away=round(exp_a, 2),
        expected_total=round(total, 2),
        likely_range=_likely_range(xs, pmf),
        over=over,
        under=under,
        sample_size=n,
        note="Árbitro não considerado: sem escala pública confirmada para jogos futuros.",
        provenance=provenance,
    )


def shots_engine(
    home_windows: dict[str, WindowStats],
    away_windows: dict[str, WindowStats],
    la: LeagueAverages | None,
    elo_diff: float | None,
    provenance: Provenance | None,
) -> CountDistribution:
    hs, n1 = _pick(home_windows, "shots_for")
    hsa, n2 = _pick(home_windows, "shots_against")
    as_, n3 = _pick(away_windows, "shots_for")
    asa, n4 = _pick(away_windows, "shots_against")
    hst, _ = _pick(home_windows, "sot_for")
    ast, _ = _pick(away_windows, "sot_for")
    n = min(n1, n2, n3, n4)
    if None in (hs, hsa, as_, asa) or n < MIN_N:
        return CountDistribution(
            model_version=versions.SHOTS, available=False, sample_size=n,
            note="Finalizações indisponíveis: histórico sem chutes (HS/AS) para estas equipes.",
        )
    adj_h, adj_a = _elo_adjust(elo_diff, 250.0, 0.20)
    exp_h = 0.5 * (hs + asa) * adj_h  # type: ignore[operator]
    exp_a = 0.5 * (as_ + hsa) * adj_a  # type: ignore[operator]
    if la and la.shots_home and la.shots_away:
        exp_h = 0.85 * exp_h + 0.15 * la.shots_home
        exp_a = 0.85 * exp_a + 0.15 * la.shots_away
    rate_h = (hst / hs) if (hst and hs) else (la.sot_rate if la and la.sot_rate else 0.34)
    rate_a = (ast / as_) if (ast and as_) else (la.sot_rate if la and la.sot_rate else 0.34)
    sot_h, sot_a = exp_h * rate_h, exp_a * rate_a
    p_hm, p_am = _p_more(exp_h, exp_a, None)
    return CountDistribution(
        model_version=versions.SHOTS,
        available=True,
        expected_home=round(exp_h, 2),
        expected_away=round(exp_a, 2),
        expected_total=round(exp_h + exp_a, 2),
        p_home_more=round(p_hm, 4),
        p_away_more=round(p_am, 4),
        extra={
            "sot_home": round(sot_h, 2),
            "sot_away": round(sot_a, 2),
            "sot_rate_home": round(rate_h, 3),
            "sot_rate_away": round(rate_a, 3),
        },
        sample_size=n,
        provenance=provenance,
    )
