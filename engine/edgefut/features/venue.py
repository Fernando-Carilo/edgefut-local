"""Resolução de mandante / visitante / campo neutro.

Nunca assume que o primeiro time listado joga em casa sem registrar a confiança
dessa afirmação.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from ..core.config import settings
from ..domain.analysis import VenueInfo
from ..normalization import CompetitionProfile
from ..providers.historical import HistoricalStore


def resolve_venue(
    *,
    profile: CompetitionProfile,
    home_canonical: str | None,
    away_canonical: str | None,
    kickoff: datetime,
    store: HistoricalStore,
    override: VenueInfo | None = None,
) -> VenueInfo:
    if override is not None and override.source == "user":
        w = 0.0 if override.neutral else 1.0
        return override.model_copy(update={"home_advantage_weight": w, "confidence": 1.0})

    # Seleções: tentar confirmar com o dataset (jogo já ocorrido) — para jogos
    # futuros o venue fica não confirmado.
    if profile.is_national_teams:
        if home_canonical and away_canonical:
            df = store.find_result(home_canonical, away_canonical, kickoff, ["INTL"])
            if not df.empty:
                r = df.iloc[0]
                neutral = bool(r["neutral"]) if pd.notna(r.get("neutral")) else None
                listed_home_is_home = r["home"] == home_canonical
                if neutral:
                    return VenueInfo(
                        status="NEUTRAL", neutral=True, city=_s(r.get("city")), country=_s(r.get("country")),
                        source="international_results", confidence=0.95,
                        note="Dataset registra campo neutro para esta partida.", home_advantage_weight=0.0,
                    )
                if listed_home_is_home:
                    return VenueInfo(
                        status="CONFIRMED_HOME", neutral=False, city=_s(r.get("city")), country=_s(r.get("country")),
                        source="international_results", confidence=0.95,
                        note="Dataset confirma o mandante.", home_advantage_weight=1.0,
                    )
                # dataset lista o mandante invertido em relação à Superbet: não assumimos nenhum dos dois
                return VenueInfo(
                    status="UNCONFIRMED", neutral=False, city=_s(r.get("city")), country=_s(r.get("country")),
                    source="international_results", confidence=0.5, listing_swapped=True,
                    note="Dataset registra o mando invertido em relação à listagem da Superbet; vantagem de mandante reduzida.",
                    home_advantage_weight=settings.home_advantage_unconfirmed_weight,
                )
        return VenueInfo(
            status="UNCONFIRMED",
            neutral=None,
            source="superbet-listing",
            confidence=0.4,
            note=(
                "Jogo de seleções: a Superbet lista o primeiro time como mandante, mas estádio/país "
                "não foram confirmados por fonte pública. O modelo aplica metade da vantagem de mandante."
            ),
            home_advantage_weight=settings.home_advantage_unconfirmed_weight,
        )

    if profile.is_international_clubs:
        return VenueInfo(
            status="UNCONFIRMED",
            neutral=None,
            source="superbet-listing",
            confidence=0.6,
            note=(
                "Competição internacional de clubes: mandante conforme listagem; finais em sede única "
                "não são detectadas automaticamente. Vantagem de mandante reduzida (75%)."
            ),
            home_advantage_weight=0.75,
        )

    if profile.supported and profile.dataset_codes:
        return VenueInfo(
            status="CONFIRMED_HOME",
            neutral=False,
            source="league-convention",
            confidence=0.9,
            note="Liga nacional: o time listado primeiro joga em casa por convenção da competição.",
            home_advantage_weight=1.0,
        )

    return VenueInfo(
        status="UNCONFIRMED",
        neutral=None,
        source="superbet-listing",
        confidence=0.3,
        note="Competição sem dataset mapeado; mandante não confirmado.",
        home_advantage_weight=settings.home_advantage_unconfirmed_weight,
    )


def _s(v) -> str | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)
