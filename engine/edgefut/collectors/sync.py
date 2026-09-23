"""Sincronização Superbet → SQLite (eventos, competições, snapshots de odds)."""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Competition, Event, OddsSnapshot, SourceLog
from ..db.session import session_scope
from ..normalization import classify_competition, is_womens
from ..providers import SourceBlocked, SourceError, get_http_client
from ..providers.superbet import SuperbetEvent, SuperbetProvider

log = logging.getLogger(__name__)


_log_queue: queue.Queue | None = None
_log_writer: threading.Thread | None = None


def _drain_source_log(q: queue.Queue) -> None:
    """Escreve entradas de source_log em lotes, fora da thread que fez o fetch.

    Um fetch pode acontecer enquanto a sessão da thread chamadora mantém uma transação
    de escrita aberta; escrever o log de forma síncrona por outra conexão bloquearia
    até o busy_timeout (auto-deadlock no SQLite). Por isso o sink apenas enfileira.
    """
    while True:
        batch = [q.get()]
        try:
            while len(batch) < 100:
                batch.append(q.get_nowait())
        except queue.Empty:
            pass
        try:
            with session_scope() as s:
                s.add_all(batch)
        except Exception as exc:  # noqa: BLE001
            log.warning("source_log: falha ao gravar %d entradas: %s", len(batch), exc)
        finally:
            for _ in batch:
                q.task_done()


def install_source_log_sink() -> None:
    global _log_queue, _log_writer
    if _log_queue is None:
        _log_queue = queue.Queue(maxsize=5000)
        _log_writer = threading.Thread(target=_drain_source_log, args=(_log_queue,), name="source-log-writer", daemon=True)
        _log_writer.start()

    def sink(provider, url, status, http_status, latency_ms, error):
        entry = SourceLog(
            provider=provider, url=url[:400], status=status, http_status=http_status,
            latency_ms=latency_ms, error=error,
        )
        try:
            _log_queue.put_nowait(entry)
        except queue.Full:
            log.debug("source_log: fila cheia, entrada descartada (%s)", url[:80])

    get_http_client().set_log_sink(sink)


def flush_source_log(timeout: float = 5.0) -> None:
    """Aguarda a fila de logs esvaziar (usado em testes e no shutdown)."""
    if _log_queue is None:
        return
    deadline = time.monotonic() + timeout
    while not _log_queue.empty() and time.monotonic() < deadline:
        time.sleep(0.05)


