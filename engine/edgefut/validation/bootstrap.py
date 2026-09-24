"""Bootstrap CI, qualidade de amostra e significância de modelo.

Regras (iteração 3):
- Todo número de desempenho vem acompanhado de N e de IC 95% (bootstrap percentílico).
- Se o IC de ROI/Yield/CLV cruza 0 → INCONCLUSIVE (não se diz "lucrativo").
- Comparação modelo × baseline usa bootstrap **pareado** da diferença de Brier/LogLoss
  (mesmos eventos re-amostrados para ambos), não dois ICs independentes.
- Amostra: INSUFFICIENT < early_min ≤ EARLY < moderate_min ≤ MODERATE < strong_min ≤ STRONG.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from ..core.config import settings

SAMPLE_STATES = ("INSUFFICIENT", "EARLY", "MODERATE", "STRONG")
SIGNIFICANCE_STATES = ("INSUFFICIENT DATA", "NO CLEAR ADVANTAGE", "PROMISING", "CONSISTENT")


def sample_quality(n: int) -> str:
    if n < settings.sample_early_min:
        return "INSUFFICIENT"
    if n < settings.sample_moderate_min:
        return "EARLY"
    if n < settings.sample_strong_min:
        return "MODERATE"
    return "STRONG"


def sample_thresholds() -> dict[str, int]:
    return {
        "early_min": settings.sample_early_min,
        "moderate_min": settings.sample_moderate_min,
        "strong_min": settings.sample_strong_min,
    }


@dataclass
class Interval:
    point: float | None
    low: float | None
    high: float | None
    n: int
    conclusive: bool | None = None  # None quando não se aplica (ex.: Brier não tem "zero")

    def to_dict(self) -> dict:
        return asdict(self)


def _rng(seed: int | None) -> np.random.Generator:
    return np.random.default_rng(seed if seed is not None else 20260923)


def bootstrap_ci(
    values: np.ndarray | list[float],
    stat=np.mean,
    *,
    resamples: int | None = None,
    alpha: float = 0.05,
    zero_test: bool = False,
    seed: int | None = None,
    min_n: int = 10,
) -> Interval:
    """IC percentílico de `stat` sobre `values` (uma observação por aposta/evento).

    `zero_test=True` marca `conclusive=False` se o IC incluir 0 (ROI, Yield, CLV, lift)."""
    arr = np.asarray(list(values), dtype=float)
    arr = arr[~np.isnan(arr)]
    n = int(arr.size)
    if n == 0:
        return Interval(None, None, None, 0, None)
    point = float(stat(arr))
    if n < min_n:
        return Interval(round(point, 4), None, None, n, False if zero_test else None)
    r = resamples or settings.bootstrap_resamples
    rng = _rng(seed)
    idx = rng.integers(0, n, size=(r, n))
    samples = np.apply_along_axis(stat, 1, arr[idx]) if stat is not np.mean else arr[idx].mean(axis=1)
    low, high = np.percentile(samples, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    conclusive: bool | None = None
    if zero_test:
        conclusive = bool(low > 0 or high < 0)
    return Interval(round(point, 4), round(float(low), 4), round(float(high), 4), n, conclusive)


def paired_bootstrap_diff(
    a: np.ndarray | list[float],
    b: np.ndarray | list[float],
    *,
    resamples: int | None = None,
    alpha: float = 0.05,
    seed: int | None = None,
) -> Interval:
    """IC de mean(a − b) re-amostrando pares. Para Brier/LogLoss: valores negativos = `a` melhor."""
    x = np.asarray(list(a), dtype=float)
    y = np.asarray(list(b), dtype=float)
    if x.shape != y.shape:
        raise ValueError("séries pareadas com tamanhos diferentes")
    mask = ~(np.isnan(x) | np.isnan(y))
    d = x[mask] - y[mask]
    return bootstrap_ci(d, resamples=resamples, alpha=alpha, zero_test=True, seed=seed)


def roi_stat(profits: np.ndarray) -> float:
    return float(profits.sum() / profits.size * 100.0) if profits.size else 0.0


def model_significance(n: int, lift_ci: Interval | None, *, windows_positive: int | None = None, windows_total: int | None = None) -> str:
    """Classifica a vantagem sobre baseline.

    - INSUFFICIENT DATA: N abaixo de EARLY ou IC indisponível.
    - NO CLEAR ADVANTAGE: IC da diferença cruza 0 ou ponto ≥ 0 (modelo não melhor).
    - PROMISING: IC inteiro < 0 (modelo melhor) mas amostra não STRONG ou instável entre janelas.
    - CONSISTENT: IC < 0, amostra STRONG e ≥ 75% das janelas com vantagem.
    """
    if lift_ci is None or lift_ci.low is None or n < settings.sample_early_min:
        return "INSUFFICIENT DATA"
    if lift_ci.point is None or lift_ci.point >= 0 or lift_ci.high is None or lift_ci.high >= 0:
        return "NO CLEAR ADVANTAGE"
    stable = True
    if windows_total:
        stable = (windows_positive or 0) / windows_total >= 0.75
    if sample_quality(n) == "STRONG" and stable:
        return "CONSISTENT"
    return "PROMISING"


def lift_pct(model_metric: float | None, baseline_metric: float | None) -> float | None:
    """Melhora relativa de uma métrica de erro (menor é melhor): (base − model) / base · 100."""
    if model_metric is None or baseline_metric is None or baseline_metric == 0 or math.isnan(baseline_metric):
        return None
    return round((baseline_metric - model_metric) / baseline_metric * 100.0, 2)


def cluster_bootstrap_ci(
    values: np.ndarray | list[float],
    clusters: np.ndarray | list,
    stat=np.mean,
    *,
    resamples: int | None = None,
    alpha: float = 0.05,
    zero_test: bool = False,
    seed: int | None = None,
    min_clusters: int = 10,
) -> Interval:
    """EVENT CLUSTER BOOTSTRAP (§20): re-amostra **clusters** (eventos) inteiros, não observações.

    Várias seleções do mesmo jogo são correlacionadas (mesmo placar decide todas); tratá-las como
    independentes estreita o IC indevidamente. `Interval.n` devolve o número de clusters."""
    arr = np.asarray(list(values), dtype=float)
    cl = np.asarray(list(clusters))
    mask = ~np.isnan(arr)
    arr, cl = arr[mask], cl[mask]
    if arr.size == 0:
        return Interval(None, None, None, 0, None)
    point = float(stat(arr))
    uniq, inv = np.unique(cl, return_inverse=True)
    k = int(uniq.size)
    if k < min_clusters:
        return Interval(round(point, 4), None, None, k, False if zero_test else None)
    r = resamples or settings.bootstrap_resamples
    rng = _rng(seed)
    # soma e contagem por cluster → estatística da média ponderada por reamostragem de clusters
    if stat is np.mean:
        sums = np.bincount(inv, weights=arr, minlength=k)
        cnts = np.bincount(inv, minlength=k).astype(float)
        pick = rng.integers(0, k, size=(r, k))
        samples = sums[pick].sum(axis=1) / cnts[pick].sum(axis=1)
    else:
        groups = [arr[inv == g] for g in range(k)]
        samples = np.empty(r)
        for i in range(r):
            pick = rng.integers(0, k, size=k)
            samples[i] = stat(np.concatenate([groups[g] for g in pick]))
    low, high = np.percentile(samples, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    conclusive: bool | None = None
    if zero_test:
        conclusive = bool(low > 0 or high < 0)
    return Interval(round(point, 4), round(float(low), 4), round(float(high), 4), k, conclusive)


def effective_sample_size(clusters: np.ndarray | list, rho: float = 0.5) -> dict:
    """EFFECTIVE SAMPLE SIZE (§19) com correlação intra-cluster aproximada `rho`.

    Para um cluster com m observações, o design effect é 1 + (m − 1)·rho; N_eff = Σ m_i / deff_i.
    Com rho = 1 (seleções do mesmo evento perfeitamente correlacionadas) N_eff = nº de clusters;
    com rho = 0, N_eff = N bruto. Devolve os três para a UI mostrar Raw N e Effective N."""
    cl = np.asarray(list(clusters))
    n = int(cl.size)
    if n == 0:
        return {"raw_n": 0, "clusters": 0, "effective_n": 0, "rho": rho}
    _, counts = np.unique(cl, return_counts=True)
    eff = float(sum(m / (1.0 + (m - 1) * rho) for m in counts))
    return {"raw_n": n, "clusters": int(counts.size), "effective_n": int(round(eff)), "rho": rho,
            "avg_cluster_size": round(float(counts.mean()), 2)}


def benjamini_hochberg(p_values: dict[str, float], q: float = 0.10) -> dict:
    """Controle de FDR (§18) para análises exploratórias por segmento.

    Devolve, por chave, o p ajustado e se sobrevive ao FDR `q`. Nunca escolhemos "o segmento com
    maior ROI": só segmentos que sobrevivem aqui viram HIPÓTESE para a fase de confirmação."""
    items = [(k, float(v)) for k, v in p_values.items() if v is not None and not math.isnan(float(v))]
    m = len(items)
    if m == 0:
        return {"q": q, "tested": 0, "survivors": [], "adjusted": {}}
    items.sort(key=lambda kv: kv[1])
    adjusted: dict[str, float] = {}
    prev = 1.0
    for rank in range(m, 0, -1):
        k, p = items[rank - 1]
        adj = min(prev, p * m / rank)
        adjusted[k] = round(float(adj), 4)
        prev = adj
    survivors = [k for k, _ in items if adjusted[k] <= q]
    return {"q": q, "tested": m, "survivors": survivors, "adjusted": adjusted}


