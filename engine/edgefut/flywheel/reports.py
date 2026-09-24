"""§60–§62 — orquestração da pesquisa (snapshot persistido), relatório diário e relatório semanal de
evidência. RESEARCH ONLY: nenhum relatório fala de lucro, ROI esperado ou "apostas do dia".

Persistência em `validation_run`:
  kind = flywheel_research  → resultado completo da pesquisa (margem, eficiência, CLV, movimento, lead/lag,
                              discovery, experimentos, estados de edge) — servido pela API sem recomputar
  kind = flywheel_daily     → §60 relatório diário
  kind = flywheel_weekly    → §61 relatório semanal de evidência
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (
    Alert,
    CollectorGap,
    DataQuarantine,
    MarketMappingRegistry,
    RawSuperbetSnapshot,
    ShadowPrediction,
    SuperbetNormalized,
    SuperbetSettlement,
    ValidationRun,
)
from . import (
    DATASET_VERSION,
    NORMALIZER_VERSION,
    SETTLEMENT_VERSION,
    SOURCE_VERSION,
    governance,
    research,
    storage,
)
from .coverage import coverage_report
from .markets import CATEGORY_LABELS
from .reliability import collector_health, mapping_coverage
from .settlement import settlement_audit

log = logging.getLogger(__name__)

RESEARCH_ONLY = "RESEARCH ONLY — nenhum número aqui é promessa de lucro. Estamos a construir evidência, não a fabricar apostas."


def _jsonable(obj):
    return json.loads(json.dumps(obj, default=str))


def _persist(session: Session, kind: str, summary: dict, detail: dict, duration_ms: int) -> None:
    session.add(ValidationRun(kind=kind, request={}, summary=_jsonable(summary), detail=_jsonable(detail), duration_ms=duration_ms))


def latest(session: Session, kind: str) -> dict | None:
    r = session.execute(select(ValidationRun).where(ValidationRun.kind == kind).order_by(ValidationRun.created_at.desc()).limit(1)).scalar_one_or_none()
    return r.detail if r and r.detail else None


def history(session: Session, kind: str, limit: int = 30) -> list[dict]:
    rows = session.execute(select(ValidationRun).where(ValidationRun.kind == kind).order_by(ValidationRun.created_at.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "created_at": r.created_at, "summary": r.summary, "duration_ms": r.duration_ms} for r in rows]


# ---------------------------------------------------------------------------
# pesquisa (snapshot completo)
# ---------------------------------------------------------------------------
def run_research(session: Session, *, now: datetime | None = None, persist: bool = True) -> dict:
    now = now or datetime.utcnow()
    t0 = time.perf_counter()
    research.seed_experiments(session, now=now)
    frames = research.derived_frame(session, since=None)
    norm, tl, st = frames["normalized"], frames["timelines"], frames["settlement"]
    margin = research.margin_lab(norm)
    efficiency = research.price_efficiency_by_time(norm, st)
    clv = research.clv_v2(norm, tl)
    movement = research.line_movement(tl)
    leadlag = research.lead_lag(norm, tl)
    discovery = research.market_discovery(tl, margin, clv)
    experiments = research.evaluate_experiments(session, frames, now=now)
    audit = settlement_audit(session)
    edge_states = governance.update_edge_states(session, discovery, experiments, audit, now=now)
    governance.invalidate_cache()
    freeze = governance.freeze_status(session)
    out = {
        "generated_at": now, "duration_ms": int((time.perf_counter() - t0) * 1000), "research_only": RESEARCH_ONLY,
        "versions": {"source": SOURCE_VERSION, "normalizer": NORMALIZER_VERSION, "settlement": SETTLEMENT_VERSION, "dataset": DATASET_VERSION},
        "frames": {k: int(len(v)) for k, v in frames.items()},
        "margin_lab": margin, "price_efficiency": efficiency, "clv_v2": clv, "line_movement": movement, "lead_lag": leadlag,
        "discovery": discovery, "experiments": experiments, "edge_states": edge_states, "freeze": freeze,
        "settlement_audit": audit,
    }
    if persist:
        _persist(session, "flywheel_research", {
            "selections": int(len(tl)), "settled": int(st["won"].notna().sum()) if not st.empty and "won" in st.columns else 0,
            "mature_markets": sum(1 for d in discovery if d["maturity"] in ("TESTABLE", "MATURE")),
            "supported_hypotheses": len(experiments.get("survivors", [])), "candidates": sum(1 for e in edge_states if e.get("enablement_candidate")),
        }, out, out["duration_ms"])
    return out


# ---------------------------------------------------------------------------
# §60 relatório diário
# ---------------------------------------------------------------------------
def daily_report(session: Session, *, now: datetime | None = None, persist: bool = True) -> dict:
    now = now or datetime.utcnow()
    t0 = time.perf_counter()
    since = now - timedelta(hours=24)
    raw_n, raw_events, raw_bytes = session.execute(select(func.count(), func.count(func.distinct(RawSuperbetSnapshot.event_id)), func.coalesce(func.sum(RawSuperbetSnapshot.payload_bytes), 0)).where(RawSuperbetSnapshot.fetched_at >= since)).one()
    norm_n = session.execute(select(func.count()).select_from(SuperbetNormalized).where(SuperbetNormalized.fetched_at >= since)).scalar_one()
    targets = dict(session.execute(select(RawSuperbetSnapshot.snapshot_target, func.count()).where(RawSuperbetSnapshot.fetched_at >= since, RawSuperbetSnapshot.snapshot_target.is_not(None)).group_by(RawSuperbetSnapshot.snapshot_target)).all())
    settled = dict(session.execute(select(SuperbetSettlement.status, func.count()).where(SuperbetSettlement.settled_at >= since).group_by(SuperbetSettlement.status)).all())
    settled_by_market = [{"market_category": c, "label": CATEGORY_LABELS.get(c, c), "n": int(n)} for c, n in session.execute(select(SuperbetSettlement.market_category, func.count()).where(SuperbetSettlement.settled_at >= since, SuperbetSettlement.status.in_(("WON", "LOST", "VOID"))).group_by(SuperbetSettlement.market_category)).all()]
    quarantine = dict(session.execute(select(DataQuarantine.reason, func.count()).where(DataQuarantine.created_at >= since).group_by(DataQuarantine.reason)).all())
    new_unknown = [{"market_id": r.superbet_market_id, "name": r.market_name, "occurrences": r.occurrences} for r in session.execute(select(MarketMappingRegistry).where(MarketMappingRegistry.first_seen_at >= since, MarketMappingRegistry.status == "UNKNOWN")).scalars()]
    gaps = [{"started_at": g.started_at, "minutes": g.minutes, "events_affected": g.events_affected} for g in session.execute(select(CollectorGap).where(CollectorGap.ended_at >= since)).scalars()]
    research_signals = session.execute(select(func.count()).select_from(Alert).where(Alert.kind == "RESEARCH_SIGNAL", Alert.created_at >= since)).scalar_one()
    shadow_n = session.execute(select(func.count()).select_from(ShadowPrediction).where(ShadowPrediction.created_at >= since)).scalar_one()
    health = collector_health(session, now)
    cov = coverage_report(session, now=now, days=2, per_event_limit=0)
    mapping = mapping_coverage(session)
    stor = storage_dashboard_light(session, now)
    research_latest = latest(session, "flywheel_research") or {}
    disc = research_latest.get("discovery") or []
    edge = research_latest.get("edge_states") or []
    out = {
        "generated_at": now, "window": "24h", "research_only": RESEARCH_ONLY,
        "collector": {"health": health["health"], "reasons": health["reasons"], "raw_snapshots": int(raw_n), "events": int(raw_events), "raw_bytes": int(raw_bytes), "normalized_rows": int(norm_n), "targets_hit": targets, "gaps": gaps},
        "coverage_48h": {"expected": cov["expected"], "observed": cov["observed"], "coverage_pct": cov["coverage_pct"], "by_target": cov["by_target"]},
        "mapping": {"mapped_pct": mapping.get("mapped_pct"), "unknown_pct": mapping.get("unknown_pct"), "unknown_markets_total": len(mapping.get("unknown") or []), "new_unknown_24h": new_unknown},
        "settlement": {"by_status": settled, "by_market": settled_by_market},
        "quarantine_24h": quarantine,
        "model": {"shadow_predictions_24h": int(shadow_n), "research_signals_24h": int(research_signals), "freeze": (research_latest.get("freeze") or {}).get("status")},
        "markets": [{"market_category": d["market_category"], "label": d["label"], "maturity": d["maturity"], "effective_n": d["effective_n"], "unique_events": d["unique_events"],
                     "edge_state": next((e["state"] for e in edge if e["market_category"] == d["market_category"]), "UNPROVEN")} for d in disc],
        "storage": stor,
        "value_enabled_markets": [e["market_category"] for e in edge if e.get("value_enabled")],
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }
    if persist:
        _persist(session, "flywheel_daily", {"raw_snapshots": int(raw_n), "events": int(raw_events), "health": health["health"], "coverage_pct": cov["coverage_pct"], "settled": sum(int(v) for k, v in settled.items() if k in ("WON", "LOST", "VOID")), "quarantine": sum(quarantine.values())}, out, out["duration_ms"])
    return out


def storage_dashboard_light(session: Session, now: datetime) -> dict:
    try:
        s = storage.storage_dashboard(session, now=now)
        return {"sqlite_bytes": s["sqlite"]["bytes"], "raw_compressed_bytes": s["raw_payloads"]["compressed_bytes"], "raw_bytes_per_day": s["growth"]["raw_bytes_per_day"], "backups": s["backups"]["count"], "latest_backup": (s["backups"]["latest"] or {}).get("created_at")}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


# ---------------------------------------------------------------------------
# §61 relatório semanal de evidência
# ---------------------------------------------------------------------------
def weekly_report(session: Session, *, now: datetime | None = None, persist: bool = True) -> dict:
    now = now or datetime.utcnow()
    t0 = time.perf_counter()
    since = now - timedelta(days=7)
    res = run_research(session, now=now, persist=False)
    disc = {d["market_category"]: d for d in res["discovery"]}
    edge = {e["market_category"]: e for e in res["edge_states"]}
    clv_rows = res["clv_v2"].get("rows", [])
    raw_week = session.execute(select(func.count(), func.count(func.distinct(RawSuperbetSnapshot.event_id))).where(RawSuperbetSnapshot.fetched_at >= since)).one()
    settled_week = session.execute(select(func.count()).select_from(SuperbetSettlement).where(SuperbetSettlement.settled_at >= since, SuperbetSettlement.status.in_(("WON", "LOST", "VOID")))).scalar_one()
    markets = []
    for cat, d in disc.items():
        e = edge.get(cat, {})
        clv_close = next((r for r in clv_rows if r["market_category"] == cat and r["target"] in ("T-1h", "T-30m", "T-15m", "T-5m")), None)
        fair = d.get("superbet_fair") or {}
        ef = d.get("edgefut") or {}
        markets.append({
            "market_category": cat, "label": d["label"], "maturity": d["maturity"], "raw_n": d["raw_n"], "unique_events": d["unique_events"], "effective_n": d["effective_n"],
            "overround_median_pct": d.get("overround_median_pct"),
            "clv_raw_pct_near_close": (clv_close or {}).get("clv_raw_pct"),
            "superbet_fair_brier": fair.get("brier"), "edgefut_brier": ef.get("brier"), "edgefut_vs_fair": d.get("edgefut_vs_fair", "INSUFFICIENT"),
            "edge_state": e.get("state", "UNPROVEN"), "enablement_candidate": bool(e.get("enablement_candidate")), "value_enabled": bool(e.get("value_enabled")),
            "confidence": (e.get("evidence") or {}).get("confidence", "INSUFFICIENT"),
            "verdict": _market_verdict(d, e),
        })
    hyps = [{"hypothesis_id": h["hypothesis_id"], "title": h["title"], "market_category": h["market_category"], "status": h["status"], "sample_label": h["sample_label"], "effective_n": h["confirmation"]["effective_n"], "estimate": h["confirmation"].get("estimate"), "ci": h["confirmation"].get("ci"), "fdr_adjusted_p": h.get("fdr_adjusted_p")} for h in res["experiments"]["hypotheses"]]
    out = {
        "generated_at": now, "window": "7d", "research_only": RESEARCH_ONLY,
        "headline": _headline(markets, res["experiments"]),
        "collection": {"raw_snapshots_7d": int(raw_week[0]), "events_7d": int(raw_week[1]), "settled_7d": int(settled_week)},
        "markets": markets, "hypotheses": hyps, "fdr": {"q": res["experiments"]["q"], "tested": res["experiments"]["tested"], "survivors": res["experiments"]["survivors"]},
        "lead_lag_verdict": res["lead_lag"].get("verdict"), "freeze": res["freeze"].get("status"),
        "answer_to_67": _answer_67(markets),
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }
    if persist:
        _persist(session, "flywheel_weekly", {"raw_snapshots_7d": int(raw_week[0]), "settled_7d": int(settled_week), "mature": sum(1 for m in markets if m["maturity"] in ("TESTABLE", "MATURE")), "candidates": sum(1 for m in markets if m["enablement_candidate"]), "survivors": len(res["experiments"]["survivors"])}, out, out["duration_ms"])
    return out


def _market_verdict(d: dict, e: dict) -> str:
    if d["maturity"] == "COLLECTING":
        return "COLLECTING — N efetivo insuficiente para qualquer conclusão"
    if d["maturity"] == "EARLY":
        return "EARLY — só descritivo (margem, movimento); nenhum teste de edge"
    v = d.get("edgefut_vs_fair", "INSUFFICIENT")
    if v == "EDGEFUT":
        return "PROMISING — EdgeFut bate a fair da Superbet neste mercado (IC); ainda não validado"
    if v == "SUPERBET":
        return "REJECTED — a fair da Superbet bate o EdgeFut; não investir pesquisa aqui"
    return f"{d['maturity']} — INCONCLUSIVE (estado {e.get('state', 'UNPROVEN')})"


def _headline(markets: list[dict], experiments: dict) -> str:
    validated = [m["label"] for m in markets if m["value_enabled"]]
    cands = [m["label"] for m in markets if m["enablement_candidate"]]
    if validated:
        return f"Mercados com VALUE ativo (validação manual): {', '.join(validated)}."
    if cands:
        return f"VALUE_ENABLEMENT_CANDIDATE: {', '.join(cands)} cumprem todas as regras — decisão humana pendente."
    testable = [m["label"] for m in markets if m["maturity"] in ("TESTABLE", "MATURE")]
    if testable:
        return f"Nenhum edge validado. Mercados testáveis: {', '.join(testable)}; restantes em coleta."
    return "Nenhum edge validado. Todos os mercados ainda em coleta (N efetivo insuficiente). 0 VALUE é o resultado correto neste estágio."


def _answer_67(markets: list[dict]) -> dict:
    """§67 — 'Em que mercado vale a pena investir pesquisa?' Só com evidência; caso contrário INSUFFICIENT."""
    ranked = sorted(markets, key=lambda m: (-(m["edgefut_vs_fair"] == "EDGEFUT"), -m["effective_n"]))
    promising = [m["label"] for m in ranked if m["edgefut_vs_fair"] == "EDGEFUT"]
    rejected = [m["label"] for m in ranked if m["edgefut_vs_fair"] == "SUPERBET"]
    growing = [f"{m['label']} (N ef. {m['effective_n']})" for m in ranked if m["maturity"] != "COLLECTING"]
    return {"promising": promising, "rejected": rejected, "growing": growing,
            "answer": "INSUFFICIENT — ainda sem mercado com evidência para decidir" if not promising and not rejected else f"Promissores: {', '.join(promising) or '—'}. Rejeitados: {', '.join(rejected) or '—'}."}


__all__ = ["run_research", "daily_report", "weekly_report", "latest", "history", "RESEARCH_ONLY"]
