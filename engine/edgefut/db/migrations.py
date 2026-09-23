"""Migrations versionadas e idempotentes.

`create_all` cria tabelas novas; passos numerados aplicam alterações incrementais
em tabelas existentes (SQLite não tem `ADD COLUMN IF NOT EXISTS`, então cada
coluna é verificada via `PRAGMA table_info`). A versão atual fica em
`schema_version`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .models import Base

log = logging.getLogger(__name__)


def _add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    cols = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))}
    if column not in cols:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _v2_identity_live_closing(conn: Connection) -> None:
    for col, ddl in (
        ("canonical_event_id", "VARCHAR(200)"),
        ("home_canonical", "VARCHAR(120)"),
        ("away_canonical", "VARCHAR(120)"),
        ("duplicate_of", "INTEGER"),
        ("live_status", "VARCHAR(24)"),
        ("live_minute", "INTEGER"),
        ("live_home_score", "INTEGER"),
        ("live_away_score", "INTEGER"),
        ("live_collected_at", "DATETIME"),
    ):
        _add_column(conn, "event", col, ddl)
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_event_canonical_event_id ON event (canonical_event_id)"))
    _add_column(conn, "prediction_snapshot", "closing_odds", "JSON")


MIGRATIONS: list[tuple[int, list[str | Callable[[Connection], None]]]] = [
    # (versão, [SQL ou callable...]) — adicionar novas entradas ao final, nunca editar as antigas.
    (1, []),
    (2, [_v2_identity_live_closing]),
]


def run_migrations(engine: Engine) -> int:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        )
        row = conn.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
        current = int(row or 0)
        for version, steps in MIGRATIONS:
            if version <= current:
                continue
            for step in steps:
                if callable(step):
                    step(conn)
                else:
                    conn.execute(text(step))
            conn.execute(text("INSERT INTO schema_version(version) VALUES (:v)"), {"v": version})
            current = version
            log.info("migration aplicada: v%s", version)
    return current
