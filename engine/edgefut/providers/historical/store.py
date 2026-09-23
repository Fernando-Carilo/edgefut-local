"""HistoricalStore — consultas analíticas em Parquet via DuckDB."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from ...core.paths import get_paths

log = logging.getLogger(__name__)


class HistoricalStore:
    def __init__(self, processed_dir: Path | None = None) -> None:
        self.dir = processed_dir or get_paths().processed
        self._lock = threading.Lock()

    def _files(self) -> list[Path]:
        return sorted(self.dir.glob("*.parquet"))

    def has_data(self) -> bool:
        return bool(self._files())

    def _query(self, sql: str, params: list | None = None) -> pd.DataFrame:
        files = self._files()
        if not files:
            return pd.DataFrame()
        glob = str(self.dir / "*.parquet").replace("\\", "/")
        sql = sql.replace("{{matches}}", f"read_parquet('{glob}', union_by_name=true)")
        with self._lock, duckdb.connect() as con:
            try:
                return con.execute(sql, params or []).fetch_df()
            except duckdb.Error as exc:
                log.error("duckdb: %s\n%s", exc, sql)
                return pd.DataFrame()

    # -- catálogo -----------------------------------------------------------
    def datasets(self) -> pd.DataFrame:
        return self._query(
            """
            SELECT dataset_code, ANY_VALUE(competition) AS competition,
                   COUNT(DISTINCT competition) AS competitions, COUNT(*) AS rows,
                   MIN(date) AS first_date, MAX(date) AS last_date,
                   MAX(collected_at) AS collected_at, ANY_VALUE(source) AS source,
                   ANY_VALUE(source_url) AS source_url,
                   SUM(CASE WHEN hc IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_corners,
                   SUM(CASE WHEN hs IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_shots,
                   SUM(CASE WHEN hy IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_cards,
                   SUM(CASE WHEN odds_h IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_odds
            FROM {{matches}}
            GROUP BY dataset_code
            ORDER BY dataset_code
            """
        )

    def datasets_with_odds(self, min_rows: int = 200) -> set[str]:
        """Datasets com odds históricas reais (permitem backtest modelo × mercado). INTL não tem."""
        df = self.datasets()
        if df.empty or "rows_with_odds" not in df:
            return set()
        return {str(c) for c, n in zip(df["dataset_code"], df["rows_with_odds"]) if int(n or 0) >= min_rows}

    def team_names(self, dataset_codes: list[str]) -> list[str]:
        if not dataset_codes:
            return []
        ph = ",".join("?" * len(dataset_codes))
        df = self._query(
            f"""
            SELECT DISTINCT name FROM (
                SELECT home AS name FROM {{{{matches}}}} WHERE dataset_code IN ({ph})
                UNION SELECT away AS name FROM {{{{matches}}}} WHERE dataset_code IN ({ph})
            ) ORDER BY name
            """,
            dataset_codes + dataset_codes,
        )
        return [] if df.empty else df["name"].dropna().tolist()

    # -- consultas de partidas ----------------------------------------------
    def team_matches(
        self,
        team: str,
        dataset_codes: list[str],
        before: datetime | None = None,
        limit: int = 40,
    ) -> pd.DataFrame:
        if not dataset_codes:
            return pd.DataFrame()
        ph = ",".join("?" * len(dataset_codes))
        before = before or datetime.utcnow()
        return self._query(
            f"""
            SELECT * FROM {{{{matches}}}}
            WHERE dataset_code IN ({ph}) AND (home = ? OR away = ?)
              AND date < ? AND hg IS NOT NULL AND ag IS NOT NULL
            ORDER BY date DESC LIMIT ?
            """,
            dataset_codes + [team, team, before, limit],
        )

    def competition_matches(
        self,
        dataset_codes: list[str],
        since: datetime | None = None,
        before: datetime | None = None,
    ) -> pd.DataFrame:
        if not dataset_codes:
            return pd.DataFrame()
        ph = ",".join("?" * len(dataset_codes))
        since = since or (datetime.utcnow() - timedelta(days=3 * 365))
        before = before or datetime.utcnow()
        return self._query(
            f"""
            SELECT * FROM {{{{matches}}}}
            WHERE dataset_code IN ({ph}) AND date >= ? AND date < ?
              AND hg IS NOT NULL AND ag IS NOT NULL
            ORDER BY date ASC
            """,
            dataset_codes + [since, before],
        )

    def h2h(self, team_a: str, team_b: str, dataset_codes: list[str], limit: int = 10, before: datetime | None = None) -> pd.DataFrame:
        if not dataset_codes:
            return pd.DataFrame()
        ph = ",".join("?" * len(dataset_codes))
        before = before or datetime.utcnow()
        return self._query(
            f"""
            SELECT * FROM {{{{matches}}}}
            WHERE dataset_code IN ({ph})
              AND ((home = ? AND away = ?) OR (home = ? AND away = ?))
              AND hg IS NOT NULL AND date < ?
            ORDER BY date DESC LIMIT ?
            """,
            dataset_codes + [team_a, team_b, team_b, team_a, before, limit],
        )

    def find_result(
        self, home: str, away: str, kickoff: datetime, dataset_codes: list[str], tolerance_days: int = 1
    ) -> pd.DataFrame:
        if not dataset_codes:
            return pd.DataFrame()
        ph = ",".join("?" * len(dataset_codes))
        lo = kickoff - timedelta(days=tolerance_days)
        hi = kickoff + timedelta(days=tolerance_days + 1)
        return self._query(
            f"""
            SELECT * FROM {{{{matches}}}}
            WHERE dataset_code IN ({ph}) AND date >= ? AND date < ?
              AND ((home = ? AND away = ?) OR (home = ? AND away = ?))
              AND hg IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            dataset_codes + [lo, hi, home, away, away, home],
        )

    def recent_team_venues(self, team: str, dataset_codes: list[str], limit: int = 10) -> pd.DataFrame:
        """Últimos jogos com país/cidade (só INTL tem estes campos)."""
        df = self.team_matches(team, dataset_codes, limit=limit)
        if df.empty:
            return df
        return df[["date", "home", "away", "country", "city", "neutral", "tournament"]]


_store: HistoricalStore | None = None


def get_store() -> HistoricalStore:
    global _store
    if _store is None:
        _store = HistoricalStore()
    return _store
