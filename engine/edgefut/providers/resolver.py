"""SourceResolver — decide qual fonte histórica atende um evento e informa qual foi usada."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..core.config import settings
from ..domain.provenance import SourceAttempt
from ..normalization import CompetitionProfile, TeamMatch, resolve_team
from .historical import INTL_CODE, FootballDataProvider, InternationalResultsProvider, get_store
from .historical.football_data import LEAGUE_NAMES, NEW_FORMAT
from .http_client import SourceError

log = logging.getLogger(__name__)


@dataclass
class ResolvedTeams:
    home: TeamMatch
    away: TeamMatch
    dataset_codes: list[str]
    attempts: list[SourceAttempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.home.canonical is not None and self.away.canonical is not None

    @property
    def home_datasets(self) -> list[str]:
        return [self.home.dataset_code] if self.home.dataset_code else []

    @property
    def away_datasets(self) -> list[str]:
        return [self.away.dataset_code] if self.away.dataset_code else []


def current_seasons(n: int = 3) -> list[str]:
    now = datetime.utcnow()
    year = now.year if now.month >= 7 else now.year - 1
    out = []
    for i in range(n):
        y = year - i
        out.append(f"{str(y)[2:]}{str(y + 1)[2:]}")
    return out


class SourceResolver:
    def __init__(self) -> None:
        self.store = get_store()
        self.football_data = FootballDataProvider()
        self.intl = InternationalResultsProvider()

    # -- garantia de datasets -------------------------------------------------
    def ensure_dataset(self, code: str, force: bool = False) -> SourceAttempt:
        try:
            if code == INTL_CODE:
                if force or not (self.store.dir / "international_results.parquet").exists():
                    _, url, _ = self.intl.fetch(force=force)
                    return SourceAttempt(provider=self.intl.name, status="ok", url=url)
                return SourceAttempt(provider=self.intl.name, status="cached")
            if code in NEW_FORMAT:
                if force or not (self.store.dir / f"football_data_{code}.parquet").exists():
                    _, url, _ = self.football_data.fetch_country(code, force=force)
                    return SourceAttempt(provider=self.football_data.name, status="ok", url=url)
                return SourceAttempt(provider=self.football_data.name, status="cached")
            if code in LEAGUE_NAMES:
                fetched = False
                for season in current_seasons(len(settings.bootstrap_seasons)):
                    p = self.store.dir / f"football_data_{code}_{season}.parquet"
                    if force or not p.exists():
                        try:
                            self.football_data.fetch_league(code, season, force=force)
                            fetched = True
                        except SourceError as exc:
                            log.info("temporada %s/%s indisponível: %s", code, season, exc)
                return SourceAttempt(
                    provider=self.football_data.name,
                    status="ok" if fetched else "cached",
                    url=self.football_data.league_url(code, current_seasons(1)[0]),
                )
            return SourceAttempt(provider="none", status="unsupported", detail=f"dataset {code} desconhecido")
        except SourceError as exc:
            return SourceAttempt(provider="historical", status="unavailable", detail=str(exc))

    # -- resolução de times -------------------------------------------------
    def resolve(self, profile: CompetitionProfile, home_name: str, away_name: str) -> ResolvedTeams:
        attempts: list[SourceAttempt] = []
        if not profile.supported or not profile.dataset_codes:
            attempts.append(
                SourceAttempt(provider="resolver", status="unsupported", detail=profile.reason)
            )
            return ResolvedTeams(
                TeamMatch(home_name, None, None, 0.0, "unmatched"),
                TeamMatch(away_name, None, None, 0.0, "unmatched"),
                [],
                attempts,
            )

        for code in profile.dataset_codes[:1] if not profile.is_international_clubs else []:
            attempts.append(self.ensure_dataset(code))

        if profile.is_national_teams:
            attempts.append(self.ensure_dataset(INTL_CODE))
            candidates = self.store.team_names([INTL_CODE])
            home = resolve_team(home_name, INTL_CODE, candidates, is_national=True)
            away = resolve_team(away_name, INTL_CODE, candidates, is_national=True)
            return ResolvedTeams(home, away, [INTL_CODE], attempts)

        if profile.is_international_clubs:
            # tenta cada liga mapeada já baixada; não baixa todas para evitar tráfego
            home = TeamMatch(home_name, None, None, 0.0, "unmatched")
            away = TeamMatch(away_name, None, None, 0.0, "unmatched")
            available = {r["dataset_code"] for _, r in self.store.datasets().iterrows()} if self.store.has_data() else set()
            for code in profile.dataset_codes:
                if code not in available:
                    continue
                candidates = self.store.team_names([code])
                if home.canonical is None:
                    m = resolve_team(home_name, code, candidates)
                    if m.canonical:
                        home = m
                if away.canonical is None:
                    m = resolve_team(away_name, code, candidates)
                    if m.canonical:
                        away = m
            attempts.append(
                SourceAttempt(
                    provider="resolver",
                    status="ok" if (home.canonical and away.canonical) else "unavailable",
                    detail="ligas domésticas disponíveis: " + ", ".join(sorted(available)) or "nenhuma",
                )
            )
            codes = [c for c in {home.dataset_code, away.dataset_code} if c]
            return ResolvedTeams(home, away, codes, attempts)

        code = profile.dataset_codes[0]
        candidates = self.store.team_names([code])
        home = resolve_team(home_name, code, candidates)
        away = resolve_team(away_name, code, candidates)
        if not candidates:
            attempts.append(
                SourceAttempt(provider="resolver", status="unavailable", detail=f"dataset {code} vazio")
            )
        return ResolvedTeams(home, away, [code], attempts)
