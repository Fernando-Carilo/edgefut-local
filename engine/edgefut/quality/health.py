"""System Health — estado real de cada componente, com freshness.

HEALTHY     funcionando e fresco
DEGRADED    funcionando com erros parciais / cobertura reduzida
STALE       último dado válido está velho demais para a política do componente
UNAVAILABLE indisponível (bloqueado, desligado, sem dados)
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.paths import get_paths
from ..db.models import DatasetState, Event, JobRun, PredictionSnapshot, SourceLog
from ..domain.freshness import Freshness, assess
from ..explanations import ollama
from ..providers import get_http_client
from ..providers.historical import get_store
from ..scheduler import jobs

HealthStatus = Literal["HEALTHY", "DEGRADED", "UNAVAILABLE", "STALE"]
_ORDER = ["HEALTHY", "DEGRADED", "STALE", "UNAVAILABLE"]


class ComponentHealth(BaseModel):
    key: str
    name: str
    status: HealthStatus
    summary: str
    last_update: datetime | None = None
    freshness: Freshness | None = None
    details: dict = {}
    note: str | None = None


class SystemHealth(BaseModel):
    generated_at: datetime
    overall: HealthStatus
    components: list[ComponentHealth]


def _provider_stats(session: Session, provider: str, hours: int = 24) -> dict:
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = session.execute(
        select(SourceLog.status, func.count(), func.max(SourceLog.collected_at), func.avg(SourceLog.latency_ms))
        .where(SourceLog.provider == provider, SourceLog.collected_at >= since)
        .group_by(SourceLog.status)
    ).all()
    out = {"ok": 0, "cached": 0, "error": 0, "blocked": 0, "last_ok": None, "avg_latency_ms": None}
    lat = []
    for status, n, last, avg in rows:
        out[status if status in out else "error"] += int(n)
        if status in ("ok", "cached"):
            out["last_ok"] = max(out["last_ok"], last) if out["last_ok"] else last
        if avg is not None and status == "ok":
            lat.append(float(avg))
    out["avg_latency_ms"] = round(sum(lat) / len(lat)) if lat else None
    out["total"] = out["ok"] + out["cached"] + out["error"] + out["blocked"]
    out["error_rate"] = round((out["error"] + out["blocked"]) / out["total"], 3) if out["total"] else 0.0
    return out


def _circuit_open(host_fragment: str) -> bool:
    for host, st in get_http_client().host_status().items():
        if host_fragment in host and st.get("circuit_open"):
            return True
    return False


def superbet_health(session: Session) -> ComponentHealth:
    now = datetime.utcnow()
    stats = _provider_stats(session, "superbet")
    n_events = session.execute(
        select(func.count()).select_from(Event).where(Event.kickoff_utc > now - timedelta(hours=2), Event.kickoff_utc < now + timedelta(hours=48), Event.duplicate_of.is_(None))
    ).scalar() or 0
    n_markets = session.execute(
        select(func.coalesce(func.sum(Event.market_count), 0)).where(Event.kickoff_utc > now - timedelta(hours=2), Event.kickoff_utc < now + timedelta(hours=48), Event.duplicate_of.is_(None))
    ).scalar() or 0
    last_odds = session.execute(select(func.max(Event.odds_collected_at))).scalar()
    fr = assess("odds", last_odds, source="superbet")
    if not settings.superbet_enabled:
        status, summary = "UNAVAILABLE", "Provider desativado nas configurações."
    elif _circuit_open("superbet"):
        status, summary = "UNAVAILABLE", "Circuito aberto: a fonte bloqueou ou falhou repetidamente. Nenhuma tentativa de contorno."
    elif stats["total"] and stats["blocked"] and stats["ok"] == 0:
        status, summary = "UNAVAILABLE", "Todas as requisições recentes foram bloqueadas (anti-bot/limite)."
    elif fr.status in ("STALE", "EXPIRED"):
        status, summary = "STALE", f"Última odd coletada {_ago(last_odds)}."
    elif stats["error_rate"] > 0.2:
        status, summary = "DEGRADED", f"{stats['error_rate']:.0%} das requisições com erro nas últimas 24 h."
    else:
        status, summary = "HEALTHY", f"{n_events} eventos · {int(n_markets):,} mercados".replace(",", ".")
    return ComponentHealth(
        key="superbet", name="Superbet (odds)", status=status, summary=summary, last_update=last_odds, freshness=fr,  # type: ignore[arg-type]
        details={"events_48h": int(n_events), "markets_48h": int(n_markets), **stats},
    )


def fixtures_health(session: Session) -> ComponentHealth:
    st = jobs.state()
    last = st.get("last_events_sync")
    last_seen = session.execute(select(func.max(Event.last_seen_at))).scalar()
    ref = max((d for d in (last, last_seen) if d), default=None)
    fr = assess("events", ref, source="superbet")
    if ref is None:
        status, summary = "UNAVAILABLE", "Nenhuma sincronização de eventos ainda."
    elif fr.status in ("STALE", "EXPIRED"):
        status, summary = "STALE", f"Eventos sincronizados {_ago(ref)}."
    else:
        status, summary = "HEALTHY", f"Eventos sincronizados {_ago(ref)}."
    return ComponentHealth(key="fixtures", name="Fixtures (eventos)", status=status, summary=summary, last_update=ref, freshness=fr, details={"last_events_sync": last.isoformat() if last else None})  # type: ignore[arg-type]


def historical_health(session: Session) -> ComponentHealth:
    store = get_store()
    try:
        df = store.datasets()
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(key="historical", name="Histórico (football-data / international_results)", status="UNAVAILABLE", summary=f"DuckDB falhou: {exc}")
    if df is None or df.empty:
        return ComponentHealth(key="historical", name="Histórico (football-data / international_results)", status="UNAVAILABLE", summary="Nenhum dataset baixado.")
    newest = df["collected_at"].max()
    newest = newest.to_pydatetime() if hasattr(newest, "to_pydatetime") else newest
    rows = int(df["rows"].sum())
    errors = [d for d in session.execute(select(DatasetState)).scalars() if d.last_error]
    fr = assess("history", newest, source="football-data.co.uk")
    stats = _provider_stats(session, "football-data.co.uk")
    if fr.status in ("STALE", "EXPIRED"):
        status, summary = "STALE", f"Última sincronização {_ago(newest)} · {len(df)} datasets"
    elif errors or stats["blocked"]:
        status, summary = "DEGRADED", f"{len(errors)} dataset(s) com erro na última coleta · {len(df)} datasets"
    else:
        status, summary = "HEALTHY", f"Última sincronização {_ago(newest)} · {len(df)} datasets · {rows:,} partidas".replace(",", ".")
    return ComponentHealth(
        key="historical", name="Histórico (football-data / international_results)", status=status, summary=summary,  # type: ignore[arg-type]
        last_update=newest, freshness=fr,
        details={"datasets": int(len(df)), "rows": rows, "errors": [{"code": e.code, "error": e.last_error} for e in errors][:10], **stats},
    )


def results_health(session: Session) -> ComponentHealth:
    now = datetime.utcnow()
    st = jobs.state()
    last = st.get("last_settlement")
    pending = session.execute(
        select(func.count()).select_from(PredictionSnapshot).where(PredictionSnapshot.result.is_(None), PredictionSnapshot.kickoff_utc < now - timedelta(hours=3))
    ).scalar() or 0
    settled = session.execute(select(func.count()).select_from(PredictionSnapshot).where(PredictionSnapshot.result.is_not(None))).scalar() or 0
    overdue = session.execute(
        select(func.count()).select_from(PredictionSnapshot).where(PredictionSnapshot.result.is_(None), PredictionSnapshot.kickoff_utc < now - timedelta(hours=36))
    ).scalar() or 0
    fr = assess("results", last, source="superbet+football-data")
    if last is None and settled == 0 and pending == 0:
        status, summary = "HEALTHY", "Nada a liquidar ainda."
    elif overdue > 0:
        status, summary = "DEGRADED", f"{overdue} previsão(ões) sem resultado há mais de 36 h."
    elif fr.status in ("STALE", "EXPIRED") and pending:
        status, summary = "STALE", f"Última liquidação {_ago(last)}; {pending} pendentes."
    else:
        status, summary = "HEALTHY", f"{settled} liquidadas · {pending} pendentes"
    return ComponentHealth(key="results", name="Resultados (settlement)", status=status, summary=summary, last_update=last, freshness=fr, details={"settled": int(settled), "pending": int(pending), "overdue_36h": int(overdue)})  # type: ignore[arg-type]


def database_health(session: Session) -> ComponentHealth:
    t0 = time.perf_counter()
    try:
        session.execute(text("SELECT 1"))
        journal = session.execute(text("PRAGMA journal_mode")).scalar()
        ms = round((time.perf_counter() - t0) * 1000, 1)
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(key="database", name="Database (SQLite)", status="UNAVAILABLE", summary=str(exc))
    path = get_paths().sqlite
    size = path.stat().st_size if path.exists() else 0
    counts = {}
    for name in ("event", "odds_snapshot", "prediction_snapshot", "source_log", "source_conflict", "job_run"):
        try:
            counts[name] = int(session.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar() or 0)
        except Exception:  # noqa: BLE001
            counts[name] = None
    return ComponentHealth(
        key="database", name="Database (SQLite)", status="HEALTHY", summary=f"{size / 1e6:.1f} MB · journal {journal} · ping {ms} ms",
        last_update=datetime.utcnow(), details={"path": str(path), "size_bytes": size, "journal_mode": journal, "ping_ms": ms, "counts": counts},
    )


def duckdb_health() -> ComponentHealth:
    store = get_store()
    t0 = time.perf_counter()
    try:
        df = store.datasets()
        ms = round((time.perf_counter() - t0) * 1000, 1)
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(key="duckdb", name="DuckDB (Parquet)", status="UNAVAILABLE", summary=str(exc))
    files = len(store._files())
    if files == 0:
        return ComponentHealth(key="duckdb", name="DuckDB (Parquet)", status="UNAVAILABLE", summary="Nenhum arquivo Parquet em data/processed.")
    return ComponentHealth(key="duckdb", name="DuckDB (Parquet)", status="HEALTHY", summary=f"{files} arquivos Parquet · consulta de catálogo {ms} ms", last_update=datetime.utcnow(), details={"files": files, "datasets": int(len(df)), "query_ms": ms})


def scheduler_health(session: Session) -> ComponentHealth:
    st = jobs.state()
    running = jobs.is_running()
    since = datetime.utcnow() - timedelta(hours=6)
    recent = session.execute(select(JobRun).where(JobRun.started_at >= since).order_by(JobRun.started_at.desc()).limit(100)).scalars().all()
    errors = [r for r in recent if r.status == "error"]
    last_ok = max((r.finished_at for r in recent if r.status == "ok" and r.finished_at), default=None)
    if not settings.scheduler_enabled:
        status, summary = "UNAVAILABLE", "Scheduler desativado por configuração (EDGEFUT_SCHEDULER_ENABLED=false)."
    elif not running:
        status, summary = "UNAVAILABLE", "Scheduler não está em execução."
    elif errors and len(errors) >= max(3, len(recent) // 3):
        status, summary = "DEGRADED", f"{len(errors)} de {len(recent)} execuções com erro nas últimas 6 h."
    elif last_ok is None and recent and not any(r.status == "running" for r in recent):
        status, summary = "DEGRADED", "Nenhuma execução bem-sucedida nas últimas 6 h."
    elif last_ok is None and recent:
        status, summary = "HEALTHY", f"{sum(1 for r in recent if r.status == 'running')} job(s) em execução (primeiro ciclo após o boot)."
    else:
        status, summary = "HEALTHY", f"{len(recent)} execuções nas últimas 6 h · {len(errors)} erros"
    return ComponentHealth(
        key="scheduler", name="Scheduler", status=status, summary=summary, last_update=last_ok,  # type: ignore[arg-type]
        details={"running": running, "recent_runs": len(recent), "recent_errors": len(errors), **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in st.items() if k != "errors"}},
    )


def ollama_health() -> ComponentHealth:
    avail = ollama.is_available()
    if avail:
        return ComponentHealth(key="ollama", name="Ollama (LLM local)", status="HEALTHY", summary=f"Disponível · modelo {settings.ollama_model}", last_update=datetime.utcnow(), details={"enabled": settings.ollama_enabled, "url": settings.ollama_base_url})
    return ComponentHealth(key="ollama", name="Ollama (LLM local)", status="UNAVAILABLE", summary="Indisponível — Edge AI funcionando via templates.", details={"enabled": settings.ollama_enabled, "url": settings.ollama_base_url}, note="Opcional. Nunca gera estatísticas; apenas reescreve explicações.")


def cache_health() -> ComponentHealth:
    p = get_paths().cache
    files = list(p.glob("*.body")) if p.exists() else []
    size = sum(f.stat().st_size for f in files)
    newest = max((datetime.utcfromtimestamp(f.stat().st_mtime) for f in files), default=None)
    status: HealthStatus = "HEALTHY" if p.exists() else "UNAVAILABLE"
    return ComponentHealth(key="cache", name="Cache HTTP (disco)", status=status, summary=f"{len(files)} respostas · {size / 1e6:.1f} MB", last_update=newest, details={"path": str(p), "files": len(files), "size_bytes": size})


def player_health() -> ComponentHealth:
    return ComponentHealth(
        key="player_data", name="Dados de jogadores / escalações", status="UNAVAILABLE",
        summary="Nenhum provider público e permitido integrado. Mercados de jogador → NO BET (LINEUP_UNCERTAINTY).",
        details={"markets_affected": ["PLAYER_TO_SCORE"]},
    )


def system_health(session: Session) -> SystemHealth:
    comps = [
        superbet_health(session), fixtures_health(session), historical_health(session), results_health(session),
        player_health(), database_health(session), duckdb_health(), scheduler_health(session), ollama_health(), cache_health(),
    ]
    core = [c for c in comps if c.key in {"superbet", "fixtures", "historical", "database", "duckdb", "scheduler"}]
    overall: HealthStatus = max((c.status for c in core), key=_ORDER.index) if core else "HEALTHY"  # type: ignore[assignment]
    return SystemHealth(generated_at=datetime.utcnow(), overall=overall, components=comps)


def _ago(dt: datetime | None) -> str:
    if dt is None:
        return "nunca"
    s = int((datetime.utcnow() - dt).total_seconds())
    if s < 90:
        return "há instantes"
    if s < 3600:
        return f"há {s // 60} min"
    if s < 86400:
        return f"há {s // 3600} h"
    return f"há {s // 86400} d"
