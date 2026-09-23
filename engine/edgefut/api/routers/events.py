from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ...analysis.pipeline import analyze_event, cached_analysis, event_summary
from ...collectors import SuperbetSync, latest_odds_rows
from ...db.models import Competition, Event, Favorite, OddsSnapshot
from ...db.session import get_session
from ...domain.analysis import MatchAnalysis
from ...odds import build_markets
from ...scheduler import jobs
from ...simulation.monte_carlo import ALLOWED_SIMULATIONS
from ..schemas import EventDetailResponse, EventListResponse, OddsHistoryResponse, OddsPoint, VenueOverride

router = APIRouter(prefix="/events", tags=["events"])


def window_bounds(window: str) -> tuple[datetime, datetime]:
    now = datetime.utcnow()
    if window == "today":
        start = now - timedelta(hours=2)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(hours=3)
    elif window == "tomorrow":
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif window == "48h":
        start, end = now - timedelta(hours=2), now + timedelta(hours=48)
    else:
        start, end = now - timedelta(hours=2), now + timedelta(days=7)
    return start, end


def main_odds_for(session: Session, ids: list[int]) -> dict[int, dict[str, float]]:
    if not ids:
        return {}
    sub = (
        select(OddsSnapshot.event_id, OddsSnapshot.selection_key, func.max(OddsSnapshot.collected_at).label("mx"))
        .where(OddsSnapshot.event_id.in_(ids), OddsSnapshot.market_key == "1X2")
        .group_by(OddsSnapshot.event_id, OddsSnapshot.selection_key)
        .subquery()
    )
    rows = session.execute(
        select(OddsSnapshot.event_id, OddsSnapshot.selection_key, OddsSnapshot.price)
        .join(sub, (OddsSnapshot.event_id == sub.c.event_id) & (OddsSnapshot.selection_key == sub.c.selection_key) & (OddsSnapshot.collected_at == sub.c.mx))
        .where(OddsSnapshot.market_key == "1X2")
    ).all()
    out: dict[int, dict[str, float]] = {}
    for eid, sel, price in rows:
        out.setdefault(eid, {})[sel] = price
    return out


def _enrich(summary, event_id: int):
    a = cached_analysis(event_id)
    if a is not None:
        summary.opportunity_score = a.opportunity_score
        summary.confidence_grade = a.confidence.grade
        summary.data_quality = a.data_quality.score
        summary.best_market = a.event.best_market
        summary.no_bet_reason = a.no_bet.reason
        summary.venue_status = a.venue.status
        summary.neutral_venue = a.venue.neutral
    return summary


@router.get("", response_model=EventListResponse)
def list_events(
    window: str = Query("48h", pattern="^(today|tomorrow|48h|week)$"),
    competition: str | None = None,
    search: str | None = None,
    supported_only: bool = False,
    limit: int = Query(300, le=1000),
    session: Session = Depends(get_session),
):
    start, end = window_bounds(window)
    q = select(Event).where(Event.kickoff_utc >= start, Event.kickoff_utc < end, Event.duplicate_of.is_(None))
    if competition:
        q = q.where(Event.competition_name.ilike(f"%{competition}%"))
    if search:
        like = f"%{search}%"
        q = q.where(or_(Event.home_name.ilike(like), Event.away_name.ilike(like), Event.competition_name.ilike(like)))
    if supported_only:
        q = q.join(Competition, Competition.id == Event.competition_id).where(
            or_(Competition.dataset_code.is_not(None), Competition.is_national_teams.is_(True))
        )
    rows = session.execute(q.order_by(Event.kickoff_utc.asc()).limit(limit)).scalars().all()
    odds = main_odds_for(session, [r.id for r in rows])
    favs = {f for f in session.execute(select(Favorite.event_id)).scalars()}
    events = []
    for r in rows:
        s = event_summary(r, None, odds.get(r.id))
        s.is_favorite = r.id in favs
        events.append(_enrich(s, r.id))
    return EventListResponse(events=events, total=len(events), window=window, generated_at=datetime.utcnow())


