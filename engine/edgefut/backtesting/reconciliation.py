"""Settlement reconciliation — nenhum evento encerrado desaparece em silêncio.

Todo evento com kickoff há mais de `FINISHED_AFTER` horas fica em exatamente um estado:

* `SETTLED`            — resultado gravado e todos os snapshots/shadow liquidados;
* `SETTLEMENT_PENDING` — ainda sem resultado em nenhuma fonte (tentativas contadas);
* `SETTLEMENT_ERROR`   — sem resultado depois de `ERROR_AFTER_HOURS`, ou fontes em
                         desacordo não resolvido, ou snapshot liquidado com placar
                         diferente do evento.

O job compara três coisas quando existem: resultado do feed (Superbet), resultado
armazenado no evento e resultado gravado nos snapshots. Divergências viram
`source_conflict(field=score)` e são contadas — nunca corrigidas em silêncio.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Event, PredictionSnapshot, ShadowPrediction

log = logging.getLogger(__name__)

FINISHED_AFTER = timedelta(hours=3)
ERROR_AFTER_HOURS = 72
STATES = ("SETTLED", "SETTLEMENT_PENDING", "SETTLEMENT_ERROR")


def finished_events(session: Session, now: datetime | None = None, limit: int = 2000) -> list[Event]:
    now = now or datetime.utcnow()
    return list(
        session.execute(
            select(Event)
            .where(Event.kickoff_utc < now - FINISHED_AFTER, Event.duplicate_of.is_(None))
            .order_by(Event.kickoff_utc.desc())
            .limit(limit)
        ).scalars().all()
    )


def _classify(ev: Event, snaps: list[PredictionSnapshot], now: datetime) -> tuple[str, str | None]:
    has_result = ev.home_score is not None and ev.away_score is not None
    hours_since = (now - ev.kickoff_utc).total_seconds() / 3600
    if not has_result:
        if hours_since > ERROR_AFTER_HOURS:
            return "SETTLEMENT_ERROR", f"sem resultado em nenhuma fonte {hours_since:.0f} h após o kickoff"
        return "SETTLEMENT_PENDING", None
    unsettled = [s for s in snaps if s.result is None]
    if unsettled:
        return "SETTLEMENT_ERROR", f"{len(unsettled)} snapshot(s) sem liquidação apesar do resultado {ev.home_score}-{ev.away_score}"
    for s in snaps:
        r = s.result or {}
        if r.get("hg") is not None and (int(r["hg"]), int(r["ag"])) != (int(ev.home_score), int(ev.away_score)):
            return "SETTLEMENT_ERROR", f"snapshot {s.id} liquidado com {r['hg']}-{r['ag']} mas evento tem {ev.home_score}-{ev.away_score}"
    return "SETTLED", None


def reconcile(session: Session, *, now: datetime | None = None, try_settle: bool = True) -> dict:
    """Job diário (e pós-boot): classifica todos os eventos encerrados e tenta liquidar os pendentes."""
    now = now or datetime.utcnow()
    counts = {s: 0 for s in STATES}
    divergences = 0
    transitions: list[dict] = []

    if try_settle:
        try:
            from .performance import settle_pending

            res = settle_pending(session, max_events=60)
            log.info("reconciliation: settle_pending → %s", res)
        except Exception as exc:  # noqa: BLE001 — a classificação abaixo precisa acontecer mesmo assim
            log.warning("reconciliation: settle_pending falhou: %s", exc)

    events = finished_events(session, now)
    ids = [e.id for e in events]
    snaps_by_event: dict[int, list[PredictionSnapshot]] = {}
    if ids:
        for s in session.execute(select(PredictionSnapshot).where(PredictionSnapshot.event_id.in_(ids))).scalars():
            snaps_by_event.setdefault(s.event_id, []).append(s)

    for ev in events:
        state, err = _classify(ev, snaps_by_event.get(ev.id, []), now)
        if state != "SETTLED" and ev.settlement_status != state:
            transitions.append({"event_id": ev.id, "from": ev.settlement_status, "to": state, "error": err})
        if state == "SETTLEMENT_ERROR" and "snapshot" in (err or ""):
            divergences += 1
        if state != "SETTLED":
            ev.settlement_attempts = int(ev.settlement_attempts or 0) + 1
        ev.settlement_status = state
        ev.settlement_error = err
        ev.settlement_checked_at = now
        counts[state] += 1

    shadow_fixed = _settle_shadow(session, now)
    session.commit()
    return {
        "ok": True,
        "checked": len(events),
        **{k.lower(): v for k, v in counts.items()},
        "unsettled_finished": counts["SETTLEMENT_PENDING"] + counts["SETTLEMENT_ERROR"],
        "divergences": divergences,
        "shadow_settled": shadow_fixed,
        "transitions": transitions[:50],
        "records": len(events),
    }


def _settle_shadow(session: Session, now: datetime) -> int:
    """Liquida shadow predictions de eventos que já têm resultado (só campos de liquidação)."""
    from .settlement import MatchResult, settle_selection

    rows = session.execute(
        select(ShadowPrediction, Event)
        .join(Event, Event.id == ShadowPrediction.event_id)
        .where(ShadowPrediction.settled_at.is_(None), Event.home_score.is_not(None), Event.away_score.is_not(None))
        .limit(5000)
    ).all()
    n = 0
    for sp, ev in rows:
        result = MatchResult(hg=int(ev.home_score), ag=int(ev.away_score), source=ev.result_source or "")
        won = settle_selection(sp.market_key, sp.selection_key, sp.line, result)
        sp.result = {"hg": result.hg, "ag": result.ag, "source": result.source}
        sp.won = won
        sp.settled_at = now
        n += 1
    return n


def summary(session: Session) -> dict:
    now = datetime.utcnow()
    rows = session.execute(
        select(Event.settlement_status, func.count()).where(Event.kickoff_utc < now - FINISHED_AFTER, Event.duplicate_of.is_(None)).group_by(Event.settlement_status)
    ).all()
    by = {str(k): int(v) for k, v in rows}
    unclassified = by.pop("None", 0)
    last = session.execute(select(func.max(Event.settlement_checked_at))).scalar()
    return {
        "settled": by.get("SETTLED", 0),
        "pending": by.get("SETTLEMENT_PENDING", 0),
        "error": by.get("SETTLEMENT_ERROR", 0),
        "unclassified": unclassified,
        "unsettled_finished": by.get("SETTLEMENT_PENDING", 0) + by.get("SETTLEMENT_ERROR", 0) + unclassified,
        "last_reconciliation": last.isoformat() if last else None,
    }


__all__ = ["reconcile", "summary", "finished_events", "STATES"]
