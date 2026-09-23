from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...analysis.pipeline import all_cached, event_summary
from ...core.config import settings
from ...db.models import Event, Favorite, PredictionSnapshot
from ...db.session import get_session
from ...domain.analysis import MatchAnalysis, Recommendation
from ...scheduler import jobs
from ..schemas import DashboardResponse, EntriesResponse, EntryRow, RadarCard, RadarItem, RadarResponse
from .events import main_odds_for, window_bounds

router = APIRouter(tags=["radar"])


def _upcoming(analyses: list[MatchAnalysis], hours: int = 48) -> list[MatchAnalysis]:
    now = datetime.utcnow()
    return [a for a in analyses if now - timedelta(hours=2) < a.event.kickoff_utc < now + timedelta(hours=hours)]


def _best(a: MatchAnalysis, category: str | None = None, statuses=("RECOMMENDED",)) -> Recommendation | None:
    for r in a.recommendations:
        if r.status in statuses and (category is None or r.category == category):
            return r
    return None


def _item(a: MatchAnalysis, rec: Recommendation | None) -> RadarItem:
    return RadarItem(
        event=a.event, recommendation=rec, opportunity_score=rec.opportunity_score if rec else a.opportunity_score,
        confidence_grade=rec.confidence_grade if rec else a.confidence.grade, data_quality=a.data_quality.score,
        no_bet_reason=a.no_bet.reason,
    )


def _maybe_refresh(analyses: list[MatchAnalysis]) -> bool:
    st = jobs.state()
    stale = st["last_radar_refresh"] is None or datetime.utcnow() - st["last_radar_refresh"] > timedelta(minutes=20)
    if (stale or not analyses) and not st["radar_running"]:
        jobs.run_in_background(jobs.job_refresh_radar)
        return True
    return bool(st["radar_running"])


@router.get("/radar", response_model=RadarResponse)
def radar(hours: int = Query(48, le=168)):
    analyses = _upcoming(all_cached(), hours)
    refreshing = _maybe_refresh(analyses)
    cards: list[RadarCard] = []

    def card(key, title, subtitle, items):
        items = sorted(items, key=lambda i: -i.opportunity_score)[:12]
        cards.append(RadarCard(key=key, title=title, subtitle=subtitle, items=items))

    top = [_item(a, _best(a)) for a in analyses if _best(a) and _best(a).confidence_grade in ("A", "B")]  # type: ignore[union-attr]
    card("top", "TOP OPORTUNIDADES", "Maior Opportunity Score com confiança A/B", top)
    card("high_confidence", "ALTA CONFIANÇA", "Confiança A", [_item(a, _best(a)) for a in analyses if _best(a) and _best(a).confidence_grade == "A"])  # type: ignore[union-attr]
    for key, title, cat in (("gols", "GOLS", "GOLS"), ("escanteios", "ESCANTEIOS", "ESCANTEIOS"), ("cartoes", "CARTÕES", "CARTOES"), ("finalizacoes", "FINALIZAÇÕES", "FINALIZACOES")):
        items = []
        for a in analyses:
            r = _best(a, cat)
            if r and r.confidence_grade in ("A", "B"):
                items.append(_item(a, r))
        card(key, title, f"Melhor seleção de {title.lower()} por jogo", items)
    valor = []
    for a in analyses:
        r = _best(a)
        if r and r.ev_pct >= settings.min_ev_pct * 2 and r.confidence_grade in ("A", "B"):
            valor.append(_item(a, r))
    card("valor", "VALOR", f"EV ≥ {settings.min_ev_pct * 2:.0f}%", valor)
    obs = []
    for a in analyses:
        r = _best(a, statuses=("WATCH",)) or (_best(a) if _best(a) and _best(a).confidence_grade == "C" else None)  # type: ignore[union-attr]
        if r:
            obs.append(_item(a, r))
    card("observacao", "EM OBSERVAÇÃO", "Confiança C ou limiares parcialmente atendidos", obs)
    no_bet = [_item(a, None) for a in analyses if a.no_bet.no_bet and not _best(a, statuses=("WATCH",))]
    card("no_bet", "NO BET", "Jogos em que o sistema recomenda não entrar", sorted(no_bet, key=lambda i: i.event.kickoff_utc.timestamp()))
    return RadarResponse(generated_at=datetime.utcnow(), analyzed_events=len(analyses), refreshing=refreshing, cards=cards)


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


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(session: Session = Depends(get_session)):
    analyses = _upcoming(all_cached(), 48)
    _maybe_refresh(analyses)
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    analyzed_today = session.execute(
        select(PredictionSnapshot.event_id).where(PredictionSnapshot.created_at >= today_start).distinct()
    ).all()
    a_count = b_count = discarded = 0
    top: list[EntryRow] = []
    for a in analyses:
        best = _best(a)
        if best:
            if best.confidence_grade == "A":
                a_count += 1
            elif best.confidence_grade == "B":
                b_count += 1
            if best.confidence_grade in ("A", "B"):
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
    return DashboardResponse(
        greeting=greeting, user_name=_user_name(session), analyzed_today=len(analyzed_today), confidence_a=a_count,
        confidence_b=b_count, discarded_markets=discarded, last_update=last, top_opportunities=top[:8],
        popular_events=popular, scheduler={k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in st.items()},
    )


def _user_name(session: Session) -> str:
    from ...db.models import Setting

    row = session.get(Setting, "app")
    return (row.value or {}).get("user_name", "Fernando") if row else "Fernando"
