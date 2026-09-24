"""football-data.co.uk — CSVs públicos por temporada (ligas) e por país (formato "new")."""

from __future__ import annotations

import io
import logging
from datetime import datetime

import pandas as pd

from ...core.paths import get_paths
from ..http_client import HttpClient, get_http_client
from .schema import conform

log = logging.getLogger(__name__)

PROVIDER = "football-data.co.uk"
BASE_MAIN = "https://www.football-data.co.uk/mmz4281"
BASE_NEW = "https://www.football-data.co.uk/new"

LEAGUE_NAMES: dict[str, str] = {
    "E0": "Premier League",
    "E1": "Championship",
    "SP1": "LaLiga",
    "SP2": "LaLiga 2",
    "I1": "Serie A",
    "I2": "Serie B",
    "D1": "Bundesliga",
    "D2": "2. Bundesliga",
    "F1": "Ligue 1",
    "F2": "Ligue 2",
    "N1": "Eredivisie",
    "P1": "Primeira Liga",
    "B1": "Pro League",
    "T1": "Süper Lig",
    "SC0": "Premiership",
    "G1": "Super League",
}

# formato "new": arquivo único por país
NEW_FORMAT: dict[str, str] = {
    "BRA": "Brasileiro Série A",
    "ARG": "Liga Profesional",
    "MEX": "Liga MX",
    "USA": "MLS",
    "JPN": "J1 League",
}


def season_label(code: str) -> str:
    # 2425 -> 2024/25
    return f"20{code[:2]}/{code[2:]}"


class FootballDataProvider:
    name = PROVIDER

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or get_http_client()
        self.paths = get_paths()

    # -- URLs --
    def league_url(self, div: str, season: str) -> str:
        return f"{BASE_MAIN}/{season}/{div}.csv"

    def country_url(self, country: str) -> str:
        return f"{BASE_NEW}/{country}.csv"

    # -- fetch + normalize --
    def fetch_league(self, div: str, season: str, force: bool = False) -> tuple[pd.DataFrame, str, datetime]:
        url = self.league_url(div, season)
        res = self.http.get(url, provider=PROVIDER, ttl_s=86400, min_interval_s=3.0, force=force)
        raw_path = self.paths.raw / f"football_data_{div}_{season}.csv"
        raw_path.write_bytes(res.content)
        df = pd.read_csv(io.BytesIO(res.content), encoding="latin-1", on_bad_lines="skip")
        df = df.dropna(how="all")
        out = pd.DataFrame(
            {
                "dataset_code": div,
                "competition": LEAGUE_NAMES.get(div, div),
                "season": season,
                "date": pd.to_datetime(df.get("Date"), dayfirst=True, errors="coerce"),
                "home": df.get("HomeTeam"),
                "away": df.get("AwayTeam"),
                "hg": df.get("FTHG"),
                "ag": df.get("FTAG"),
                "hthg": df.get("HTHG"),
                "htag": df.get("HTAG"),
                "hs": df.get("HS"),
                "as_": df.get("AS"),
                "hst": df.get("HST"),
                "ast": df.get("AST"),
                "hc": df.get("HC"),
                "ac": df.get("AC"),
                "hy": df.get("HY"),
                "ay": df.get("AY"),
                "hr": df.get("HR"),
                "ar": df.get("AR"),
                "hf": df.get("HF"),
                "af": df.get("AF"),
                "referee": df.get("Referee"),
                "neutral": False,
                "odds_h": df.get("B365H", df.get("AvgH")),
                "odds_d": df.get("B365D", df.get("AvgD")),
                "odds_a": df.get("B365A", df.get("AvgA")),
                "odds_close_h": df.get("PSCH", df.get("B365CH")),
                "odds_close_d": df.get("PSCD", df.get("B365CD")),
                "odds_close_a": df.get("PSCA", df.get("B365CA")),
                "odds_o25": df.get("Avg>2.5", df.get("B365>2.5")),
                "odds_u25": df.get("Avg<2.5", df.get("B365<2.5")),
                "source": PROVIDER,
                "source_url": url,
                "collected_at": res.collected_at,
            }
        )
        out = conform(out)
        parquet = self.paths.processed / f"football_data_{div}_{season}.parquet"
        out.to_parquet(parquet, index=False)
        log.info("football-data %s %s: %s partidas", div, season, len(out))
        return out, url, res.collected_at

    def fetch_country(self, country: str, force: bool = False) -> tuple[pd.DataFrame, str, datetime]:
        url = self.country_url(country)
        res = self.http.get(url, provider=PROVIDER, ttl_s=86400, min_interval_s=3.0, force=force)
        raw_path = self.paths.raw / f"football_data_{country}.csv"
        raw_path.write_bytes(res.content)
        df = pd.read_csv(io.BytesIO(res.content), encoding="latin-1", on_bad_lines="skip")
        out = pd.DataFrame(
            {
                "dataset_code": country,
                "competition": df.get("League", NEW_FORMAT.get(country, country)),
                "season": df.get("Season").astype(str) if "Season" in df else None,
                "date": pd.to_datetime(df.get("Date"), dayfirst=True, errors="coerce"),
                "home": df.get("Home"),
                "away": df.get("Away"),
                "hg": df.get("HG"),
                "ag": df.get("AG"),
                "neutral": False,
                "country": df.get("Country"),
                "odds_h": df.get("PH", df.get("AvgH")),
                "odds_d": df.get("PD", df.get("AvgD")),
                "odds_a": df.get("PA", df.get("AvgA")),
                "odds_close_h": df.get("PSCH", df.get("PH")),
                "odds_close_d": df.get("PSCD", df.get("PD")),
                "odds_close_a": df.get("PSCA", df.get("PA")),
                "source": PROVIDER,
                "source_url": url,
                "collected_at": res.collected_at,
            }
        )
        out = conform(out)
        parquet = self.paths.processed / f"football_data_{country}.parquet"
        out.to_parquet(parquet, index=False)
        log.info("football-data %s: %s partidas", country, len(out))
        return out, url, res.collected_at
