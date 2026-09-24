"""Source Conflict Engine — registra e explica divergências entre fontes.

Nenhuma escolha entre fontes é silenciosa: quando dois providers discordam em um
campo crítico (competição, kickoff, mandante, visitante, venue, país, campo
neutro, placar, odds), gravamos `source_conflict` com o valor escolhido e o
método de resolução. A tela da partida mostra "N divergências de dados resolvidas".
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import SourceConflict
from ..domain.conflicts import ConflictOut

log = logging.getLogger(__name__)

CRITICAL_FIELDS = {"competition", "kickoff", "home_team", "away_team", "venue", "country", "neutral_venue", "score", "odds"}

FIELD_LABELS = {
    "competition": "Competição",
    "kickoff": "Horário",
    "home_team": "Mandante",
    "away_team": "Visitante",
    "venue": "Local",
    "country": "País",
    "neutral_venue": "Campo neutro",
    "score": "Placar",
    "odds": "Odds",
}

METHOD_LABELS = {
    "user_override": "definido manualmente pelo usuário",
    "dataset_flag": "flag explícita do dataset histórico",
    "prefer_curated_history": "dataset curado prevalece sobre feed da casa",
    "prefer_superbet": "feed da Superbet prevalece (mais recente)",
    "listing_kept_unconfirmed": "listagem mantida, mando marcado como não confirmado",
    "tolerance_window": "diferença dentro da tolerância de horário",
    "canonical_teams": "times canônicos resolvidos por alias",
}


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


def record_conflict(
    session: Session,
    *,
    event_id: int | None,
    field: str,
    source_a: str,
    value_a: Any,
    source_b: str,
    value_b: Any,
    selected_value: Any,
    selected_source: str | None,
    method: str,
    confidence: float,
    canonical_event_id: str | None = None,
    dedupe_hours: int = 48,
) -> SourceConflict:
    """Grava a divergência (idempotente por evento/campo/valores dentro de `dedupe_hours`)."""
    va, vb, sel = _jsonable(value_a), _jsonable(value_b), _jsonable(selected_value)
    since = datetime.utcnow() - timedelta(hours=dedupe_hours)
    existing = session.execute(
        select(SourceConflict).where(
            SourceConflict.event_id == event_id, SourceConflict.field == field, SourceConflict.created_at >= since
        )
    ).scalars().all()
    for c in existing:
        if c.value_a == va and c.value_b == vb and c.source_a == source_a and c.source_b == source_b:
            return c
    row = SourceConflict(
        event_id=event_id, canonical_event_id=canonical_event_id, field=field, source_a=source_a, value_a=va,
        source_b=source_b, value_b=vb, selected_value=sel, selected_source=selected_source,
        resolution_method=method, confidence=max(0.0, min(1.0, confidence)),
    )
    session.add(row)
    session.flush()
    log.info("conflito %s em evento %s: %s=%r × %s=%r → %r (%s)", field, event_id, source_a, va, source_b, vb, sel, method)
    return row


def to_out(c: SourceConflict) -> ConflictOut:
    return ConflictOut(
        id=c.id, field=c.field, field_label=FIELD_LABELS.get(c.field, c.field), source_a=c.source_a, value_a=c.value_a,
        source_b=c.source_b, value_b=c.value_b, selected_value=c.selected_value, selected_source=c.selected_source,
        resolution_method=c.resolution_method, resolution_label=METHOD_LABELS.get(c.resolution_method, c.resolution_method),
        confidence=c.confidence, created_at=c.created_at,
    )


def conflicts_for_event(session: Session, event_id: int, limit: int = 50) -> list[ConflictOut]:
    rows = session.execute(
        select(SourceConflict).where(SourceConflict.event_id == event_id).order_by(SourceConflict.created_at.desc()).limit(limit)
    ).scalars().all()
    # último registro por campo+valores
    seen: set[tuple] = set()
    out: list[ConflictOut] = []
    for c in rows:
        key = (c.field, json.dumps(c.value_a, sort_keys=True, default=str), json.dumps(c.value_b, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        out.append(to_out(c))
    return out
