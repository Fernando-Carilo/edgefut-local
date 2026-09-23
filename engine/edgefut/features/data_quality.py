from __future__ import annotations

from datetime import datetime, timedelta

from ..domain.analysis import DataQuality, DataQualityCheck, TeamProfile, VenueInfo


def compute_data_quality(
    *,
    home: TeamProfile,
    away: TeamProfile,
    venue: VenueInfo,
    odds_collected_at: datetime | None,
    has_1x2: bool,
    has_corners_data: bool,
    has_cards_data: bool,
    has_shots_data: bool,
    dc_available: bool,
    lineups_available: bool = False,
) -> DataQuality:
    checks: list[DataQualityCheck] = []
    n_min = min(home.sample_size, away.sample_size)
    checks.append(DataQualityCheck(ok=n_min >= 20, label=f"{n_min} jogos recentes por equipe (mín. 20)", weight=2.0))
    checks.append(DataQualityCheck(ok=n_min >= 10, label="amostra mínima de 10 jogos", weight=1.5))
    checks.append(
        DataQualityCheck(
            ok=home.canonical is not None and away.canonical is not None,
            label="times identificados no dataset histórico",
            weight=2.0,
        )
    )
    fresh = odds_collected_at is not None and datetime.utcnow() - odds_collected_at < timedelta(minutes=30)
    checks.append(DataQualityCheck(ok=fresh, label="odds atualizadas (< 30 min)", weight=1.0))
    checks.append(DataQualityCheck(ok=has_1x2, label="mercado 1X2 disponível", weight=1.0))
    checks.append(DataQualityCheck(ok=venue.status != "UNCONFIRMED", label="estádio/mandante confirmado", weight=1.0))
    checks.append(DataQualityCheck(ok=has_shots_data, label="estatísticas de finalizações disponíveis", weight=0.75))
    checks.append(DataQualityCheck(ok=has_corners_data, label="estatísticas de escanteios disponíveis", weight=0.75))
    checks.append(DataQualityCheck(ok=has_cards_data, label="estatísticas de cartões disponíveis", weight=0.5))
    checks.append(DataQualityCheck(ok=dc_available, label="Dixon-Coles ajustado para a competição", weight=0.75))
    checks.append(DataQualityCheck(ok=lineups_available, label="escalações confirmadas", weight=0.5))
    total = sum(c.weight for c in checks)
    score = 100 * sum(c.weight for c in checks if c.ok) / total
    return DataQuality(score=round(score, 1), checks=checks)
