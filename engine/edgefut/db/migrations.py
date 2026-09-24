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


def _v3_settlement_governance(conn: Connection) -> None:
    for col, ddl in (
        ("settlement_status", "VARCHAR(24)"),
        ("settlement_attempts", "INTEGER DEFAULT 0"),
        ("settlement_error", "VARCHAR(300)"),
        ("settlement_checked_at", "DATETIME"),
    ):
        _add_column(conn, "event", col, ddl)
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_event_settlement_status ON event (settlement_status)"))
    _add_column(conn, "model_registry", "role", "VARCHAR(16)")
    # eventos já liquidados antes desta versão
    conn.execute(text("UPDATE event SET settlement_status = 'SETTLED' WHERE settlement_status IS NULL AND home_score IS NOT NULL AND away_score IS NOT NULL"))


def _v4_shadow_market_aware(conn: Connection) -> None:
    """Iteração 4: colunas de previsão market-aware no shadow (preenchidas só na criação; linhas antigas
    ficam NULL — nunca são reconstruídas a posteriori)."""
    for col, ddl in (
        ("hybrid_prob", "FLOAT"),
        ("residual_edge_pp", "FLOAT"),
        ("required_edge_pp", "FLOAT"),
        ("adjusted_edge_pp", "FLOAT"),
        ("minutes_to_kickoff", "INTEGER"),
    ):
        _add_column(conn, "shadow_prediction", col, ddl)


MIGRATIONS: list[tuple[int, list[str | Callable[[Connection], None]]]] = [
    # (versão, [SQL ou callable...]) — adicionar novas entradas ao final, nunca editar as antigas.
    (1, []),
    (2, [_v2_identity_live_closing]),
    (3, [_v3_settlement_governance]),
    (4, [_v4_shadow_market_aware]),
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
