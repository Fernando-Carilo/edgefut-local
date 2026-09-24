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
from ..flywheel.governance import value_enabled_for
from ..models.calibration import pick
from ..providers.player import player_market_verdict
from .confidence import grade_for
from .correlation import apply_clusters
from .edge import CATEGORY_BY_MARKET, WATCH_ONLY_MARKETS, edge_and_ev, model_probability
from .gate import GateContext, quality_gate
from .opportunity import OpportunityInputs, compute_opportunity
from .pricing import WATCH_PRICE_TEXT, is_watching_price, price_target
from .required_edge import (
    RequiredEdgeInputs,
    UncertaintyInputs,
    required_edge,
    uncertainty_interval,
)
from .why import WhyContext, event_why_not, why_bet, why_not

EXTREME_MOVEMENT_PCT = 15.0
MODEL_DISAGREEMENT_PP = 10.0
SMALL_SAMPLE_N = 10

MODEL_ONLY_TEXT = "Probabilidade calculada, mas sem preço de mercado válido para determinar valor."
STATE_TEXT = {
    "MODEL_ONLY": MODEL_ONLY_TEXT,
    "MARKET_OBSERVED": "Modelo e mercado concordam: sem edge relevante.",
    "VALUE_CANDIDATE": "Passou no quality gate; falta prova out-of-sample suficiente neste mercado.",
    "VALUE": "Passou no quality gate e o mercado tem histórico out-of-sample suficiente.",
    "RESEARCH_SIGNAL": "Sinal de pesquisa: o modelo vê edge, mas este mercado ainda não está validado contra a Superbet (VALUE desativado). Observar, não apostar.",
    "OBSERVATION": "Edge existe, mas ficou em observação.",
    "NO_BET": "Sem entrada.",
}
EDGE_NOT_ROBUST_TEXT = "Edge bruto abaixo do required edge (margem + incerteza + calibração + amostra): em observação."
RESIDUAL_LOW_TEXT = "O challenger market-aware validado não vê edge além do mercado nesta seleção: em observação."


def member_probabilities(rows: list[dict] | None, market_key: str, selection_key: str, line: float | None) -> list[float]:
    """Probabilidades da mesma seleção nos membros do consenso (para o intervalo de incerteza)."""
    out: list[float] = []
    for r in rows or []:
        if not r.get("available") or (r.get("weight") or 0) <= 0:
            continue
        p = None
        if market_key == "1X2":
            p = {"HOME": r.get("p_home"), "DRAW": r.get("p_draw"), "AWAY": r.get("p_away")}.get(selection_key)
        elif market_key == "TOTAL_GOALS" and line is not None and abs(line - 2.5) < 1e-9 and r.get("over25") is not None:
            p = r["over25"] if selection_key == "OVER" else 1 - r["over25"]
        elif market_key == "BTTS" and r.get("btts") is not None:
            p = r["btts"] if selection_key == "YES" else 1 - r["btts"]
        if p is not None:
            out.append(float(p))
    return out


def state_label(state: str | None, model_prob: float) -> str | None:
    """Rótulo derivado do estado. MODEL_FAVORITE fala de probabilidade, nunca de valor."""
    if state == "VALUE":
        return "VALUE"
    if state == "VALUE_CANDIDATE":
        return "VALUE_CANDIDATE"
    if state == "RESEARCH_SIGNAL":
        return "RESEARCH_SIGNAL"
    if state == "MODEL_ONLY":
        return "MODEL_ONLY"
    if state == "OBSERVATION":
        return "WATCH"
    if state in ("MARKET_OBSERVED", "NO_BET") and model_prob >= settings.high_probability_min:
        return "MODEL_FAVORITE"
    return None


EXTREME_PROB = 0.90
EXTREME_STRONG_TEAM_SAMPLE = 30  # jogos na menor amostra de time para sustentar > 90 %


