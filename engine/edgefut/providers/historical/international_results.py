"""Resultados de seleções (martj42/international_results, CC0).

Contém `neutral`, `city` e `country` — base da detecção de campo neutro e do
ELO/forma/H2H de seleções. Não traz escanteios, cartões nem finalizações.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime

import pandas as pd

from ...core.paths import get_paths
from ..http_client import HttpClient, get_http_client
from .schema import conform

log = logging.getLogger(__name__)

PROVIDER = "international_results"
URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
DATASET_CODE = "INTL"

FRIENDLY_TOURNAMENTS = {"Friendly"}


class InternationalResultsProvider:
    name = PROVIDER

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or get_http_client()
        self.paths = get_paths()

    def fetch(self, force: bool = False, since_year: int = 2000) -> tuple[pd.DataFrame, str, datetime]:
        res = self.http.get(URL, provider=PROVIDER, ttl_s=86400, min_interval_s=10.0, force=force)
        (self.paths.raw / "international_results.csv").write_bytes(res.content)
        df = pd.read_csv(io.BytesIO(res.content))
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df[df["date"].dt.year >= since_year]
        out = pd.DataFrame(
            {
                "dataset_code": DATASET_CODE,
                "competition": df["tournament"],
                "season": df["date"].dt.year.astype(str),
                "date": df["date"],
                "home": df["home_team"],
                "away": df["away_team"],
                "hg": df["home_score"],
                "ag": df["away_score"],
                "neutral": df["neutral"],
                "country": df["country"],
                "city": df["city"],
                "tournament": df["tournament"],
                "source": PROVIDER,
                "source_url": URL,
                "collected_at": res.collected_at,
            }
        )
        out = conform(out)
        parquet = self.paths.processed / "international_results.parquet"
        out.to_parquet(parquet, index=False)
        log.info("international_results: %s partidas desde %s", len(out), since_year)
        return out, URL, res.collected_at
