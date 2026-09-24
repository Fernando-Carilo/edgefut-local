"""Price target — a que preço esta seleção passaria a ter valor?

* `break_even_odd`       = 1 / p_modelo (EV = 0).
* `min_acceptable_odd`   = menor odd que satisfaz **os dois** limiares configurados:
      EV   ≥ min_ev_pct   → odd ≥ (1 + min_ev) / p
      edge ≥ min_edge_pp  → fair(odd) ≤ p − edge_min, com fair(odd) = 1/(odd·S) onde S é a soma
                            das implícitas do mercado (overround) → odd ≥ 1 / ((p − edge_min)·S)
* `price_gap_pct`        = odd_atual / min_acceptable_odd − 1 (negativo = preço curto).
* `edge_sensitivity`     = edge se a probabilidade do modelo estiver 3 pp errada para cada lado —
                           mostra quão frágil é o valor.
Nada aqui decide; só descreve o preço. Texto para a UI quando não há margem:
"Probabilidade interessante, mas preço atual não oferece margem suficiente."
"""

from __future__ import annotations

from ..core.config import settings

SENSITIVITY_PP = 3.0
WATCH_PRICE_TEXT = "Probabilidade interessante, mas preço atual não oferece margem suficiente."


def price_target(model_prob: float, odd: float | None, overround_sum: float | None, fair_prob: float | None) -> dict:
    p = float(min(0.999, max(0.001, model_prob)))
    s = float(overround_sum) if overround_sum and overround_sum > 0 else 1.0
    edge_min = settings.min_edge_pp / 100.0
    ev_min = settings.min_ev_pct / 100.0
    break_even = 1.0 / p
    odd_ev = (1.0 + ev_min) / p
    odd_edge = 1.0 / ((p - edge_min) * s) if p - edge_min > 0 else None
    min_odd = max(odd_ev, odd_edge) if odd_edge is not None else None
    out: dict = {
        "break_even_odd": round(break_even, 3),
        "min_acceptable_odd": round(min_odd, 3) if min_odd is not None else None,
        "min_odd_reason": "edge" if (odd_edge is not None and odd_edge >= odd_ev) else "ev",
    }
    if odd and odd > 1 and min_odd is not None:
        out["price_gap_pct"] = round((odd / min_odd - 1.0) * 100.0, 2)
    if odd and odd > 1 and fair_prob is not None:
        sens = {}
        for label, dp in (("prob_minus", -SENSITIVITY_PP / 100.0), ("prob_plus", SENSITIVITY_PP / 100.0)):
            pp = min(0.999, max(0.001, p + dp))
            sens[label] = {"edge_pp": round((pp - fair_prob) * 100.0, 2), "ev_pct": round((pp * odd - 1.0) * 100.0, 2)}
        out["edge_sensitivity_pp"] = SENSITIVITY_PP
        out["edge_sensitivity"] = sens
        out["edge_survives_minus"] = sens["prob_minus"]["edge_pp"] >= settings.min_edge_pp and sens["prob_minus"]["ev_pct"] >= settings.min_ev_pct
    return out


def is_watching_price(model_prob: float, odd: float | None, price_gap_pct: float | None, *, max_gap_pct: float = 12.0, min_acceptable_odd: float | None = None) -> bool:
    """Seleção "quase": probabilidade relevante e preço até `max_gap_pct` abaixo do mínimo aceitável.
    Só faz sentido se a odd-alvo cair dentro da faixa configurada — uma odd 1,05 que precisaria
    chegar a 1,10 nunca seria recomendada, logo não é "quase"."""
    if odd is None or price_gap_pct is None:
        return False
    if min_acceptable_odd is not None and not (settings.min_odd <= min_acceptable_odd <= settings.max_odd):
        return False
    return model_prob >= 0.30 and -max_gap_pct <= price_gap_pct < 0


__all__ = ["price_target", "is_watching_price", "WATCH_PRICE_TEXT", "SENSITIVITY_PP"]
