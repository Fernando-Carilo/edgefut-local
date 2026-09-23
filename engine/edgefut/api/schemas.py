"""Schemas de request/response da API (além dos tipos em domain/analysis.py)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..domain.analysis import EventSummary, MarketOdds, NoBetReason, Recommendation


class HealthResponse(BaseModel):
    status: str
    version: str
    host: str
    port: int
    db_path: str
    datasets: int
    scheduler: dict
    superbet_enabled: bool
    ollama_available: bool


class EventListResponse(BaseModel):
    events: list[EventSummary]
    total: int
    window: str
    generated_at: datetime


class EventDetailResponse(BaseModel):
    event: EventSummary
    markets: list[MarketOdds]


class VenueOverride(BaseModel):
    neutral: bool
    name: str | None = None
    city: str | None = None
    country: str | None = None


class OddsPoint(BaseModel):
    collected_at: datetime
    price: float


class OddsHistoryResponse(BaseModel):
    event_id: int
    series: dict[str, list[OddsPoint]]  # "1X2|HOME|None" → pontos


class RadarItem(BaseModel):
    event: EventSummary
    recommendation: Recommendation | None
    opportunity_score: float
    confidence_grade: str
    data_quality: float
    no_bet_reason: NoBetReason | None = None


class RadarCard(BaseModel):
    key: str
    title: str
    subtitle: str
    items: list[RadarItem]


class RadarResponse(BaseModel):
    generated_at: datetime
    analyzed_events: int
    refreshing: bool
    cards: list[RadarCard]


class EntryRow(BaseModel):
    event: EventSummary
    recommendation: Recommendation


class EntriesResponse(BaseModel):
    rows: list[EntryRow]
    total: int


class DashboardResponse(BaseModel):
    greeting: str
    user_name: str
    analyzed_today: int
    confidence_a: int
    confidence_b: int
    discarded_markets: int
    last_update: datetime | None
    top_opportunities: list[EntryRow]
    popular_events: list[EventSummary]
    scheduler: dict


class SimulatorRequest(BaseModel):
    stake: float = Field(gt=0)
    odd: float = Field(gt=1)
    model_prob: float | None = Field(default=None, ge=0, le=1)


class SimulatorResponse(BaseModel):
    stake: float
    odd: float
    gross_return: float
    profit: float
    implied_probability: float
    model_probability: float | None
    ev_pct: float | None
    edge_pp: float | None
    note: str = "Simulação apenas. O EdgeFut não realiza apostas."


class StakeRequest(BaseModel):
    bankroll: float = Field(gt=0)
    odd: float = Field(gt=1)
    model_prob: float = Field(gt=0, lt=1)
    method: Literal["fixed", "percent", "kelly"] = "kelly"
    fixed_value: float | None = None
    percent: float | None = Field(default=None, gt=0, le=100)
    kelly_fraction: float = Field(default=0.25, gt=0, le=1)
    max_stake_pct: float = Field(default=5.0, gt=0, le=100)


class StakeResponse(BaseModel):
    method: str
    stake: float
    stake_pct: float
    kelly_full_pct: float
    kelly_fraction_used: float
    warning: str | None


class MultipleLeg(BaseModel):
    event_id: int
    market_key: str
    selection_key: str
    line: float | None = None
    odd: float
    label: str | None = None


class MultipleRequest(BaseModel):
    legs: list[MultipleLeg] = Field(min_length=1, max_length=6)


class MultipleResponse(BaseModel):
    legs: int
    combined_odd: float
    naive_probability: float
    joint_probability: float | None
    implied_probability: float
    ev_pct: float | None
    correlations: list[str]
    warnings: list[str]
    per_event: list[dict]


class ChatRequest(BaseModel):
    event_id: int
    question: str = Field(min_length=2, max_length=500)


class ChatResponse(BaseModel):
    answer: str
    engine: Literal["templates", "ollama"]
    event_id: int
    grounded: bool = True


class SearchResponse(BaseModel):
    query: str
    events: list[EventSummary]
    teams: list[dict]
    competitions: list[dict]


class SettingsModel(BaseModel):
    user_name: str = "Fernando"
    default_simulations: int = 50_000
    min_edge_pp: float = 3.0
    min_ev_pct: float = 3.0
    min_odd: float = 1.20
    max_odd: float = 6.00
    bankroll: float = 0.0
    kelly_fraction_max: float = 0.25
    ollama_enabled: bool = False
    ollama_model: str = "llama3.1"
    events_refresh_min: int = 15
    odds_refresh_min: int = 5


class BootstrapStatus(BaseModel):
    running: bool
    done: bool
    steps: list[dict]
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
