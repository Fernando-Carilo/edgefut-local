"""§49–§53 — backup diário com retenção 7/4/3, restore com cópia de segurança, export CSV/Parquet
(raw / normalized / derived) e painel de armazenamento.

Regras: o raw nunca é apagado automaticamente (não existe política de retenção do raw nesta iteração;
só se mede o crescimento). Backups são cópias consistentes via API `sqlite3.Connection.backup`
(funciona com WAL e com o motor a correr) acompanhadas de um manifesto com contagens e versões.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..core import versions
from ..core.paths import get_paths
from ..db.models import (
    CollectorGap,
    DataQuarantine,
    Event,
    OddsSnapshot,
    RawSuperbetSnapshot,
    ShadowPrediction,
    SuperbetNormalized,
    SuperbetSettlement,
)
from . import DATASET_VERSION, NORMALIZER_VERSION, SETTLEMENT_VERSION, SOURCE_VERSION

log = logging.getLogger(__name__)

RETENTION = {"daily": 7, "weekly": 4, "monthly": 3}
BACKUP_PREFIX = "edgefut-"
BACKUP_SUFFIX = ".sqlite3"


def backups_dir() -> Path:
    p = get_paths().root / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def exports_dir() -> Path:
    p = get_paths().root / "exports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in ("event", "odds_snapshot", "raw_superbet_snapshot", "superbet_normalized_v1", "superbet_settlement_v1", "shadow_prediction", "data_quarantine", "experiment_registry"):
        try:
            out[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.Error:
            out[table] = -1
    return out


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------
def create_backup(*, reason: str = "scheduled", now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    src = get_paths().sqlite
    if not src.exists():
        return {"ok": False, "error": "base de dados inexistente"}
    stamp = now.strftime("%Y%m%d-%H%M%S")
    dest = backups_dir() / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    t0 = time.perf_counter()
    # backup online consistente: a API copia página a página respeitando o WAL, sem parar o motor
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30) as s_conn, sqlite3.connect(dest) as d_conn:
        s_conn.backup(d_conn, pages=1024)
    with sqlite3.connect(dest) as d_conn:
        integrity = d_conn.execute("PRAGMA integrity_check").fetchone()[0]
        counts = _counts(d_conn)
        schema_version = d_conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    manifest = {
        "file": dest.name, "created_at": now.isoformat(), "reason": reason, "bytes": dest.stat().st_size, "sha256": _sha256(dest),
        "integrity": integrity, "schema_version": schema_version, "counts": counts,
        "versions": {"app": versions.APP_VERSION, "source": SOURCE_VERSION, "normalizer": NORMALIZER_VERSION, "settlement": SETTLEMENT_VERSION, "dataset": DATASET_VERSION},
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }
    dest.with_suffix(".json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    if integrity != "ok":
        log.error("backup %s com integrity_check=%s", dest.name, integrity)
    removed = apply_retention(now=now)
    return {"ok": integrity == "ok", **manifest, "removed": removed}


def _parse_stamp(name: str) -> datetime | None:
    try:
        return datetime.strptime(name[len(BACKUP_PREFIX):-len(BACKUP_SUFFIX)], "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def list_backups() -> list[dict]:
    out = []
    for f in sorted(backups_dir().glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"), reverse=True):
        man_path = f.with_suffix(".json")
        man = {}
        if man_path.exists():
            try:
                man = json.loads(man_path.read_text(encoding="utf-8"))
            except ValueError:
                man = {}
        out.append({"file": f.name, "created_at": man.get("created_at") or (_parse_stamp(f.name) or datetime.utcfromtimestamp(f.stat().st_mtime)).isoformat(),
                    "bytes": f.stat().st_size, "integrity": man.get("integrity"), "schema_version": man.get("schema_version"), "counts": man.get("counts"), "reason": man.get("reason"), "tier": man.get("tier")})
    return out


def apply_retention(*, now: datetime | None = None, retention: dict[str, int] | None = None) -> list[str]:
    """Mantém 7 diários (um por dia), 4 semanais (um por semana ISO) e 3 mensais (um por mês); o mais
    recente de cada balde vence. Só apaga ficheiros de backup — nunca a base principal nem o raw."""
    now = now or datetime.utcnow()
    ret = retention or RETENTION
    files = [(f, _parse_stamp(f.name)) for f in backups_dir().glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")]
    files = [(f, d) for f, d in files if d is not None]
    files.sort(key=lambda x: x[1], reverse=True)
    keep: dict[Path, str] = {}

    def pick(bucket_of, limit: int, tier: str) -> None:
        seen: list = []
        for f, d in files:
            b = bucket_of(d)
            if b in seen:
                continue
            if len(seen) >= limit:
                break
            seen.append(b)
            keep.setdefault(f, tier)

    pick(lambda d: d.date(), ret["daily"], "daily")
    pick(lambda d: d.isocalendar()[:2], ret["weekly"], "weekly")
    pick(lambda d: (d.year, d.month), ret["monthly"], "monthly")
    removed = []
    for f, _ in files:
        if f in keep:
            man = f.with_suffix(".json")
            if man.exists():
                try:
                    m = json.loads(man.read_text(encoding="utf-8"))
                    if m.get("tier") != keep[f]:
                        m["tier"] = keep[f]
                        man.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
                except ValueError:
                    pass
            continue
        try:
            f.unlink()
            f.with_suffix(".json").unlink(missing_ok=True)
            removed.append(f.name)
        except OSError as exc:
            log.warning("retenção: não removeu %s: %s", f.name, exc)
    return removed


def restore_backup(file_name: str, *, now: datetime | None = None) -> dict:
    """Substitui a base atual pelo backup indicado. Antes disso guarda uma cópia de segurança da base atual
    (`pre-restore-*.sqlite3`) e verifica a integridade do backup. Requer que quem chama tenha parado o
    scheduler e descartado as conexões (ver `api/routers/flywheel.py`)."""
    now = now or datetime.utcnow()
    src = backups_dir() / Path(file_name).name
    if not src.exists() or not src.name.startswith(BACKUP_PREFIX):
        return {"ok": False, "error": "backup não encontrado"}
    try:
        with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as c:
            integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as exc:
        integrity = f"unreadable ({exc})"
    if integrity != "ok":
        return {"ok": False, "error": f"backup com integrity_check={integrity}; restore recusado"}
    db = get_paths().sqlite
    safety = backups_dir() / f"pre-restore-{now.strftime('%Y%m%d-%H%M%S')}{BACKUP_SUFFIX}"
    if db.exists():
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30) as s_conn, sqlite3.connect(safety) as d_conn:
            s_conn.backup(d_conn)
    for suffix in ("-wal", "-shm"):
        side = Path(str(db) + suffix)
        if side.exists():
            side.unlink()
    shutil.copy2(src, db)
    return {"ok": True, "restored_from": src.name, "safety_copy": safety.name if db.exists() else None, "restored_at": now.isoformat(), "note": "Reinicie o motor para garantir que todas as conexões usam a base restaurada."}


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def _write(df: pd.DataFrame, base: Path, fmt: str) -> dict:
    if fmt == "csv":
        p = base.with_suffix(".csv")
        df.to_csv(p, index=False)
    else:
        p = base.with_suffix(".parquet")
        df.to_parquet(p, index=False)
    return {"file": p.name, "rows": int(len(df)), "bytes": p.stat().st_size}


def export_dataset(session: Session, *, layers: tuple[str, ...] = ("raw", "normalized", "derived"), fmt: str = "parquet", now: datetime | None = None) -> dict:
    """Exporta para `data/exports/<timestamp>/`. `raw` = índice dos snapshots (metadados; o payload comprimido
    fica na BD e pode ser lido por `raw_payload`), `normalized` = odds canónicas + settlement,
    `derived` = timelines por seleção (opening/closing/fair@T)."""
    from .research import derived_frame

    now = now or datetime.utcnow()
    fmt = "csv" if fmt == "csv" else "parquet"
    out_dir = exports_dir() / now.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict] = {}
    if "raw" in layers:
        rows = session.execute(select(
            RawSuperbetSnapshot.id, RawSuperbetSnapshot.event_id, RawSuperbetSnapshot.fetched_at, RawSuperbetSnapshot.kickoff_utc, RawSuperbetSnapshot.minutes_to_kickoff,
            RawSuperbetSnapshot.event_state, RawSuperbetSnapshot.snapshot_target, RawSuperbetSnapshot.payload_hash, RawSuperbetSnapshot.payload_ref_id, RawSuperbetSnapshot.payload_bytes,
            RawSuperbetSnapshot.raw_bytes, RawSuperbetSnapshot.odds_total, RawSuperbetSnapshot.odds_mapped, RawSuperbetSnapshot.odds_unknown, RawSuperbetSnapshot.odds_out_of_scope,
            RawSuperbetSnapshot.parse_failures, RawSuperbetSnapshot.http_status, RawSuperbetSnapshot.source_version, RawSuperbetSnapshot.source_url,
        )).all()
        files["raw_index"] = _write(pd.DataFrame(rows, columns=["id", "event_id", "fetched_at", "kickoff_utc", "minutes_to_kickoff", "event_state", "snapshot_target", "payload_hash", "payload_ref_id", "payload_bytes", "raw_bytes", "odds_total", "odds_mapped", "odds_unknown", "odds_out_of_scope", "parse_failures", "http_status", "source_version", "source_url"]), out_dir / "raw_superbet_snapshot_index", fmt)
    if "normalized" in layers:
        n = pd.read_sql(select(SuperbetNormalized), session.connection())
        files["normalized"] = _write(n, out_dir / "superbet_normalized_v1", fmt)
        st = pd.read_sql(select(SuperbetSettlement), session.connection())
        files["settlement"] = _write(st, out_dir / "superbet_settlement_v1", fmt)
    if "derived" in layers:
        fr = derived_frame(session, since=None)
        tl = fr["timelines"]
        if not tl.empty:
            tl = tl.copy()
            for c in tl.columns:
                if tl[c].dtype == object and tl[c].map(lambda v: isinstance(v, (dict, list))).any():
                    tl[c] = tl[c].map(lambda v: json.dumps(v, default=str) if isinstance(v, (dict, list)) else v)
        files["derived_timelines"] = _write(tl, out_dir / "superbet_selection_timelines", fmt)
    manifest = {"created_at": now.isoformat(), "format": fmt, "layers": list(layers), "files": files,
                "versions": {"app": versions.APP_VERSION, "source": SOURCE_VERSION, "normalizer": NORMALIZER_VERSION, "settlement": SETTLEMENT_VERSION, "dataset": DATASET_VERSION},
                "note": "Dados reais recolhidos localmente da oferta pública da Superbet. Sem odds históricas fabricadas."}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "dir": str(out_dir), **manifest}


def list_exports() -> list[dict]:
    out = []
    for d in sorted(exports_dir().iterdir(), reverse=True):
        if not d.is_dir():
            continue
        man = d / "manifest.json"
        m = {}
        if man.exists():
            try:
                m = json.loads(man.read_text(encoding="utf-8"))
            except ValueError:
                m = {}
        size = sum(f.stat().st_size for f in d.glob("*") if f.is_file())
        out.append({"dir": d.name, "path": str(d), "bytes": size, "created_at": m.get("created_at"), "format": m.get("format"), "files": m.get("files")})
    return out[:50]


# ---------------------------------------------------------------------------
# storage dashboard
# ---------------------------------------------------------------------------
def _dir_size(p: Path, pattern: str = "*") -> int:
    if not p.exists():
        return 0
    return sum(f.stat().st_size for f in p.rglob(pattern) if f.is_file())


def storage_dashboard(session: Session, *, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    paths = get_paths()
    db = paths.sqlite
    db_bytes = db.stat().st_size if db.exists() else 0
    wal = Path(str(db) + "-wal")
    wal_bytes = wal.stat().st_size if wal.exists() else 0
    raw_bytes, raw_uncompressed, raw_n = session.execute(select(func.coalesce(func.sum(RawSuperbetSnapshot.payload_bytes), 0), func.coalesce(func.sum(RawSuperbetSnapshot.raw_bytes), 0), func.count())).one()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    raw_24h = session.execute(select(func.coalesce(func.sum(RawSuperbetSnapshot.payload_bytes), 0), func.count()).where(RawSuperbetSnapshot.fetched_at >= day_ago)).one()
    raw_7d = session.execute(select(func.coalesce(func.sum(RawSuperbetSnapshot.payload_bytes), 0), func.count()).where(RawSuperbetSnapshot.fetched_at >= week_ago)).one()
    first_raw = session.execute(select(func.min(RawSuperbetSnapshot.fetched_at))).scalar_one()
    days_active = max(1.0, (now - first_raw).total_seconds() / 86400) if first_raw else None
    counts = {
        "event": session.execute(select(func.count()).select_from(Event)).scalar_one(),
        "odds_snapshot": session.execute(select(func.count()).select_from(OddsSnapshot)).scalar_one(),
        "raw_superbet_snapshot": int(raw_n), "superbet_normalized_v1": session.execute(select(func.count()).select_from(SuperbetNormalized)).scalar_one(),
        "superbet_settlement_v1": session.execute(select(func.count()).select_from(SuperbetSettlement)).scalar_one(),
        "shadow_prediction": session.execute(select(func.count()).select_from(ShadowPrediction)).scalar_one(),
        "data_quarantine": session.execute(select(func.count()).select_from(DataQuarantine)).scalar_one(),
        "collector_gap": session.execute(select(func.count()).select_from(CollectorGap)).scalar_one(),
    }
    try:
        page_size = session.execute(text("PRAGMA page_size")).scalar_one()
        page_count = session.execute(text("PRAGMA page_count")).scalar_one()
        freelist = session.execute(text("PRAGMA freelist_count")).scalar_one()
        sqlite_internal = {"page_size": page_size, "page_count": page_count, "freelist_pages": freelist, "free_bytes": int(page_size) * int(freelist)}
    except Exception:  # noqa: BLE001
        sqlite_internal = {}
    per_day_bytes = int(raw_7d[0]) / 7 if raw_7d[1] else (int(raw_24h[0]) if raw_24h[1] else 0)
    backups = list_backups()
    return {
        "generated_at": now,
        "sqlite": {"bytes": db_bytes, "wal_bytes": wal_bytes, "path": str(db), **sqlite_internal},
        "raw_payloads": {"snapshots": int(raw_n), "compressed_bytes": int(raw_bytes), "uncompressed_bytes": int(raw_uncompressed), "compression_ratio": round(int(raw_uncompressed) / int(raw_bytes), 1) if raw_bytes else None,
                         "last_24h_bytes": int(raw_24h[0]), "last_24h_snapshots": int(raw_24h[1]), "last_7d_bytes": int(raw_7d[0]), "first_snapshot_at": first_raw, "days_active": round(days_active, 1) if days_active else None},
        "growth": {"raw_bytes_per_day": int(per_day_bytes), "raw_snapshots_per_day": round(int(raw_7d[1]) / 7, 1) if raw_7d[1] else int(raw_24h[1]),
                   "projection_30d_bytes": int(per_day_bytes * 30), "projection_365d_bytes": int(per_day_bytes * 365)},
        "parquet": {"bytes": _dir_size(paths.processed, "*.parquet"), "path": str(paths.processed)},
        "cache": {"bytes": _dir_size(paths.cache), "files": sum(1 for _ in paths.cache.glob("*")) if paths.cache.exists() else 0},
        "backups": {"bytes": _dir_size(backups_dir()), "count": len(backups), "latest": backups[0] if backups else None, "retention": RETENTION},
        "exports": {"bytes": _dir_size(exports_dir()), "count": len(list_exports())},
        "logs": {"bytes": _dir_size(paths.logs)},
        "counts": counts,
        "raw_retention_policy": "NONE — o raw nunca é apagado automaticamente (§52). Só backups seguem retenção 7/4/3.",
    }


__all__ = ["create_backup", "list_backups", "apply_retention", "restore_backup", "export_dataset", "list_exports", "storage_dashboard", "backups_dir", "exports_dir", "RETENTION"]
