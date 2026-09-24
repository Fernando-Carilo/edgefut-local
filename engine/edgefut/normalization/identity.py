"""CanonicalEventResolver — identidade única de partida entre fontes.

`canonical_event_id` = data UTC + slug da competição + times canônicos, ex.:

    20260923-gulf-cup-iraq-oman

Regras:
- times são resolvidos por aliases curados (PT/EN/código FIFA/dataset). Um casamento
  por similaridade textual (fuzzy) NUNCA é suficiente para unir duas partidas;
- kickoff dentro de uma tolerância (padrão 3 h) para absorver fuso/horário ajustado;
- competição compatível (mesmo dataset ou mesmo slug) quando ambas as fontes a informam;
- a troca de ordem (home/away invertidos) é detectada e devolvida como `swapped`
  para o SourceConflict, nunca resolvida em silêncio.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from .teams import TeamMatch, resolve_country, resolve_team

STRONG_METHODS = {"curated", "exact"}


def slugify(value: str | None) -> str:
    if not value:
        return "unknown"
    s = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "unknown"


def canonical_team_key(name: str, *, is_national: bool, dataset_code: str | None = None, candidates: list[str] | None = None) -> TeamMatch:
    """Resolve o time para o nome canônico do dataset; para seleções aceita PT, EN e código FIFA."""
    if is_national:
        en = resolve_country(name)
        if en:
            return TeamMatch(name, en, dataset_code or "INTL", 0.98, "curated", True)
        return TeamMatch(name, None, dataset_code, 0.0, "unmatched", True)
    return resolve_team(name, dataset_code, candidates or [])


def canonical_event_id(kickoff_utc: datetime, competition: str | None, home_canonical: str, away_canonical: str) -> str:
    return f"{kickoff_utc:%Y%m%d}-{slugify(competition)}-{slugify(home_canonical)}-{slugify(away_canonical)}"


@dataclass(frozen=True)
class EventIdentity:
    source: str
    source_event_id: str
    kickoff_utc: datetime
    home: TeamMatch
    away: TeamMatch
    competition: str | None = None
    dataset_code: str | None = None
    country: str | None = None
    venue: str | None = None

    @property
    def resolved(self) -> bool:
        return self.home.canonical is not None and self.away.canonical is not None

    @property
    def strong(self) -> bool:
        return self.resolved and self.home.method in STRONG_METHODS and self.away.method in STRONG_METHODS

    @property
    def canonical_id(self) -> str | None:
        if not self.resolved:
            return None
        return canonical_event_id(self.kickoff_utc, self.dataset_code or self.competition, self.home.canonical, self.away.canonical)  # type: ignore[arg-type]


@dataclass(frozen=True)
class IdentityMatch:
    canonical_id: str
    a: EventIdentity
    b: EventIdentity
    swapped: bool
    kickoff_delta_min: float
    confidence: float
    method: str  # canonical_teams+kickoff | canonical_teams+kickoff+competition


class CanonicalEventResolver:
    def __init__(self, kickoff_tolerance: timedelta = timedelta(hours=3)) -> None:
        self.tolerance = kickoff_tolerance

    def same_event(self, a: EventIdentity, b: EventIdentity) -> IdentityMatch | None:
        if not (a.strong and b.strong):
            return None  # fuzzy nunca une partidas
        delta = abs((a.kickoff_utc - b.kickoff_utc).total_seconds()) / 60
        if delta > self.tolerance.total_seconds() / 60:
            return None
        direct = a.home.canonical == b.home.canonical and a.away.canonical == b.away.canonical
        swapped = a.home.canonical == b.away.canonical and a.away.canonical == b.home.canonical
        if not (direct or swapped):
            return None
        method = "canonical_teams+kickoff"
        confidence = 0.9 - min(0.2, delta / 60 * 0.05)
        if a.dataset_code and b.dataset_code:
            if a.dataset_code != b.dataset_code:
                return None
            method += "+competition"
            confidence += 0.08
        elif a.competition and b.competition and slugify(a.competition) == slugify(b.competition):
            method += "+competition"
            confidence += 0.05
        if swapped:
            confidence -= 0.1
        return IdentityMatch(
            canonical_id=a.canonical_id,  # type: ignore[arg-type]
            a=a, b=b, swapped=swapped, kickoff_delta_min=round(delta, 1), confidence=round(min(1.0, confidence), 3), method=method,
        )

    def find(self, candidate: EventIdentity, existing: list[EventIdentity]) -> IdentityMatch | None:
        best: IdentityMatch | None = None
        for other in existing:
            if other.source == candidate.source and other.source_event_id == candidate.source_event_id:
                continue
            m = self.same_event(candidate, other)
            if m and (best is None or m.confidence > best.confidence):
                best = m
        return best