def extreme_probability_guard(model_prob: float, min_sample: int, hist: dict | None) -> dict | None:
    """§37 — probabilidades > 90 % exigem amostra forte. Nunca truncamos a probabilidade:
    a penalidade cai sobre a confiança (fator multiplicativo) e fica registrada como motivo.

    Amostra forte = menor amostra de time ≥ `EXTREME_STRONG_TEAM_SAMPLE` **e** o mercado tem
    histórico settled ≥ `settings.sample_moderate_min` (quando há histórico)."""
    if model_prob <= EXTREME_PROB:
        return None
    hist_n = int((hist or {}).get("n") or 0)
    team_ok = min_sample >= EXTREME_STRONG_TEAM_SAMPLE
    market_ok = hist_n >= settings.sample_moderate_min
    if team_ok and market_ok:
        return None
    factor = 0.85 if team_ok or market_ok else 0.7
    return {
        "model_prob": round(model_prob, 4), "confidence_factor": factor, "team_sample": min_sample,
        "team_sample_min": EXTREME_STRONG_TEAM_SAMPLE, "market_settled_n": hist_n, "market_settled_min": settings.sample_moderate_min,
    }


def oos_check(market_key: str, oos: dict[str, dict] | None) -> tuple[str, dict]:
    """CALIBRATION / OOS CHECK: o mercado tem prova out-of-sample suficiente?

    `oos[market_key]` vem do último replay (apostas simuladas do campeão, gate simplificado) e/ou
    das apostas reais liquidadas: {"n": int, "roi_low": float|None, "verdict": str}.
    Retorna ("PASS" | "INSUFFICIENT" | "NEGATIVE", detalhe)."""
    row = (oos or {}).get(market_key)
    if not row or int(row.get("n") or 0) < settings.value_min_oos_bets:
        return "INSUFFICIENT", {"n": int((row or {}).get("n") or 0), "min": settings.value_min_oos_bets, "source": (row or {}).get("source")}
    if row.get("verdict") == "NEGATIVE":
        return "NEGATIVE", {"n": int(row["n"]), "roi_low": row.get("roi_low"), "roi_high": row.get("roi_high"), "source": row.get("source")}
    return "PASS", {"n": int(row["n"]), "verdict": row.get("verdict"), "source": row.get("source")}


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


