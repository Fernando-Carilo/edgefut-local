"""Recommendation Engine: EDGE → RISCO → RECOMENDAÇÃO ou NO BET, por seleção e por evento."""

from __future__ import annotations

from ..core import versions
from ..core.config import settings
from ..domain.analysis import (
    ConfidenceBreakdown,
    CountDistribution,
    DataQuality,
    MarketOdds,
    NoBetVerdict,
    Recommendation,
    SimulationOutput,
)
from ..domain.freshness import FreshnessStatus
from ..models.calibration import pick
from ..providers.player import player_market_verdict
from .confidence import grade_for
from .edge import CATEGORY_BY_MARKET, WATCH_ONLY_MARKETS, edge_and_ev, model_probability
from .gate import GateContext, quality_gate
from .opportunity import OpportunityInputs, compute_opportunity
from .why import WhyContext, event_why_not, why_bet, why_not

EXTREME_MOVEMENT_PCT = 15.0
MODEL_DISAGREEMENT_PP = 10.0
SMALL_SAMPLE_N = 10


def opportunity_label(model_prob: float, edge_pp: float, ev_pct: float) -> str | None:
    """SAFE ≠ VALUE. HIGH_PROBABILITY fala da probabilidade; VALUE fala do edge. Podem coexistir."""
    high = model_prob >= settings.high_probability_min
    value = edge_pp >= settings.min_edge_pp and ev_pct >= settings.min_ev_pct
    if high and value:
        return "HIGH_PROBABILITY_VALUE"
    if high:
        return "HIGH_PROBABILITY"
    if value:
        return "VALUE"
    return None


def _event_no_bet(
    *,
    supported: bool,
    teams_resolved: bool,
    min_sample: int,
    sim: SimulationOutput | None,
    model_disagreement_pp: float | None,
    confidence: ConfidenceBreakdown,
    unreliable_source: bool,
    stale_data: str | None = None,
) -> NoBetVerdict | None:
    if stale_data:
        return NoBetVerdict(no_bet=True, reason="STALE_DATA", detail=stale_data)
    if not supported:
        return NoBetVerdict(no_bet=True, reason="UNSUPPORTED_COMPETITION", detail="Competição sem dataset histórico público mapeado.")
    if not teams_resolved or sim is None:
        return NoBetVerdict(no_bet=True, reason="LOW_DATA", detail="Não foi possível identificar histórico suficiente para as duas equipes.")
    if unreliable_source:
        return NoBetVerdict(no_bet=True, reason="UNRELIABLE_SOURCE", detail="Fonte histórica indisponível ou bloqueada na coleta.")
    if min_sample < SMALL_SAMPLE_N:
        return NoBetVerdict(no_bet=True, reason="SMALL_SAMPLE", detail=f"Menor amostra com apenas {min_sample} jogos (mín. {SMALL_SAMPLE_N}).")
    if model_disagreement_pp is not None and model_disagreement_pp > settings.gate_max_disagreement_pp:
        return NoBetVerdict(no_bet=True, reason="MODEL_DISAGREEMENT", detail=f"Modelos de gols divergem {model_disagreement_pp:.1f} pp no 1X2 (limite {settings.gate_max_disagreement_pp:.0f} pp).")
    if confidence.grade == "D":
        return NoBetVerdict(no_bet=True, reason="LOW_CONFIDENCE", detail=f"Confiança {confidence.score:.0f}/100 (grade D).")
    return None