class SuperbetSync:
    def __init__(self, provider: SuperbetProvider | None = None) -> None:
        self.provider = provider or SuperbetProvider()
        self._tournaments: dict[int, dict] | None = None

    def tournaments(self) -> dict[int, dict]:
        if self._tournaments is None:
            try:
                self._tournaments, _ = self.provider.fetch_tournaments()
            except SourceError as exc:
                log.warning("torneios indisponíveis: %s", exc)
                self._tournaments = {}
        return self._tournaments

    # -- eventos ------------------------------------------------------------
    def sync_events(self, session: Session, hours_ahead: int = 48, force: bool = False) -> dict:
        try:
            events, res = self.provider.fetch_events(hours_ahead=hours_ahead, force=force)
        except SourceBlocked as exc:
            return {"ok": False, "blocked": True, "error": str(exc), "events": 0}
        except SourceError as exc:
            return {"ok": False, "blocked": False, "error": str(exc), "events": 0}
        tours = self.tournaments()
        now = datetime.utcnow()
        n_new = 0
        for ev in events:
            comp = self._upsert_competition(session, ev, tours)
            row = session.get(Event, ev.event_id)
            t = tours.get(ev.tournament_id or -1, {})
            if row is None:
                row = Event(
                    id=ev.event_id, home_name=ev.home_name, away_name=ev.away_name, kickoff_utc=ev.kickoff_utc,
                    first_seen_at=now,
                )
                session.add(row)
                n_new += 1
            row.competition_id = comp.id if comp else None
            row.competition_name = comp.name if comp else t.get("name")
            row.category_name = comp.category_name if comp else t.get("category_name")
            row.home_team_ext_id = ev.home_team_ext_id
            row.away_team_ext_id = ev.away_team_ext_id
            row.kickoff_utc = ev.kickoff_utc
            row.status = ev.status
            row.market_count = ev.market_count
            row.last_seen_at = now
            row.raw = ev.raw
            row.event_url = self.provider.public_event_link(ev, row.category_name, row.competition_name)
        session.flush()
        return {"ok": True, "blocked": False, "events": len(events), "new": n_new, "source": res.status, "url": res.url}

    def _upsert_competition(self, session: Session, ev: SuperbetEvent, tours: dict[int, dict]) -> Competition | None:
        if ev.tournament_id is None:
            return None
        t = tours.get(ev.tournament_id, {})
        comp = session.get(Competition, ev.tournament_id)
        name = t.get("name") or (comp.name if comp else str(ev.tournament_id))
        profile = classify_competition(
            ev.tournament_id, name, ev.category_id, t.get("category_name"),
            womens_hint=is_womens(ev.home_name) or is_womens(ev.away_name),
        )
        if comp is None:
            comp = Competition(id=ev.tournament_id, name=name)
            session.add(comp)
        comp.name = name
        comp.category_id = ev.category_id
        comp.category_name = t.get("category_name")
        comp.dataset_code = profile.dataset_codes[0] if profile.dataset_codes and not profile.is_international_clubs else None
        comp.is_national_teams = profile.is_national_teams
        comp.is_womens = profile.is_womens
        comp.updated_at = datetime.utcnow()
        return comp

    # -- odds ---------------------------------------------------------------
    def sync_odds(self, session: Session, event_id: int, force: bool = False, max_age_min: int = 4) -> dict:
        row = session.get(Event, event_id)
        if row is not None and not force and row.odds_collected_at:
            if datetime.utcnow() - row.odds_collected_at < timedelta(minutes=max_age_min):
                return {"ok": True, "skipped": True, "odds": 0}
        try:
            ev = self.provider.fetch_event(event_id, force=force)
        except SourceBlocked as exc:
            return {"ok": False, "blocked": True, "error": str(exc), "odds": 0}
        except SourceError as exc:
            return {"ok": False, "blocked": False, "error": str(exc), "odds": 0}
        if row is None:
            tours = self.tournaments()
            comp = self._upsert_competition(session, ev, tours)
            row = Event(
                id=ev.event_id, home_name=ev.home_name, away_name=ev.away_name, kickoff_utc=ev.kickoff_utc,
                competition_id=comp.id if comp else None, competition_name=comp.name if comp else None,
                category_name=comp.category_name if comp else None,
            )
            session.add(row)
            row.event_url = self.provider.public_event_link(ev, row.category_name, row.competition_name)
        row.status = ev.status
        row.market_count = ev.market_count
        row.last_seen_at = datetime.utcnow()
        meta = ev.raw.get("metadata") or {}
        if meta.get("status") == "FINISHED" and meta.get("homeTeamScore") is not None:
            try:
                row.home_score = int(meta["homeTeamScore"])
                row.away_score = int(meta["awayTeamScore"])
                row.result_source = "superbet"
                row.settled_at = row.settled_at or datetime.utcnow()
            except (TypeError, ValueError):
                pass

        collected = ev.collected_at or datetime.utcnow()
        # evita duplicar snapshot idêntico ao último (economiza espaço, preserva histórico real)
        last = self._latest_prices(session, event_id)
        n = 0
        for o in ev.odds:
            key = (o.market_key, o.selection_key, o.line)
            if last.get(key) == o.price and not force:
                continue
            session.add(
                OddsSnapshot(
                    event_id=event_id, market_key=o.market_key, market_name=o.market_name,
                    selection_key=o.selection_key, selection_name=o.selection_name, line=o.line, price=o.price,
                    status=o.status, source="superbet", source_url=ev.source_url, collected_at=collected,
                )
            )
            n += 1
        row.odds_collected_at = collected
        session.flush()
        return {"ok": True, "skipped": False, "odds": n, "total": len(ev.odds), "source": "cached" if n == 0 else "ok"}

    def _latest_prices(self, session: Session, event_id: int) -> dict[tuple, float]:
        sub = (
            select(
                OddsSnapshot.market_key, OddsSnapshot.selection_key, OddsSnapshot.line,
                func.max(OddsSnapshot.collected_at).label("mx"),
            )
            .where(OddsSnapshot.event_id == event_id)
            .group_by(OddsSnapshot.market_key, OddsSnapshot.selection_key, OddsSnapshot.line)
            .subquery()
        )
        rows = session.execute(
            select(OddsSnapshot).join(
                sub,
                (OddsSnapshot.market_key == sub.c.market_key)
                & (OddsSnapshot.selection_key == sub.c.selection_key)
                & ((OddsSnapshot.line == sub.c.line) | (OddsSnapshot.line.is_(None) & sub.c.line.is_(None)))
                & (OddsSnapshot.collected_at == sub.c.mx),
            ).where(OddsSnapshot.event_id == event_id)
        ).scalars()
        return {(r.market_key, r.selection_key, r.line): r.price for r in rows}


def latest_odds_rows(session: Session, event_id: int) -> tuple[list[dict], dict[tuple, float], datetime | None, str | None]:
    """Retorna (linhas atuais, preços de abertura, collected_at, source_url)."""
    rows = session.execute(
        select(OddsSnapshot).where(OddsSnapshot.event_id == event_id).order_by(OddsSnapshot.collected_at.asc())
    ).scalars().all()
    latest: dict[tuple, OddsSnapshot] = {}
    opening: dict[tuple, float] = {}
    for r in rows:
        key = (r.market_key, r.selection_key, r.line)
        opening.setdefault(key, r.price)
        latest[key] = r
    if not latest:
        return [], {}, None, None
    max_ts = max(r.collected_at for r in latest.values())
    # odds "atuais" = presentes na última coleta (mercados removidos pela casa somem)
    current = [
        {
            "market_key": r.market_key, "market_name": r.market_name, "selection_key": r.selection_key,
            "selection_name": r.selection_name, "line": r.line, "price": r.price,
        }
        for r in latest.values()
        if r.status == "active"
    ]
    src = next(iter(latest.values())).source_url
    return current, opening, max_ts, src
