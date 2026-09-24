"""Superbet Collector V2 — ingestão append-only (§4–§9, §48, §52–§55).

`ingest_event_payload` recebe o payload **fresco** de `/events/{id}` (respostas em cache nunca
entram: não são observações novas) e:

1. corre verificações de integridade → `data_quarantine` em vez de descartar;
2. grava a linha RAW (payload zlib quando o hash mudou; confirmação quando é idêntico);
3. atribui o alvo de snapshot (T-48h … T-5m) que esta coleta cobre pela primeira vez;
4. canonicaliza as odds e grava na camada normalizada as seleções cujo preço mudou — ou todas,
   quando a coleta cobre um alvo — com implícita, fair e overround do mesmo instante;
5. atualiza o registo de mapeamento (MAPPED / OUT_OF_SCOPE / UNKNOWN) e regista problemas de schema.

Nada aqui fabrica observações: se o app esteve parado, o alvo fica por cobrir e a lacuna é
registada em `collector_gap`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (
    CollectorGap,
    DataQuarantine,
    Event,
    MarketMappingRegistry,
    RawSuperbetSnapshot,
    SuperbetNormalized,
)
from . import NORMALIZER_VERSION, SOURCE_VERSION
from .markets import CanonicalOdd, NormalizeIssue, classify_market, normalize

log = logging.getLogger(__name__)

# (nome, minutos-alvo, janela [min, max] em minutos até o kickoff) — janelas não se sobrepõem
SNAPSHOT_TARGETS: tuple[tuple[str, int, tuple[float, float]], ...] = (
    ("T-48h", 2880, (2520, 3240)),
    ("T-24h", 1440, (1260, 1620)),
    ("T-12h", 720, (630, 810)),
    ("T-6h", 360, (300, 420)),
    ("T-3h", 180, (150, 210)),
    ("T-2h", 120, (105, 135)),
    ("T-1h", 60, (50, 70)),
    ("T-30m", 30, (22, 38)),
    ("T-15m", 15, (11, 19)),
    ("T-5m", 5, (2, 8)),
)
TARGET_NAMES = tuple(t[0] for t in SNAPSHOT_TARGETS) + ("LAST_PRE_KO",)
REQUIRED_EVENT_FIELDS = ("eventId", "matchName", "marketCount")
REQUIRED_ODD_FIELDS = ("marketId", "price", "name", "marketName")
MAX_QUARANTINE_PER_SNAPSHOT = 25
GAP_MIN_MINUTES = 20.0


def target_for_minutes(minutes: float | None) -> str | None:
    if minutes is None or (isinstance(minutes, float) and math.isnan(minutes)):
        return None
    for name, _, (lo, hi) in SNAPSHOT_TARGETS:
        if lo <= minutes < hi:
            return name
    return None


def event_state_from_payload(payload: dict, minutes_to_kickoff: float | None) -> str:
    meta = payload.get("metadata") or {}
    st = str(meta.get("status") or "").upper()
    if st == "FINISHED":
        return "finished"
    if st == "STARTED":
        return "live"
    if st == "NOT_STARTED" or minutes_to_kickoff is None or minutes_to_kickoff >= 0:
        return "prematch"
    return "unknown"


def _kickoff_from_payload(payload: dict) -> datetime | None:
    s = payload.get("utcDate") or payload.get("matchDate")
    if s:
        try:
            s = str(s).replace("Z", "+00:00").replace(" ", "T")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is not None:
                dt = dt.astimezone(UTC).replace(tzinfo=None)
            return dt
        except ValueError:
            return None
    ms = payload.get("unixDateMillis")
    if ms is not None:
        try:
            return datetime.utcfromtimestamp(int(ms) / 1000)
        except (TypeError, ValueError, OSError):
            return None
    return None


def _split_match_name(name: str) -> tuple[str, str]:
    for sep in ("·", " - ", " x ", " vs "):
        if sep in name:
            a, b = name.split(sep, 1)
            return a.strip(), b.strip()
    return name.strip(), ""


@dataclass
class IngestResult:
    ok: bool
    raw_id: int | None = None
    stored_payload: bool = False
    confirmation: bool = False
    snapshot_target: str | None = None
    event_state: str | None = None
    minutes_to_kickoff: float | None = None
    normalized_rows: int = 0
    odds_total: int = 0
    odds_mapped: int = 0
    odds_unknown: int = 0
    odds_out_of_scope: int = 0
    parse_failures: int = 0
    quarantined: int = 0
    schema_issues: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


def _quarantine(session: Session, *, reason: str, event_id: int | None, raw_id: int | None, payload_hash: str | None, detail: str, excerpt: dict | None = None) -> None:
    session.add(DataQuarantine(reason=reason, event_id=event_id, raw_snapshot_id=raw_id, payload_hash=payload_hash, payload_excerpt=excerpt, detail=detail[:400]))


def _schema_check(payload: dict) -> tuple[list[str], int, int]:
    """→ (issues, odds_total, parse_failures). Não altera nada; só observa."""
    issues = [f"missing:{f}" for f in REQUIRED_EVENT_FIELDS if f not in payload]
    if not (payload.get("utcDate") or payload.get("matchDate") or payload.get("unixDateMillis")):
        issues.append("missing:matchDate")
    odds = payload.get("odds")
    if odds is None:
        odds = []
    elif not isinstance(odds, list):
        issues.append("odds:not_a_list")
        odds = []
    failures = 0
    for o in odds:
        if not isinstance(o, dict) or any(f not in o for f in REQUIRED_ODD_FIELDS):
            failures += 1
    total = len(odds)
    if total >= 20 and failures / total > 0.10:
        issues.append(f"parse_rate_low:{1 - failures / total:.2f}")
    meta = payload.get("metadata")
    if meta is not None and not isinstance(meta, dict):
        issues.append("metadata:not_a_dict")
    return issues, total, failures


def _last_prices(session: Session, event_id: int) -> dict[tuple[str, str], float]:
    sub = (
        select(SuperbetNormalized.canonical_market_id, SuperbetNormalized.selection_id, func.max(SuperbetNormalized.id).label("mx"))
        .where(SuperbetNormalized.event_id == event_id)
        .group_by(SuperbetNormalized.canonical_market_id, SuperbetNormalized.selection_id)
        .subquery()
    )
    rows = session.execute(select(SuperbetNormalized.canonical_market_id, SuperbetNormalized.selection_id, SuperbetNormalized.odd).join(sub, SuperbetNormalized.id == sub.c.mx)).all()
    return {(m, s): float(o) for m, s, o in rows}


def _upsert_registry(session: Session, stats: dict[int, dict], now: datetime) -> None:
    if not stats:
        return
    existing = {r.superbet_market_id: r for r in session.execute(select(MarketMappingRegistry).where(MarketMappingRegistry.superbet_market_id.in_(list(stats)))).scalars()}
    for mid, st in stats.items():
        row = existing.get(mid)
        if row is None:
            row = MarketMappingRegistry(superbet_market_id=mid, market_name=st["name"], status=st["status"], market_category=st["category"], reason=st["reason"], first_seen_at=now, last_seen_at=now, occurrences=0, events_seen=0, sample_selections=[], specifier_keys=[])
            session.add(row)
        # status manual (MAPPED via correção) nunca é sobrescrito por classificação automática
        if row.status not in ("MAPPED",) or st["status"] == "MAPPED":
            row.status, row.market_category, row.reason = st["status"], st["category"], st["reason"]
        row.market_name = row.market_name or st["name"]
        row.last_seen_at = now
        row.occurrences = (row.occurrences or 0) + st["count"]
        row.events_seen = (row.events_seen or 0) + 1
        samples = list(row.sample_selections or [])
        for s in st["samples"]:
            if s not in samples and len(samples) < 8:
                samples.append(s)
        row.sample_selections = samples
        keys = set(row.specifier_keys or [])
        keys.update(st["specifiers"])
        row.specifier_keys = sorted(keys)


def ingest_event_payload(
    session: Session,
    *,
    event_id: int,
    payload: dict,
    fetched_at: datetime,
    source_url: str,
    http_status: int | None,
    now: datetime | None = None,
    source_version: str = SOURCE_VERSION,
) -> IngestResult:
    now = now or datetime.utcnow()
    res = IngestResult(ok=False)
    raw_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_hash = hashlib.sha256(raw_json).hexdigest()

    # ---- integridade antes de gravar -------------------------------------------------------
    if fetched_at > now + timedelta(minutes=5) or fetched_at < datetime(2020, 1, 1):
        _quarantine(session, reason="NEGATIVE_TIMESTAMP", event_id=event_id, raw_id=None, payload_hash=payload_hash, detail=f"fetched_at={fetched_at.isoformat()} fora do intervalo plausível (now={now.isoformat()})")
        res.quarantined, res.skipped_reason = 1, "NEGATIVE_TIMESTAMP"
        return res
    try:
        payload_event_id = int(payload.get("eventId"))
    except (TypeError, ValueError):
        payload_event_id = None
    if payload_event_id != event_id:
        _quarantine(session, reason="UNKNOWN_EVENT", event_id=event_id, raw_id=None, payload_hash=payload_hash, detail=f"payload.eventId={payload.get('eventId')!r} ≠ pedido {event_id}", excerpt={"eventId": payload.get("eventId"), "matchName": payload.get("matchName")})
        res.quarantined, res.skipped_reason = 1, "UNKNOWN_EVENT"
        return res
    dup = session.execute(select(RawSuperbetSnapshot.id).where(RawSuperbetSnapshot.event_id == event_id, RawSuperbetSnapshot.fetched_at == fetched_at)).scalar_one_or_none()
    if dup is not None:
        _quarantine(session, reason="DUPLICATE_SNAPSHOT", event_id=event_id, raw_id=dup, payload_hash=payload_hash, detail=f"já existe raw #{dup} para event {event_id} em {fetched_at.isoformat()}")
        res.quarantined, res.skipped_reason = 1, "DUPLICATE_SNAPSHOT"
        return res

    schema_issues, odds_total, parse_failures = _schema_check(payload)
    kickoff = _kickoff_from_payload(payload)
    ev_row = session.get(Event, event_id)
    kickoff_ref = kickoff or (ev_row.kickoff_utc if ev_row else None)
    kickoff_inconsistent = bool(kickoff and ev_row and ev_row.kickoff_utc and abs((kickoff - ev_row.kickoff_utc).total_seconds()) > 24 * 3600)
    ttk = (kickoff_ref - fetched_at).total_seconds() / 60.0 if kickoff_ref else None
    state = event_state_from_payload(payload, ttk)

    # ---- classificação/normalização em memória (a raw é gravada de uma vez: é append-only) ---
    home, away = _split_match_name(str(payload.get("matchName") or ""))
    canon: list[CanonicalOdd] = []
    registry: dict[int, dict] = {}
    issues: list[tuple[NormalizeIssue, dict]] = []
    seen_issue_keys: set[tuple] = set()
    if not kickoff_inconsistent:
        for o in payload.get("odds") or []:
            if not isinstance(o, dict) or "marketId" not in o:
                continue
            try:
                mid = int(o.get("marketId"))
            except (TypeError, ValueError):
                continue
            status, category, reason = classify_market(mid, o.get("marketName"))
            st = registry.setdefault(mid, {"name": o.get("marketName"), "status": status, "category": category, "reason": reason, "count": 0, "samples": [], "specifiers": set()})
            st["count"] += 1
            if len(st["samples"]) < 4 and o.get("name") not in st["samples"]:
                st["samples"].append(o.get("name"))
            st["specifiers"].update((o.get("specifiers") or {}).keys())
            if status == "UNKNOWN":
                res.odds_unknown += 1
                continue
            if status == "OUT_OF_SCOPE":
                res.odds_out_of_scope += 1
                continue
            r = normalize(o, home, away)
            if r is None:
                continue  # seleção bloqueada (price=1, status=block)
            if isinstance(r, NormalizeIssue):
                key = (r.kind, r.market_id)
                if key not in seen_issue_keys and len(issues) < MAX_QUARANTINE_PER_SNAPSHOT:
                    seen_issue_keys.add(key)
                    issues.append((r, {k: o.get(k) for k in ("marketId", "marketName", "name", "code", "price", "status", "specifiers")}))
                continue
            res.odds_mapped += 1
            canon.append(r)
    active = [c for c in canon if c.status == "active"]

    # ---- raw (append-only; nunca UPDATE) ---------------------------------------------------
    last = session.execute(select(RawSuperbetSnapshot).where(RawSuperbetSnapshot.event_id == event_id).order_by(RawSuperbetSnapshot.fetched_at.desc()).limit(1)).scalar_one_or_none()
    covered = set(session.execute(select(RawSuperbetSnapshot.snapshot_target).where(RawSuperbetSnapshot.event_id == event_id, RawSuperbetSnapshot.snapshot_target.is_not(None))).scalars().all())
    target = target_for_minutes(ttk) if state == "prematch" else None
    if target in covered:
        target = None
    confirmation = last is not None and last.payload_hash == payload_hash
    ref_id = (last.payload_ref_id or last.id) if confirmation else None
    blob = None if confirmation else zlib.compress(raw_json, 6)
    raw = RawSuperbetSnapshot(
        event_id=event_id, fetched_at=fetched_at, source_url=source_url[:300], http_status=http_status, source_version=source_version,
        payload_hash=payload_hash, payload=blob, payload_ref_id=ref_id, payload_bytes=len(blob) if blob else 0, raw_bytes=len(raw_json),
        kickoff_utc=kickoff_ref, minutes_to_kickoff=ttk, event_state=state, odds_total=odds_total, parse_failures=parse_failures,
        odds_mapped=res.odds_mapped, odds_unknown=res.odds_unknown, odds_out_of_scope=res.odds_out_of_scope,
        markets_present=sorted({c.market_category for c in active}), schema_issues=schema_issues or None, snapshot_target=target,
    )
    session.add(raw)
    session.flush()
    res.raw_id, res.stored_payload, res.confirmation, res.snapshot_target, res.event_state, res.minutes_to_kickoff = raw.id, blob is not None, confirmation, target, state, ttk
    res.odds_total, res.parse_failures, res.schema_issues = odds_total, parse_failures, schema_issues

    quarantined = 0
    if schema_issues:
        _quarantine(session, reason="SCHEMA_CHANGE", event_id=event_id, raw_id=raw.id, payload_hash=payload_hash, detail="; ".join(schema_issues), excerpt={"keys": sorted(payload.keys())[:40]})
        quarantined += 1
    if kickoff_inconsistent:
        _quarantine(session, reason="KICKOFF_INCONSISTENT", event_id=event_id, raw_id=raw.id, payload_hash=payload_hash, detail=f"payload kickoff {kickoff} vs evento {ev_row.kickoff_utc}")
        res.quarantined = quarantined + 1
        res.ok = True
        return res
    for r, excerpt in issues:
        _quarantine(session, reason=r.kind, event_id=event_id, raw_id=raw.id, payload_hash=payload_hash, detail=r.detail, excerpt=excerpt)
        quarantined += 1
    _upsert_registry(session, registry, now)
    res.quarantined = quarantined

    # ---- normalizada: fair/overround por mercado canónico completo (mesmo payload = mesmo instante) ----
    groups: dict[str, list[CanonicalOdd]] = {}
    for c in active:
        groups.setdefault(c.canonical_market_id, []).append(c)
    fair: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    for mk, items in groups.items():
        need = items[0].complete_n
        sels = {c.selection_id for c in items}
        if need and len(sels) == need and len(items) == need:
            s = sum(1.0 / c.odd for c in items)
            book_total = 2.0 if items[0].market_category == "DOUBLE_CHANCE" else 1.0
            over = s / book_total - 1.0
            for c in items:
                fair[(mk, c.selection_id)] = ((1.0 / c.odd) / s * book_total, over)
    last_prices = _last_prices(session, event_id)
    comp_id = ev_row.competition_id if ev_row else None
    comp_name = ev_row.competition_name if ev_row else None
    write_all = target is not None or not last_prices
    n = 0
    for c in active:
        key = (c.canonical_market_id, c.selection_id)
        if not write_all and last_prices.get(key) == c.odd:
            continue
        fp, ov = fair.get(key, (None, None))
        session.add(SuperbetNormalized(
            raw_snapshot_id=raw.id, event_id=event_id, competition_id=comp_id, competition_name=comp_name, market_category=c.market_category,
            canonical_market_id=c.canonical_market_id, selection_id=c.selection_id, selection_name=c.selection_name[:160], superbet_market_id=c.superbet_market_id,
            line=c.line, odd=c.odd, implied_prob=1.0 / c.odd, fair_prob=fp, overround=ov, fetched_at=fetched_at, kickoff_utc=kickoff_ref, minutes_to_kickoff=ttk,
            event_state=state, snapshot_target=target, identity_confidence=c.identity_confidence, normalizer_version=NORMALIZER_VERSION,
        ))
        n += 1
    res.normalized_rows = n
    res.ok = True
    session.flush()
    return res


# ---------------------------------------------------------------------------
# §48 — lacunas de coleta (downtime): registadas, nunca preenchidas
# ---------------------------------------------------------------------------
def record_gap_if_needed(session: Session, *, now: datetime | None = None, expected_cadence_min: float = 5.0, reason: str = "DOWNTIME") -> CollectorGap | None:
    """Compara a última coleta raw com `now`; se a distância exceder max(20 min, 3× cadência) e não houver
    lacuna já registada para o intervalo, grava `collector_gap`. Chamado no boot e a cada ciclo do coletor."""
    now = now or datetime.utcnow()
    last = session.execute(select(func.max(RawSuperbetSnapshot.fetched_at))).scalar_one()
    if last is None:
        return None
    minutes = (now - last).total_seconds() / 60.0
    threshold = max(GAP_MIN_MINUTES, 3 * expected_cadence_min)
    if minutes < threshold:
        return None
    exists = session.execute(select(CollectorGap.id).where(CollectorGap.started_at == last)).scalar_one_or_none()
    if exists is not None:
        return None
    affected = session.execute(select(func.count()).select_from(Event).where(Event.kickoff_utc >= last - timedelta(hours=2), Event.kickoff_utc <= now + timedelta(hours=48), Event.duplicate_of.is_(None))).scalar_one()
    gap = CollectorGap(started_at=last, ended_at=now, minutes=round(minutes, 1), reason=reason, events_affected=int(affected or 0), detail=f"sem coleta por {minutes:.0f} min (limiar {threshold:.0f} min)")
    session.add(gap)
    session.flush()
    log.warning("lacuna de coleta registada: %.0f min desde %s (%d eventos na janela)", minutes, last.isoformat(), affected or 0)
    return gap


def backfill_from_cache(session: Session, cache_dir: Path, *, now: datetime | None = None) -> dict:
    """Ingere uma única vez as respostas reais guardadas no cache HTTP (`<hash>.json` + `.body`) como snapshots
    raw. Não fabrica histórico: cada ficheiro tem `collected_at` e `url` reais; a origem fica marcada em
    `source_version` (`…/cache-backfill`) e respostas já ingeridas (mesmo evento + mesmo instante) são
    ignoradas via DUPLICATE_SNAPSHOT."""
    now = now or datetime.utcnow()
    out = {"files": 0, "ingested": 0, "duplicates": 0, "skipped": 0, "errors": 0}
    metas = sorted(Path(cache_dir).glob("*.json"))
    for meta_path in metas:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out["errors"] += 1
            continue
        url = str(meta.get("url") or "")
        if "/events/" not in url or "by-date" in url:
            continue
        body_path = meta_path.with_suffix(".body")
        try:
            doc = json.loads(body_path.read_bytes())
            data = doc.get("data") if isinstance(doc, dict) else None
            ev = data[0] if isinstance(data, list) and data else data
            event_id = int(ev["eventId"])
            fetched_at = datetime.fromisoformat(str(meta["collected_at"]))
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            out["errors"] += 1
            continue
        if fetched_at.tzinfo is not None:
            fetched_at = fetched_at.astimezone(UTC).replace(tzinfo=None)
        out["files"] += 1
        exists = session.execute(select(RawSuperbetSnapshot.id).where(RawSuperbetSnapshot.event_id == event_id, RawSuperbetSnapshot.fetched_at == fetched_at)).scalar_one_or_none()
        if exists is not None:
            out["duplicates"] += 1
            continue
        try:
            with session.begin_nested():
                r = ingest_event_payload(session, event_id=event_id, payload=ev, fetched_at=fetched_at, source_url=url, http_status=meta.get("http_status"), now=now, source_version=f"{SOURCE_VERSION}/cache-backfill")
        except Exception as exc:  # noqa: BLE001 — um ficheiro corrompido não pode parar o backfill
            log.warning("backfill: falha em %s: %s", meta_path.name, exc)
            out["errors"] += 1
            continue
        if r.ok:
            out["ingested"] += 1
        else:
            out["skipped"] += 1
    return out


def raw_payload(session: Session, raw_id: int) -> dict | None:
    row = session.get(RawSuperbetSnapshot, raw_id)
    if row is None:
        return None
    if row.payload is None and row.payload_ref_id:
        row = session.get(RawSuperbetSnapshot, row.payload_ref_id)
    if row is None or row.payload is None:
        return None
    return json.loads(zlib.decompress(row.payload).decode("utf-8"))


__all__ = ["ingest_event_payload", "IngestResult", "backfill_from_cache", "record_gap_if_needed", "raw_payload", "SNAPSHOT_TARGETS", "TARGET_NAMES", "target_for_minutes", "event_state_from_payload"]
