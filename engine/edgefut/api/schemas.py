"""Schemas de request/response da API (além dos tipos em domain/analysis.py)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..domain.analysis import EventSummary, LineMovement, MarketOdds, NoBetReason, Recommendation


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
    movement: dict[str, LineMovement] = {}  # resumo abertura/atual/mín/máx por seleção
    kickoff_utc: datetime | None = None
    closing: dict[str, float] = {}  # closing line fixada (só após kickoff)


class RadarItem(BaseModel):
    event: EventSummary
    recommendation: Recommendation | None
    opportunity_score: float
    confidence_grade: str
    data_quality: float
    no_bet_reason: NoBetReason | None = None
    label: str | None = None  # MODEL_FAVORITE | MODEL_ONLY | WATCH | VALUE_CANDIDATE | VALUE | NO_BET
    quality_gate_passed: bool = False
    freshness_status: str | None = None
    evidence: str | None = None  # SETTLED | BACKTEST_ODDS | MODEL_ONLY
    why: list[str] = Field(default_factory=list)  # 2 primeiras razões (WHY / WHY NOT)
    # Iteração 3
    state: str | None = None  # estado da seleção primária mostrada
    cluster_id: str | None = None  # tese da seleção primária
    cluster_label: str | None = None
    alternatives: int = 0  # seleções alternativas da mesma tese (não contam como oportunidade)
    exposure: str | None = None  # LOW | MEDIUM | HIGH (evento)
    actionable_clusters: int = 0  # teses acionáveis no evento


class RadarCard(BaseModel):
    key: str
    title: str
    subtitle: str
    items: list[RadarItem]


class RadarSummary(BaseModel):
    """Contadores do cabeçalho do Radar V2 — sempre sobre a janela pedida."""

    last_update: datetime | None
    events_found: int  # eventos futuros na janela (Superbet), sem duplicatas
    with_sufficient_data: int  # analisados sem NO BET por dados (UNSUPPORTED/LOW_DATA/SMALL_SAMPLE/UNRELIABLE)
    analyzed: int
    quality_gate_passed: int
    confidence_a: int
    confidence_b: int
    high_probability: int
    value: int
    watch: int
    no_bet: int
    stale: int  # análises com freshness STALE/EXPIRED
    alerts_unread: int = 0
    no_bet_by_reason: dict[str, int] = Field(default_factory=dict)
    gate_passed_by_evidence: dict[str, int] = Field(default_factory=dict)  # SETTLED / BACKTEST_ODDS / MODEL_ONLY
    # Iteração 3 — eventos pelo estado da melhor primária + clusters (oportunidades sem duplicidade)
    events_by_state: dict[str, int] = Field(default_factory=dict)  # MODEL_ONLY / MARKET_OBSERVED / VALUE_CANDIDATE / VALUE / OBSERVATION / NO_BET
    value_candidates: int = 0
    research_signals: int = 0  # iteração 5 — eventos cuja melhor primária é RESEARCH_SIGNAL (VALUE desativado)
    model_only: int = 0
    actionable_clusters: int = 0  # soma de teses acionáveis (primárias VALUE/VALUE_CANDIDATE) — "oportunidades reais"
    selections_actionable: int = 0  # seleções RECOMMENDED (inclui alternativas) — para mostrar a redundância evitada
    exposure_high: int = 0  # eventos com exposição HIGH


class RadarResponse(BaseModel):
    generated_at: datetime
    analyzed_events: int
    refreshing: bool
    cards: list[RadarCard]
    summary: RadarSummary | None = None
    thresholds: dict[str, float] = Field(default_factory=dict)  # limiares em vigor (transparência)


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
    # Iteração 2 — workflow da manhã
    morning_summary: str = ""
    cta: str = "VER RADAR"
    events_found: int = 0
    quality_gate_passed: int = 0
    watch: int = 0
    no_bet: int = 0
    alerts_unread: int = 0
    health_overall: str | None = None
    # Iteração 3 — MODEL HEALTH (discreto)
    model_health: dict | None = None  # champion, last_replay (vs mercado), drift status, unsettled, shadow N
    value: int = 0
    value_candidates: int = 0
    research_signals: int = 0
    model_only: int = 0
    actionable_clusters: int = 0


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
    disabled: bool = False  # iteração 5 §64 — staking DISABLED enquanto MARKET_EDGE != VALIDATED
    disabled_reason: str | None = None


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
    margin_method: Literal["MULTIPLICATIVE", "SHIN"] = "MULTIPLICATIVE"
    live_poll_seconds: int = 30
    # Quality Gate / rótulos / Opportunity V2 (pisos em core.config.HARD_FLOORS)
    gate_min_data_quality: float = 60.0
    gate_min_confidence: float = 65.0
    gate_min_sample: int = 15
    gate_max_disagreement_pp: float = 10.0
    gate_max_edge_pp_uncalibrated: float = 15.0
    high_probability_min: float = 0.65
    opportunity_weights: dict[str, float] = Field(default_factory=dict)  # vazio = padrão do engine
    # Iteração 5 (§46–§47) — Windows always-on: o shell Tauri lê estas flags
    background_collector: bool = True  # fechar a janela mantém o coletor (bandeja); False = fechar encerra tudo
    autostart_on_login: bool = False  # iniciar com o Windows (minimizado na bandeja)
    notifications_enabled: bool = True  # notificações locais (nunca "BET NOW")
    backup_enabled: bool = True  # backup diário 7/4/3


class BootstrapStatus(BaseModel):
    running: bool
    done: bool
    steps: list[dict]
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
