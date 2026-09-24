"""Scheduler local (APScheduler) — v2.

Jobs: fixtures (15 min), odds (5 min), histórico (diário), settlement (60 min), radar
(20 min), closing lines (10 min), performance (6 h), calibração (6 h), limpeza de cache
(diário), alertas (após odds) e polling ao vivo (intervalo configurável, só com jogos
em andamento).

Toda execução grava `job_run` (startedAt, finishedAt, duration, status,
recordsProcessed, errors) com correlation id, e nunca mantém uma transação SQLite
aberta durante fetches HTTP.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from ..analysis.pipeline import analyze_event, invalidate_cache
from ..backtesting.performance import settle_pending
from ..collectors import SuperbetSync
from ..core.config import settings
from ..core.context import correlation, new_correlation_id
from ..db.models import Competition, Event, JobRun
from ..db.session import session_scope
from ..providers.resolver import SourceResolver

log = logging.getLogger(__name__)

_state: dict = {
    "last_events_sync": None,
    "last_odds_sync": None,
    "last_history_sync": None,
    "last_settlement": None,
    "last_radar_refresh": None,
    "last_closing_lines": None,
    "last_performance_update": None,
    "last_calibration_update": None,
    "last_cache_cleanup": None,
    "last_ensemble_weights": None,
    "last_live_poll": None,
    "last_reconciliation": None,
    "last_shadow_report": None,
    "last_drift_check": None,
    "radar_running": False,
    "errors": [],
}
_lock = threading.Lock()
_running_jobs: set[str] = set()

JOB_LABELS = {
    "sync_events": "Fixtures (eventos Superbet)",
    "sync_odds": "Odds pré-jogo",
    "sync_history": "Histórico (CSV → Parquet)",
    "settle": "Settlement (resultados)",
    "radar": "Radar (análises)",
    "closing_lines": "Closing lines",
    "performance": "Performance",
    "calibration": "Calibração",
    "ensemble_weights": "Pesos do ensemble (walk-forward)",
    "cache_cleanup": "Limpeza de cache",
    "alerts": "Alertas locais",
    "live_poll": "Ao vivo (observação)",
    "reconcile": "Reconciliação de settlement",
    "shadow_report": "Relatório diário do modelo (shadow)",
    "drift": "Drift monitor",
}


def state() -> dict:
    return dict(_state)


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


def _record_error(job: str, exc: Exception) -> None:
    _state["errors"] = ([f"{datetime.utcnow().isoformat()} {job}: {exc}"] + _state["errors"])[:20]
    log.exception("job %s falhou", job)


def _records(result: dict) -> int:
    for key in ("records", "events", "odds", "settled", "analyzed", "closing", "removed", "groups", "alerts", "live"):
        v = result.get(key)
        if isinstance(v, int):
            return v
    return 0


def run_job(name: str, fn: Callable[[], dict], *, state_key: str | None = None) -> dict:
    """Executa `fn` com correlation id e registro persistente em `job_run`."""
    with _lock:
        if name in _running_jobs:
            return {"ok": True, "skipped": True, "reason": "already running"}
        _running_jobs.add(name)
    cid = new_correlation_id("job")
    started = datetime.utcnow()
    run_id: int | None = None
    try:
        with session_scope() as s:
            row = JobRun(job=name, correlation_id=cid, started_at=started, status="running")
            s.add(row)
            s.flush()
            run_id = row.id
    except Exception as exc:  # noqa: BLE001 - o job roda mesmo sem conseguir registrar
        log.warning("job_run não registrado (%s): %s", name, exc)
    result: dict
    status = "ok"
    errors: list[str] = []
    with correlation("job", cid, job=name):
        try:
            result = fn() or {}
            if result.get("skipped"):
                status = "skipped"
            elif result.get("ok") is False:
                status = "error"
                errors.append(str(result.get("error")))
            if state_key and status == "ok":
                _state[state_key] = datetime.utcnow()
        except Exception as exc:  # noqa: BLE001
            status = "error"
            errors.append(f"{type(exc).__name__}: {exc}")
            _record_error(name, exc)
            result = {"ok": False, "error": str(exc)}
        finished = datetime.utcnow()
        duration = int((finished - started).total_seconds() * 1000)
        log.info("job %s → %s em %d ms (%s registros)", name, status, duration, _records(result), extra={"job": name, "duration_ms": duration})
        if run_id is not None:
            try:
                with session_scope() as s:
                    row = s.get(JobRun, run_id)
                    if row is not None:
                        row.finished_at = finished
                        row.duration_ms = duration
                        row.status = status
                        row.records_processed = _records(result)
                        row.errors = errors or None
                        row.detail = {k: v for k, v in result.items() if k not in ("bets", "datasets") and isinstance(v, (int, float, str, bool, type(None)))}
            except Exception as exc:  # noqa: BLE001
                log.warning("job_run não atualizado (%s): %s", name, exc)
    with _lock:
        _running_jobs.discard(name)
    return result


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------


def _sync_events() -> dict:
    with session_scope() as s:
        return SuperbetSync().sync_events(s)


def job_sync_events() -> dict:
    return run_job("sync_events", _sync_events, state_key="last_events_sync")


def _sync_odds(hours: int = 48, limit: int = 60) -> dict:
    n = 0
    with session_scope() as s:
        now = datetime.utcnow()
        ids = s.execute(
            select(Event.id)
            .where(Event.kickoff_utc > now - timedelta(hours=2), Event.kickoff_utc < now + timedelta(hours=hours), Event.duplicate_of.is_(None))
            .order_by(Event.kickoff_utc.asc()).limit(limit)
        ).scalars().all()
        s.commit()
    sync = SuperbetSync()
    for eid in ids:
        # Uma transação curta por evento: o fetch HTTP acontece fora de qualquer
        # escrita pendente, evitando manter o SQLite bloqueado por minutos.
        with session_scope() as s:
            r = sync.sync_odds(s, eid)
        if r.get("blocked"):
            break
        n += 1
    return {"ok": True, "events": n}


def job_sync_odds(hours: int = 48, limit: int = 60) -> dict:
    res = run_job("sync_odds", lambda: _sync_odds(hours, limit), state_key="last_odds_sync")
    if res.get("ok") and not res.get("skipped"):
        run_in_background(job_alerts)
    return res


def _sync_history(force: bool = False) -> dict:
    resolver = SourceResolver()
    out = []
    for code in ["INTL", *settings.bootstrap_leagues]:
        out.append((code, resolver.ensure_dataset(code, force=force).status))
    invalidate_cache()
    return {"ok": True, "records": len(out), "datasets": out}


def job_sync_history(force: bool = False) -> dict:
    return run_job("sync_history", lambda: _sync_history(force), state_key="last_history_sync")


def _settle() -> dict:
    with session_scope() as s:
        res = settle_pending(s)
    if res.get("settled"):
        run_in_background(job_update_calibration)
        run_in_background(job_update_performance)
    return res


def job_settle() -> dict:
    return run_job("settle", _settle, state_key="last_settlement")


def _refresh_radar(hours: int = 48, limit: int = 80) -> dict:
    _state["radar_running"] = True
    analyzed = 0
    failed = 0
    try:
        with session_scope() as s:
            now = datetime.utcnow()
            rows = s.execute(
                select(Event).join(Competition, Competition.id == Event.competition_id)
                .where(Event.kickoff_utc > now, Event.kickoff_utc < now + timedelta(hours=hours), Event.duplicate_of.is_(None))
                .where((Competition.dataset_code.is_not(None)) | (Competition.is_national_teams.is_(True)))
                .order_by(Event.kickoff_utc.asc()).limit(limit)
            ).scalars().all()
            ids = [r.id for r in rows]
        for eid in ids:
            try:
                with session_scope() as s, correlation("analysis", event_id=eid):
                    analyze_event(s, eid, use_cache=True)
                analyzed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log.info("radar: evento %s falhou: %s", eid, exc)
        return {"ok": True, "analyzed": analyzed, "failed": failed, "candidates": len(ids)}
    finally:
        _state["radar_running"] = False


def job_refresh_radar(hours: int = 48, limit: int = 80) -> dict:
    if _state["radar_running"]:
        return {"ok": True, "skipped": True}
    return run_job("radar", lambda: _refresh_radar(hours, limit), state_key="last_radar_refresh")


def _closing_lines() -> dict:
    from ..odds.closing import capture_closing_lines

    with session_scope() as s:
        return capture_closing_lines(s)


def job_closing_lines() -> dict:
    return run_job("closing_lines", _closing_lines, state_key="last_closing_lines")


def _update_performance() -> dict:
    from ..backtesting.performance import refresh_performance_summary

    with session_scope() as s:
        return refresh_performance_summary(s)


def job_update_performance() -> dict:
    return run_job("performance", _update_performance, state_key="last_performance_update")


def _update_calibration() -> dict:
    from ..models.calibration import refit_all

    with session_scope() as s:
        res = refit_all(s)
    invalidate_cache()
    return res


def job_update_calibration() -> dict:
    return run_job("calibration", _update_calibration, state_key="last_calibration_update")


def _ensemble_weights() -> dict:
    from ..models.ensemble import refresh_ensemble_weights

    with session_scope() as s:
        res = refresh_ensemble_weights(s)
    invalidate_cache()
    return res


def job_ensemble_weights() -> dict:
    return run_job("ensemble_weights", _ensemble_weights, state_key="last_ensemble_weights")


def _cache_cleanup() -> dict:
    from ..core.paths import get_paths
    from ..db.models import SourceLog

    removed = 0
    cutoff = datetime.utcnow() - timedelta(days=7)
    cache = get_paths().cache
    for f in list(cache.glob("*")):
        try:
            if datetime.utcfromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    with session_scope() as s:
        old_logs = datetime.utcnow() - timedelta(days=30)
        n_logs = s.query(SourceLog).filter(SourceLog.collected_at < old_logs).delete(synchronize_session=False)
        n_runs = s.query(JobRun).filter(JobRun.started_at < old_logs).delete(synchronize_session=False)
    return {"ok": True, "removed": removed, "source_log_deleted": int(n_logs), "job_run_deleted": int(n_runs)}


def job_cache_cleanup() -> dict:
    return run_job("cache_cleanup", _cache_cleanup, state_key="last_cache_cleanup")


def _alerts() -> dict:
    from ..alerts import scan_alerts

    with session_scope() as s:
        return scan_alerts(s)


def job_alerts() -> dict:
    return run_job("alerts", _alerts)


def _live_poll() -> dict:
    from ..collectors.live import poll_live

    return poll_live()


def job_live_poll() -> dict:
    return run_job("live_poll", _live_poll, state_key="last_live_poll")


def _reconcile() -> dict:
    from ..backtesting.reconciliation import reconcile

    with session_scope() as s:
        res = reconcile(s)
    if res.get("shadow_settled"):
        run_in_background(job_update_performance)
    return res


def job_reconcile() -> dict:
    return run_job("reconcile", _reconcile, state_key="last_reconciliation")


def _shadow_report() -> dict:
    from ..validation.shadow import daily_report
    from ..validation.superbet import evidence_report

    with session_scope() as s:
        rep = daily_report(s, persist=True)
        # SUPERBET SHADOW REPORT (§48): evidência própria da Superbet, persistida com o diário
        ev = evidence_report(s, persist=True, export=True)
        rep["superbet_evidence"] = {"events": ev["coverage"].get("events"), "shadow_settled": ev["shadow_panel"]["total"]["settled"], "clv_n": ev["clv"].get("n_with_closing")}
        return rep


def job_shadow_report() -> dict:
    return run_job("shadow_report", _shadow_report, state_key="last_shadow_report")


def _drift() -> dict:
    from ..validation.drift import check_drift

    with session_scope() as s:
        return check_drift(s, persist=True)


def job_drift() -> dict:
    return run_job("drift", _drift, state_key="last_drift_check")


def mark_orphan_runs() -> int:
    """Execuções deixadas em `running` por um processo anterior (crash, kill, VM suspensa)
    são marcadas `interrupted` no boot — nunca ficam eternamente 'rodando'."""
    now = datetime.utcnow()
    try:
        with session_scope() as s:
            rows = s.execute(select(JobRun).where(JobRun.status == "running")).scalars().all()
            for r in rows:
                r.status = "interrupted"
                r.finished_at = now
                r.errors = ["processo reiniciado antes do fim da execução"]
            return len(rows)
    except Exception as exc:  # noqa: BLE001
        log.warning("não foi possível marcar execuções órfãs: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

_scheduler: BackgroundScheduler | None = None


def start() -> BackgroundScheduler | None:
    global _scheduler
    if not settings.scheduler_enabled or _scheduler is not None:
        return _scheduler
    orphans = mark_orphan_runs()
    if orphans:
        log.warning("%d execução(ões) órfã(s) marcadas como interrompidas", orphans)
    sched = BackgroundScheduler(timezone="UTC")
    # misfire_grace_time=None: um job perdido (VM suspensa, processo congelado) roda assim
    # que o scheduler acorda, uma única vez (coalesce). Antes, um `settle` perdido só voltava
    # na próxima hora cheia — e nada registrava a lacuna.
    common = {"max_instances": 1, "coalesce": True, "misfire_grace_time": None}
    sched.add_job(job_sync_events, "interval", minutes=settings.events_refresh_min, id="events", **common)
    sched.add_job(job_sync_odds, "interval", minutes=settings.odds_refresh_min, id="odds", **common)
    sched.add_job(job_sync_history, "interval", hours=settings.history_refresh_hours, id="history", **common)
    sched.add_job(job_settle, "interval", minutes=60, id="settle", **common)
    sched.add_job(job_refresh_radar, "interval", minutes=20, id="radar", **common)
    sched.add_job(job_closing_lines, "interval", minutes=10, id="closing_lines", **common)
    sched.add_job(job_update_performance, "interval", hours=6, id="performance", **common)
    sched.add_job(job_update_calibration, "interval", hours=6, id="calibration", **common)
    sched.add_job(job_ensemble_weights, "interval", hours=24, id="ensemble_weights", **common)
    sched.add_job(job_cache_cleanup, "interval", hours=24, id="cache_cleanup", **common)
    sched.add_job(job_live_poll, "interval", seconds=max(20, settings.live_poll_seconds), id="live_poll", **common)
    sched.add_job(job_reconcile, "interval", hours=6, id="reconcile", next_run_time=datetime.utcnow() + timedelta(seconds=90), **common)
    sched.add_job(job_shadow_report, "interval", hours=24, id="shadow_report", next_run_time=datetime.utcnow() + timedelta(minutes=5), **common)
    sched.add_job(job_drift, "interval", hours=24, id="drift", next_run_time=datetime.utcnow() + timedelta(minutes=6), **common)
    sched.start()
    _scheduler = sched
    return sched


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def run_in_background(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def jobs_overview() -> list[dict]:
    """Próximas execuções por job (para a tela Sistema → Jobs)."""
    out = []
    if _scheduler is None:
        return out
    for j in _scheduler.get_jobs():
        out.append({"id": j.id, "label": JOB_LABELS.get(j.func.__name__.replace("job_", "").replace("refresh_", ""), j.id), "next_run_at": j.next_run_time.isoformat() if j.next_run_time else None, "trigger": str(j.trigger)})
    return out
