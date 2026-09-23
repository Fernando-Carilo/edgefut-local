"""Blocos WHY THIS BET / WHY NOT: frases curtas, factuais, com os números da análise.

Regra: nunca usar linguagem de garantia ("garantido", "certeza", "seguro", "infalível").
`assert_no_guarantee_language` é usado em teste para travar isso.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.analysis import NoBetVerdict, QualityGate, Recommendation
from ..domain.freshness import FreshnessStatus, describe_age

FORBIDDEN_TERMS = ("garant", "certeza", "infal", "seguro", "seguríssim", "100%", "não tem erro", "sem risco")


@dataclass
class WhyContext:
    model_source: str  # ex.: "consenso de 3 modelos (pesos walk-forward:USA)"
    odds_freshness: FreshnessStatus | None
    odds_age_seconds: int | None
    model_disagreement_pp: float | None
    n_models: int
    min_sample: int
    data_quality: float
    provider_status: str | None
    evidence: str = "MODEL_ONLY"


def _pct(x: float, nd: int = 1) -> str:
    return f"{100 * x:.{nd}f}%"


def why_bet(rec: Recommendation, ctx: WhyContext) -> list[str]:
    out: list[str] = []
    fair = "justa (margem removida)" if rec.market_prob_is_fair else "implícita (margem não removida)"
    out.append(f"Modelo estima {_pct(rec.model_prob)} contra {_pct(rec.market_prob)} {fair} na odd {rec.odd:.2f} → edge {rec.edge_pp:+.1f} pp, EV {rec.ev_pct:+.1f}%.")
    out.append(f"Probabilidade vem de {ctx.model_source}.")
    if rec.calibration_reliable and rec.model_prob_calibrated is not None and rec.model_prob_raw is not None:
        out.append(f"Calibrada: RAW {_pct(rec.model_prob_raw)} → CALIBRADA {_pct(rec.model_prob_calibrated)} (grupo {rec.calibration_group}).")
    else:
        out.append("Probabilidade RAW (ainda sem 300 previsões settled neste mercado para calibrar).")
    if ctx.n_models >= 2 and ctx.model_disagreement_pp is not None:
        out.append(f"Modelos de gols concordam: divergência máxima {ctx.model_disagreement_pp:.1f} pp no 1X2.")
    if ctx.odds_freshness:
        out.append(f"Odds {ctx.odds_freshness}: coletadas {describe_age(ctx.odds_age_seconds)} da Superbet.")
    out.append(f"Qualidade dos dados {ctx.data_quality:.0f}% · menor amostra {ctx.min_sample} jogos · confiança {rec.confidence_score:.0f}/100 ({rec.confidence_grade}).")
    if ctx.evidence == "SETTLED":
        out.append("Evidência SETTLED: apostas reais liquidadas nesta competição com ROI/CLV medidos.")
    elif ctx.evidence == "BACKTEST_ODDS":
        out.append("Evidência BACKTEST_ODDS: dataset com odds históricas reais — o edge pode ser verificado no Backtest Lab.")
    else:
        out.append("Evidência MODEL_ONLY: sem odds históricas nesta competição; o modelo nunca foi comparado ao mercado aqui. Trate o edge como hipótese.")
    if rec.label in ("HIGH_PROBABILITY", "MODEL_FAVORITE"):
        out.append("Rótulo MODEL FAVORITE: probabilidade alta, não sinônimo de valor — o edge é o que justifica a entrada.")
    elif rec.label == "VALUE":
        out.append("Rótulo VALUE: o valor está na diferença entre modelo e mercado, não na probabilidade absoluta.")
    elif rec.label == "VALUE_CANDIDATE":
        out.append("Rótulo VALUE CANDIDATE: passou no gate, mas este mercado ainda não tem prova out-of-sample suficiente para ser chamado de VALUE.")
    if "EXTREME_PROBABILITY" in rec.reasons:
        out.append(f"Probabilidade extrema ({_pct(rec.model_prob, 0)}) com amostra insuficiente para sustentá-la: confiança penalizada, probabilidade não truncada.")
    return out


def why_not(rec: Recommendation, ctx: WhyContext, gate: QualityGate | None) -> list[str]:
    out: list[str] = []
    if gate is not None and not gate.passed:
        for c in gate.checks:
            if not c.passed:
                out.append(f"{c.label}: {c.detail}.")
    for r in rec.reasons:
        if r == "NO_EDGE":
            out.append(f"Sem edge: {rec.edge_pp:+.1f} pp / EV {rec.ev_pct:+.1f}% abaixo dos mínimos configurados.")
        elif r == "EXTREME_ODDS_MOVEMENT":
            out.append("Movimento extremo de odds desde a abertura: o mercado sabe algo que os dados públicos não mostram.")
        elif r == "LOW_CONFIDENCE":
            out.append(f"Confiança {rec.confidence_score:.0f}/100 (grade {rec.confidence_grade}).")
        elif r == "LINEUP_UNCERTAINTY":
            out.append("Mercado de jogador sem fonte de escalação/minutos: Player Engine desativado.")
        elif r == "QUALITY_GATE":
            continue  # já detalhado acima
        elif r == "WATCHING_PRICE":
            gap = (rec.price or {}).get("price_gap_pct")
            min_odd = (rec.price or {}).get("min_acceptable_odd")
            out.append(f"Sem edge suficiente: {rec.edge_pp:+.1f} pp / EV {rec.ev_pct:+.1f}%. Probabilidade interessante, mas preço atual não oferece margem suficiente" + (f" — odd mínima aceitável {min_odd:.2f} ({gap:+.1f}%)." if min_odd and gap is not None else "."))
        elif r == "MODEL_ONLY":
            out.append("Probabilidade calculada, mas sem preço de mercado válido para determinar valor: esta competição não tem odds históricas para validar o modelo contra o mercado (evidência MODEL_ONLY). Nunca rotulamos VALUE aqui.")
        elif r == "OOS_NEGATIVE":
            o = rec.oos or {}
            out.append(f"Prova out-of-sample negativa neste mercado (N={o.get('n')}, ROI IC 95% [{o.get('roi_low')}, {o.get('roi_high')}]): o edge existe no papel, mas o histórico não confirma.")
        elif r == "EXTREME_PROBABILITY":
            out.append(f"Probabilidade extrema ({rec.model_prob:.0%}) sem amostra forte que a sustente: a probabilidade não foi truncada, mas a confiança foi penalizada.")
        elif r.startswith("ALTERNATIVE_OF:"):
            out.append(f"Alternativa da mesma tese que {r.split(':', 1)[1].replace('/', ' · ')} — não é uma oportunidade adicional.")
        elif r in ("STALE_DATA", "MODEL_DISAGREEMENT", "SMALL_SAMPLE", "LOW_DATA", "UNSUPPORTED_COMPETITION", "UNRELIABLE_SOURCE"):
            continue  # motivo do evento, detalhado em event_why_not
        else:
            out.append(r[0].upper() + r[1:] + ".")
    if rec.status == "WATCH" and not out:
        out.append("Em observação: mercado de alta variância ou confiança C — acompanhar, não entrar.")
    return _dedupe(out)


def event_why_not(verdict: NoBetVerdict, ctx: WhyContext) -> list[str]:
    if not verdict.no_bet:
        return []
    out: list[str] = []
    if verdict.detail:
        out.append(verdict.detail)
    if verdict.reason == "MODEL_DISAGREEMENT":
        out.append("Quando os modelos divergem acima do limite, nenhuma probabilidade é confiável o bastante para comparar com a odd.")
    elif verdict.reason == "STALE_DATA":
        out.append("Dados EXPIRED nunca são usados silenciosamente: a análise fica visível, mas sem recomendação.")
    elif verdict.reason == "SMALL_SAMPLE":
        out.append("Amostra pequena infla a variância das taxas de gols; o modelo não distingue sorte de força.")
    elif verdict.reason == "UNSUPPORTED_COMPETITION":
        out.append("Sem histórico público mapeado não há como estimar força das equipes — o sistema não inventa.")
    elif verdict.reason == "LOW_CONFIDENCE":
        out.append(f"Qualidade dos dados {ctx.data_quality:.0f}% e amostra de {ctx.min_sample} jogos não sustentam uma entrada.")
    elif verdict.reason == "NO_EDGE":
        out.append("Mercado bem precificado: a diferença modelo × odd não cobre a margem e a incerteza.")
    if ctx.provider_status in ("STALE", "UNAVAILABLE"):
        out.append(f"Superbet {ctx.provider_status}: odds podem não refletir o preço atual.")
    return _dedupe(out)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def assert_no_guarantee_language(texts: list[str]) -> None:
    for t in texts:
        low = t.lower()
        for term in FORBIDDEN_TERMS:
            if term in low:
                raise AssertionError(f"linguagem de garantia proibida ({term!r}) em: {t}")


__all__ = ["FORBIDDEN_TERMS", "WhyContext", "assert_no_guarantee_language", "event_why_not", "why_bet", "why_not"]
