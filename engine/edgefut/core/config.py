"""Configuração do engine.

Toda configuração pode ser sobrescrita por variáveis de ambiente `EDGEFUT_*`.
O servidor escuta apenas em 127.0.0.1 — isso não é configurável de propósito.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EDGEFUT_", extra="ignore")

    host: str = Field(default="127.0.0.1", frozen=True)
    port: int = 8765
    data_dir: Path | None = None
    log_level: str = "INFO"

    # Coleta
    superbet_base_url: str = "https://production-superbet-offer-br.freetls.fastly.net/v2/pt-BR"
    superbet_site_url: str = "https://superbet.bet.br"
    superbet_enabled: bool = True
    http_timeout_s: float = 15.0
    user_agent: str = "EdgeFutAI/0.1 (+local desktop analysis; contact: user)"

    # Scheduler (minutos)
    events_refresh_min: int = 15
    odds_refresh_min: int = 5
    history_refresh_hours: int = 24
    scheduler_enabled: bool = True

    # Modelos
    default_simulations: int = 50_000
    min_edge_pp: float = 3.0
    min_ev_pct: float = 3.0
    min_odd: float = 1.20
    max_odd: float = 6.00
    home_advantage_unconfirmed_weight: float = 0.5

    # LLM opcional
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1"
    ollama_enabled: bool = False

    # Banca
    kelly_fraction_max: float = 0.25

    # Primeiro boot
    autostart_bootstrap: bool = True
    bootstrap_leagues: list[str] = Field(
        default_factory=lambda: ["E0", "SP1", "I1", "D1", "F1", "N1", "P1", "B1", "T1", "SC0"]
    )
    bootstrap_seasons: list[str] = Field(default_factory=lambda: ["2324", "2425", "2526"])

    @property
    def host_is_loopback(self) -> bool:
        return self.host in {"127.0.0.1", "localhost", "::1"}


settings = Settings()

if not settings.host_is_loopback:  # pragma: no cover - defesa em profundidade
    raise RuntimeError("EdgeFut engine só pode escutar em loopback (127.0.0.1).")
