"""Data Freshness — todo dado relevante carrega idade, validade e estado.

Estados:
  FRESH        dentro da janela ideal
  AGING        utilizável, mas o usuário deve saber que está envelhecendo
  STALE        utilizável com penalidade explícita (nunca em TOP OPORTUNIDADES)
  EXPIRED      nunca é usado silenciosamente: bloqueia recomendação
  UNAVAILABLE  fonte inexistente para este dado (ex.: escalação)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

FreshnessStatus = Literal["FRESH", "AGING", "STALE", "EXPIRED", "UNAVAILABLE"]


@dataclass(frozen=True)
class FreshnessPolicy:
    fresh: timedelta
    aging: timedelta
    stale: timedelta  # após isto → EXPIRED

    def status_for(self, age: timedelta) -> FreshnessStatus:
        if age <= self.fresh:
            return "FRESH"
        if age <= self.aging:
            return "AGING"
        if age <= self.stale:
            return "STALE"
        return "EXPIRED"


# Política por tipo de dado. Odds envelhecem em minutos; histórico em dias.
POLICIES: dict[str, FreshnessPolicy] = {
    "odds": FreshnessPolicy(timedelta(minutes=10), timedelta(minutes=45), timedelta(hours=6)),
    "odds_live": FreshnessPolicy(timedelta(seconds=45), timedelta(minutes=2), timedelta(minutes=10)),
    "form": FreshnessPolicy(timedelta(hours=36), timedelta(days=4), timedelta(days=12)),
    "ranking": FreshnessPolicy(timedelta(hours=36), timedelta(days=4), timedelta(days=12)),
    "stats": FreshnessPolicy(timedelta(hours=36), timedelta(days=4), timedelta(days=12)),
    "history": FreshnessPolicy(timedelta(hours=36), timedelta(days=4), timedelta(days=12)),
    "venue": FreshnessPolicy(timedelta(days=7), timedelta(days=30), timedelta(days=365)),
    "events": FreshnessPolicy(timedelta(minutes=20), timedelta(hours=1), timedelta(hours=6)),
    "results": FreshnessPolicy(timedelta(hours=6), timedelta(days=1), timedelta(days=3)),
}

LABELS = {
    "odds": "Odds",
    "odds_live": "Odds ao vivo",
    "form": "Forma",
    "ranking": "Ranking (ELO)",
    "stats": "Estatísticas",
    "history": "Histórico",
    "venue": "Local / mando",
    "lineup": "Escalação",
    "events": "Eventos",
    "results": "Resultados",
}


class Freshness(BaseModel):
    kind: str
    label: str
    collected_at: datetime | None
    valid_until: datetime | None
    age_seconds: int | None
    status: FreshnessStatus
    source: str | None = None
    note: str | None = None

    @property
    def usable(self) -> bool:
        return self.status in ("FRESH", "AGING", "STALE")

    @property
    def penalty(self) -> float:
        """Multiplicador 0–1 aplicado à confiança."""
        return {"FRESH": 1.0, "AGING": 0.95, "STALE": 0.8, "EXPIRED": 0.0, "UNAVAILABLE": 0.0}[self.status]


def assess(kind: str, collected_at: datetime | None, *, now: datetime | None = None, source: str | None = None, note: str | None = None) -> Freshness:
    label = LABELS.get(kind, kind)
    if collected_at is None:
        return Freshness(kind=kind, label=label, collected_at=None, valid_until=None, age_seconds=None, status="UNAVAILABLE", source=source, note=note or "Fonte não disponível.")
    policy = POLICIES.get(kind, POLICIES["stats"])
    now = now or datetime.utcnow()
    age = now - collected_at
    if age < timedelta(0):
        age = timedelta(0)
    return Freshness(
        kind=kind, label=label, collected_at=collected_at, valid_until=collected_at + policy.stale,
        age_seconds=int(age.total_seconds()), status=policy.status_for(age), source=source, note=note,
    )


def unavailable(kind: str, note: str) -> Freshness:
    return Freshness(kind=kind, label=LABELS.get(kind, kind), collected_at=None, valid_until=None, age_seconds=None, status="UNAVAILABLE", note=note)


def worst(items: list[Freshness], kinds: set[str] | None = None) -> FreshnessStatus | None:
    order = ["FRESH", "AGING", "STALE", "EXPIRED"]
    picked = [f for f in items if (kinds is None or f.kind in kinds) and f.status != "UNAVAILABLE"]
    if not picked:
        return None
    return max((f.status for f in picked), key=order.index)  # type: ignore[return-value]


def describe_age(age_seconds: int | None) -> str:
    if age_seconds is None:
        return "não disponível"
    s = age_seconds
    if s < 60:
        return f"há {s} s"
    if s < 3600:
        return f"há {s // 60} min"
    if s < 86400:
        h = s // 3600
        return f"há {h} h"
    d = s // 86400
    return "hoje" if d == 0 else f"há {d} dia{'s' if d > 1 else ''}"
