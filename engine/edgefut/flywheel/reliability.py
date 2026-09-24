"""Collector Reliability (§7) + detecção de mudança de schema (§54) + cobertura de mapeamento (§55).

Tudo é lido de `source_log`, `raw_superbet_snapshot`, `market_mapping_registry`, `collector_gap` e
`data_quarantine`. Saúde: HEALTHY / DEGRADED / BROKEN, com os motivos listados — nunca um semáforo
sem explicação.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db.models import (
    Alert,
    CollectorGap,
    DataQuarantine,
    Event,
    MarketMappingRegistry,
    RawSuperbetSnapshot,
    SourceLog,
)

PROVIDER = "superbet"


def _source_stats(session: Session, since: datetime) -> dict:
    rows = session.execute(select(SourceLog.status, SourceLog.http_status, SourceLog.error, SourceLog.latency_ms).where(SourceLog.provider == PROVIDER, SourceLog.collected_at >= since)).all()
    out = {"requests": 0, "success": 0, "cached": 0, "errors": 0, "blocked": 0, "rate_limited": 0, "timeouts": 0, "http_5xx": 0, "latency_ms_p50": None}
    lat = []
    for status, http, err, ms in rows:
        out["requests"] += 1
        if status == "ok":
            out["success"] += 1
        elif status == "cached":
            out["cached"] += 1
        elif status == "blocked":
            out["blocked"] += 1
        else:
            out["errors"] += 1
        if http == 429:
            out["rate_limited"] += 1
        if http is not None and http >= 500:
            out["http_5xx"] += 1
        if err and "timeout" in str(err).lower():
            out["timeouts"] += 1
        if ms is not None:
            lat.append(ms)
    if lat:
        lat.sort()
        out["latency_ms_p50"] = int(lat[len(lat) // 2])
    live = out["requests"] - out["cached"]
    out["success_rate"] = round(out["success"] / live, 4) if live else None
    return out


def _raw_stats(session: Session, since: datetime) -> dict:
    q = select(
        func.count(), func.sum(RawSuperbetSnapshot.odds_total), func.sum(RawSuperbetSnapshot.odds_mapped), func.sum(RawSuperbetSnapshot.odds_unknown),
        func.sum(RawSuperbetSnapshot.odds_out_of_scope), func.sum(RawSuperbetSnapshot.parse_failures), func.max(RawSuperbetSnapshot.fetched_at),
        func.sum(RawSuperbetSnapshot.payload_bytes), func.count(func.distinct(RawSuperbetSnapshot.event_id)),
    ).where(RawSuperbetSnapshot.fetched_at >= since)
    n, total, mapped, unknown, oos, fails, last, pbytes, events = session.execute(q).one()
    schema = session.execute(select(func.count()).select_from(RawSuperbetSnapshot).where(RawSuperbetSnapshot.fetched_at >= since, RawSuperbetSnapshot.schema_issues.is_not(None))).scalar_one()
    total = int(total or 0)
    return {
        "snapshots": int(n or 0), "events": int(events or 0), "last_fetched_at": last, "odds_total": total, "odds_mapped": int(mapped or 0), "odds_unknown": int(unknown or 0),
        "odds_out_of_scope": int(oos or 0), "parse_failures": int(fails or 0), "parse_rate": round(1 - int(fails or 0) / total, 4) if total else None,
        "mapping_coverage": round((int(mapped or 0) + int(oos or 0)) / total, 4) if total else None, "unknown_share": round(int(unknown or 0) / total, 4) if total else None,
        "schema_issue_snapshots": int(schema or 0), "payload_bytes": int(pbytes or 0),
    }


def mapping_coverage(session: Session) -> dict:
    rows = session.execute(select(MarketMappingRegistry)).scalars().all()
    by_status: dict[str, int] = {}
    occ: dict[str, int] = {}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        occ[r.status] = occ.get(r.status, 0) + int(r.occurrences or 0)
    total_occ = sum(occ.values())
    unknown = sorted([r for r in rows if r.status in ("UNKNOWN", "AMBIGUOUS")], key=lambda r: -(r.occurrences or 0))
    return {
        "market_ids": len(rows), "by_status": by_status, "occurrences_by_status": occ,
        "mapped_pct": round(100 * (occ.get("MAPPED", 0) + occ.get("OUT_OF_SCOPE", 0)) / total_occ, 2) if total_occ else None,
        "unknown_pct": round(100 * (occ.get("UNKNOWN", 0) + occ.get("AMBIGUOUS", 0)) / total_occ, 2) if total_occ else None,
        "unknown": [
            {"superbet_market_id": r.superbet_market_id, "market_name": r.market_name, "status": r.status, "occurrences": r.occurrences, "events_seen": r.events_seen,
             "first_seen_at": r.first_seen_at, "last_seen_at": r.last_seen_at, "sample_selections": r.sample_selections, "specifier_keys": r.specifier_keys}
            for r in unknown[:200]
        ],
        "out_of_scope_reasons": _reason_counts(rows),
    }


def _reason_counts(rows) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        if r.status == "OUT_OF_SCOPE":
            out[r.reason or "?"] = out.get(r.reason or "?", 0) + int(r.occurrences or 0)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def collector_health(session: Session, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    h1 = _source_stats(session, now - timedelta(hours=1))
    h24 = _source_stats(session, now - timedelta(hours=24))
    r1 = _raw_stats(session, now - timedelta(hours=1))
    r24 = _raw_stats(session, now - timedelta(hours=24))
    last_any = session.execute(select(func.max(RawSuperbetSnapshot.fetched_at))).scalar_one()
    upcoming = session.execute(select(func.count()).select_from(Event).where(Event.kickoff_utc > now - timedelta(hours=2), Event.kickoff_utc < now + timedelta(hours=48), Event.duplicate_of.is_(None))).scalar_one()
    gaps = session.execute(select(CollectorGap).where(CollectorGap.ended_at >= now - timedelta(days=7)).order_by(CollectorGap.started_at.desc()).limit(20)).scalars().all()
    quarantine_open = session.execute(select(DataQuarantine.reason, func.count()).where(DataQuarantine.resolver_status == "OPEN").group_by(DataQuarantine.reason)).all()
    cadence = float(settings.odds_refresh_min or 5)
    reasons: list[str] = []
    level = "HEALTHY"

    def degrade(msg: str, broken: bool = False) -> None:
        nonlocal level
        reasons.append(msg)
        level = "BROKEN" if broken else ("DEGRADED" if level != "BROKEN" else level)

    if h1["blocked"] > 0:
        degrade(f"fonte bloqueou {h1['blocked']} pedido(s) na última hora", broken=True)
    if h1["requests"] - h1["cached"] >= 5 and (h1["success_rate"] or 0) < 0.5:
        degrade(f"taxa de sucesso {100 * (h1['success_rate'] or 0):.0f}% na última hora", broken=True)
    elif h1["requests"] - h1["cached"] >= 5 and (h1["success_rate"] or 0) < 0.9:
        degrade(f"taxa de sucesso {100 * (h1['success_rate'] or 0):.0f}% na última hora")
    if h1["rate_limited"]:
        degrade(f"{h1['rate_limited']} resposta(s) 429 (rate limit) na última hora")
    if r1["parse_rate"] is not None and r1["parse_rate"] < 0.95:
        degrade(f"taxa de parse {100 * r1['parse_rate']:.1f}% na última hora", broken=r1["parse_rate"] < 0.5)
    if r1["schema_issue_snapshots"] and r1["snapshots"] and r1["schema_issue_snapshots"] / r1["snapshots"] > 0.5:
        degrade(f"Superbet schema changed: {r1['schema_issue_snapshots']}/{r1['snapshots']} snapshots com campos obrigatórios em falta", broken=True)
    elif r1["schema_issue_snapshots"]:
        degrade(f"{r1['schema_issue_snapshots']} snapshot(s) com problemas de schema na última hora")
    if r24["unknown_share"] is not None and r24["unknown_share"] > 0.10:
        degrade(f"{100 * r24['unknown_share']:.1f}% das odds em marketIds desconhecidos (24 h)")
    stale_min = (now - last_any).total_seconds() / 60.0 if last_any else None
    if upcoming and (stale_min is None or stale_min > max(20.0, 3 * cadence)):
        degrade(f"sem coleta há {stale_min:.0f} min com {upcoming} evento(s) nas próximas 48 h" if stale_min is not None else f"nenhuma coleta raw ainda ({upcoming} eventos por coletar)")
    if not settings.superbet_enabled:
        degrade("provider Superbet desativado nas configurações")
    return {
        "generated_at": now, "health": level, "reasons": reasons, "cadence_minutes": cadence, "last_fetched_at": last_any, "stale_minutes": round(stale_min, 1) if stale_min is not None else None,
        "upcoming_events": int(upcoming or 0), "last_hour": {"requests": h1, "raw": r1}, "last_24h": {"requests": h24, "raw": r24},
        "gaps_7d": [{"started_at": g.started_at, "ended_at": g.ended_at, "minutes": g.minutes, "reason": g.reason, "events_affected": g.events_affected} for g in gaps],
        "quarantine_open": {k: int(v) for k, v in quarantine_open},
    }


ALERT_KINDS = {
    "COLLECTOR_DEGRADED": "Coletor Superbet degradado",
    "SCHEMA_CHANGED": "Superbet schema changed",
    "SETTLEMENT_COMPLETED": "Liquidação concluída",
    "LINE_MOVED": "Linha moveu",
    "PRICE_TARGET_REACHED": "Preço-alvo de observação atingido",
    "RESEARCH_SIGNAL": "Research signal",
}


def emit_health_alerts(session: Session, health: dict) -> int:
    """Alertas locais (nunca 'BET NOW'): coletor degradado/broken e mudança de schema. Dedup 6 h."""
    created = 0
    recent = datetime.utcnow() - timedelta(hours=6)

    def emit(kind: str, title: str, detail: dict, severity: str) -> None:
        nonlocal created
        dup = session.execute(select(Alert.id).where(Alert.kind == kind, Alert.title == title, Alert.created_at >= recent).limit(1)).scalar_one_or_none()
        if dup is None:
            session.add(Alert(kind=kind, event_id=None, title=title, detail=detail, severity=severity))
            created += 1

    if health["health"] in ("DEGRADED", "BROKEN"):
        emit("COLLECTOR_DEGRADED", f"Coletor Superbet {health['health']}: {health['reasons'][0] if health['reasons'] else ''}"[:200], {"reasons": health["reasons"], "health": health["health"]}, "critical" if health["health"] == "BROKEN" else "warning")
    if any(r.startswith("Superbet schema changed") for r in health["reasons"]):
        emit("SCHEMA_CHANGED", "Superbet schema changed — verificar mapeamento e campos obrigatórios", {"reasons": health["reasons"]}, "critical")
    return created


__all__ = ["collector_health", "mapping_coverage", "emit_health_alerts", "ALERT_KINDS"]
