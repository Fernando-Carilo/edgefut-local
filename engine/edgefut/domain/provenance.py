from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    """Toda estatística exibida ao usuário carrega isto."""

    source: str
    source_url: str | None = None
    collected_at: datetime | None = None
    confidence: float = Field(ge=0, le=1, default=1.0)
    sample_size: int = 0
    field: str | None = None  # campo/coluna utilizado
    note: str | None = None


class SourceAttempt(BaseModel):
    provider: str
    status: str  # ok | cached | unavailable | unsupported | error
    detail: str | None = None
    url: str | None = None
