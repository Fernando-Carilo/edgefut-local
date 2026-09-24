"""Alertas locais (sem push, sem rede): mudanças relevantes entre dois ciclos do radar.

Tipos: ODD_MOVEMENT, DATA_QUALITY_CHANGE, MODEL_CONFIDENCE_CHANGE, OPPORTUNITY_APPEARED,
OPPORTUNITY_LOST. O estado do ciclo anterior fica em `setting[alerts_state]`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Alert, Setting
from ..domain.analysis import MatchAnalysis

log = logging.getLogger(__name__)

STATE_KEY = "alerts_state"
ODD_MOVE_PCT = 5.0
DQ_DELTA = 15.0
KINDS = {
    "ODD_MOVEMENT": "Movimento de odd",
    "DATA_QUALITY_CHANGE": "Mudança na qualidade dos dados",
    "MODEL_CONFIDENCE_CHANGE": "Mudança na confiança do modelo",
    "OPPORTUNITY_APPEARED": "Oportunidade encontrada",
    "OPPORTUNITY_LOST": "Oportunidade perdida",
    # iteração 5 (§45) — nunca "BET NOW": sinais para observar, saúde do coletor e settlement
    "RESEARCH_SIGNAL": "Novo research signal",
    "PRICE_TARGET_REACHED": "Preço atingiu alvo de observação",
    "LINE_MOVED": "Movimento relevante detectado",
    "SETTLEMENT_COMPLETED": "Settlement concluído",
    "COLLECTOR_DEGRADED": "Coletor degradado",
    "SCHEMA_CHANGED": "Superbet schema changed",
}


def _fingerprint(a: MatchAnalysis) -> dict:
    return {
        "grade": a.confidence.grade,
        "dq": a.data_quality.score,
        "best": a.event.best_market,
        "no_bet": a.no_bet.reason,
        "odds": a.event.main_odds or {},
        "label": f"{a.event.home_name} × {a.event.away_name}",
        "kickoff": a.event.kickoff_utc.isoformat(),
        # iteração 3 — watchlist de preço: primárias acionáveis e seleções "quase" (WATCHING PRICE)
        "value": {_rk(r): r.odd for r in a.recommendations if r.is_primary and r.state in ("VALUE", "VALUE_CANDIDATE", "RESEARCH_SIGNAL")},
        "watching": {_rk(r): (r.price or {}).get("min_acceptable_odd") for r in a.recommendations if "WATCHING_PRICE" in r.reasons},
        "research": {_rk(r): r.odd for r in a.recommendations if r.is_primary and r.state == "RESEARCH_SIGNAL"},
    }


def _rk(r) -> str:
    return f"{r.market_label}: {r.selection_name}" + (f" {r.line}" if r.line is not None else "")


def scan_alerts(session: Session, analyses: list[MatchAnalysis] | None = None) -> dict:
    if analyses is None:
        from ..analysis.pipeline import all_cached

        analyses = all_cached()
    row = session.get(Setting, STATE_KEY)
    prev: dict[str, dict] = dict(row.value) if row and row.value else {}
    now_state: dict[str, dict] = {}
    created = 0
    for a in analyses:
        key = str(a.event.id)
        cur = _fingerprint(a)
        now_state[key] = cur
        old = prev.get(key)
        if old is None:
            if cur["best"]:
                created += _emit(session, "OPPORTUNITY_APPEARED", a.event.id, f"{cur['label']}: {cur['best']}", {"grade": cur["grade"]})
            continue
        for sel, price in cur["odds"].items():
            before = (old.get("odds") or {}).get(sel)
            if before and price and abs(price / before - 1) * 100 >= ODD_MOVE_PCT:
                created += _emit(
                    session, "ODD_MOVEMENT", a.event.id, f"{cur['label']}: {sel} {before:.2f} → {price:.2f}",
                    {"selection": sel, "from": before, "to": price, "pct": round((price / before - 1) * 100, 1)}, severity="warning",
                )
        if old.get("dq") is not None and abs(cur["dq"] - old["dq"]) >= DQ_DELTA:
            created += _emit(session, "DATA_QUALITY_CHANGE", a.event.id, f"{cur['label']}: qualidade {old['dq']:.0f} → {cur['dq']:.0f}", {"from": old["dq"], "to": cur["dq"]})
        if old.get("grade") and cur["grade"] != old["grade"]:
            created += _emit(session, "MODEL_CONFIDENCE_CHANGE", a.event.id, f"{cur['label']}: confiança {old['grade']} → {cur['grade']}", {"from": old["grade"], "to": cur["grade"]})
        if cur["best"] and not old.get("best"):
            created += _emit(session, "OPPORTUNITY_APPEARED", a.event.id, f"{cur['label']}: {cur['best']}", {"grade": cur["grade"]})
        if old.get("best") and not cur["best"]:
            created += _emit(session, "OPPORTUNITY_LOST", a.event.id, f"{cur['label']}: {old['best']} ({cur['no_bet'] or 'sem edge'})", {"reason": cur["no_bet"]}, severity="warning")
        # iteração 5: novo RESEARCH_SIGNAL numa primária (observar, não apostar)
        for key, odd in (cur.get("research") or {}).items():
            if key not in (old.get("research") or {}):
                created += _emit(session, "RESEARCH_SIGNAL", a.event.id, f"{cur['label']}: {key} @ {odd:.2f} — research signal (VALUE desativado; observar preço e movimento)", {"odd": odd, "state": "RESEARCH_SIGNAL"})
        # watchlist de preço: seleção observada atingiu a odd mínima aceitável / oportunidade perdeu o preço
        old_watch, old_value = old.get("watching") or {}, old.get("value") or {}
        for key, odd in (cur.get("value") or {}).items():
            if key in old_watch and key not in old_value:
                created += _emit(session, "PRICE_TARGET_REACHED", a.event.id, f"{cur['label']}: {key} atingiu o preço-alvo (odd {odd:.2f} ≥ mín. {old_watch[key]:.2f})" if old_watch.get(key) else f"{cur['label']}: {key} atingiu o preço-alvo (odd {odd:.2f})", {"trigger": "price_target", "odd": odd, "min_acceptable_odd": old_watch.get(key)})
        for key, odd in old_value.items():
            if key in (cur.get("watching") or {}) and key not in (cur.get("value") or {}):
                created += _emit(session, "OPPORTUNITY_LOST", a.event.id, f"{cur['label']}: {key} perdeu o preço (odd abaixo do mínimo aceitável)", {"trigger": "price_target", "previous_odd": odd, "min_acceptable_odd": (cur.get("watching") or {}).get(key)}, severity="warning")
    # mantém estado de eventos ainda futuros que não estavam neste ciclo
    horizon = (datetime.utcnow() - timedelta(hours=3)).isoformat()
    for k, v in prev.items():
        if k not in now_state and v.get("kickoff", "") > horizon:
            now_state[k] = v
    if row is None:
        session.add(Setting(key=STATE_KEY, value=now_state))
    else:
        row.value = now_state
        row.updated_at = datetime.utcnow()
    session.commit()
    return {"ok": True, "analyses": len(analyses), "alerts_created": created}


def _emit(session: Session, kind: str, event_id: int, title: str, detail: dict, severity: str = "info") -> int:
    recent = datetime.utcnow() - timedelta(hours=6)
    dup = session.execute(
        select(Alert.id).where(Alert.kind == kind, Alert.event_id == event_id, Alert.title == title, Alert.created_at >= recent).limit(1)
    ).scalar_one_or_none()
    if dup is not None:
        return 0
    session.add(Alert(kind=kind, event_id=event_id, title=title, detail=detail, severity=severity))
    return 1


def unread_count(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(Alert).where(Alert.read_at.is_(None))).scalar_one() or 0)


def list_alerts(session: Session, limit: int = 100, unread_only: bool = False) -> list[dict]:
    q = select(Alert).order_by(Alert.created_at.desc())
    if unread_only:
        q = q.where(Alert.read_at.is_(None))
    q = q.limit(limit)
    return [
        {
            "id": a.id, "kind": a.kind, "kind_label": KINDS.get(a.kind, a.kind), "event_id": a.event_id, "title": a.title,
            "detail": a.detail, "severity": a.severity, "created_at": a.created_at, "read_at": a.read_at,
        }
        for a in session.execute(q).scalars()
    ]


def mark_read(session: Session, ids: list[int] | None = None) -> int:
    q = select(Alert).where(Alert.read_at.is_(None))
    if ids:
        q = q.where(Alert.id.in_(ids))
    n = 0
    for a in session.execute(q).scalars():
        a.read_at = datetime.utcnow()
        n += 1
    session.commit()
    return n
