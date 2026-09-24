"""Modelos SQLAlchemy (SQLite — aplicação, eventos, odds, snapshots)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Competition(Base):
    __tablename__ = "competition"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # Superbet tournamentId
    name: Mapped[str] = mapped_column(String(160))
    category_id: Mapped[int | None] = mapped_column(Integer)
    category_name: Mapped[str | None] = mapped_column(String(120))
    dataset_code: Mapped[str | None] = mapped_column(String(32))  # ex.: E0, INTL
    is_national_teams: Mapped[bool] = mapped_column(Boolean, default=False)
    is_womens: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Event(Base):
    __tablename__ = "event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # Superbet eventId
    competition_id: Mapped[int | None] = mapped_column(ForeignKey("competition.id"))
    competition_name: Mapped[str | None] = mapped_column(String(160))
    category_name: Mapped[str | None] = mapped_column(String(120))
    home_name: Mapped[str] = mapped_column(String(120))
    away_name: Mapped[str] = mapped_column(String(120))
    home_team_ext_id: Mapped[str | None] = mapped_column(String(32))
    away_team_ext_id: Mapped[str | None] = mapped_column(String(32))
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(24), default="prematch")
    market_count: Mapped[int] = mapped_column(Integer, default=0)
    event_url: Mapped[str | None] = mapped_column(String(300))
    # venue
    venue_status: Mapped[str] = mapped_column(String(16), default="UNCONFIRMED")  # CONFIRMED_HOME | NEUTRAL | UNCONFIRMED
    neutral_venue: Mapped[bool | None] = mapped_column(Boolean)
    venue_name: Mapped[str | None] = mapped_column(String(160))
    venue_city: Mapped[str | None] = mapped_column(String(120))
    venue_country: Mapped[str | None] = mapped_column(String(120))
    venue_source: Mapped[str | None] = mapped_column(String(64))
    venue_confidence: Mapped[float | None] = mapped_column(Float)
    # resultado (settlement)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    result_source: Mapped[str | None] = mapped_column(String(64))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime)
    # reconciliação: todo evento encerrado está em SETTLED | SETTLEMENT_PENDING | SETTLEMENT_ERROR
    settlement_status: Mapped[str | None] = mapped_column(String(24), index=True)
    settlement_attempts: Mapped[int] = mapped_column(Integer, default=0)
    settlement_error: Mapped[str | None] = mapped_column(String(300))
    settlement_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    # metadados
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    odds_collected_at: Mapped[datetime | None] = mapped_column(DateTime)
    raw: Mapped[dict | None] = mapped_column(JSON)
    # identidade canônica (CanonicalEventResolver)
    canonical_event_id: Mapped[str | None] = mapped_column(String(200), index=True)
    home_canonical: Mapped[str | None] = mapped_column(String(120))
    away_canonical: Mapped[str | None] = mapped_column(String(120))
    duplicate_of: Mapped[int | None] = mapped_column(Integer)  # eventId principal quando esta linha é duplicata
    # ao vivo (observação)
    live_status: Mapped[str | None] = mapped_column(String(24))
    live_minute: Mapped[int | None] = mapped_column(Integer)
    live_home_score: Mapped[int | None] = mapped_column(Integer)
    live_away_score: Mapped[int | None] = mapped_column(Integer)
    live_collected_at: Mapped[datetime | None] = mapped_column(DateTime)


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshot"
    __table_args__ = (
        Index("ix_odds_event_market", "event_id", "market_key", "selection_key", "line"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"), index=True)
    market_key: Mapped[str] = mapped_column(String(40))
    market_name: Mapped[str] = mapped_column(String(160))
    selection_key: Mapped[str] = mapped_column(String(80))
    selection_name: Mapped[str] = mapped_column(String(160))
    line: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="active")
    source: Mapped[str] = mapped_column(String(32), default="superbet")
    source_url: Mapped[str | None] = mapped_column(String(300))
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class TeamAlias(Base):
    __tablename__ = "team_alias"
    __table_args__ = (UniqueConstraint("alias", "dataset_code", name="uq_alias_dataset"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias: Mapped[str] = mapped_column(String(120), index=True)  # nome Superbet normalizado
    canonical: Mapped[str] = mapped_column(String(120))  # nome no dataset
    dataset_code: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(32), default="curated")  # curated | fuzzy | user


class PredictionSnapshot(Base):
    __tablename__ = "prediction_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime)
    home_name: Mapped[str] = mapped_column(String(120))
    away_name: Mapped[str] = mapped_column(String(120))
    competition_name: Mapped[str | None] = mapped_column(String(160))
    model_version: Mapped[str] = mapped_column(String(64))
    model_versions: Mapped[dict] = mapped_column(JSON)
    features: Mapped[dict] = mapped_column(JSON)
    probabilities: Mapped[dict] = mapped_column(JSON)
    odds: Mapped[dict] = mapped_column(JSON)
    recommendations: Mapped[list] = mapped_column(JSON)
    confidence_grade: Mapped[str | None] = mapped_column(String(2))
    data_quality: Mapped[float | None] = mapped_column(Float)
    no_bet_reason: Mapped[str | None] = mapped_column(String(40))
    # settlement — nunca altera os campos acima
    result: Mapped[dict | None] = mapped_column(JSON)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime)
    # closing line (gravada após o kickoff) — usada só para CLV, nunca para recomendar
    closing_odds: Mapped[dict | None] = mapped_column(JSON)


PREDICTION_FIELDS = frozenset(
    {
        "event_id", "created_at", "kickoff_utc", "home_name", "away_name", "competition_name", "model_version",
        "model_versions", "features", "probabilities", "odds", "recommendations", "confidence_grade", "data_quality",
        "no_bet_reason",
    }
)


class SnapshotCorrection(Base):
    """Correções de resultado geram evento; a previsão em si nunca é alterada."""

    __tablename__ = "snapshot_correction"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("prediction_snapshot.id"), index=True)
    field: Mapped[str] = mapped_column(String(40))
    old_value: Mapped[dict | None] = mapped_column(JSON)
    new_value: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ClosingLine(Base):
    """Última odd observada antes do kickoff, por seleção."""

    __tablename__ = "closing_line"
    __table_args__ = (UniqueConstraint("event_id", "market_key", "selection_key", "line", name="uq_closing_sel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"), index=True)
    market_key: Mapped[str] = mapped_column(String(40))
    selection_key: Mapped[str] = mapped_column(String(80))
    line: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    collected_at: Mapped[datetime] = mapped_column(DateTime)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime)
    minutes_before_kickoff: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SourceConflict(Base):
    __tablename__ = "source_conflict"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("event.id"), index=True)
    canonical_event_id: Mapped[str | None] = mapped_column(String(200), index=True)
    field: Mapped[str] = mapped_column(String(40))
    source_a: Mapped[str] = mapped_column(String(64))
    value_a: Mapped[dict | None] = mapped_column(JSON)
    source_b: Mapped[str] = mapped_column(String(64))
    value_b: Mapped[dict | None] = mapped_column(JSON)
    selected_value: Mapped[dict | None] = mapped_column(JSON)
    selected_source: Mapped[str | None] = mapped_column(String(64))
    resolution_method: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ModelRegistry(Base):
    __tablename__ = "model_registry"
    __table_args__ = (UniqueConstraint("model_id", "version", name="uq_model_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    training_window: Mapped[dict | None] = mapped_column(JSON)
    features: Mapped[list | None] = mapped_column(JSON)
    parameters: Mapped[dict | None] = mapped_column(JSON)
    metrics: Mapped[dict | None] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    # governança: champion (em produção) | challenger (shadow) | baseline | none
    role: Mapped[str | None] = mapped_column(String(16))


class ShadowPrediction(Base):
    """Shadow mode — toda recomendação gerada para um evento futuro é registrada aqui,
    sem interação do usuário. Append-only: as colunas de previsão nunca são atualizadas;
    só `result`/`won`/`settled_at`/`closing_odd` são preenchidas depois pelo settlement."""

    __tablename__ = "shadow_prediction"
    __table_args__ = (
        Index("ix_shadow_event_sel", "event_id", "market_key", "selection_key", "line"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"), index=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("prediction_snapshot.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime, index=True)
    competition_name: Mapped[str | None] = mapped_column(String(160))
    dataset_code: Mapped[str | None] = mapped_column(String(32))
    market_key: Mapped[str] = mapped_column(String(40))
    selection_key: Mapped[str] = mapped_column(String(80))
    line: Mapped[float | None] = mapped_column(Float)
    odd: Mapped[float | None] = mapped_column(Float)
    model_prob: Mapped[float] = mapped_column(Float)
    model_prob_raw: Mapped[float | None] = mapped_column(Float)
    model_prob_calibrated: Mapped[float | None] = mapped_column(Float)
    market_prob: Mapped[float | None] = mapped_column(Float)
    edge_pp: Mapped[float | None] = mapped_column(Float)
    ev_pct: Mapped[float | None] = mapped_column(Float)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    opportunity_score: Mapped[float | None] = mapped_column(Float)
    data_quality: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(24))  # MODEL_ONLY | MARKET_OBSERVED | VALUE_CANDIDATE | VALUE | OBSERVATION | NO_BET
    evidence: Mapped[str | None] = mapped_column(String(16))
    cluster_id: Mapped[str | None] = mapped_column(String(64))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    model_version: Mapped[str] = mapped_column(String(64))
    model_versions: Mapped[dict | None] = mapped_column(JSON)
    # iteração 4 — registrados no instante da previsão (nunca recalculados): probabilidade do híbrido
    # congelado (market-aware), residual edge vs mercado, required edge e edge ajustado por incerteza,
    # e a distância ao kickoff no momento da previsão.
    hybrid_prob: Mapped[float | None] = mapped_column(Float)
    residual_edge_pp: Mapped[float | None] = mapped_column(Float)
    required_edge_pp: Mapped[float | None] = mapped_column(Float)
    adjusted_edge_pp: Mapped[float | None] = mapped_column(Float)
    minutes_to_kickoff: Mapped[int | None] = mapped_column(Integer)
    # liquidação (únicos campos escritos depois)
    won: Mapped[bool | None] = mapped_column(Boolean)
    result: Mapped[dict | None] = mapped_column(JSON)
    closing_odd: Mapped[float | None] = mapped_column(Float)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime)


SHADOW_PREDICTION_FIELDS = frozenset(
    {
        "event_id", "snapshot_id", "created_at", "kickoff_utc", "competition_name", "dataset_code", "market_key",
        "selection_key", "line", "odd", "model_prob", "model_prob_raw", "model_prob_calibrated", "market_prob",
        "edge_pp", "ev_pct", "confidence_score", "opportunity_score", "data_quality", "state", "evidence",
        "cluster_id", "is_primary", "model_version", "model_versions",
        "hybrid_prob", "residual_edge_pp", "required_edge_pp", "adjusted_edge_pp", "minutes_to_kickoff",
    }
)


class ValidationRun(Base):
    """Resultado persistido de uma validação (replay histórico, comparação de modelos, drift)."""

    __tablename__ = "validation_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # replay | model_comparison | drift | daily_report
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    request: Mapped[dict | None] = mapped_column(JSON)
    summary: Mapped[dict | None] = mapped_column(JSON)
    detail: Mapped[dict | None] = mapped_column(JSON)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class CalibrationModel(Base):
    """Calibração isotônica por grupo (mercado × grupo de competição/modelo)."""

    __tablename__ = "calibration_model"

    group_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    method: Mapped[str] = mapped_column(String(24), default="isotonic")
    n: Mapped[int] = mapped_column(Integer, default=0)
    thresholds: Mapped[list] = mapped_column(JSON)  # x (probabilidade crua)
    values: Mapped[list] = mapped_column(JSON)  # y (frequência calibrada)
    brier_raw: Mapped[float | None] = mapped_column(Float)
    brier_calibrated: Mapped[float | None] = mapped_column(Float)
    reliable: Mapped[bool] = mapped_column(Boolean, default=False)
    fitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class JobRun(Base):
    __tablename__ = "job_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(48), index=True)
    correlation_id: Mapped[str] = mapped_column(String(36))
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | error | skipped
    records_processed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list | None] = mapped_column(JSON)
    detail: Mapped[dict | None] = mapped_column(JSON)


class Alert(Base):
    __tablename__ = "alert"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)  # ODD_MOVEMENT | DATA_QUALITY_CHANGE | ...
    event_id: Mapped[int | None] = mapped_column(ForeignKey("event.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[dict | None] = mapped_column(JSON)
    severity: Mapped[str] = mapped_column(String(12), default="info")  # info | warning
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


class SourceLog(Base):
    __tablename__ = "source_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(48), index=True)
    url: Mapped[str] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(16))  # ok | cached | error | blocked
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Favorite(Base):
    __tablename__ = "favorite"

    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DatasetState(Base):
    __tablename__ = "dataset_state"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)  # E0-2425, INTL
    provider: Mapped[str] = mapped_column(String(48))
    source_url: Mapped[str] = mapped_column(String(400))
    rows: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    parquet_path: Mapped[str | None] = mapped_column(String(400))


# ---------------------------------------------------------------------------
# Iteração 5 — SUPERBET DATA FLYWHEEL
# ---------------------------------------------------------------------------


class RawSuperbetSnapshot(Base):
    """Camada RAW, append-only e imutável (triggers SQLite impedem UPDATE/DELETE).

    Uma linha por **fetch real** (nunca por resposta em cache) de `/events/{id}`. O payload
    comprimido (zlib) só é guardado quando o hash muda; quando a Superbet devolve exatamente o
    mesmo payload, grava-se uma linha de *confirmação* (`payload=None`, `payload_ref_id` →
    linha com o payload) — a observação no tempo fica registada sem duplicar bytes.
    """

    __tablename__ = "raw_superbet_snapshot"
    __table_args__ = (
        Index("ix_raw_sb_event_time", "event_id", "fetched_at"),
        UniqueConstraint("event_id", "fetched_at", name="uq_raw_sb_event_fetched"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True)  # Superbet eventId (sem FK: raw não depende de `event`)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    source_url: Mapped[str] = mapped_column(String(300))
    http_status: Mapped[int | None] = mapped_column(Integer)
    source_version: Mapped[str] = mapped_column(String(32))  # superbet-offer-v2
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[bytes | None] = mapped_column(LargeBinary)  # zlib(JSON) ou None (confirmação)
    payload_ref_id: Mapped[int | None] = mapped_column(Integer)  # linha que contém o payload idêntico
    payload_bytes: Mapped[int] = mapped_column(Integer, default=0)  # bytes comprimidos gravados nesta linha
    raw_bytes: Mapped[int] = mapped_column(Integer, default=0)  # tamanho do JSON original
    kickoff_utc: Mapped[datetime | None] = mapped_column(DateTime)
    minutes_to_kickoff: Mapped[float | None] = mapped_column(Float)
    event_state: Mapped[str | None] = mapped_column(String(24))  # prematch | live | finished | unknown
    odds_total: Mapped[int] = mapped_column(Integer, default=0)
    odds_mapped: Mapped[int] = mapped_column(Integer, default=0)
    odds_unknown: Mapped[int] = mapped_column(Integer, default=0)
    odds_out_of_scope: Mapped[int] = mapped_column(Integer, default=0)
    parse_failures: Mapped[int] = mapped_column(Integer, default=0)
    markets_present: Mapped[list | None] = mapped_column(JSON(none_as_null=True))  # categorias canónicas presentes no payload
    schema_issues: Mapped[list | None] = mapped_column(JSON(none_as_null=True))  # campos obrigatórios em falta
    snapshot_target: Mapped[str | None] = mapped_column(String(12))  # T-48h … T-5m quando esta coleta cobre um alvo


class SuperbetNormalized(Base):
    """Camada PROCESSADA `superbet_normalized_v1` (append-only): uma linha por seleção observada
    num raw snapshot, gravada quando o preço mudou ou quando a coleta cobre um alvo T-x."""

    __tablename__ = "superbet_normalized_v1"
    __table_args__ = (
        Index("ix_sbn_event_market", "event_id", "canonical_market_id", "selection_id"),
        Index("ix_sbn_market_time", "canonical_market_id", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_snapshot_id: Mapped[int] = mapped_column(ForeignKey("raw_superbet_snapshot.id"), index=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True)
    competition_id: Mapped[int | None] = mapped_column(Integer)
    competition_name: Mapped[str | None] = mapped_column(String(160))
    market_category: Mapped[str] = mapped_column(String(32))  # MATCH_RESULT, TOTAL_GOALS, CORNERS_TOTAL…
    canonical_market_id: Mapped[str] = mapped_column(String(64))  # TOTAL_GOALS_2_5, TEAM_CORNERS_HOME_4_5
    selection_id: Mapped[str] = mapped_column(String(120))  # HOME / OVER / YES / player:12345
    selection_name: Mapped[str] = mapped_column(String(160))
    superbet_market_id: Mapped[int] = mapped_column(Integer)
    line: Mapped[float | None] = mapped_column(Float)
    odd: Mapped[float] = mapped_column(Float)
    implied_prob: Mapped[float] = mapped_column(Float)
    fair_prob: Mapped[float | None] = mapped_column(Float)  # só com mercado completo no mesmo payload
    overround: Mapped[float | None] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    kickoff_utc: Mapped[datetime | None] = mapped_column(DateTime)
    minutes_to_kickoff: Mapped[float | None] = mapped_column(Float)
    event_state: Mapped[str | None] = mapped_column(String(24))
    snapshot_target: Mapped[str | None] = mapped_column(String(12))
    identity_confidence: Mapped[str | None] = mapped_column(String(16))  # STRONG | NAME_ONLY (mercados de jogador)
    normalizer_version: Mapped[str] = mapped_column(String(32))


class SuperbetSettlement(Base):
    """Liquidação por seleção canónica (`superbet_settlement_v1`). Nunca assume derrota: sem
    estatística → UNSETTLED_DATA_MISSING; mercado sem regra → UNSUPPORTED."""

    __tablename__ = "superbet_settlement_v1"
    __table_args__ = (UniqueConstraint("event_id", "canonical_market_id", "selection_id", name="uq_sbs_selection"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True)
    market_category: Mapped[str] = mapped_column(String(32), index=True)
    canonical_market_id: Mapped[str] = mapped_column(String(64))
    selection_id: Mapped[str] = mapped_column(String(120))
    line: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), index=True)  # WON | LOST | VOID | UNSETTLED_DATA_MISSING | UNSUPPORTED | ERROR
    missing_fields: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    result_source: Mapped[str | None] = mapped_column(String(32))
    result: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    settlement_version: Mapped[str] = mapped_column(String(32))
    settled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    error: Mapped[str | None] = mapped_column(String(300))


class MarketMappingRegistry(Base):
    """Todo `marketId` visto na oferta: MAPPED, OUT_OF_SCOPE (com motivo) ou UNKNOWN. Nada é ignorado em silêncio."""

    __tablename__ = "market_mapping_registry"

    superbet_market_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), index=True)  # MAPPED | OUT_OF_SCOPE | UNKNOWN | AMBIGUOUS
    market_category: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(200))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    events_seen: Mapped[int] = mapped_column(Integer, default=0)
    sample_selections: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    specifier_keys: Mapped[list | None] = mapped_column(JSON(none_as_null=True))


class DataQuarantine(Base):
    __tablename__ = "data_quarantine"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reason: Mapped[str] = mapped_column(String(40), index=True)  # DUPLICATE_SNAPSHOT, ODD_LE_1, NEGATIVE_TIMESTAMP, KICKOFF_INCONSISTENT, UNKNOWN_EVENT, UNKNOWN_SELECTION, MARKET_MAPPING_CONFLICT, SCHEMA_CHANGE
    event_id: Mapped[int | None] = mapped_column(Integer, index=True)
    raw_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    payload_excerpt: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    detail: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    resolver_status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)  # OPEN | RESOLVED | IGNORED
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution_note: Mapped[str | None] = mapped_column(String(400))


class CollectorGap(Base):
    """Intervalo sem coleta (engine parado, VM suspensa, fonte indisponível). Registado, nunca preenchido."""

    __tablename__ = "collector_gap"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime)
    minutes: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(32))  # DOWNTIME | SOURCE_UNAVAILABLE | BLOCKED
    events_affected: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExperimentRegistry(Base):
    """Hipóteses pré-registadas (antes do período de confirmação). BH-FDR sobre o conjunto em CONFIRMING."""

    __tablename__ = "experiment_registry"

    hypothesis_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    title: Mapped[str] = mapped_column(String(200))
    market_category: Mapped[str] = mapped_column(String(32), index=True)
    canonical_market_id: Mapped[str | None] = mapped_column(String(64))
    competition: Mapped[str | None] = mapped_column(String(160))
    odds_band: Mapped[str | None] = mapped_column(String(24))
    time_window: Mapped[str | None] = mapped_column(String(24))  # bucket T-x
    feature: Mapped[str] = mapped_column(String(64))  # ex.: superbet_fair_bias, edgefut_vs_fair, clv_at_bucket
    expected_direction: Mapped[str] = mapped_column(String(8))  # + | - | 0
    discovery_start: Mapped[datetime | None] = mapped_column(DateTime)
    discovery_end: Mapped[datetime | None] = mapped_column(DateTime)
    confirmation_start: Mapped[datetime] = mapped_column(DateTime)
    confirmation_end: Mapped[datetime | None] = mapped_column(DateTime)
    min_effective_n: Mapped[int] = mapped_column(Integer, default=200)
    status: Mapped[str] = mapped_column(String(16), default="DISCOVERY", index=True)  # DISCOVERY | CANDIDATE | CONFIRMING | REJECTED | SUPPORTED
    last_evaluation: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)


class ManualCorrection(Base):
    """Trilha de qualquer alteração manual (mapeamento, quarentena, identidade, estado de mercado)."""

    __tablename__ = "manual_correction"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity: Mapped[str] = mapped_column(String(48), index=True)
    entity_id: Mapped[str] = mapped_column(String(80))
    field: Mapped[str] = mapped_column(String(48))
    before: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    after: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    reason: Mapped[str] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MarketEdgeState(Base):
    """Máquina de estados MARKET_EDGE por mercado (nunca global): UNPROVEN → COLLECTING → PROMISING → VALIDATED | REJECTED.
    `value_enabled` só muda por ação explícita do utilizador (trilha em `manual_correction`)."""

    __tablename__ = "market_edge_state"

    market_category: Mapped[str] = mapped_column(String(32), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="UNPROVEN")
    value_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    enablement_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    history: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
