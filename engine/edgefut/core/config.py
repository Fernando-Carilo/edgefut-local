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
    live_poll_seconds: int = 30
    prematch_poll_seconds: int = 300

    # Odds
    margin_method: str = "MULTIPLICATIVE"  # MULTIPLICATIVE | SHIN

    # Modelos
    default_simulations: int = 50_000
    min_edge_pp: float = 3.0
    min_ev_pct: float = 3.0
    min_odd: float = 1.20
    max_odd: float = 6.00
    home_advantage_unconfirmed_weight: float = 0.5

    # Quality Gate (TOP OPORTUNIDADES). Só o usuário altera; nunca são relaxados automaticamente
    # ("não caçar entradas"). HARD_FLOORS abaixo impede que a UI vá além do razoável.
    gate_min_data_quality: float = 60.0
    gate_min_confidence: float = 65.0  # grade B
    gate_min_sample: int = 15  # jogos da menor amostra entre as equipes
    gate_max_disagreement_pp: float = 10.0
    # Edge acima disso SEM calibrador confiável é mais provável erro do modelo do que do mercado.
    gate_max_edge_pp_uncalibrated: float = 15.0
    high_probability_min: float = 0.65  # rótulo HIGH PROBABILITY (não é "seguro")

    # Opportunity Score V2 — pesos configuráveis (soma normalizada em runtime)
    opportunity_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "model_confidence": 20, "data_quality": 15, "calibration_quality": 10, "edge": 15, "ev": 10,
            "odds_freshness": 10, "model_agreement": 10, "historical_performance": 5, "sample_size": 5,
        }
    )

    # Validação estatística (iteração 3) — limites de qualidade de amostra
    sample_early_min: int = 100  # < EARLY → INSUFFICIENT
    sample_moderate_min: int = 300
    sample_strong_min: int = 1000
    bootstrap_resamples: int = 1000
    # decaimento temporal do strength-v2 (meia-vida em dias); escolhido por walk-forward
    # (validation/decay.py) — não por gosto. None → sem decaimento.
    strength_half_life_days: float | None = 365.0  # walk-forward 2022-2026, 10 ligas, 13.365 jogos: 365 ≈ 180 > 730 > none > 90 > 60 > 30
    # estados de VALUE exigem OOS: mínimo de apostas liquidadas no mercado para permitir VALUE
    value_min_oos_bets: int = 100

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

# Pisos que nem a UI pode ultrapassar: relaxar abaixo disso é "caçar entradas".
HARD_FLOORS: dict[str, float] = {
    "min_edge_pp": 1.0,
    "min_ev_pct": 1.0,
    "gate_min_data_quality": 40.0,
    "gate_min_confidence": 50.0,
    "gate_min_sample": 10,
    "high_probability_min": 0.55,
}
HARD_CEILINGS: dict[str, float] = {"gate_max_disagreement_pp": 15.0, "gate_max_edge_pp_uncalibrated": 25.0}

if not settings.host_is_loopback:  # pragma: no cover - defesa em profundidade
    raise RuntimeError("EdgeFut engine só pode escutar em loopback (127.0.0.1).")
