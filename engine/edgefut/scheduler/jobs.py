"""Scheduler local (APScheduler): eventos 15 min, odds próximas 5 min, histórico diário, settlement."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from ..analysis.pipeline import analyze_event, invalidate_cache
from ..backtesting.performance import settle_pending
from ..collectors import SuperbetSync
from ..core.config import settings
from ..db.models import Competition, Event
from ..db.session import session_scope
from ..providers.resolver import SourceResolver

log = logging.getLogger(__name__)

_state = {
    "last_events_sync": None,
    "last_odds_sync": None,
    "last_history_sync": None,
    "last_settlement": None,
    "last_radar_refresh": None,
    "radar_running": False,
    "errors": [],
}
_lock = threading.Lock()


def state() -> dict:
    return dict(_state)


def _record_error(job: str, exc: Exception) -> None:
    _state["errors"] = ([f"{datetime.utcnow().isoformat()} {job}: {exc}"] + _state["errors"])[:20]
    log.exception("job %s falhou", job)


def job_sync_events() -> dict:
    try:
        with session_scope() as s:
            res = SuperbetSync().sync_events(s)
        _state["last_events_sync"] = datetime.utcnow()
        return res
    except Exception as exc:  # noqa: BLE001
        _record_error("sync_events", exc)
        return {"ok": False, "error": str(exc)}


def job_sync_odds(hours: int = 48, limit: int = 60) -> dict:
    try:
        n = 0
        with session_scope() as s:
            now = datetime.utcnow()
            ids = s.execute(
                select(Event.id).where(Event.kickoff_utc > now - timedelta(hours=2), Event.kickoff_utc < now + timedelta(hours=hours))
                .order_by(Event.kickoff_utc.asc()).limit(limit)
            ).scalars().all()
            sync = SuperbetSync()
            for eid in ids:
                r = sync.sync_odds(s, eid)
                if r.get("blocked"):
                    break
                n += 1
        _state["last_odds_sync"] = datetime.utcnow()
        return {"ok": True, "events": n}
    except Exception as exc:  # noqa: BLE001
        _record_error("sync_odds", exc)
        return {"ok": False, "error": str(exc)}


def job_sync_history(force: bool = False) -> dict:
    try:
        resolver = SourceResolver()
        out = []
        for code in ["INTL", *settings.bootstrap_leagues]:
            out.append((code, resolver.ensure_dataset(code, force=force).status))
        invalidate_cache()
        _state["last_history_sync"] = datetime.utcnow()
        return {"ok": True, "datasets": out}
    except Exception as exc:  # noqa: BLE001
        _record_error("sync_history", exc)
        return {"ok": False, "error": str(exc)}


def job_settle() -> dict:
    try:
        with session_scope() as s:
            res = settle_pending(s)
        _state["last_settlement"] = datetime.utcnow()
        return res
    except Exception as exc:  # noqa: BLE001
        _record_error("settle", exc)
        return {"ok": False, "error": str(exc)}


def job_refresh_radar(hours: int = 48, limit: int = 80) -> dict:
    """Analisa eventos de competições suportadas para alimentar o Radar."""
    if _state["radar_running"]:
        return {"ok": True, "skipped": True}
    _state["radar_running"] = True
    analyzed = 0
    try:
        with session_scope() as s:
            now = datetime.utcnow()
            rows = s.execute(
                select(Event).join(Competition, Competition.id == Event.competition_id)
                .where(Event.kickoff_utc > now, Event.kickoff_utc < now + timedelta(hours=hours))
                .where((Competition.dataset_code.is_not(None)) | (Competition.is_national_teams.is_(True)))
                .order_by(Event.kickoff_utc.asc()).limit(limit)
            ).scalars().all()
            ids = [r.id for r in rows]
        for eid in ids:
            try:
                with session_scope() as s:
                    analyze_event(s, eid, use_cache=True)
                analyzed += 1
            except Exception as exc:  # noqa: BLE001
                log.info("radar: evento %s falhou: %s", eid, exc)
        _state["last_radar_refresh"] = datetime.utcnow()
        return {"ok": True, "analyzed": analyzed}
    except Exception as exc:  # noqa: BLE001
        _record_error("radar", exc)
        return {"ok": False, "error": str(exc)}
    finally:
        _state["radar_running"] = False


_scheduler: BackgroundScheduler | None = None


def start() -> BackgroundScheduler | None:
    global _scheduler
    if not settings.scheduler_enabled or _scheduler is not None:
        return _scheduler
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(job_sync_events, "interval", minutes=settings.events_refresh_min, id="events", max_instances=1, coalesce=True)
    sched.add_job(job_sync_odds, "interval", minutes=settings.odds_refresh_min, id="odds", max_instances=1, coalesce=True)
    sched.add_job(job_sync_history, "interval", hours=settings.history_refresh_hours, id="history", max_instances=1, coalesce=True)
    sched.add_job(job_settle, "interval", minutes=60, id="settle", max_instances=1, coalesce=True)
    sched.add_job(job_refresh_radar, "interval", minutes=20, id="radar", max_instances=1, coalesce=True)
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