def _market_aware_row(view: dict | None, market_key: str, selection_key: str, line: float | None) -> dict | None:
    from ..analysis.market_view import selection_lookup

    return selection_lookup(view, market_key, selection_key, line)


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
    oos: dict[str, dict] | None = None,  # market_key → prova out-of-sample (replay/settled)
    model_rows: list[dict] | None = None,  # ModelComparison.rows serializado (membros do consenso)
    replay_ece: dict[str, float] | None = None,  # {"1X2": ece, "OU25": ece} do campeão no último replay
    market_view: dict | None = None,  # analysis.market_view.market_view(): bloco híbrido do evento
    market_efficiency: dict[str, str] | None = None,  # {"1X2": status, "OU25": status} da validação market-aware
) -> tuple[list[Recommendation], NoBetVerdict, list[str]]:
    """Retorna (recomendações, veredito do evento, WHY NOT do evento).

    Estados (iteração 3): RECOMMENDED só existe como status quando o estado é VALUE ou
    VALUE_CANDIDATE. Com evidência MODEL_ONLY (competição sem odds históricas para validar o
    modelo contra o mercado) nenhuma seleção passa de MODEL_ONLY — nunca VALUE, nunca ROI."""
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
            # §37 — probabilidade extrema: a probabilidade NÃO é truncada; a confiança paga o preço
            # quando a amostra não sustenta uma afirmação tão forte.
            hist = historical.get(market.market_key) or {}
            extreme = extreme_probability_guard(mp, min_sample, hist)
            if extreme is not None:
                conf_score *= extreme["confidence_factor"]
                reasons.append("EXTREME_PROBABILITY")
            grade = grade_for(conf_score)
            cal_quality = market_calibration.get(market.market_key)
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

            # ---- iteração 4: edge bruto vs required edge (§21–§24) e market-aware (§36–§37) ------
            price_ok = sel.price is not None and sel.price > 1.0
            unc = uncertainty_interval(UncertaintyInputs(
                model_prob=mp, member_probs=member_probabilities(model_rows, market.market_key, sel.key, market.line),
                replay_ece=(replay_ece or {}).get("OU25" if market.market_key == "TOTAL_GOALS" else "1X2"), min_sample=min_sample,
            ))
            ma_key = "OU25" if market.market_key == "TOTAL_GOALS" and market.line is not None and abs(market.line - 2.5) < 1e-9 else ("1X2" if market.market_key == "1X2" else None)
            req_edge = required_edge(RequiredEdgeInputs(
                edge_raw_pp=edge_pp, market_overround=market.overround, uncertainty_half_pp=unc["half_width_pp"], calibration_reliable=cal is not None,
                market_oos_n=int(((oos or {}).get(market.market_key) or {}).get("n") or 0), market_efficiency_status=(market_efficiency or {}).get(ma_key) if ma_key else None,
            )) if price_ok else None
            ma_row = _market_aware_row(market_view, market.market_key, sel.key, market.line)
            if status == "RECOMMENDED" and req_edge is not None and not req_edge["robust"]:
                status, reasons = "WATCH", ["EDGE_NOT_ROBUST", *reasons]
            elif status == "RECOMMENDED" and ma_row is not None and ma_row.get("validated") and ma_row["residual_edge_pp"] < settings.min_edge_pp:
                status, reasons = "WATCH", ["RESIDUAL_EDGE_LOW", *reasons]

            # ---- estado (§22) --------------------------------------------------------------
            price = price_target(mp, sel.price if price_ok else None, (1.0 + market.overround) if market.overround is not None else None, market_prob if price_ok else None)
            oos_row: dict | None = None
            if not price_ok:
                state = "MODEL_ONLY"
                if status == "RECOMMENDED":
                    status, reasons = "WATCH", ["MODEL_ONLY", *reasons]
            elif evidence == "MODEL_ONLY" and event_block is None:
                # há preço, mas a competição nunca foi validada contra odds (seleções/FIFA, ligas sem odds
                # históricas) → §29: tudo MODEL_ONLY; não se fala em valor, edge é hipótese, nunca ROI.
                state = "MODEL_ONLY"
                if status in ("RECOMMENDED", "WATCH"):
                    status, reasons = "WATCH", ["MODEL_ONLY", *[r for r in reasons if r != "QUALITY_GATE"]]
                elif "MODEL_ONLY" not in reasons:
                    reasons = ["MODEL_ONLY", *reasons]
            elif status == "RECOMMENDED":
                verdict, oos_row = oos_check(market.market_key, oos)
                if verdict == "PASS":
                    state = "VALUE"
                elif verdict == "NEGATIVE":
                    state, status, reasons = "OBSERVATION", "WATCH", ["OOS_NEGATIVE", *reasons]
                else:
                    state = "VALUE_CANDIDATE"
            elif status == "WATCH":
                state = "OBSERVATION"
            elif "NO_EDGE" in reasons and event_block is None:
                state = "MARKET_OBSERVED"
                if is_watching_price(mp, sel.price, price.get("price_gap_pct"), min_acceptable_odd=price.get("min_acceptable_odd")):
                    state, status, reasons = "OBSERVATION", "WATCH", ["WATCHING_PRICE", *[r for r in reasons if r != "NO_EDGE"]]
            else:
                state = "NO_BET"
            # ---- iteração 5 (§31–§34): VALUE nunca surge só da previsão. Enquanto o mercado não estiver
            # MARKET_VALIDATED na Superbet (value_enabled=false, padrão), o sinal é RESEARCH_SIGNAL em WATCH.
            if state in ("VALUE", "VALUE_CANDIDATE") and not value_enabled_for(market.market_key):
                state, status, reasons = "RESEARCH_SIGNAL", "WATCH", ["VALUE_DISABLED", *reasons]
            if "WATCHING_PRICE" in reasons:
                state_text = WATCH_PRICE_TEXT
            elif state == "OBSERVATION" and "EDGE_NOT_ROBUST" in reasons:
                state_text = EDGE_NOT_ROBUST_TEXT
            elif state == "OBSERVATION" and "RESIDUAL_EDGE_LOW" in reasons:
                state_text = RESIDUAL_LOW_TEXT
            else:
                state_text = STATE_TEXT[state]

            rec = Recommendation(
                market_key=market.market_key, market_label=market.label, selection_key=sel.key,
                selection_name=sel.name, line=market.line, odd=sel.price, model_prob=round(mp, 4),
                model_prob_raw=round(raw_p, 4), model_prob_calibrated=cal_p,
                calibration_group=cal.group_key if cal is not None else None, calibration_reliable=cal is not None,
                market_prob=round(market_prob, 4), market_prob_is_fair=sel.fair is not None,
                edge_pp=edge_pp, ev_pct=ev_pct, confidence_score=round(conf_score, 1), confidence_grade=grade,
                opportunity_score=opp.score, status=status, reasons=reasons, category=category,  # type: ignore[arg-type]
                label=state_label(state, mp), opportunity=opp, quality_gate=gate,  # type: ignore[arg-type]
                evidence=evidence,  # type: ignore[arg-type]
                state=state, state_text=state_text, price=price, oos=oos_row,  # type: ignore[arg-type]
                uncertainty=unc, required_edge=req_edge, market_aware=ma_row,
            )
            if state == "MODEL_ONLY":
                rec.opportunity_adjustments = {"model_only": -rec.opportunity_score}
                rec.opportunity_score = 0.0  # sem preço/validação não existe "oportunidade" a pontuar
            elif oos_row is not None and state == "VALUE_CANDIDATE":
                rec.opportunity_adjustments = {"uncertainty_penalty": -5.0}
                rec.opportunity_score = round(max(0.0, rec.opportunity_score - 5.0), 1)
            rec.why = why_bet(rec, why_ctx) if status in ("RECOMMENDED", "WATCH") else []
            rec.why_not = why_not(rec, why_ctx, gate) if status != "RECOMMENDED" else []
            recs.append(rec)

    # ---- correlation gate: uma primária por tese; alternativas penalizadas ----------------
    apply_clusters(recs)
    recs.sort(key=lambda r: (-(r.status == "RECOMMENDED"), -(r.status == "WATCH"), -r.opportunity_score))

    if event_block is not None:
        return recs, event_block, event_why_not(event_block, why_ctx)
    if any(r.status == "RECOMMENDED" for r in recs):
        return recs, NoBetVerdict(no_bet=False), []
    if any(r.status == "WATCH" for r in recs):
        gated = [r for r in recs if r.status == "WATCH" and "QUALITY_GATE" in r.reasons]
        model_only = [r for r in recs if r.state == "MODEL_ONLY" and "MODEL_ONLY" in r.reasons]
        research = [r for r in recs if r.state == "RESEARCH_SIGNAL"]
        if research:
            v = NoBetVerdict(no_bet=True, reason="VALUE_DISABLED", detail=f"{len(research)} sinal(is) de pesquisa: o modelo vê edge, mas VALUE está desativado até o mercado ser validado contra a Superbet (iteração 5). Nenhuma aposta é recomendada.")
        elif model_only and not gated:
            v = NoBetVerdict(no_bet=True, reason="MODEL_ONLY", detail=f"{len(model_only)} seleção(ões) com probabilidade calculada, mas sem validação modelo × mercado nesta competição. {MODEL_ONLY_TEXT}")
        elif gated:
            failed = sorted({k for r in gated for k in (r.quality_gate.failed if r.quality_gate else [])})
            v = NoBetVerdict(no_bet=True, reason="QUALITY_GATE", detail=f"{len(gated)} seleção(ões) com edge, mas reprovadas no quality gate: {', '.join(failed)}.")
        else:
            v = NoBetVerdict(no_bet=True, reason="NO_EDGE", detail="Apenas seleções em observação; nenhuma atende aos limiares de edge, EV, odd e confiança.")
        return recs, v, event_why_not(v, why_ctx)
    v = NoBetVerdict(no_bet=True, reason="NO_EDGE", detail="Mercado bem precificado: nenhuma seleção com edge suficiente.")
    return recs, v, event_why_not(v, why_ctx)


__all__ = ["evaluate", "opportunity_label", "state_label", "oos_check", "MODEL_ONLY_TEXT", "STATE_TEXT", "versions"]

