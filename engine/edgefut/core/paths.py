"""Diretórios de dados do EdgeFut.

Em desenvolvimento: `<repo>/data`. Empacotado (PyInstaller/Tauri): `%LOCALAPPDATA%/EdgeFutAI/data`
(Windows) ou `~/.local/share/edgefut-ai/data` (Linux/macOS).
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from .config import settings


def _default_data_dir() -> Path:
    if settings.data_dir:
        return Path(settings.data_dir).expanduser().resolve()
    frozen = getattr(sys, "frozen", False)
    if frozen:
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            return base / "EdgeFutAI" / "data"
        return Path.home() / ".local" / "share" / "edgefut-ai" / "data"
    # dev: engine/edgefut/core/paths.py -> repo root
    return Path(__file__).resolve().parents[3] / "data"


class Paths:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _default_data_dir()
        self.raw = self.root / "raw"
        self.processed = self.root / "processed"
        self.cache = self.root / "cache"
        self.logs = self.root / "logs"
        self.sqlite = self.root / "edgefut.sqlite3"

    def ensure(self) -> "Paths":
        for p in (self.root, self.raw, self.processed, self.cache, self.logs):
            p.mkdir(parents=True, exist_ok=True)
        return self


@lru_cache(maxsize=1)
def get_paths() -> Paths:
    return Paths().ensure()
