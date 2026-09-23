"""Closing line — última odd observada imediatamente antes do kickoff.

Usada exclusivamente para CLV (Closing Line Value) na tela de Performance.
NUNCA alimenta uma recomendação: a recomendação usa a odd do momento da análise
(`recommendation odd`), gravada no snapshot antes do jogo.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..collectors import SuperbetSync
from ..db.models import ClosingLine, Event, OddsSnapshot, PredictionSnapshot
from ..providers import SourceError

log = logging.getLogger(__name__)

# Janela em que tentamos uma última coleta antes do kickoff.
PRE_KICKOFF_WINDOW = timedelta(minutes=12)
# Depois disto o closing é fixado com a última odd que já tínhamos.
POST_KICKOFF_GRACE = timedelta(hours=3)


def capture_closing_lines(session: Session, max_events: int = 30) -> dict:
    now = datetime.utcnow()
    upcoming = session.execute(
        select(Event)
        .where(Event.kickoff_utc <= now + PRE_KICKOFF_WINDOW, Event.kickoff_utc >= now - POST_KICKOFF_GRACE, Event.duplicate_of.is_(None))
        .order_by(Event.kickoff_utc.asc())
        .limit(max_events)
    ).scalars().all()
    refreshed = 0
    fixed = 0
    sync = SuperbetSync()
    for ev in upcoming:
        already = session.execute(select(ClosingLine.id).where(ClosingLine.event_id == ev.id).limit(1)).scalar_one_or_none()
        if already is not None:
            continue
        # Uma última coleta enquanto ainda é pré-jogo (não insiste se a fonte falhar).
        if ev.kickoff_utc > now:
            try:
                res = sync.sync_odds(session, ev.id, force=True)
                if res.get("ok") and not res.get("skipped"):
                    refreshed += 1
            except SourceError as exc:
                log.info("closing: Superbet indisponível para %s: %s", ev.id, exc)
            session.commit()
            continue  # o kickoff ainda não passou; fixamos depois
        n = _fix_closing(session, ev)
        if n:
            fixed += 1
        session.commit()
    return {"ok": True, "checked": len(upcoming), "refreshed": refreshed, "fixed": fixed}


def _fix_closing(session: Session, ev: Event) -> int:
    """Grava a última odd pré-kickoff de cada seleção e anexa `closing_odds` aos snapshots."""
    rows = session.execute(
        select(OddsSnapshot)
        .where(OddsSnapshot.event_id == ev.id, OddsSnapshot.collected_at <= ev.kickoff_utc + timedelta(minutes=2))
        .order_by(OddsSnapshot.collected_at.desc())
    ).scalars().all()
    latest: dict[tuple, OddsSnapshot] = {}
    for r in rows:
        key = (r.market_key, r.selection_key, r.line)
        if key not in latest:
            latest[key] = r
    if not latest:
        return 0
    closing_map: dict[str, dict] = {}
    for (mk, sk, line), r in latest.items():
        minutes_before = (ev.kickoff_utc - r.collected_at).total_seconds() / 60
        session.add(
            ClosingLine(
                event_id=ev.id, market_key=mk, selection_key=sk, line=line, price=r.price, collected_at=r.collected_at,
                kickoff_utc=ev.kickoff_utc, minutes_before_kickoff=round(minutes_before, 1),
            )
        )
        closing_map[f"{mk}|{sk}|{line}"] = {"price": r.price, "collected_at": r.collected_at.isoformat(), "minutes_before_kickoff": round(minutes_before, 1)}
    # closing_odds é o ÚNICO campo do snapshot que pode ser escrito após o kickoff (não é previsão).
    snaps = session.execute(select(PredictionSnapshot).where(PredictionSnapshot.event_id == ev.id, PredictionSnapshot.closing_odds.is_(None))).scalars().all()
    for s in snaps:
        s.closing_odds = closing_map
    return len(latest)


def closing_for_event(session: Session, event_id: int) -> dict[str, float]:
    rows = session.execute(select(ClosingLine).where(ClosingLine.event_id == event_id)).scalars().all()
    return {f"{r.market_key}|{r.selection_key}|{r.line}": r.price for r in rows}