def bootstrap_p_value(diffs: np.ndarray | list[float], clusters: np.ndarray | list | None = None, *, resamples: int | None = None, seed: int | None = None) -> float | None:
    """p-valor bilateral aproximado de H0: mean(diffs) = 0 via bootstrap (percentil de 0 na distribuição).

    Usado só como insumo do Benjamini-Hochberg; a decisão principal continua sendo o IC."""
    arr = np.asarray(list(diffs), dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size < 10:
        return None
    r = resamples or settings.bootstrap_resamples
    rng = _rng(seed)
    if clusters is not None:
        cl = np.asarray(list(clusters))[: arr.size]
        uniq, inv = np.unique(cl, return_inverse=True)
        k = uniq.size
        sums = np.bincount(inv, weights=arr, minlength=k)
        cnts = np.bincount(inv, minlength=k).astype(float)
        pick = rng.integers(0, k, size=(r, k))
        samples = sums[pick].sum(axis=1) / cnts[pick].sum(axis=1)
    else:
        idx = rng.integers(0, arr.size, size=(r, arr.size))
        samples = arr[idx].mean(axis=1)
    point = float(arr.mean())
    # centrar em 0 e medir a fração tão extrema quanto o observado
    centered = samples - point
    p = float(np.mean(np.abs(centered) >= abs(point)))
    return round(max(p, 1.0 / r), 4)


def brier_decomposition(probs: np.ndarray | list[float], outcomes: np.ndarray | list[float], bins: int = 10) -> dict | None:
    """Decomposição de Murphy (§43): Brier = reliability − resolution + uncertainty (binário, por bins).

    reliability: quão longe a frequência observada fica da probabilidade prevista (menor = melhor);
    resolution: quanto as previsões separam os resultados (maior = melhor);
    uncertainty: variância do resultado (não depende do modelo)."""
    p = np.asarray(list(probs), dtype=float)
    o = np.asarray(list(outcomes), dtype=float)
    mask = ~(np.isnan(p) | np.isnan(o))
    p, o = p[mask], o[mask]
    n = p.size
    if n < 50:
        return None
    base = float(o.mean())
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    rel = res = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            nk = m.sum()
            ok = float(o[m].mean())
            rel += nk * (float(p[m].mean()) - ok) ** 2
            res += nk * (ok - base) ** 2
    rel, res = rel / n, res / n
    unc = base * (1 - base)
    return {"n": int(n), "brier": round(float(np.mean((p - o) ** 2)), 5), "reliability": round(rel, 5),
            "resolution": round(res, 5), "uncertainty": round(unc, 5), "bins": bins}


__all__ = [
    "Interval", "bootstrap_ci", "paired_bootstrap_diff", "cluster_bootstrap_ci", "effective_sample_size",
    "benjamini_hochberg", "bootstrap_p_value", "brier_decomposition", "sample_quality", "sample_thresholds",
    "model_significance", "lift_pct", "roi_stat", "SAMPLE_STATES", "SIGNIFICANCE_STATES",
]
