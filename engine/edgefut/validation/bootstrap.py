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


__all__ = [
    "Interval", "bootstrap_ci", "paired_bootstrap_diff", "sample_quality", "sample_thresholds",
    "model_significance", "lift_pct", "roi_stat", "SAMPLE_STATES", "SIGNIFICANCE_STATES",
]
