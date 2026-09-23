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
    # metadados
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    odds_collected_at: Mapped[datetime | None] = mapped_column(DateTime)
    raw: Mapped[dict | None] = mapped_column(JSON)


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
