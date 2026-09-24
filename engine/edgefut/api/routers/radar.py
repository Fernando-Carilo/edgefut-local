from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...analysis.pipeline import all_cached, event_summary
from ...core.config import settings
from ...db.models import Alert, Event, Favorite, PredictionSnapshot
from ...db.session import get_session
from ...domain.analysis import MatchAnalysis, Recommendation
from ...domain.freshness import describe_age
from ...scheduler import jobs
from ..schemas import DashboardResponse, EntriesResponse, EntryRow, RadarCard, RadarItem, RadarResponse, RadarSummary
from .events import main_odds_for, window_bounds

router = APIRouter(tags=["radar"])

DATA_NO_BET = {"UNSUPPORTED_COMPETITION", "LOW_DATA", "SMALL_SAMPLE", "UNRELIABLE_SOURCE", "STALE_DATA"}


def _upcoming(analyses: list[MatchAnalysis], hours: int = 48) -> list[MatchAnalysis]:
    now = datetime.utcnow()
    return [a for a in analyses if now - timedelta(hours=2) < a.event.kickoff_utc < now + timedelta(hours=hours)]


def _best(a: MatchAnalysis, category: str | None = None, statuses=("RECOMMENDED",), primary_only: bool = True) -> Recommendation | None:
    """Melhor seleção do evento — só primárias de cluster (uma por tese); alternativas nunca lideram."""
    for r in a.recommendations:
        if r.status in statuses and (category is None or r.category == category) and (r.is_primary or not primary_only):
            return r
    return None


def _best_state(a: MatchAnalysis, states: tuple[str, ...]) -> Recommendation | None:
    for r in a.recommendations:
        if r.is_primary and r.state in states and r.market_key != "PLAYER_TO_SCORE":
            return r
    return None


def _event_state(a: MatchAnalysis) -> str:
    """Estado do evento = melhor estado entre as primárias (ordem VALUE > VALUE_CANDIDATE > OBSERVATION > MODEL_ONLY > MARKET_OBSERVED > NO_BET)."""
    if a.no_bet.no_bet and a.no_bet.reason in DATA_NO_BET | {"LOW_CONFIDENCE", "MODEL_DISAGREEMENT"}:
        return "NO_BET"
    for st in ("VALUE", "VALUE_CANDIDATE", "RESEARCH_SIGNAL", "OBSERVATION", "MODEL_ONLY", "MARKET_OBSERVED"):
        if _best_state(a, (st,)):
            return st
    return "NO_BET"


def _item(a: MatchAnalysis, rec: Recommendation | None) -> RadarItem:
    if rec is not None:
        why = (rec.why if rec.status == "RECOMMENDED" else rec.why_not)[:2]
    else:
        why = a.why_not[:2]
    cluster = next((c for c in a.clusters if rec and c.get("cluster_id") == rec.cluster_id), None) if rec else None
    return RadarItem(
        event=a.event, recommendation=rec, opportunity_score=rec.opportunity_score if rec else a.opportunity_score,
        confidence_grade=rec.confidence_grade if rec else a.confidence.grade, data_quality=a.data_quality.score,
        no_bet_reason=a.no_bet.reason, label=rec.label if rec else None,
        quality_gate_passed=bool(rec and rec.quality_gate and rec.quality_gate.passed),
        freshness_status=a.freshness_status, evidence=a.evidence, why=why,
        state=rec.state if rec else _event_state(a), cluster_id=rec.cluster_id if rec else None,
        cluster_label=cluster.get("label") if cluster else None, alternatives=len(cluster.get("alternatives") or []) if cluster else 0,
        exposure=(a.exposure or {}).get("level"), actionable_clusters=len((a.exposure or {}).get("actionable_clusters") or []),
    )


def _maybe_refresh(analyses: list[MatchAnalysis]) -> bool:
    st = jobs.state()
    stale = st["last_radar_refresh"] is None or datetime.utcnow() - st["last_radar_refresh"] > timedelta(minutes=20)
    if (stale or not analyses) and not st["radar_running"]:
        jobs.run_in_background(jobs.job_refresh_radar)
        return True
    return bool(st["radar_running"])


def _events_found(session: Session, hours: int) -> int:
    now = datetime.utcnow()
    return int(
        session.execute(
            select(func.count()).select_from(Event).where(
                Event.kickoff_utc > now - timedelta(hours=2), Event.kickoff_utc < now + timedelta(hours=hours), Event.duplicate_of.is_(None),
            )
        ).scalar() or 0
    )


def _alerts_unread(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(Alert).where(Alert.read_at.is_(None))).scalar() or 0)


