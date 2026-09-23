"""Player Engine — ARQUITETURA APENAS (Iteração 2).

Nenhum provider público e permitido de escalações/minutos/xG de jogador está integrado. Enquanto
`registry().available` for False, todo mercado PLAYER_MARKET recebe NO BET (LINEUP_UNCERTAINTY) e
a UI mostra PLAYER DATA UNAVAILABLE. Nada aqui inventa dados: os tipos existem para que um provider
real possa ser plugado sem mexer no pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Availability = Literal["AVAILABLE", "DOUBTFUL", "INJURED", "SUSPENDED", "UNKNOWN"]


@dataclass(frozen=True)
class PlayerStats:
    """Estatísticas por 90 minutos observadas numa janela (nunca estimadas quando ausentes)."""

    player_id: str
    name: str
    team_canonical: str
    matches: int
    minutes: int
    goals: int
    assists: int
    shots: int | None = None
    shots_on_target: int | None = None
    xg: float | None = None  # None quando o provider não expõe
    window_start: datetime | None = None
    window_end: datetime | None = None
    source: str = ""
    collected_at: datetime | None = None


@dataclass(frozen=True)
class PlayerAvailability:
    player_id: str
    status: Availability
    reason: str | None = None
    source: str = ""
    collected_at: datetime | None = None


@dataclass(frozen=True)
class Lineup:
    """Escalação de UMA equipe. `confirmed=False` significa provável/prevista → LINEUP_UNCERTAINTY."""

    team_canonical: str
    event_id: int
    confirmed: bool
    starters: tuple[str, ...] = ()
    bench: tuple[str, ...] = ()
    formation: str | None = None
    source: str = ""
    collected_at: datetime | None = None


@dataclass(frozen=True)
class PlayerPrediction:
    """Saída do futuro modelo de jogador. Só existe com lineup confirmada e amostra mínima."""

    player_id: str
    market_key: str  # PLAYER_TO_SCORE, PLAYER_SHOTS, ...
    probability: float
    model_version: str
    sample_size: int
    lineup_confirmed: bool
    notes: tuple[str, ...] = ()


class PlayerProvider(ABC):
    """Contrato para um provider de dados de jogadores. Deve respeitar robots/ToS; nunca contorna bloqueios."""

    key: str = "abstract"
    name: str = "Provider abstrato"

    @abstractmethod
    def lineups(self, event_id: int) -> tuple[Lineup, Lineup] | None: ...

    @abstractmethod
    def player_stats(self, team_canonical: str, since: datetime) -> list[PlayerStats]: ...

    @abstractmethod
    def availability(self, team_canonical: str) -> list[PlayerAvailability]: ...


@dataclass
class PlayerRegistry:
    providers: list[PlayerProvider] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.providers)

    @property
    def status(self) -> str:
        return "AVAILABLE" if self.available else "PLAYER DATA UNAVAILABLE"

    def lineups(self, event_id: int) -> tuple[Lineup, Lineup] | None:
        for p in self.providers:
            out = p.lineups(event_id)
            if out is not None:
                return out
        return None


_registry = PlayerRegistry()


def registry() -> PlayerRegistry:
    return _registry


def player_market_verdict() -> tuple[str, str]:
    """(reason, detail) aplicados a qualquer mercado de jogador enquanto não há provider."""
    if registry().available:
        return ("LINEUP_UNCERTAINTY", "Escalação não confirmada.")
    return ("LINEUP_UNCERTAINTY", "Player Engine desativado: sem fonte pública de minutos/xG e escalação não confirmada (PLAYER DATA UNAVAILABLE).")


__all__ = [
    "Availability", "Lineup", "PlayerAvailability", "PlayerPrediction", "PlayerProvider", "PlayerRegistry", "PlayerStats",
    "player_market_verdict", "registry",
]
