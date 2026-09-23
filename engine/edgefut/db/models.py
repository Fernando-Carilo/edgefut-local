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
