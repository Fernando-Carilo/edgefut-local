"""RequiredEdgeEngine (§21–§24): quanto edge é preciso para uma seleção sair de OBSERVATION?

Edge bruto = p_modelo − p_mercado (justa). Ele só é "edge" se sobreviver a:

* **margem da casa** — o overround do mercado no instante da odd (pp);
* **incerteza do modelo** — meia-largura do intervalo de incerteza da probabilidade, que combina a
  dispersão entre os membros do consenso, a calibração medida no replay (ECE) e o erro amostral
  da menor amostra de time;
* **calibração** — penalidade quando não há calibrador confiável para o mercado;
* **força da amostra** — penalidade quando o mercado não tem prova out-of-sample suficiente;
* **eficiência do mercado** — penalidade quando a validação market-aware concluiu, neste mercado,
  que o mercado bate os challengers (NO EVIDENCE OF MARKET EDGE).

    required = margem + incerteza + calibração + amostra + eficiência

Exemplo (spec): bruto +5,2 pp, margem 5,1 %, incerteza 2,4 pp → required ≈ 7,8 pp → OBSERVATION.
O engine só torna a decisão **mais conservadora**: nunca cria VALUE, só o bloqueia.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.config import settings

DEFAULT_REPLAY_ECE = 0.02       # quando o replay não informa a calibração do campeão
CALIBRATION_PENALTY_PP = 0.5    # sem calibrador confiável
SAMPLE_PENALTY_PP = 0.5         # mercado sem prova OOS suficiente
MARKET_EFFICIENCY_PENALTY_PP = 1.0
MAX_OVERROUND = 0.15            # acima disto o mercado é considerado exótico: teto na margem


@dataclass
class UncertaintyInputs:
    model_prob: float
    member_probs: list[float]          # probabilidades da mesma seleção nos membros do consenso
    replay_ece: float | None           # ECE do campeão no último replay (fração, ex.: 0,021)
    min_sample: int                    # menor amostra de time (jogos)


def uncertainty_interval(inp: UncertaintyInputs) -> dict:
    """Intervalo de incerteza da probabilidade (§22). Três fontes, combinadas em quadratura:
    dispersão entre membros (meia-amplitude), calibração (ECE do replay) e erro amostral da menor
    amostra de time (√(p(1−p)/n), encolhido: os ratings usam bem mais do que n jogos por time)."""
    p = min(max(inp.model_prob, 0.0), 1.0)
    members = [m for m in inp.member_probs if m is not None and not math.isnan(m)]
    spread = (max(members) - min(members)) / 2.0 if len(members) >= 2 else 0.0
    ece = inp.replay_ece if inp.replay_ece is not None else DEFAULT_REPLAY_ECE
    n = max(int(inp.min_sample or 0), 1)
    sample = math.sqrt(p * (1 - p) / n) * 0.5
    half = math.sqrt(spread ** 2 + ece ** 2 + sample ** 2)
    return {
        "low": round(max(0.0, p - half), 4), "high": round(min(1.0, p + half), 4), "half_width_pp": round(half * 100, 2),
        "components_pp": {"member_spread": round(spread * 100, 2), "calibration": round(ece * 100, 2), "sample": round(sample * 100, 2)},
        "n_members": len(members),
    }


@dataclass
class RequiredEdgeInputs:
    edge_raw_pp: float
    market_overround: float | None
    uncertainty_half_pp: float
    calibration_reliable: bool
    market_oos_n: int
    market_efficiency_status: str | None   # veredito market-aware para o mercado (ou None)


def required_edge(inp: RequiredEdgeInputs) -> dict:
    margin = min(inp.market_overround if inp.market_overround is not None else 0.0, MAX_OVERROUND) * 100.0
    margin = max(margin, 0.0)
    calibration = 0.0 if inp.calibration_reliable else CALIBRATION_PENALTY_PP
    sample = 0.0 if inp.market_oos_n >= settings.value_min_oos_bets else SAMPLE_PENALTY_PP
    efficiency = MARKET_EFFICIENCY_PENALTY_PP if (inp.market_efficiency_status or "").startswith("NO EVIDENCE") else 0.0
    required = margin + inp.uncertainty_half_pp + calibration + sample + efficiency
    # o limiar configurado continua a valer: required nunca fica abaixo dele
    required = max(required, settings.min_edge_pp)
    adjusted = inp.edge_raw_pp - inp.uncertainty_half_pp
    return {
        "required_pp": round(required, 2), "edge_raw_pp": round(inp.edge_raw_pp, 2), "edge_adjusted_pp": round(adjusted, 2),
        "robust": inp.edge_raw_pp >= required,
        "components_pp": {"margin": round(margin, 2), "uncertainty": round(inp.uncertainty_half_pp, 2), "calibration": round(calibration, 2), "sample": round(sample, 2), "market_efficiency": round(efficiency, 2)},
        "gap_pp": round(inp.edge_raw_pp - required, 2),
    }


__all__ = ["uncertainty_interval", "required_edge", "UncertaintyInputs", "RequiredEdgeInputs"]