def evaluate(
    *,
    markets: list[MarketOdds],
    sim: SimulationOutput | None,
    corners: CountDistribution,
    cards: CountDistribution,
    confidence: ConfidenceBreakdown,
    data_quality: DataQuality,
    supported: bool,
    teams_resolved: bool,
    min_sample: int,
    model_disagreement_pp: float | None,
    unreliable_source: bool,
    market_calibration: dict[str, float] | None = None,
    stale_data: str | None = None,
    calibrators: dict | None = None,
    competition: str | None = None,
    odds_freshness: FreshnessStatus | None = None,
    odds_age_seconds: int | None = None,
    provider_status: str | None = None,
    n_models: int = 1,
    model_source: str = "modelo de gols",
    historical: dict[str, dict] | None = None,  # market_key → {"roi": %, "n": int}
    evidence: str = "MODEL_ONLY",
) -> tuple[list[Recommendation], NoBetVerdict, list[str]]:
    """Retorna (recomendações, veredito do evento, WHY NOT do evento)."""
    event_block = _event_no_bet(
        supported=supported, teams_resolved=teams_resolved, min_sample=min_sample, sim=sim,
        model_disagreement_pp=model_disagreement_pp, confidence=confidence, unreliable_source=unreliable_source,
        stale_data=stale_data,
    )
    market_calibration = market_calibration or {}
    historical = historical or {}
    why_ctx = WhyContext(
        model_source=model_source, odds_freshness=odds_freshness, odds_age_seconds=odds_age_seconds,
        model_disagreement_pp=model_disagreement_pp, n_models=n_models, min_sample=min_sample,
        data_quality=data_quality.score, provider_status=provider_status, evidence=evidence,
    )
    recs: list[Recommendation] = []
    player_markets = [m for m in markets if m.market_key == "PLAYER_TO_SCORE"]
    if player_markets:
        n_players = sum(len(m.selections) for m in player_markets)
        recs.append(
            Recommendation(
                market_key="PLAYER_TO_SCORE", market_label=player_markets[0].label, selection_key="*",
                selection_name=f"{n_players} jogadores ofertados", line=None, odd=0.0, model_prob=0.0,
                market_prob=0.0, market_prob_is_fair=False, edge_pp=0.0, ev_pct=0.0, confidence_score=0.0,
                confidence_grade="D", opportunity_score=0.0, status="NO_BET", reasons=[player_market_verdict()[0]],
                category="JOGADOR",
                explanation=player_market_verdict()[1],
                why_not=[player_market_verdict()[1]],
            )
        )

    for market in markets:
        if market.market_key == "PLAYER_TO_SCORE":
            continue
        for sel in market.selections:
            mp = model_probability(market, sel.key, sim, corners, cards)
            market_prob = sel.fair if sel.fair is not None else sel.implied
            reasons: list[str] = []
            category = CATEGORY_BY_MARKET.get(market.market_key, "OUTRO")
            if mp is None:
                continue  # sem modelo para esta seleção — não inventamos

            raw_p = mp
            cal = pick(calibrators, market.market_key, competition) if calibrators else None
            cal_p = round(cal(raw_p), 4) if cal is not None else None
            mp = cal_p if cal_p is not None else raw_p
            sel.model_prob = round(mp, 4)
            edge_pp, ev_pct = edge_and_ev(mp, market_prob, sel.price)
            sel.edge_pp, sel.ev_pct = edge_pp, ev_pct

            # confiança por seleção: evento + penalidades específicas do mercado
            conf_score = confidence.score
            if market.market_key in {"TOTAL_CORNERS", "TOTAL_CARDS"}:
                dist = corners if market.market_key == "TOTAL_CORNERS" else cards
                conf_score = conf_score * min(1.0, dist.sample_size / 20) * 0.9
            if not market.margin_removed:
                conf_score *= 0.9
                reasons.append("margem não removida (mercado incompleto)")
            if market.market_key in WATCH_ONLY_MARKETS:
                conf_score *= 0.6
                reasons.append("mercado de alta variância / aproximação")
            grade = grade_for(conf_score)
            cal_quality = market_calibration.get(market.market_key)
            hist = historical.get(market.market_key) or {}
            opp = compute_opportunity(
                OpportunityInputs(
                    confidence_score=conf_score, data_quality=data_quality.score, edge_pp=edge_pp, ev_pct=ev_pct,
                    odds_freshness=odds_freshness, odds_age_seconds=odds_age_seconds,
                    model_disagreement_pp=model_disagreement_pp, n_models=n_models, sample_size=min_sample,
                    calibration_quality=cal_quality, calibration_reliable=cal is not None,
                    historical_roi=hist.get("roi"), historical_n=int(hist.get("n") or 0),
                )
            )

            status = "RECOMMENDED"
            gate = None
            if event_block is not None:
                status, reasons = "NO_BET", [event_block.reason or "NO_BET", *reasons]
            elif sel.movement_pct is not None and abs(sel.movement_pct) > EXTREME_MOVEMENT_PCT:
                status, reasons = "NO_BET", ["EXTREME_ODDS_MOVEMENT", *reasons]
            elif grade == "D":
                status, reasons = "NO_BET", ["LOW_CONFIDENCE", *reasons]
            elif edge_pp < settings.min_edge_pp or ev_pct < settings.min_ev_pct:
                status, reasons = "NO_BET", ["NO_EDGE", *reasons]
            elif not (settings.min_odd <= sel.price <= settings.max_odd):
                status, reasons = "WATCH", ["odd fora do intervalo configurado", *reasons]
            elif market.market_key in WATCH_ONLY_MARKETS or grade == "C":
                status = "WATCH"

            if status == "RECOMMENDED":
                gate = quality_gate(
                    GateContext(
                        data_quality=data_quality.score, confidence_score=conf_score, min_sample=min_sample,
                        model_disagreement_pp=model_disagreement_pp, n_models=n_models, odds_freshness=odds_freshness,
                        provider_status=provider_status, edge_pp=edge_pp, ev_pct=ev_pct, odd=sel.price,
                        calibration_reliable=cal is not None,
                    )
                )
                if not gate.passed:
                    status, reasons = "WATCH", ["QUALITY_GATE", *reasons]

            rec = Recommendation(
                market_key=market.market_key, market_label=market.label, selection_key=sel.key,
                selection_name=sel.name, line=market.line, odd=sel.price, model_prob=round(mp, 4),
                model_prob_raw=round(raw_p, 4), model_prob_calibrated=cal_p,
                calibration_group=cal.group_key if cal is not None else None, calibration_reliable=cal is not None,
                market_prob=round(market_prob, 4), market_prob_is_fair=sel.fair is not None,
                edge_pp=edge_pp, ev_pct=ev_pct, confidence_score=round(conf_score, 1), confidence_grade=grade,
                opportunity_score=opp.score, status=status, reasons=reasons, category=category,  # type: ignore[arg-type]
                label=opportunity_label(mp, edge_pp, ev_pct), opportunity=opp, quality_gate=gate,  # type: ignore[arg-type]
                evidence=evidence,  # type: ignore[arg-type]
            )
            rec.why = why_bet(rec, why_ctx) if status in ("RECOMMENDED", "WATCH") else []
            rec.why_not = why_not(rec, why_ctx, gate) if status != "RECOMMENDED" else []
            recs.append(rec)

    recs.sort(key=lambda r: (-(r.status == "RECOMMENDED"), -(r.status == "WATCH"), -r.opportunity_score))

    if event_block is not None:
        return recs, event_block, event_why_not(event_block, why_ctx)
    if any(r.status == "RECOMMENDED" for r in recs):
        return recs, NoBetVerdict(no_bet=False), []
    if any(r.status == "WATCH" for r in recs):
        gated = [r for r in recs if r.status == "WATCH" and "QUALITY_GATE" in r.reasons]
        if gated:
            failed = sorted({k for r in gated for k in (r.quality_gate.failed if r.quality_gate else [])})
            v = NoBetVerdict(no_bet=True, reason="QUALITY_GATE", detail=f"{len(gated)} seleção(ões) com edge, mas reprovadas no quality gate: {', '.join(failed)}.")
        else:
            v = NoBetVerdict(no_bet=True, reason="NO_EDGE", detail="Apenas seleções em observação; nenhuma atende aos limiares de edge, EV, odd e confiança.")
        return recs, v, event_why_not(v, why_ctx)
    v = NoBetVerdict(no_bet=True, reason="NO_EDGE", detail="Mercado bem precificado: nenhuma seleção com edge suficiente.")
    return recs, v, event_why_not(v, why_ctx)


__all__ = ["evaluate", "opportunity_label", "versions"]