def summarize(session: Session, analyses: list[MatchAnalysis], hours: int) -> RadarSummary:
    st = jobs.state()
    gate = sum(1 for a in analyses if a.quality_gate_passed)
    a_count = b_count = high = value = watch = no_bet = stale = 0
    candidates = model_only_n = clusters_n = sel_n = exp_high = research_n = 0
    reasons: Counter[str] = Counter()
    by_evidence: Counter[str] = Counter()
    by_state: Counter[str] = Counter()
    for a in analyses:
        best = _best(a)
        est = _event_state(a)
        by_state[est] += 1
        if est == "VALUE":
            value += 1
        elif est == "VALUE_CANDIDATE":
            candidates += 1
        elif est == "RESEARCH_SIGNAL":
            research_n += 1
        elif est == "MODEL_ONLY":
            model_only_n += 1
        if any(r.is_primary and r.label == "MODEL_FAVORITE" for r in a.recommendations):
            high += 1
        clusters_n += len((a.exposure or {}).get("actionable_clusters") or [])
        sel_n += sum(1 for r in a.recommendations if r.status == "RECOMMENDED")
        if (a.exposure or {}).get("level") == "HIGH":
            exp_high += 1
        if best and a.quality_gate_passed:
            by_evidence[a.evidence or "MODEL_ONLY"] += 1
        if best:
            if best.confidence_grade == "A":
                a_count += 1
            elif best.confidence_grade == "B":
                b_count += 1
        elif _best(a, statuses=("WATCH",)):
            watch += 1
        elif a.no_bet.no_bet:
            # NO BET "puro": sem nada recomendado nem em observação (mesmo critério do card NO BET)
            no_bet += 1
            reasons[a.no_bet.reason or "NO_BET"] += 1
        if a.freshness_status in ("STALE", "EXPIRED"):
            stale += 1
    return RadarSummary(
        last_update=st.get("last_radar_refresh"), events_found=_events_found(session, hours),
        with_sufficient_data=sum(1 for a in analyses if a.no_bet.reason not in DATA_NO_BET), analyzed=len(analyses),
        quality_gate_passed=gate, confidence_a=a_count, confidence_b=b_count, high_probability=high, value=value,
        watch=watch, no_bet=no_bet, stale=stale, alerts_unread=_alerts_unread(session), no_bet_by_reason=dict(reasons),
        gate_passed_by_evidence=dict(by_evidence), events_by_state=dict(by_state), value_candidates=candidates, research_signals=research_n, model_only=model_only_n,
        actionable_clusters=clusters_n, selections_actionable=sel_n, exposure_high=exp_high,
    )


def _thresholds() -> dict[str, float]:
    return {
        "min_edge_pp": settings.min_edge_pp, "min_ev_pct": settings.min_ev_pct, "min_odd": settings.min_odd, "max_odd": settings.max_odd,
        "gate_min_data_quality": settings.gate_min_data_quality, "gate_min_confidence": settings.gate_min_confidence,
        "gate_min_sample": settings.gate_min_sample, "gate_max_disagreement_pp": settings.gate_max_disagreement_pp,
        "gate_max_edge_pp_uncalibrated": settings.gate_max_edge_pp_uncalibrated,
        "high_probability_min": settings.high_probability_min,
    }