@router.get("/{event_id}", response_model=EventDetailResponse)
def get_event(event_id: int, session: Session = Depends(get_session)):
    row = session.get(Event, event_id)
    if row is None:
        res = SuperbetSync().sync_odds(session, event_id, force=True)
        row = session.get(Event, event_id)
        if row is None:
            raise HTTPException(404, f"evento {event_id} não encontrado ({res.get('error')})")
    rows, opening, collected_at, url = latest_odds_rows(session, event_id)
    markets = build_markets(rows, opening, collected_at, url)
    summary = _enrich(event_summary(row, session, next(({s.key: s.price for s in m.selections} for m in markets if m.market_key == "1X2"), None)), event_id)
    return EventDetailResponse(event=summary, markets=markets)


@router.get("/{event_id}/analysis", response_model=MatchAnalysis)
def get_analysis(
    event_id: int,
    simulations: int | None = Query(None),
    refresh: bool = False,
    session: Session = Depends(get_session),
):
    if simulations is not None and simulations not in ALLOWED_SIMULATIONS:
        raise HTTPException(400, f"simulations deve ser um de {ALLOWED_SIMULATIONS}")
    try:
        return analyze_event(session, event_id, simulations=simulations, force_odds=refresh, use_cache=not refresh)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{event_id}/conflicts")
def event_conflicts(event_id: int, session: Session = Depends(get_session)):
    from ...quality import conflicts_for_event

    row = session.get(Event, event_id)
    if row is None:
        raise HTTPException(404, f"evento {event_id} não encontrado")
    items = conflicts_for_event(session, event_id)
    return {
        "event_id": event_id,
        "canonical_event_id": row.canonical_event_id,
        "home_canonical": row.home_canonical,
        "away_canonical": row.away_canonical,
        "duplicate_of": row.duplicate_of,
        "count": len(items),
        "conflicts": items,
    }


@router.get("/{event_id}/odds/history", response_model=OddsHistoryResponse)
def odds_history(event_id: int, market_key: str | None = None, session: Session = Depends(get_session)):
    q = select(OddsSnapshot).where(OddsSnapshot.event_id == event_id)
    if market_key:
        q = q.where(OddsSnapshot.market_key == market_key)
    rows = session.execute(q.order_by(OddsSnapshot.collected_at.asc())).scalars().all()
    series: dict[str, list[OddsPoint]] = {}
    for r in rows:
        series.setdefault(f"{r.market_key}|{r.selection_key}|{r.line}", []).append(OddsPoint(collected_at=r.collected_at, price=r.price))
    return OddsHistoryResponse(event_id=event_id, series=series)


@router.patch("/{event_id}/venue", response_model=MatchAnalysis)
def set_venue(event_id: int, body: VenueOverride, session: Session = Depends(get_session)):
    row = session.get(Event, event_id)
    if row is None:
        raise HTTPException(404, "evento não encontrado")
    row.venue_status = "NEUTRAL" if body.neutral else "CONFIRMED_HOME"
    row.neutral_venue = body.neutral
    row.venue_name, row.venue_city, row.venue_country = body.name, body.city, body.country
    row.venue_source = "user"
    row.venue_confidence = 1.0
    session.flush()
    return analyze_event(session, event_id, use_cache=False)


@router.post("/{event_id}/favorite")
def add_favorite(event_id: int, session: Session = Depends(get_session)):
    if session.get(Event, event_id) is None:
        raise HTTPException(404, "evento não encontrado")
    if session.get(Favorite, event_id) is None:
        session.add(Favorite(event_id=event_id))
    return {"ok": True, "favorite": True}


@router.delete("/{event_id}/favorite")
def remove_favorite(event_id: int, session: Session = Depends(get_session)):
    fav = session.get(Favorite, event_id)
    if fav is not None:
        session.delete(fav)
    return {"ok": True, "favorite": False}


@router.post("/sync")
def sync_events(session: Session = Depends(get_session)):
    res = SuperbetSync().sync_events(session, force=True)
    jobs.run_in_background(jobs.job_refresh_radar)
    return res


@router.post("/{event_id}/sync-odds")
def sync_odds(event_id: int, session: Session = Depends(get_session)):
    return SuperbetSync().sync_odds(session, event_id, force=True)
