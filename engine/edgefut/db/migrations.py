"""Migrations versionadas e idempotentes.

`create_all` cria o schema inicial; passos numerados aplicam alterações
incrementais. A versão atual fica em `schema_version`.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .models import Base

log = logging.getLogger(__name__)

MIGRATIONS: list[tuple[int, list[str]]] = [
    # (versão, [SQL...]) — adicionar novas entradas ao final, nunca editar as antigas.
    (1, []),
]


def run_migrations(engine: Engine) -> int:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        )
        row = conn.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
        current = int(row or 0)
        for version, statements in MIGRATIONS:
            if version <= current:
                continue
            for stmt in statements:
                conn.execute(text(stmt))
            conn.execute(text("INSERT INTO schema_version(version) VALUES (:v)"), {"v": version})
            current = version
            log.info("migration aplicada: v%s", version)
    return current