@router.get("/radar", response_model=RadarResponse)
def radar(hours: int = Query(48, le=168), session: Session = Depends(get_session)):
    analyses = _upcoming(all_cached(), hours)
    refreshing = _maybe_refresh(analyses)
    cards: list[RadarCard] = []

    def card(key, title, subtitle, items, sort_key=None):
        items = sorted(items, key=sort_key or (lambda i: -i.opportunity_score))[:12]
        cards.append(RadarCard(key=key, title=title, subtitle=subtitle, items=items))

    def gated(a: MatchAnalysis, category: str | None = None) -> Recommendation | None:
        r = _best(a, category)
        return r if r and r.quality_gate and r.quality_gate.passed else None

    # TOP: só quem passou no quality gate (RECOMMENDED já implica isso; a checagem é explícita).
    # Uma PRIMÁRIA por tese: alternativas da mesma tese (DNB, DC, handicap…) não aparecem como oportunidades extra.
    top = [_item(a, gated(a)) for a in analyses if gated(a)]
    card("top", "TOP OPORTUNIDADES", "Uma primária por tese · passaram no quality gate · ordenadas por Opportunity Score V3", top)
    card("valor", "VALUE", f"Edge ≥ {settings.min_edge_pp:.0f} pp, EV ≥ {settings.min_ev_pct:.0f}% e prova out-of-sample no mercado (N ≥ {settings.value_min_oos_bets})", [_item(a, gated(a)) for a in analyses if gated(a) and gated(a).state == "VALUE"])  # type: ignore[union-attr]
    card("value_candidate", "VALUE CANDIDATE", "Passaram no quality gate, mas o mercado ainda não tem prova out-of-sample suficiente", [_item(a, gated(a)) for a in analyses if gated(a) and gated(a).state == "VALUE_CANDIDATE"])  # type: ignore[union-attr]
    # iteração 5: o modelo vê edge e passou no gate, mas o mercado não está validado contra a Superbet (VALUE desativado) → observar e medir, não apostar
    card("research_signal", "RESEARCH SIGNAL", "Passaram no quality gate, mas VALUE está desativado neste mercado até validação contra a Superbet — sinal para observar, não para apostar", [_item(a, r) for a in analyses for r in [_best_state(a, ("RESEARCH_SIGNAL",))] if r and r.quality_gate and r.quality_gate.passed])
    card("high_probability", "MODEL FAVORITE", f"Probabilidade do modelo ≥ {settings.high_probability_min:.0%} — fala de probabilidade, não de valor", [_item(a, r) for a in analyses for r in [next((x for x in a.recommendations if x.is_primary and x.label == "MODEL_FAVORITE"), None)] if r])
    card("model_only", "MODEL ONLY", "Probabilidade calculada, mas sem preço de mercado válido para determinar valor — nunca VALUE, nunca ROI", [_item(a, r) for a in analyses for r in [_best_state(a, ("MODEL_ONLY",))] if r and not gated(a)])
    card("high_confidence", "ALTA CONFIANÇA", "Confiança A", [_item(a, gated(a)) for a in analyses if gated(a) and gated(a).confidence_grade == "A"])  # type: ignore[union-attr]
    for key, title, cat in (("gols", "GOLS", "GOLS"), ("escanteios", "ESCANTEIOS", "ESCANTEIOS"), ("cartoes", "CARTÕES", "CARTOES"), ("finalizacoes", "FINALIZAÇÕES", "FINALIZACOES")):
        items = [_item(a, gated(a, cat)) for a in analyses if gated(a, cat)]
        card(key, title, f"Melhor seleção de {title.lower()} por jogo (quality gate)", items)
    obs = []
    for a in analyses:
        r = _best_state(a, ("OBSERVATION",))
        if r and not gated(a):
            obs.append(_item(a, r))
    card("observacao", "EM OBSERVAÇÃO", "Edge existe mas reprovou no gate, confiança C, prova OOS negativa ou preço curto (WATCHING PRICE)", obs)
    no_bet = [_item(a, None) for a in analyses if a.no_bet.no_bet and not _best(a, statuses=("WATCH",))]
    card("no_bet", "NO BET", "Jogos em que o sistema recomenda não entrar — cada um com o motivo", no_bet, sort_key=lambda i: i.event.kickoff_utc.timestamp())
    return RadarResponse(
        generated_at=datetime.utcnow(), analyzed_events=len(analyses), refreshing=refreshing, cards=cards,
        summary=summarize(session, analyses, hours), thresholds=_thresholds(),
    )


@router.get("/entries", response_model=EntriesResponse)
def entries(
    window: str = Query("48h", pattern="^(today|tomorrow|48h|week)$"),
    competition: str | None = None,
    market: str | None = None,
    min_odd: float | None = None,
    max_odd: float | None = None,
    grades: str = Query("A,B", description="graus aceitos, separados por vírgula"),
    include_watch: bool = False,
):
    start, end = window_bounds(window)
    allowed = {g.strip().upper() for g in grades.split(",") if g.strip()}
    statuses = ("RECOMMENDED", "WATCH") if include_watch else ("RECOMMENDED",)
    rows: list[EntryRow] = []
    for a in all_cached():
        if not (start <= a.event.kickoff_utc < end):
            continue
        if competition and competition.lower() not in (a.event.competition_name or "").lower():
            continue
        for r in a.recommendations:
            if r.status not in statuses or r.confidence_grade not in allowed:
                continue
            if market and r.market_key != market:
                continue
            if min_odd and r.odd < min_odd or max_odd and r.odd > max_odd:
                continue
            rows.append(EntryRow(event=a.event, recommendation=r))
    rows.sort(key=lambda x: -x.recommendation.opportunity_score)
    return EntriesResponse(rows=rows, total=len(rows))


