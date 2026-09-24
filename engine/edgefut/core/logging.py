"""Logging estruturado local.

- stderr: legível (`ts level logger [cid scope] msg`)
- arquivo `data/logs/engine.jsonl`: uma linha JSON por registro (ts, level, logger,
  msg, correlation_id, scope, exc), rotacionado (5 MB × 5).
- Nunca registra credenciais: não há credenciais no sistema. URLs de fontes públicas
  são registradas em `source_log`, não aqui.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from .config import settings
from .context import current_correlation_id, current_scope
from .paths import get_paths


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = current_correlation_id() or "-"
        record.scope = current_scope() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "cid": getattr(record, "correlation_id", "-"),
            "scope": getattr(record, "scope", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key in ("event_id", "job", "provider", "duration_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(settings.log_level.upper())
    ctx = ContextFilter()

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(correlation_id)s %(scope)s]: %(message)s"))
    stream.addFilter(ctx)
    root.addHandler(stream)

    try:
        fh = RotatingFileHandler(get_paths().logs / "engine.jsonl", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(JsonFormatter())
        fh.addFilter(ctx)
        root.addHandler(fh)
    except OSError:  # pragma: no cover
        pass

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").addFilter(ctx)
