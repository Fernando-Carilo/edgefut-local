"""Opportunity Score V2 (opportunity-v2): 0-100, componentes explícitos e pesos configuráveis.

Cada componente vale 0-1 e carrega uma nota com o número que o originou, para que a UI
possa mostrar "por que 71 e não 90". Pesos vêm de `settings.opportunity_weights`; a soma é
normalizada em runtime, então o usuário pode editar sem precisar fechar 100.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import versions
from ..core.config import settings
from ..domain.analysis import OpportunityBreakdown, ScoreComponent
from ..domain.freshness import FreshnessStatus

COMPONENT_LABELS: dict[str, str] = {
    "model_confidence": "Confiança do modelo",
    "data_quality": "Qualidade dos dados",
    "calibration_quality": "Qualidade da calibração",
    "edge": "Edge",
    "ev": "EV",
    "odds_freshness": "Frescor das odds",
    "model_agreement": "Concordância entre modelos",
    "historical_performance": "Performance histórica",
    "sample_size": "Tamanho da amostra",
}

EDGE_FULL_PP = 10.0  # edge de 10 pp → componente 1.0
EV_FULL_PCT = 15.0
SAMPLE_FULL_N = 30
DISAGREEMENT_FULL_PP = 10.0

FRESHNESS_VALUE: dict[str, float] = {"FRESH": 1.0, "AGING": 0.7, "STALE": 0.3, "EXPIRED": 0.0, "UNAVAILABLE": 0.0}


@dataclass
class OpportunityInputs:
    confidence_score: float  # 0-100 (por seleção)
    data_quality: float  # 0-100
    edge_pp: float
    ev_pct: float
    odds_freshness: FreshnessStatus | None
    odds_age_seconds: int | None
    model_disagreement_pp: float | None
    n_models: int
    sample_size: int
    calibration_quality: float | None  # 0-1 derivado do Brier settled (None = sem amostra)
    calibration_reliable: bool
    historical_roi: float | None  # ROI % do mercado em apostas settled (None = sem amostra)
    historical_n: int


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def normalized_weights(weights: dict[str, float] | None = None) -> dict[str, float]:
    raw = {k: float(max(0.0, (weights or settings.opportunity_weights).get(k, 0.0))) for k in COMPONENT_LABELS}
    total = sum(raw.values())
    if total <= 0:
        raw = {k: 1.0 for k in COMPONENT_LABELS}
        total = float(len(raw))
    return {k: v / total for k, v in raw.items()}


def compute_opportunity(inp: OpportunityInputs, weights: dict[str, float] | None = None) -> OpportunityBreakdown:
    w = normalized_weights(weights)
    comps: list[ScoreComponent] = []

    def add(key: str, value: float, note: str | None) -> None:
        comps.append(ScoreComponent(key=key, label=COMPONENT_LABELS[key], weight=round(100 * w[key], 1), value=round(_clamp(value), 4), note=note))

    add("model_confidence", inp.confidence_score / 100, f"{inp.confidence_score:.0f}/100")
    add("data_quality", inp.data_quality / 100, f"{inp.data_quality:.0f}%")

    if inp.calibration_reliable and inp.calibration_quality is not None:
        add("calibration_quality", inp.calibration_quality, "calibrador isotônico ativo (N≥300)")
    elif inp.calibration_quality is not None:
        add("calibration_quality", 0.5 * inp.calibration_quality + 0.25, "probabilidade RAW; Brier settled parcial")
    else:
        add("calibration_quality", 0.5, "neutro: sem previsões settled suficientes")

    add("edge", inp.edge_pp / EDGE_FULL_PP, f"{inp.edge_pp:+.1f} pp (1.0 em {EDGE_FULL_PP:.0f} pp)")
    add("ev", inp.ev_pct / EV_FULL_PCT, f"{inp.ev_pct:+.1f}% (1.0 em {EV_FULL_PCT:.0f}%)")

    fr_val = FRESHNESS_VALUE.get(inp.odds_freshness or "UNAVAILABLE", 0.0)
    age = f"{inp.odds_age_seconds // 60} min" if inp.odds_age_seconds is not None else "n/d"
    add("odds_freshness", fr_val, f"{inp.odds_freshness or 'UNAVAILABLE'} · coletadas há {age}")

    if inp.n_models <= 1 or inp.model_disagreement_pp is None:
        add("model_agreement", 0.5, "apenas um modelo de gols disponível")
    else:
        add("model_agreement", 1 - inp.model_disagreement_pp / DISAGREEMENT_FULL_PP, f"divergência {inp.model_disagreement_pp:.1f} pp entre {inp.n_models} modelos")

    if inp.historical_roi is None or inp.historical_n < 30:
        add("historical_performance", 0.5, f"neutro: INSUFFICIENT SAMPLE ({inp.historical_n} apostas settled neste mercado)")
    else:
        # ROI −10% → 0.0 · 0% → 0.5 · +10% → 1.0
        add("historical_performance", 0.5 + inp.historical_roi / 20, f"ROI {inp.historical_roi:+.1f}% em {inp.historical_n} apostas settled")

    add("sample_size", inp.sample_size / SAMPLE_FULL_N, f"{inp.sample_size} jogos na menor amostra (1.0 em {SAMPLE_FULL_N})")

    score = 100 * sum(c.value * (c.weight / 100) for c in comps)
    return OpportunityBreakdown(model_version=versions.OPPORTUNITY, score=round(score, 1), components=comps)


__all__ = ["COMPONENT_LABELS", "OpportunityInputs", "compute_opportunity", "normalized_weights"]