def morning_summary(name: str, s: RadarSummary, health_overall: str | None) -> str:
    """Texto do workflow da manhã: números reais, sem promessa."""
    if s.analyzed == 0:
        if s.events_found == 0:
            return f"{name}, a Superbet ainda não retornou jogos para as próximas 48 h. O radar tentará de novo no próximo ciclo."
        return f"{name}, encontramos {s.events_found} jogos nas próximas 48 h e a primeira análise está rodando. Volte em alguns minutos."
    parts = [f"{name}, analisamos {s.analyzed} de {s.events_found} jogos das próximas 48 h."]
    if s.quality_gate_passed:
        parts.append(f"{s.quality_gate_passed} passaram no quality gate ({s.confidence_a} com confiança A, {s.confidence_b} com B): {s.value} VALUE, {s.value_candidates} VALUE CANDIDATE, {s.research_signals} RESEARCH SIGNAL, {s.actionable_clusters} tese(s) em {s.selections_actionable} seleção(ões).")
        if s.research_signals and not s.value:
            parts.append("VALUE está desativado em todos os mercados até haver validação contra a Superbet — os sinais são para observar preço e movimento, não para apostar.")
    else:
        parts.append("Nenhum passou no quality gate hoje — isso é o sistema funcionando, não falhando.")
    if s.model_only:
        parts.append(f"{s.model_only} em MODEL ONLY: probabilidade calculada, mas sem validação modelo × mercado nesta competição.")
    if s.watch:
        parts.append(f"{s.watch} em observação.")
    if s.no_bet:
        top_reason = max(s.no_bet_by_reason.items(), key=lambda kv: kv[1])[0] if s.no_bet_by_reason else None
        from ...explanations.templates import NO_BET_LABELS

        reason_txt = f" (principal motivo: {NO_BET_LABELS.get(top_reason, top_reason)})" if top_reason else ""
        parts.append(f"{s.no_bet} NO BET{reason_txt}.")
    if s.stale:
        parts.append(f"Atenção: {s.stale} análises com dados STALE/EXPIRED.")
    if health_overall and health_overall != "HEALTHY":
        parts.append(f"Saúde do sistema: {health_overall} — veja Diagnóstico.")
    if s.last_update:
        age = int((datetime.utcnow() - s.last_update).total_seconds())
        parts.append(f"Última atualização {describe_age(age)}.")
    return " ".join(parts)


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(session: Session = Depends(get_session)):
    analyses = _upcoming(all_cached(), 48)
    _maybe_refresh(analyses)
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    analyzed_today = session.execute(
        select(PredictionSnapshot.event_id).where(PredictionSnapshot.created_at >= today_start).distinct()
    ).all()
    discarded = 0
    top: list[EntryRow] = []
    for a in analyses:
        best = _best(a)
        if best and best.quality_gate and best.quality_gate.passed:
            top.append(EntryRow(event=a.event, recommendation=best))
        discarded += sum(1 for r in a.recommendations if r.status == "NO_BET")
    top.sort(key=lambda x: -x.recommendation.opportunity_score)
    now = datetime.utcnow()
    popular_rows = session.execute(
        select(Event).where(Event.kickoff_utc > now - timedelta(hours=2), Event.kickoff_utc < now + timedelta(hours=48), Event.duplicate_of.is_(None))
        .order_by(Event.market_count.desc()).limit(8)
    ).scalars().all()
    odds = main_odds_for(session, [r.id for r in popular_rows])
    favs = {f for f in session.execute(select(Favorite.event_id)).scalars()}
    popular = []
    for r in popular_rows:
        s = event_summary(r, None, odds.get(r.id))
        s.is_favorite = r.id in favs
        popular.append(s)
    hour = (datetime.utcnow() - timedelta(hours=3)).hour  # BRT aproximado
    greeting = "Bom dia" if 5 <= hour < 12 else "Boa tarde" if 12 <= hour < 18 else "Boa noite"
    st = jobs.state()
    last = max((d for d in (st["last_events_sync"], st["last_odds_sync"], st["last_radar_refresh"]) if d), default=None)
    summary = summarize(session, analyses, 48)
    health_overall = None
    try:
        from ...quality.health import system_health

        health_overall = system_health(session).overall
    except Exception:  # noqa: BLE001 — diagnóstico é auxiliar; nunca derruba o dashboard
        health_overall = None
    name = _user_name(session)
    model_health = None
    try:
        from ...validation.model_health import model_health_summary

        model_health = model_health_summary(session)
    except Exception as exc:  # noqa: BLE001 — bloco discreto; nunca derruba o dashboard
        model_health = {"status": "UNAVAILABLE", "error": str(exc)}
    return DashboardResponse(
        greeting=greeting, user_name=name, analyzed_today=len(analyzed_today), confidence_a=summary.confidence_a,
        confidence_b=summary.confidence_b, discarded_markets=discarded, last_update=last, top_opportunities=top[:8],
        popular_events=popular, scheduler={k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in st.items()},
        morning_summary=morning_summary(name, summary, health_overall), events_found=summary.events_found,
        quality_gate_passed=summary.quality_gate_passed, watch=summary.watch, no_bet=summary.no_bet,
        alerts_unread=summary.alerts_unread, health_overall=health_overall,
        model_health=model_health, value=summary.value, value_candidates=summary.value_candidates, research_signals=summary.research_signals, model_only=summary.model_only,
        actionable_clusters=summary.actionable_clusters,
    )


def _user_name(session: Session) -> str:
    from ...db.models import Setting

    row = session.get(Setting, "app")
    return (row.value or {}).get("user_name", "Fernando") if row else "Fernando"
