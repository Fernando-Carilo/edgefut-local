"""SuperbetProvider — leitura da oferta pública (JSON).

Sem login, sem cookies, sem browser. Apenas GET em endpoints públicos com cache
e rate limit. Se a fonte bloquear, `SourceBlocked` sobe para o resolver.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ...core.config import settings
from ..http_client import FetchResult, HttpClient, SourceError, get_http_client
from .markets import NormalizedOdd, normalize_odd

log = logging.getLogger(__name__)

PROVIDER = "superbet"
SPORT_FOOTBALL = 5


@dataclass
class SuperbetEvent:
    event_id: int
    match_name: str
    home_name: str
    away_name: str
    home_team_ext_id: str | None
    away_team_ext_id: str | None
    kickoff_utc: datetime
    tournament_id: int | None
    category_id: int | None
    market_count: int
    status: str
    odds: list[NormalizedOdd] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    source_url: str = ""
    collected_at: datetime | None = None
    event_url: str = ""


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "evento"


def _split_match_name(name: str) -> tuple[str, str]:
    for sep in ("·", " - ", " x ", " vs "):
        if sep in name:
            a, b = name.split(sep, 1)
            return a.strip(), b.strip()
    return name.strip(), ""


def _parse_utc(raw: dict) -> datetime:
    s = raw.get("utcDate") or raw.get("matchDate")
    if s:
        s = s.replace("Z", "+00:00").replace(" ", "T")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    ms = raw.get("unixDateMillis")
    return datetime.utcfromtimestamp(int(ms) / 1000)


class SuperbetProvider:
    name = PROVIDER

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or get_http_client()
        self.base = settings.superbet_base_url.rstrip("/")

    # -- URLs ---------------------------------------------------------------
    def events_by_date_url(self, start: datetime, end: datetime) -> str:
        fmt = "%Y-%m-%d+%H:%M:%S"
        return (
            f"{self.base}/events/by-date?offerState=prematch"
            f"&startDate={start.strftime(fmt)}&endDate={end.strftime(fmt)}&sportId={SPORT_FOOTBALL}"
        )

    def event_url(self, event_id: int) -> str:
        return f"{self.base}/events/{event_id}"

    def tournaments_url(self) -> str:
        return f"{self.base}/sport/{SPORT_FOOTBALL}/tournaments"

    def public_event_link(self, ev: SuperbetEvent, category: str | None, tournament: str | None) -> str:
        parts = ["apostas", "futebol"]
        if category:
            parts.append(_slug(category))
        if tournament:
            parts.append(_slug(tournament))
        parts.append(_slug(f"{ev.home_name}-{ev.away_name}"))
        parts.append(str(ev.event_id))
        return f"{settings.superbet_site_url.rstrip('/')}/" + "/".join(parts)

    # -- fetch --------------------------------------------------------------
    def fetch_events(self, hours_ahead: int = 48, force: bool = False) -> tuple[list[SuperbetEvent], FetchResult]:
        now = datetime.utcnow()
        start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
        end = start + timedelta(hours=hours_ahead + 3)
        url = self.events_by_date_url(start, end)
        res = self.http.get(url, provider=PROVIDER, ttl_s=600, min_interval_s=2.0, force=force)
        payload = res.json()
        if payload.get("error"):
            raise SourceError(f"superbet respondeu error=true para {url}")
        events: list[SuperbetEvent] = []
        for raw in payload.get("data", []):
            try:
                ev = self._parse_event(raw, res)
            except Exception as exc:  # noqa: BLE001
                log.debug("evento ignorado (%s): %s", exc, raw.get("eventId"))
                continue
            if ev.kickoff_utc < now - timedelta(hours=3):
                continue
            events.append(ev)
        return events, res

    def fetch_event(self, event_id: int, force: bool = False) -> SuperbetEvent:
        url = self.event_url(event_id)
        res = self.http.get(url, provider=PROVIDER, ttl_s=240, min_interval_s=2.0, force=force)
        payload = res.json()
        data = payload.get("data")
        if not data:
            raise SourceError(f"evento {event_id} não encontrado na Superbet")
        raw = data[0] if isinstance(data, list) else data
        return self._parse_event(raw, res)

    def fetch_tournaments(self) -> tuple[dict[int, dict], FetchResult]:
        url = self.tournaments_url()
        res = self.http.get(url, provider=PROVIDER, ttl_s=86400, min_interval_s=2.0)
        out: dict[int, dict] = {}
        for cat in res.json().get("data", []):
            cat_name = (cat.get("localNames") or {}).get("pt-BR")
            for comp in cat.get("competitions", []):
                tid = comp.get("tournamentId")
                if tid is None:
                    continue
                out[int(tid)] = {
                    "tournament_id": int(tid),
                    "name": (comp.get("localNames") or {}).get("pt-BR") or str(tid),
                    "category_id": cat.get("categoryId"),
                    "category_name": cat_name,
                }
        return out, res

    # -- parse --------------------------------------------------------------
    def _parse_event(self, raw: dict, res: FetchResult) -> SuperbetEvent:
        home, away = _split_match_name(str(raw.get("matchName", "")))
        meta = raw.get("metadata") or {}
        status = str(meta.get("status") or "prematch").lower()
        odds: list[NormalizedOdd] = []
        for o in raw.get("odds") or []:
            n = normalize_odd(o, home, away)
            if n is not None and n.status == "active":
                odds.append(n)
        ev = SuperbetEvent(
            event_id=int(raw["eventId"]),
            match_name=str(raw.get("matchName", "")),
            home_name=home,
            away_name=away,
            home_team_ext_id=str(raw.get("homeTeamId")) if raw.get("homeTeamId") else None,
            away_team_ext_id=str(raw.get("awayTeamId")) if raw.get("awayTeamId") else None,
            kickoff_utc=_parse_utc(raw),
            tournament_id=int(raw["tournamentId"]) if raw.get("tournamentId") is not None else None,
            category_id=int(raw["categoryId"]) if raw.get("categoryId") is not None else None,
            market_count=int(raw.get("marketCount") or 0),
            status=status,
            odds=odds,
            raw={k: v for k, v in raw.items() if k != "odds"},
            source_url=res.url,
            collected_at=res.collected_at,
        )
        return ev
