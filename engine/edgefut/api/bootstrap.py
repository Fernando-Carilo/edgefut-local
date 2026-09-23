"""Primeiro boot: database, diretórios, datasets iniciais, validação de providers, eventos."""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from ..core.config import settings
from ..core.paths import get_paths
from ..db.migrations import run_migrations
from ..db.session import get_engine, session_scope
from ..providers import SourceError, get_http_client
from ..providers.resolver import SourceResolver
from ..providers.superbet import SuperbetProvider
from ..scheduler import jobs

log = logging.getLogger(__name__)

_status = {"running": False, "done": False, "steps": [], "started_at": None, "finished_at": None, "error": None}
_lock = threading.Lock()


def status() -> dict:
    return dict(_status)


def _step(name: str, fn) -> None:
    entry = {"name": name, "status": "running", "detail": None, "at": datetime.utcnow().isoformat()}
    _status["steps"].append(entry)
    try:
        detail = fn()
        entry["status"] = "ok"
        entry["detail"] = detail
    except Exception as exc:  # noqa: BLE001
        entry["status"] = "error"
        entry["detail"] = str(exc)
        log.warning("bootstrap step %s: %s", name, exc)


def run(minimal: bool = False) -> None:
    with _lock:
        if _status["running"]:
            return
        _status.update({"running": True, "done": False, "steps": [], "started_at": datetime.utcnow(), "finished_at": None, "error": None})
    try:
        _step("Criar diretórios", lambda: str(get_paths().ensure().root))
        _step("Criar/migrar database", lambda: f"schema v{run_migrations(get_engine())}")

        def validate_superbet():
            if not settings.superbet_enabled:
                return "desativado"
            evs, res = SuperbetProvider().fetch_events(hours_ahead=48)
            return f"{len(evs)} eventos ({res.status})"

        _step("Validar provider Superbet", validate_superbet)

        resolver = SourceResolver()

        def dataset(code):
            def _f():
                a = resolver.ensure_dataset(code)
                if a.status in ("unavailable", "error"):
                    raise SourceError(a.detail or a.status)
                return a.status

            return _f

        _step("Baixar resultados internacionais", dataset("INTL"))
        leagues = settings.bootstrap_leagues[:3] if minimal else settings.bootstrap_leagues
        for code in leagues:
            _step(f"Baixar football-data {code}", dataset(code))

        def load_events():
            with session_scope() as s:
                from ..collectors import SuperbetSync

                r = SuperbetSync().sync_events(s)
            return f"{r.get('events', 0)} eventos"

        _step("Carregar próximos eventos", load_events)
        _step("Iniciar scheduler", lambda: "ok" if jobs.start() else "desativado")
        _status["done"] = True
    except Exception as exc:  # noqa: BLE001
        _status["error"] = str(exc)
    finally:
        _status["running"] = False
        _status["finished_at"] = datetime.utcnow()
        get_http_client()  # garante cliente inicializado
    jobs.run_in_background(jobs.job_refresh_radar)


def run_in_background(minimal: bool = False) -> None:
    threading.Thread(target=run, args=(minimal,), daemon=True).start()
