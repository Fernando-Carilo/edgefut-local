"""Iteração 5 — Data Flywheel: coletor Superbet V2, cobertura, mapeamento, quarentena, settlement por
mercado, pesquisa (margem/CLV/movimento/lead-lag/discovery/experimentos), estados de edge por mercado,
armazenamento, backup/restore, export e relatórios.

Tudo é leitura/medição, exceto ações explícitas e registadas do operador: resolver quarentena, marcar
mercado desconhecido como fora de escopo, ativar/desativar VALUE (só com VALUE_ENABLEMENT_CANDIDATE),
backup/restore/export. Nenhum endpoint aposta, recomenda stake ou "força" VALUE.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.config import settings
from ...db.models import (
    CollectorGap,
    DataQuarantine,
    Event,
    ManualCorrection,
    MarketMappingRegistry,
    RawSuperbetSnapshot,
    SuperbetNormalized,
    SuperbetSettlement,
)
from ...db.session import get_session
from ...flywheel import (
    DATASET_VERSION,
    NORMALIZER_VERSION,
    SETTLEMENT_VERSION,
    SOURCE_VERSION,
    governance,
    reports,
    research,
    storage,
)
from ...flywheel.collector import SNAPSHOT_TARGETS, raw_payload
from ...flywheel.coverage import coverage_report
from ...flywheel.markets import CATEGORY_LABELS, CATEGORY_ORDER, MAPPED, OUT_OF_SCOPE_PATTERNS
from ...flywheel.reliability import collector_health, mapping_coverage
from ...flywheel.settlement import settlement_audit
from ...scheduler import jobs

log = logging.getLogger(__name__)
router = APIRouter(prefix="/flywheel", tags=["flywheel"])

STRIP = ["FOOTBALL MODEL PREDICTIVE VS NAIVE", "MARKET EDGE UNPROVEN", "SUPERBET EVIDENCE COLLECTING"]


def _strip(edge_states: list[dict], health: dict) -> list[dict]:
    """§3 — faixa do topo. O terceiro item muda com a saúde do coletor; o segundo só muda com validação real."""
    validated = [e for e in edge_states if e.get("value_enabled")]
    promising = [e for e in edge_states if e.get("state") == "PROMISING"]
    market = "MARKET EDGE VALIDATED: " + ", ".join(e["label"] for e in validated) if validated else ("MARKET EDGE PROMISING (não validado)" if promising else "MARKET EDGE UNPROVEN")
    coll = {"HEALTHY": "SUPERBET EVIDENCE COLLECTING", "DEGRADED": "SUPERBET EVIDENCE COLLECTING (DEGRADED)", "BROKEN": "SUPERBET COLLECTOR BROKEN"}.get(health.get("health"), "SUPERBET EVIDENCE COLLECTING")
    return [{"key": "model", "text": STRIP[0], "tone": "ok"}, {"key": "market", "text": market, "tone": "ok" if validated else "warn"}, {"key": "collector", "text": coll, "tone": {"HEALTHY": "ok", "DEGRADED": "warn", "BROKEN": "bad"}.get(health.get("health"), "warn")}]


# ---------------------------------------------------------------------------
# resumo (página Data Flywheel)
# ---------------------------------------------------------------------------
@router.get("/summary")
def summary(session: Session = Depends(get_session)):
    now = datetime.utcnow()
    health = collector_health(session, now)
    cov = coverage_report(session, now=now, days=7, per_event_limit=0)
    mapping = mapping_coverage(session)
    audit = settlement_audit(session)
    edge = governance.edge_states(session)
    latest_research = reports.latest(session, "flywheel_research") or {}
    stor = reports.storage_dashboard_light(session, now)
    gaps = session.execute(select(CollectorGap).order_by(CollectorGap.started_at.desc()).limit(10)).scalars().all()
    quarantine_open = session.execute(select(func.count()).select_from(DataQuarantine).where(DataQuarantine.resolver_status == "OPEN")).scalar_one()
    unique_events = session.execute(select(func.count(func.distinct(RawSuperbetSnapshot.event_id))).select_from(RawSuperbetSnapshot)).scalar_one()
    raw_total = session.execute(select(func.count()).select_from(RawSuperbetSnapshot)).scalar_one()
    norm_total = session.execute(select(func.count()).select_from(SuperbetNormalized)).scalar_one()
    settled_total = session.execute(select(func.count()).select_from(SuperbetSettlement).where(SuperbetSettlement.status.in_(("WON", "LOST", "VOID")))).scalar_one()
    first_raw = session.execute(select(func.min(RawSuperbetSnapshot.fetched_at))).scalar_one()
    by_state = dict(session.execute(select(RawSuperbetSnapshot.event_state, func.count()).group_by(RawSuperbetSnapshot.event_state)).all())
    daily_points = [{"day": d, "snapshots": int(n), "events": int(e)} for d, n, e in session.execute(
        select(func.date(RawSuperbetSnapshot.fetched_at), func.count(), func.count(func.distinct(RawSuperbetSnapshot.event_id))).where(RawSuperbetSnapshot.fetched_at >= now - timedelta(days=30)).group_by(func.date(RawSuperbetSnapshot.fetched_at)).order_by(func.date(RawSuperbetSnapshot.fetched_at))).all()]
    disc = latest_research.get("discovery") or []
    return {
        "generated_at": now,
        "strip": _strip(edge, health),
        "versions": {"source": SOURCE_VERSION, "normalizer": NORMALIZER_VERSION, "settlement": SETTLEMENT_VERSION, "dataset": DATASET_VERSION},
        "dataset": {"unique_events": int(unique_events), "raw_snapshots": int(raw_total), "normalized_rows": int(norm_total), "settled_selections": int(settled_total), "first_snapshot_at": first_raw, "by_event_state": by_state, "daily": daily_points},
        "collector": {k: health[k] for k in ("health", "reasons", "cadence_minutes", "last_fetched_at", "stale_minutes", "upcoming_events", "last_hour", "last_24h", "gaps_7d", "quarantine_open") if k in health},
        "coverage": {"window_days": 7, "expected": cov["expected"], "observed": cov["observed"], "coverage_pct": cov["coverage_pct"], "by_target": cov["by_target"], "by_market": cov["by_market"], "note": cov["note"]},
        "mapping": {k: v for k, v in mapping.items() if k not in ("unknown", "out_of_scope_reasons")} | {"unknown_count": len(mapping.get("unknown") or [])},
        "settlement": {"events": audit["events"], "match_result_vs_secondary": audit["match_result_vs_secondary"], "market_data_coverage": audit["market_data_coverage"]},
        "markets": [{"market_category": d["market_category"], "label": d["label"], "family": d["family"], "selections_observed": d["selections_observed"], "events_observed": d["events_observed"], "raw_n": d["raw_n"], "unique_events": d["unique_events"], "effective_n": d["effective_n"], "maturity": d["maturity"], "overround_median_pct": d.get("overround_median_pct"), "edgefut_vs_fair": d.get("edgefut_vs_fair", "INSUFFICIENT"),
                     "edge_state": next((e["state"] for e in edge if e["market_category"] == d["market_category"]), "UNPROVEN"), "value_enabled": next((e["value_enabled"] for e in edge if e["market_category"] == d["market_category"]), False),
                     "enablement_candidate": next((e["enablement_candidate"] for e in edge if e["market_category"] == d["market_category"]), False)} for d in disc] or [
            {"market_category": c, "label": CATEGORY_LABELS[c], "maturity": "COLLECTING", "edge_state": "UNPROVEN", "value_enabled": False, "enablement_candidate": False, "effective_n": 0, "raw_n": 0, "unique_events": 0, "selections_observed": 0, "events_observed": 0} for c in CATEGORY_ORDER],
        "research_generated_at": latest_research.get("generated_at"),
        "gaps": [{"id": g.id, "started_at": g.started_at, "ended_at": g.ended_at, "minutes": g.minutes, "reason": g.reason, "events_affected": g.events_affected, "detail": g.detail} for g in gaps],
        "quarantine_open": int(quarantine_open),
        "storage": stor,
        "freeze": (latest_research.get("freeze") or {}).get("status"),
        "value_enabled": bool(any(e.get("value_enabled") for e in edge)),
        "staking": {"enabled": governance.staking_enabled()[0], "reason": governance.staking_enabled()[1]},
        "research_only": reports.RESEARCH_ONLY,
    }


@router.get("/collector/health")
def collector_health_endpoint(session: Session = Depends(get_session)):
    return collector_health(session)


@router.get("/coverage")
def coverage(days: int = Query(14, ge=1, le=120), limit: int = Query(200, ge=0, le=1000), session: Session = Depends(get_session)):
    return coverage_report(session, days=days, per_event_limit=limit)


@router.get("/gaps")
def gaps(limit: int = Query(100, ge=1, le=1000), session: Session = Depends(get_session)):
    rows = session.execute(select(CollectorGap).order_by(CollectorGap.started_at.desc()).limit(limit)).scalars().all()
    return {"gaps": [{"id": g.id, "started_at": g.started_at, "ended_at": g.ended_at, "minutes": g.minutes, "reason": g.reason, "events_affected": g.events_affected, "detail": g.detail} for g in rows],
            "note": "Lacunas registadas, nunca preenchidas: snapshots em falta não são interpolados nem fabricados."}


# ---------------------------------------------------------------------------
# mapeamento de mercados
# ---------------------------------------------------------------------------
@router.get("/mapping")
def mapping(session: Session = Depends(get_session)):
    mc = mapping_coverage(session)
    mapped = [{"superbet_market_id": mid, "market_category": spec.category, "kind": spec.kind, "side": spec.side, "label": CATEGORY_LABELS[spec.category]} for mid, spec in sorted(MAPPED.items(), key=lambda kv: (CATEGORY_ORDER.index(kv[1].category), kv[0]))]
    return {**mc, "mapped_spec": mapped, "out_of_scope_patterns": [{"reason": r, "pattern": p} for r, p in OUT_OF_SCOPE_PATTERNS], "targets": [{"name": n, "minutes": m, "window": w} for n, m, w in SNAPSHOT_TARGETS]}


@router.get("/mapping/unknown")
def unknown_markets(session: Session = Depends(get_session)):
    rows = session.execute(select(MarketMappingRegistry).where(MarketMappingRegistry.status.in_(("UNKNOWN", "AMBIGUOUS"))).order_by(MarketMappingRegistry.occurrences.desc())).scalars().all()
    return {"markets": [{"superbet_market_id": r.superbet_market_id, "market_name": r.market_name, "status": r.status, "reason": r.reason, "first_seen_at": r.first_seen_at, "last_seen_at": r.last_seen_at, "occurrences": r.occurrences, "events_seen": r.events_seen, "sample_selections": r.sample_selections, "specifier_keys": r.specifier_keys} for r in rows],
            "note": "Mercados nunca são mapeados automaticamente quando ambíguos (§56). Mapear = alterar `flywheel/markets.py` (versão do normalizador) — aqui só se marca fora de escopo com motivo."}


class MappingDecision(BaseModel):
    status: str = Field(pattern="^(OUT_OF_SCOPE|UNKNOWN|AMBIGUOUS)$")
    reason: str = Field(min_length=5, max_length=300)


@router.post("/mapping/{market_id}")
def decide_mapping(market_id: int, body: MappingDecision, session: Session = Depends(get_session)):
    row = session.get(MarketMappingRegistry, market_id)
    if row is None:
        raise HTTPException(404, "mercado não encontrado no registry")
    if row.status == "MAPPED":
        raise HTTPException(400, "mercado MAPPED só muda por código (versão do normalizador)")
    before = {"status": row.status, "reason": row.reason}
    row.status, row.reason = body.status, f"MANUAL: {body.reason}"
    session.add(ManualCorrection(entity="market_mapping_registry", entity_id=str(market_id), field="status", before=before, after={"status": row.status, "reason": row.reason}, reason=body.reason))
    return {"ok": True, "superbet_market_id": market_id, "status": row.status, "reason": row.reason}


# ---------------------------------------------------------------------------
# quarentena
# ---------------------------------------------------------------------------
@router.get("/quarantine")
def quarantine(status: str = Query("OPEN"), limit: int = Query(200, ge=1, le=2000), session: Session = Depends(get_session)):
    q = select(DataQuarantine).order_by(DataQuarantine.created_at.desc()).limit(limit)
    if status != "ALL":
        q = q.where(DataQuarantine.resolver_status == status)
    rows = session.execute(q).scalars().all()
    by_reason = dict(session.execute(select(DataQuarantine.reason, func.count()).where(DataQuarantine.resolver_status == "OPEN").group_by(DataQuarantine.reason)).all())
    return {"items": [{"id": r.id, "reason": r.reason, "event_id": r.event_id, "raw_snapshot_id": r.raw_snapshot_id, "payload_hash": r.payload_hash, "payload_excerpt": r.payload_excerpt, "detail": r.detail, "created_at": r.created_at, "resolver_status": r.resolver_status, "resolved_at": r.resolved_at, "resolution_note": r.resolution_note} for r in rows],
            "open_by_reason": by_reason, "note": "Quarentena em vez de descarte silencioso: o raw associado (quando existe) continua imutável."}


class QuarantineResolution(BaseModel):
    status: str = Field(pattern="^(RESOLVED|IGNORED|OPEN)$")
    note: str = Field(min_length=3, max_length=400)


@router.post("/quarantine/{item_id}/resolve")
def resolve_quarantine(item_id: int, body: QuarantineResolution, session: Session = Depends(get_session)):
    row = session.get(DataQuarantine, item_id)
    if row is None:
        raise HTTPException(404, "item não encontrado")
    before = {"resolver_status": row.resolver_status, "resolution_note": row.resolution_note}
    row.resolver_status, row.resolution_note, row.resolved_at = body.status, body.note, datetime.utcnow() if body.status != "OPEN" else None
    session.add(ManualCorrection(entity="data_quarantine", entity_id=str(item_id), field="resolver_status", before=before, after={"resolver_status": row.resolver_status, "resolution_note": row.resolution_note}, reason=body.note))
    return {"ok": True, "id": item_id, "resolver_status": row.resolver_status}


@router.get("/raw/{raw_id}")
def raw_snapshot(raw_id: int, session: Session = Depends(get_session)):
    row = session.get(RawSuperbetSnapshot, raw_id)
    if row is None:
        raise HTTPException(404, "snapshot não encontrado")
    payload = raw_payload(session, raw_id)
    meta = {c: getattr(row, c) for c in ("id", "event_id", "fetched_at", "kickoff_utc", "minutes_to_kickoff", "event_state", "snapshot_target", "payload_hash", "payload_ref_id", "payload_bytes", "raw_bytes", "odds_total", "odds_mapped", "odds_unknown", "odds_out_of_scope", "parse_failures", "markets_present", "schema_issues", "http_status", "source_version", "source_url")}
    if payload is not None:
        # payload completo pode ter 300 KB: devolvemos cabeçalho + primeiras odds; o export tem tudo
        odds = payload.get("odds") or []
        meta["payload_head"] = {k: v for k, v in payload.items() if k != "odds"} | {"odds_count": len(odds), "odds_sample": odds[:40]}
    return meta


# ---------------------------------------------------------------------------
# settlement
# ---------------------------------------------------------------------------
@router.get("/settlement/audit")
def settlement_audit_endpoint(session: Session = Depends(get_session)):
    return settlement_audit(session)


@router.get("/settlement/event/{event_id}")
def settlement_event(event_id: int, session: Session = Depends(get_session)):
    rows = session.execute(select(SuperbetSettlement).where(SuperbetSettlement.event_id == event_id).order_by(SuperbetSettlement.market_category, SuperbetSettlement.canonical_market_id, SuperbetSettlement.selection_id)).scalars().all()
    return {"event_id": event_id, "items": [{"market_category": r.market_category, "canonical_market_id": r.canonical_market_id, "selection_id": r.selection_id, "line": r.line, "status": r.status, "missing_fields": r.missing_fields, "result_source": r.result_source, "result": r.result, "settled_at": r.settled_at, "error": r.error} for r in rows]}


# ---------------------------------------------------------------------------
# pesquisa
# ---------------------------------------------------------------------------
@router.get("/research")
def research_latest(session: Session = Depends(get_session)):
    rep = reports.latest(session, "flywheel_research")
    if rep is None:
        return {"available": False, "note": "A pesquisa ainda não correu. Use 'Recalcular' ou aguarde o job (6 h)."}
    return {"available": True, **rep}


@router.post("/research/run")
def research_run():
    jobs.run_in_background(jobs.job_flywheel_research)
    return {"ok": True, "started": "flywheel_research", "background": True}


@router.get("/research/lab")
def superbet_lab(
    category: str | None = Query(None), competition: str | None = Query(None), odds_band: str | None = Query(None), target: str | None = Query(None),
    line: float | None = Query(None), days: int = Query(60, ge=1, le=365), limit: int = Query(300, ge=1, le=2000), session: Session = Depends(get_session),
):
    """SUPERBET LAB (§38): filtros por mercado, competição, faixa de odd, alvo temporal e linha. Só descritivo."""
    since = datetime.utcnow() - timedelta(days=days)
    frames = research.derived_frame(session, since=since)
    norm, tl = frames["normalized"], frames["timelines"]
    if norm.empty:
        return {"filters": {"category": category, "competition": competition, "odds_band": odds_band, "target": target, "line": line}, "n": 0, "rows": [], "note": "sem observações no período"}
    if category:
        norm, tl = norm[norm["market_category"] == category], tl[tl["market_category"] == category]
    if competition:
        norm = norm[norm["competition_name"].fillna("").str.contains(competition, case=False, regex=False)]
        tl = tl[tl["competition_name"].fillna("").str.contains(competition, case=False, regex=False)] if "competition_name" in tl.columns else tl
    if line is not None and "line" in norm.columns:
        norm = norm[(norm["line"] - line).abs() < 1e-9]
        tl = tl[(tl["line"] - line).abs() < 1e-9] if "line" in tl.columns else tl
    if odds_band:
        norm = norm[norm["odd"].map(research.odds_band) == odds_band]
        tl = tl[tl["closing_odd"].map(research.odds_band) == odds_band] if "closing_odd" in tl.columns else tl
    if target:
        norm = norm[norm["snapshot_target"] == target]
    margin = research.margin_lab(norm)
    clv = research.clv_v2(norm, tl)
    movement = research.line_movement(tl, top=limit)
    competitions = sorted(frames["normalized"]["competition_name"].dropna().unique().tolist())[:300]
    keys = ["event_id", "market_category", "canonical_market_id", "selection_id"]
    sample = tl.sort_values("closing_at" if "closing_at" in tl.columns else keys[0], ascending=False).head(limit) if not tl.empty else tl
    rows = [{k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in r.items()} for r in sample.to_dict("records")] if not sample.empty else []
    return {"filters": {"category": category, "competition": competition, "odds_band": odds_band, "target": target, "line": line, "days": days},
            "n": int(len(norm)), "events": int(norm["event_id"].nunique()), "selections": int(tl.groupby(keys).ngroups) if not tl.empty else 0,
            "margin": margin, "clv": clv, "movement": {"by_market": movement.get("by_market"), "counts": movement.get("counts")},
            "rows": rows, "options": {"categories": CATEGORY_ORDER, "competitions": competitions, "targets": [t[0] for t in SNAPSHOT_TARGETS], "odds_bands": ["1.01-1.50", "1.50-2.00", "2.00-3.00", "3.00-5.00", "5.00+"]},
            "note": "Descritivo. Nada aqui é edge: margem, CLV do preço da casa e movimento não implicam valor."}


@router.get("/research/movement")
def movement(category: str | None = Query(None), days: int = Query(30, ge=1, le=365), top: int = Query(60, ge=1, le=500), session: Session = Depends(get_session)):
    frames = research.derived_frame(session, since=datetime.utcnow() - timedelta(days=days))
    tl = frames["timelines"]
    if category and not tl.empty:
        tl = tl[tl["market_category"] == category]
    mv = research.line_movement(tl, top=top)
    ll = research.lead_lag(frames["normalized"], tl)
    return {"movement": mv, "lead_lag": ll, "note": "STEAM/DRIFT/STABLE são descrições do movimento do preço da Superbet. Não interpretamos como smart money."}


@router.get("/research/selection/{event_id}")
def selection_timeline(event_id: int, category: str | None = Query(None), session: Session = Depends(get_session)):
    """Timeline canónica de um evento (todas as seleções normalizadas, com alvos T-x): tela LINE MOVEMENT."""
    q = select(SuperbetNormalized).where(SuperbetNormalized.event_id == event_id).order_by(SuperbetNormalized.canonical_market_id, SuperbetNormalized.selection_id, SuperbetNormalized.fetched_at)
    if category:
        q = q.where(SuperbetNormalized.market_category == category)
    rows = session.execute(q).scalars().all()
    ev = session.get(Event, event_id)
    out: dict[str, dict] = {}
    for r in rows:
        key = f"{r.canonical_market_id}|{r.selection_id}"
        d = out.setdefault(key, {"canonical_market_id": r.canonical_market_id, "selection_id": r.selection_id, "selection_name": r.selection_name, "market_category": r.market_category, "line": r.line, "points": []})
        d["points"].append({"fetched_at": r.fetched_at, "minutes_to_kickoff": r.minutes_to_kickoff, "odd": r.odd, "implied_prob": r.implied_prob, "fair_prob": r.fair_prob, "overround": r.overround, "snapshot_target": r.snapshot_target, "event_state": r.event_state})
    edgefut = research.load_edgefut(session)
    ef = edgefut[edgefut["event_id"] == event_id] if not edgefut.empty else edgefut
    ef_map = {f"{r.canonical_market_id}|{r.selection_id}": float(r.edgefut_prob) for r in ef.itertuples()} if not ef.empty else {}
    series = []
    for key, d in out.items():
        pts = d["points"]
        pre = [p for p in pts if p["event_state"] == "prematch"]
        opening, closing = (pre[0] if pre else None), (pre[-1] if pre else None)
        move = (closing["fair_prob"] - opening["fair_prob"]) * 100 if opening and closing and opening.get("fair_prob") is not None and closing.get("fair_prob") is not None else None
        d.update({"opening": opening, "closing": closing, "move_pp": round(move, 2) if move is not None else None, "classification": research.classify_move(move, None, opening.get("fair_prob") if opening else None, closing.get("fair_prob") if closing else None), "edgefut_prob": ef_map.get(key), "n_obs": len(pts)})
        series.append(d)
    return {"event_id": event_id, "label": f"{ev.home_name} × {ev.away_name}" if ev else None, "kickoff_utc": ev.kickoff_utc if ev else None, "series": series,
            "note": "EdgeFut é a última previsão pré-kickoff. 'Closing' = último snapshot antes do kickoff realmente observado (nunca interpolado)."}


@router.get("/experiments")
def experiments(session: Session = Depends(get_session)):
    from ...db.models import ExperimentRegistry

    rows = session.execute(select(ExperimentRegistry).order_by(ExperimentRegistry.hypothesis_id)).scalars().all()
    return {"hypotheses": [{"hypothesis_id": h.hypothesis_id, "title": h.title, "market_category": h.market_category, "canonical_market_id": h.canonical_market_id, "competition": h.competition, "odds_band": h.odds_band, "time_window": h.time_window, "feature": h.feature, "expected_direction": h.expected_direction,
                            "discovery_start": h.discovery_start, "discovery_end": h.discovery_end, "confirmation_start": h.confirmation_start, "confirmation_end": h.confirmation_end, "min_effective_n": h.min_effective_n, "status": h.status, "last_evaluation": h.last_evaluation, "evaluated_at": h.evaluated_at, "notes": h.notes} for h in rows],
            "note": "Hipóteses registadas ANTES da confirmação; BH-FDR obrigatório; 'SMALL SAMPLE' nunca vira tendência."}


@router.get("/edge-states")
def edge_states(session: Session = Depends(get_session)):
    return {"states": governance.edge_states(session), "value_enabled_default": False, "staking": dict(zip(("enabled", "reason"), governance.staking_enabled(), strict=True)), "rules": {"min_effective_n": governance.VALIDATION_MIN_EFFECTIVE_N, "confirmation_min_days": governance.CONFIRMATION_MIN_DAYS}}


class ValueToggle(BaseModel):
    enabled: bool
    reason: str = Field(min_length=5, max_length=400)


@router.post("/edge-states/{category}/value")
def toggle_value(category: str, body: ValueToggle, session: Session = Depends(get_session)):
    try:
        row = governance.set_value_enabled(session, category, body.enabled, reason=body.reason)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    from ...analysis.pipeline import invalidate_cache

    invalidate_cache()
    return row


@router.get("/freeze")
def freeze(session: Session = Depends(get_session)):
    return governance.freeze_status(session)


# ---------------------------------------------------------------------------
# armazenamento / backup / export / relatórios
# ---------------------------------------------------------------------------
@router.get("/storage")
def storage_endpoint(session: Session = Depends(get_session)):
    return storage.storage_dashboard(session)


@router.get("/backups")
def backups():
    return {"backups": storage.list_backups(), "retention": storage.RETENTION, "dir": str(storage.backups_dir())}


@router.post("/backups/run")
def backup_run():
    return storage.create_backup(reason="manual")


class RestoreRequest(BaseModel):
    file: str = Field(min_length=8, max_length=80)
    confirm: bool = False


@router.post("/backups/restore")
def backup_restore(body: RestoreRequest):
    if not body.confirm:
        raise HTTPException(400, "restore exige confirm=true (a base atual é copiada para pre-restore-*.sqlite3 antes)")
    from ...db.session import get_engine

    jobs.stop()
    get_engine().dispose()
    try:
        res = storage.restore_backup(body.file)
        if res.get("ok"):
            from ...db.migrations import run_migrations

            run_migrations(get_engine())
            from ...analysis.pipeline import invalidate_cache

            invalidate_cache()
            governance.invalidate_cache()
    finally:
        if settings.scheduler_enabled:
            jobs.start()
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "restore falhou"))
    return res


class ExportRequest(BaseModel):
    layers: list[str] = Field(default_factory=lambda: ["raw", "normalized", "derived"])
    format: str = Field("parquet", pattern="^(parquet|csv)$")


@router.get("/exports")
def exports():
    return {"exports": storage.list_exports(), "dir": str(storage.exports_dir())}


@router.post("/exports/run")
def export_run(body: ExportRequest, session: Session = Depends(get_session)):
    layers = tuple(x for x in body.layers if x in ("raw", "normalized", "derived")) or ("raw", "normalized", "derived")
    return storage.export_dataset(session, layers=layers, fmt=body.format)


@router.get("/reports/daily")
def report_daily(session: Session = Depends(get_session)):
    rep = reports.latest(session, "flywheel_daily")
    return {"available": rep is not None, "report": rep, "history": reports.history(session, "flywheel_daily")}


@router.get("/reports/weekly")
def report_weekly(session: Session = Depends(get_session)):
    rep = reports.latest(session, "flywheel_weekly")
    return {"available": rep is not None, "report": rep, "history": reports.history(session, "flywheel_weekly")}


@router.post("/reports/{kind}/run")
def report_run(kind: str):
    fn = {"daily": jobs.job_flywheel_daily, "weekly": jobs.job_flywheel_weekly}.get(kind)
    if fn is None:
        raise HTTPException(404, "kind deve ser daily|weekly")
    jobs.run_in_background(fn)
    return {"ok": True, "started": f"flywheel_{kind}", "background": True}


# ---------------------------------------------------------------------------
# §58 auditoria de identidade + §59 correções manuais
# ---------------------------------------------------------------------------
@router.get("/identity")
def identity_audit(limit: int = Query(200, ge=1, le=2000), session: Session = Depends(get_session)):
    now = datetime.utcnow()
    dup = session.execute(select(func.count()).select_from(Event).where(Event.duplicate_of.is_not(None))).scalar_one()
    no_canonical = session.execute(select(func.count()).select_from(Event).where(Event.canonical_event_id.is_(None), Event.duplicate_of.is_(None))).scalar_one()
    unmapped_comp = session.execute(select(Event.competition_name, func.count()).where(Event.kickoff_utc >= now - timedelta(days=30), Event.duplicate_of.is_(None)).group_by(Event.competition_name).order_by(func.count().desc())).all()
    player_conf = dict(session.execute(select(SuperbetNormalized.identity_confidence, func.count()).where(SuperbetNormalized.market_category.like("PLAYER_%")).group_by(SuperbetNormalized.identity_confidence)).all())
    kickoff_q = session.execute(select(func.count()).select_from(DataQuarantine).where(DataQuarantine.reason == "KICKOFF_INCONSISTENT")).scalar_one()
    unknown_ev = session.execute(select(func.count()).select_from(DataQuarantine).where(DataQuarantine.reason == "UNKNOWN_EVENT")).scalar_one()
    corrections = session.execute(select(ManualCorrection).order_by(ManualCorrection.created_at.desc()).limit(limit)).scalars().all()
    return {
        "generated_at": now,
        "events": {"duplicates_merged": int(dup), "without_canonical_id": int(no_canonical), "kickoff_inconsistent_quarantined": int(kickoff_q), "unknown_event_payloads": int(unknown_ev)},
        "competitions_30d": [{"competition_name": c, "events": int(n)} for c, n in unmapped_comp][:100],
        "player_identity": {"by_confidence": player_conf, "note": "STRONG = player_id da Superbet; NAME_ONLY = só nome normalizado (não usado para settlement)."},
        "manual_corrections": [{"id": c.id, "entity": c.entity, "entity_id": c.entity_id, "field": c.field, "before": c.before, "after": c.after, "reason": c.reason, "created_at": c.created_at} for c in corrections],
    }
