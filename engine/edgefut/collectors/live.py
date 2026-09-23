"""Live Mode — SOMENTE OBSERVAÇÃO.

Lê `offerState=live` da oferta pública da Superbet e mantém em memória o estado
dos jogos em andamento: placar, minuto, período, estatísticas expostas pela
fonte (escanteios, cartões) e odds ao vivo com movimentação entre polls.

Não gera recomendação, não calcula edge ao vivo e não persiste odds ao vivo em
`odds_snapshot` (elas contaminariam movimentação/closing pré-jogo). Polling com
backoff exponencial e respeito ao circuit breaker do HttpClient.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select

from ..core.config import settings
from ..db.models import Competition, Event
from ..db.session import session_scope
from ..domain import freshness as fresh
from ..odds import build_markets
from ..providers import CircuitOpen, SourceError
from ..providers.superbet import SuperbetEvent, SuperbetProvider

log = logging.getLogger(__name__)

MAX_FULL_MARKET_FETCHES = 5  # eventos por poll com mercados completos (/events/{id})
MAX_BACKOFF_S = 600


@dataclass
class LiveEvent:
    event_id: int
    home_name: str
    away_name: str
    kickoff_utc: datetime
    competition_name: str | None
    category_name: str | None
    status: str
    period: str | None
    minute: int | None
    stoppage_time: str | None
    home_score: int | None
    away_score: int | None
    stats: dict[str, int | None]
    market_count: int
    markets: list[dict]
    odds_collected_at: datetime | None
    tracked: bool  # evento já existia na base (tinha análise pré-jogo)
    event_url: str | None
    movement: dict[str, dict] = field(default_factory=dict)  # "MK|SEL|LINE" → {from, to, pct}
    full_markets: bool = False


@dataclass
class LiveState:
    events: dict[int, LiveEvent] = field(default_factory=dict)
    updated_at: datetime | None = None
    last_attempt_at: datetime | None = None
    next_poll_at: datetime | None = None
    backoff_s: int = 0
    consecutive_errors: int = 0
    last_error: str | None = None
    polls: int = 0
    prev_prices: dict[int, dict[str, float]] = field(default_factory=dict)


_state = LiveState()
_lock = threading.Lock()


def _int(v) -> int | None:
    try:
        return int(str(v).rstrip("'")) if v is not None else None
    except (TypeError, ValueError):
        return None


def _to_live(ev: SuperbetEvent, known: dict[int, Event], comps: dict[int, Competition], prev: dict[str, float]) -> LiveEvent:
    meta = ev.raw.get("metadata") or {}
    rows = [
        {
            "market_key": o.market_key, "market_name": o.market_name, "selection_key": o.selection_key,
            "selection_name": o.selection_name, "line": o.line, "price": o.price,
        }
        for o in ev.odds
    ]
    markets = build_markets(rows, {}, ev.collected_at, ev.source_url)
    movement: dict[str, dict] = {}
    for m in markets:
        for s in m.selections:
            key = f"{m.market_key}|{s.key}|{m.line}"
            before = prev.get(key)
            if before and before != s.price:
                movement[key] = {"from": before, "to": s.price, "pct": round((s.price / before - 1) * 100, 1)}
    row = known.get(ev.event_id)
    comp = comps.get(ev.tournament_id) if ev.tournament_id is not None else None
    stats = {
        "corners_home": _int(meta.get("homeTeamCorners")), "corners_away": _int(meta.get("awayTeamCorners")),
        "yellow_home": _int(meta.get("homeTeamYellowCards")), "yellow_away": _int(meta.get("awayTeamYellowCards")),
        "red_home": _int(meta.get("homeTeamRedCards")), "red_away": _int(meta.get("awayTeamRedCards")),
    }
    return LiveEvent(
        event_id=ev.event_id, home_name=ev.home_name, away_name=ev.away_name, kickoff_utc=ev.kickoff_utc,
        competition_name=(row.competition_name if row else (comp.name if comp else None)),
        category_name=(row.category_name if row else (comp.category_name if comp else None)),
        status=str(meta.get("status") or "STARTED"), period=meta.get("periodStatus") or meta.get("matchStatusLabel"),
        minute=_int(meta.get("minutes")), stoppage_time=meta.get("stoppageTime"),
        home_score=_int(meta.get("homeTeamScore")), away_score=_int(meta.get("awayTeamScore")), stats=stats,
        market_count=ev.market_count, markets=[m.model_dump(mode="json") for m in markets],
        odds_collected_at=ev.collected_at, tracked=row is not None, event_url=(row.event_url if row else None),
        movement=movement,
    )


def poll_live(force: bool = False) -> dict:
    """Um ciclo de observação. Respeita `next_poll_at` (backoff) a menos que `force`."""
    now = datetime.utcnow()
    with _lock:
        if not force and _state.next_poll_at and now < _state.next_poll_at:
            return {"ok": True, "skipped": True, "next_poll_at": _state.next_poll_at.isoformat(), "reason": "backoff"}
        _state.last_attempt_at = now
    provider = SuperbetProvider()
    try:
        events, res = provider.fetch_live_events(ttl_s=max(5, settings.live_poll_seconds - 5))
    except (CircuitOpen, SourceError) as exc:
        with _lock:
            _state.consecutive_errors += 1
            _state.backoff_s = min(MAX_BACKOFF_S, max(settings.live_poll_seconds, (_state.backoff_s or settings.live_poll_seconds) * 2))
            _state.next_poll_at = now + timedelta(seconds=_state.backoff_s)
            _state.last_error = str(exc)
        log.warning("live: fonte indisponível (%s); próximo poll em %ss", exc, _state.backoff_s)
        return {"ok": False, "error": str(exc), "backoff_s": _state.backoff_s}

    ids = [e.event_id for e in events]
    with session_scope() as s:
        known = {r.id: r for r in s.execute(select(Event).where(Event.id.in_(ids))).scalars()} if ids else {}
        comps = {c.id: c for c in s.execute(select(Competition)).scalars()}
        # mercados completos apenas para eventos que já acompanhávamos (limite por poll)
        detailed: dict[int, SuperbetEvent] = {}
        tracked_first = sorted(events, key=lambda e: (e.event_id not in known, -e.market_count))
        for e in tracked_first[:MAX_FULL_MARKET_FETCHES]:
            if e.market_count <= 1 or e.event_id not in known:
                continue
            try:
                detailed[e.event_id] = provider.fetch_event(e.event_id, force=True, ttl_s=10)
            except SourceError as exc:
                log.info("live: mercados completos indisponíveis para %s: %s", e.event_id, exc)
        with _lock:
            prev_prices = dict(_state.prev_prices)
        new_events: dict[int, LiveEvent] = {}
        new_prices: dict[int, dict[str, float]] = {}
        for e in events:
            src = detailed.get(e.event_id, e)
            le = _to_live(src, known, comps, prev_prices.get(e.event_id, {}))
            le.full_markets = e.event_id in detailed
            new_events[e.event_id] = le
            new_prices[e.event_id] = {f"{m['market_key']}|{sel['key']}|{m['line']}": sel["price"] for m in le.markets for sel in m["selections"]}
            row = known.get(e.event_id)
            if row is not None:
                row.live_status = (le.period or "live").lower()
                row.live_minute = le.minute
                row.live_home_score = le.home_score
                row.live_away_score = le.away_score
                row.live_collected_at = le.odds_collected_at or now
    with _lock:
        _state.events = new_events
        _state.prev_prices = new_prices
        _state.updated_at = res.collected_at or now
        _state.consecutive_errors = 0
        _state.backoff_s = 0
        _state.last_error = None
        _state.polls += 1
        _state.next_poll_at = now + timedelta(seconds=settings.live_poll_seconds)
    return {"ok": True, "events": len(new_events), "tracked": sum(1 for e in new_events.values() if e.tracked), "detailed": len(detailed), "cached": res.status == "cached"}


def live_snapshot() -> dict:
    """Estado atual para a API. Nunca inventa: sem poll bem-sucedido → `available=False`."""
    with _lock:
        st = _state
        events = list(st.events.values())
        updated = st.updated_at
        f = fresh.assess("odds_live", updated, source="superbet") if updated else fresh.unavailable("odds_live", "Nenhum poll ao vivo concluído.")
        return {
            "available": updated is not None and f.status != "EXPIRED",
            "mode": "OBSERVATION_ONLY",
            "updated_at": updated,
            "age_seconds": f.age_seconds,
            "freshness": f.model_dump(mode="json"),
            "poll_interval_s": settings.live_poll_seconds,
            "next_poll_at": st.next_poll_at,
            "backoff_s": st.backoff_s,
            "consecutive_errors": st.consecutive_errors,
            "last_error": st.last_error,
            "polls": st.polls,
            "events": sorted(
                (e.__dict__ for e in events), key=lambda e: (not e["tracked"], e["kickoff_utc"])
            ),
            "notice": (
                "Modo ao vivo é apenas observação: nenhuma recomendação, edge ou EV é calculado durante o jogo. "
                "Estatísticas exibidas são exclusivamente as expostas pela fonte."
            ),
        }
