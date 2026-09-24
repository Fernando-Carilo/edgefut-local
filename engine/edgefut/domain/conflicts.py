"""Divergência entre fontes, como exposta ao frontend."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ConflictOut(BaseModel):
    id: int
    field: str
    field_label: str
    source_a: str
    value_a: Any
    source_b: str
    value_b: Any
    selected_value: Any
    selected_source: str | None
    resolution_method: str
    resolution_label: str
    confidence: float
    created_at: datetime
