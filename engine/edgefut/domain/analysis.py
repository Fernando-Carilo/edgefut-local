"""Tipos estruturados da análise. O frontend só renderiza isto."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .conflicts import ConflictOut
from .freshness import Freshness, FreshnessStatus
from .provenance import Provenance, SourceAttempt

VenueStatus = Literal["CONFIRMED_HOME", "NEUTRAL", "UNCONFIRMED"]
Grade = Literal["A", "B", "C", "D"]
RecommendationStatus = Literal["RECOMMENDED", "WATCH", "NO_BET"]
NoBetReason = Literal[
    "LOW_DATA",
    "LOW_CONFIDENCE",
    "NO_EDGE",
    "MODEL_DISAGREEMENT",
    "UNRELIABLE_SOURCE",
    "SMALL_SAMPLE",
    "LINEUP_UNCERTAINTY",
    "EXTREME_ODDS_MOVEMENT",
    "UNSUPPORTED_COMPETITION",
    "STALE_DATA",
    "QUALITY_GATE",
]


class WindowStats(BaseModel):
    window: str  # all_5 | all_10 | all_20 | home_10 | away_10
    n: int
    wins: int = 0
    draws: int = 0
    losses: int = 0
    points_per_game: float | None = None
    goals_for: float | None = None
    goals_against: float | None = None
    shots_for: float | None = None
    shots_against: float | None = None
    sot_for: float | None = None
    sot_against: float | None = None
    conversion: float | None = None  # gols / chutes no alvo
    corners_for: float | None = None
    corners_against: float | None = None
    cards: float | None = None
    cards_against: float | None = None
    clean_sheet_pct: float | None = None
    btts_pct: float | None = None
    over15_pct: float | None = None
    over25_pct: float | None = None
    over35_pct: float | None = None


class RecentMatch(BaseModel):
    date: datetime
    home: str
    away: str
    hg: int
    ag: int
    competition: str | None = None
    result_for_team: Literal["V", "E", "D"] | None = None
    neutral: bool | None = None


class TeamProfile(BaseModel):
    name: str  # nome Superbet
    canonical: str | None  # nome no dataset
    dataset_code: str | None
    match_method: str
    match_confidence: float
    is_national: bool = False
    elo: float | None = None
    strength_score: float | None = None  # 0-100
    attack: float | None = None
    defense: float | None = None
    form: list[str] = Field(default_factory=list)  # mais recente primeiro
    recent: list[RecentMatch] = Field(default_factory=list)
    windows: dict[str, WindowStats] = Field(default_factory=dict)
    sample_size: int = 0
    provenance: Provenance | None = None


class H2HSummary(BaseModel):
    matches: int
    home_wins: int
    draws: int
    away_wins: int
    home_goals: int
    away_goals: int
    avg_goals: float | None
    btts_pct: float | None
    over25_pct: float | None
    avg_corners: float | None = None
    avg_cards: float | None = None
    recent: list[RecentMatch] = Field(default_factory=list)
    provenance: Provenance | None = None
    weight_note: str = "H2H tem peso menor que a forma atual e não decide a previsão."


class VenueInfo(BaseModel):
    status: VenueStatus
    neutral: bool | None
    name: str | None = None
    city: str | None = None
    country: str | None = None
    source: str | None = None
    confidence: float = 0.0
    note: str | None = None
    home_advantage_weight: float = 1.0  # 1 confirmado, 0 neutro, 0.5 não confirmado
    listing_swapped: bool = False  # fonte histórica lista o mandante invertido em relação à Superbet


class GoalsModelOutput(BaseModel):
    model_version: str
    available: bool = True
    lambda_home: float | None = None
    lambda_away: float | None = None
    rho: float | None = None
    p_home: float | None = None
    p_draw: float | None = None
    p_away: float | None = None
    dist_home: list[float] = Field(default_factory=list)  # P(0..5+)
    dist_away: list[float] = Field(default_factory=list)
    over: dict[str, float] = Field(default_factory=dict)  # "1.5": p
    under: dict[str, float] = Field(default_factory=dict)
    btts: float | None = None
    top_scores: list[tuple[str, float]] = Field(default_factory=list)
    fit_matches: int = 0
    note: str | None = None


class EloOutput(BaseModel):
    model_version: str
    available: bool
    home_elo: float | None = None
    away_elo: float | None = None
    p_home: float | None = None
    p_draw: float | None = None
    p_away: float | None = None
    home_advantage_points: float = 0.0
    matches_used: int = 0
    note: str | None = None


class CountDistribution(BaseModel):
    """Escanteios / cartões / finalizações esperados por time e total."""

    model_version: str
    available: bool
    expected_home: float | None = None
    expected_away: float | None = None
    expected_total: float | None = None
    likely_range: tuple[float, float] | None = None
    over: dict[str, float] = Field(default_factory=dict)
    under: dict[str, float] = Field(default_factory=dict)
    p_home_more: float | None = None
    p_away_more: float | None = None
    extra: dict[str, float] = Field(default_factory=dict)
    sample_size: int = 0
    note: str | None = None
    provenance: Provenance | None = None


class SimulationOutput(BaseModel):
    model_version: str
    simulations: int
    seed: int
    base_model: str
    p_home: float
    p_draw: float
    p_away: float
    p_home_draw: float
    p_draw_away: float
    p_home_away: float
    p_dnb_home: float
    p_dnb_away: float
    over: dict[str, float]
    under: dict[str, float]
    btts: float
    expected_goals_home: float
    expected_goals_away: float
    total_goals_dist: list[float]  # P(0..6+)
    top_scores: list[tuple[str, float]]
    most_likely_score: str
    team_totals_home_over: dict[str, float] = Field(default_factory=dict)
    team_totals_away_over: dict[str, float] = Field(default_factory=dict)
    first_goal: dict[str, float] = Field(default_factory=dict)
    handicap_home: dict[str, float] = Field(default_factory=dict)  # linha → P(cobre)


class SelectionOdds(BaseModel):
    key: str
    name: str
    price: float
    implied: float
    fair: float | None = None
    opening_price: float | None = None
    movement_pct: float | None = None
    direction: Literal["up", "down", "flat"] | None = None
    model_prob: float | None = None
    edge_pp: float | None = None
    ev_pct: float | None = None


class MarketOdds(BaseModel):
    market_key: str
    label: str
    line: float | None = None
    selections: list[SelectionOdds]
    overround: float | None = None
    margin_removed: bool = False
    collected_at: datetime | None = None
    source: str = "superbet"
    source_url: str | None = None


class ConfidenceComponent(BaseModel):
    name: str
    weight: float
    value: float  # 0-1
    note: str | None = None


class ConfidenceBreakdown(BaseModel):
    model_version: str
    score: float
    grade: Grade
    components: list[ConfidenceComponent]


class DataQualityCheck(BaseModel):
    ok: bool
    label: str
    weight: float = 1.0


class DataQuality(BaseModel):
    score: float  # 0-100
    checks: list[DataQualityCheck]


class Recommendation(BaseModel):
    market_key: str
    market_label: str
    selection_key: str
    selection_name: str
    line: float | None
    odd: float
    model_prob: float
    market_prob: float
    market_prob_is_fair: bool
    edge_pp: float
    ev_pct: float
    confidence_score: float
    confidence_grade: Grade
    opportunity_score: float
    status: RecommendationStatus
    reasons: list[str] = Field(default_factory=list)
    explanation: str | None = None
    category: Literal["GOLS", "RESULTADO", "ESCANTEIOS", "CARTOES", "FINALIZACOES", "JOGADOR", "OUTRO"] = "OUTRO"


class NoBetVerdict(BaseModel):
    no_bet: bool
    reason: NoBetReason | None = None
    detail: str | None = None


class EventSummary(BaseModel):
    id: int
    home_name: str
    away_name: str
    kickoff_utc: datetime
    competition_name: str | None
    category_name: str | None
    status: str
    market_count: int
    event_url: str | None
    venue_status: VenueStatus
    neutral_venue: bool | None
    odds_collected_at: datetime | None = None
    main_odds: dict[str, float] | None = None  # 1X2 rápido
    is_favorite: bool = False
    # preenchidos pelo radar quando há análise em cache
    opportunity_score: float | None = None
    confidence_grade: Grade | None = None
    data_quality: float | None = None
    best_market: str | None = None
    no_bet_reason: NoBetReason | None = None
    demo: bool = False


class MatchAnalysis(BaseModel):
    generated_at: datetime
    pipeline_version: str
    model_versions: dict[str, str]
    event: EventSummary
    venue: VenueInfo
    home: TeamProfile
    away: TeamProfile
    h2h: H2HSummary | None
    elo: EloOutput
    poisson: GoalsModelOutput
    dixon_coles: GoalsModelOutput
    simulation: SimulationOutput | None
    corners: CountDistribution
    cards: CountDistribution
    shots: CountDistribution
    markets: list[MarketOdds]
    recommendations: list[Recommendation]
    no_bet: NoBetVerdict
    data_quality: DataQuality
    confidence: ConfidenceBreakdown
    opportunity_score: float
    model_disagreement_pp: float | None
    explanation: str
    sources: list[Provenance]
    source_attempts: list[SourceAttempt]
    snapshot_id: int | None = None
    warnings: list[str] = Field(default_factory=list)
    # Iteração 2 — confiança nos dados
    freshness: list[Freshness] = Field(default_factory=list)
    freshness_status: FreshnessStatus | None = None  # pior status entre odds/forma/histórico
    conflicts: list[ConflictOut] = Field(default_factory=list)
    conflicts_count: int = 0
    canonical_event_id: str | None = None
