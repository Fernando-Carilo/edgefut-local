"""Quality Gate: uma seleção só vira RECOMMENDED (e só entra em TOP OPORTUNIDADES) se passar
em todas as verificações. Quem falha vai para observação (WATCH) com motivo QUALITY_GATE.

Os limiares vêm de `settings` (editáveis pelo usuário, com pisos em HARD_FLOORS) e nunca são
relaxados automaticamente pelo sistema.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.config import settings
from ..domain.analysis import QualityGate, QualityGateCheck
from ..domain.freshness import FreshnessStatus

HealthStatus = str  # HEALTHY | DEGRADED | STALE | UNAVAILABLE


@dataclass
class GateContext:
    data_quality: float
    confidence_score: float
    min_sample: int
    model_disagreement_pp: float | None
    n_models: int
    odds_freshness: FreshnessStatus | None
    provider_status: HealthStatus | None  # saúde do provider de odds (Superbet)
    edge_pp: float
    ev_pct: float
    odd: float
    calibration_reliable: bool = False


def quality_gate(ctx: GateContext) -> QualityGate:
    checks: list[QualityGateCheck] = []

    def check(key: str, label: str, ok: bool, detail: str) -> None:
        checks.append(QualityGateCheck(key=key, label=label, passed=bool(ok), detail=detail))

    check("data_quality", "Qualidade mínima dos dados", ctx.data_quality >= settings.gate_min_data_quality, f"{ctx.data_quality:.0f}% (mín. {settings.gate_min_data_quality:.0f}%)")
    check("confidence", "Confiança mínima", ctx.confidence_score >= settings.gate_min_confidence, f"{ctx.confidence_score:.0f}/100 (mín. {settings.gate_min_confidence:.0f})")
    check("sample", "Amostra mínima", ctx.min_sample >= settings.gate_min_sample, f"{ctx.min_sample} jogos (mín. {settings.gate_min_sample})")
    if ctx.n_models >= 2 and ctx.model_disagreement_pp is not None:
        check("disagreement", "Divergência máxima entre modelos", ctx.model_disagreement_pp <= settings.gate_max_disagreement_pp, f"{ctx.model_disagreement_pp:.1f} pp (máx. {settings.gate_max_disagreement_pp:.0f} pp)")
    else:
        check("disagreement", "Divergência máxima entre modelos", False, "apenas um modelo de gols disponível: sem verificação cruzada")
    check("odds_fresh", "Odds frescas", ctx.odds_freshness in ("FRESH", "AGING"), f"odds {ctx.odds_freshness or 'UNAVAILABLE'}")
    check("provider", "Provider de odds saudável", ctx.provider_status in ("HEALTHY", "DEGRADED"), f"Superbet {ctx.provider_status or 'desconhecido'}")
    check("edge", "Edge positivo acima do mínimo", ctx.edge_pp >= settings.min_edge_pp, f"{ctx.edge_pp:+.1f} pp (mín. {settings.min_edge_pp:.1f})")
    check("ev", "EV positivo acima do mínimo", ctx.ev_pct >= settings.min_ev_pct, f"{ctx.ev_pct:+.1f}% (mín. {settings.min_ev_pct:.1f}%)")
    check("odd_range", "Odd dentro do intervalo", settings.min_odd <= ctx.odd <= settings.max_odd, f"{ctx.odd:.2f} ({settings.min_odd:.2f}–{settings.max_odd:.2f})")
    if ctx.calibration_reliable:
        check("edge_plausible", "Edge plausível", True, f"{ctx.edge_pp:+.1f} pp com calibrador confiável")
    else:
        check(
            "edge_plausible", "Edge plausível", ctx.edge_pp <= settings.gate_max_edge_pp_uncalibrated,
            f"{ctx.edge_pp:+.1f} pp sem calibrador (máx. {settings.gate_max_edge_pp_uncalibrated:.0f} pp): acima disso é mais provável erro do modelo do que do mercado",
        )

    failed = [c.key for c in checks if not c.passed]
    return QualityGate(passed=not failed, checks=checks, failed=failed)


__all__ = ["GateContext", "quality_gate"]
