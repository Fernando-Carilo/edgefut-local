"""TemporalFeatureStore — a única porta de acesso a dados históricos com proteção temporal.

Todo consumidor (pipeline, replay, baselines, modelos) pede dados com um `as_of` e recebe
apenas partidas com `date < as_of`. A guarda é aplicada no retorno de **toda** consulta
(`_guard`), não em chamadas individuais espalhadas pelo código. Um DataFrame que contenha
uma linha com `date >= as_of` levanta `LeakageError` antes de chegar ao modelo.

Disponibilidade temporal: uma partida histórica é considerada conhecida a partir da sua
`date` (00:00 UTC do dia seguinte na prática, porque `date` não tem hora e a comparação é
estrita). Não usamos `collected_at` do CSV para isso — ele marca o download, não o
momento em que o resultado existiu.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from ..providers.historical.store import HistoricalStore, get_store

log = logging.getLogger(__name__)


class LeakageError(RuntimeError):
    """Dados com data >= as_of chegaram a um consumidor de features."""


def assert_before(df: pd.DataFrame, as_of: datetime | pd.Timestamp, what: str = "dados") -> pd.DataFrame:
    if df is None or df.empty or "date" not in df:
        return df
    mx = df["date"].max()
    if pd.notna(mx) and pd.Timestamp(mx) >= pd.Timestamp(as_of):
        raise LeakageError(f"{what}: partida em {mx} >= as_of {as_of}")
    return df


@dataclass
class TemporalSlice:
    """Tudo que um modelo pode ver para um evento em `as_of`."""

    as_of: datetime
    dataset_codes: list[str]
    home: str | None
    away: str | None
    home_matches: pd.DataFrame
    away_matches: pd.DataFrame
    competition_matches: pd.DataFrame
    h2h: pd.DataFrame
    elo_matches: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def max_date(self) -> pd.Timestamp | None:
        dates = [d["date"].max() for d in (self.home_matches, self.away_matches, self.competition_matches, self.h2h) if d is not None and not d.empty]
        return max(dates) if dates else None


class TemporalFeatureStore:
    def __init__(self, store: HistoricalStore | None = None, *, frame: pd.DataFrame | None = None) -> None:
        """`store` consulta Parquet via DuckDB; `frame` (opcional) serve um DataFrame já
        carregado — usado pelo replay para não bater no DuckDB milhares de vezes."""
        self._store = store
        self._frame = frame.sort_values("date").reset_index(drop=True) if frame is not None else None

    @property
    def store(self) -> HistoricalStore:
        if self._store is None:
            self._store = get_store()
        return self._store

    # -- consultas protegidas ------------------------------------------------
    def team_matches(self, team: str, codes: list[str], as_of: datetime, limit: int = 40) -> pd.DataFrame:
        if not team or not codes:
            return pd.DataFrame()
        if self._frame is not None:
            f = self._frame
            m = f[(f["dataset_code"].isin(codes)) & ((f["home"] == team) | (f["away"] == team)) & (f["date"] < pd.Timestamp(as_of))]
            df = m.sort_values("date", ascending=False).head(limit)
        else:
            df = self.store.team_matches(team, codes, before=as_of, limit=limit)
        return assert_before(df, as_of, f"jogos de {team}")

    def competition_matches(self, codes: list[str], as_of: datetime, since: datetime | None = None) -> pd.DataFrame:
        if not codes:
            return pd.DataFrame()
        since = since or (as_of - timedelta(days=3 * 365))
        if self._frame is not None:
            f = self._frame
            df = f[(f["dataset_code"].isin(codes)) & (f["date"] >= pd.Timestamp(since)) & (f["date"] < pd.Timestamp(as_of))]
        else:
            df = self.store.competition_matches(codes, since=since, before=as_of)
        return assert_before(df, as_of, "jogos da competição")

    def h2h(self, a: str, b: str, codes: list[str], as_of: datetime, limit: int = 10) -> pd.DataFrame:
        if not a or not b or not codes:
            return pd.DataFrame()
        if self._frame is not None:
            f = self._frame
            m = f[(f["dataset_code"].isin(codes)) & (((f["home"] == a) & (f["away"] == b)) | ((f["home"] == b) & (f["away"] == a))) & (f["date"] < pd.Timestamp(as_of))]
            df = m.sort_values("date", ascending=False).head(limit)
        else:
            df = self.store.h2h(a, b, codes, limit=limit, before=as_of)
        return assert_before(df, as_of, "H2H")

    def slice(
        self,
        *,
        home: str | None,
        away: str | None,
        codes: list[str],
        as_of: datetime,
        is_national: bool = False,
        team_limit: int = 40,
        home_codes: list[str] | None = None,
        away_codes: list[str] | None = None,
    ) -> TemporalSlice:
        comp = self.competition_matches(codes, as_of)
        if is_national and not comp.empty:
            comp = comp[comp["date"] >= pd.Timestamp(as_of - timedelta(days=3 * 365))]
        elo_df = None
        if is_national and codes:
            elo_df = self.competition_matches(codes, as_of, since=datetime(2000, 1, 1))
        hc = home_codes if home_codes is not None else codes
        ac = away_codes if away_codes is not None else codes
        sl = TemporalSlice(
            as_of=as_of, dataset_codes=list(codes), home=home, away=away,
            home_matches=self.team_matches(home, hc, as_of, team_limit) if home else pd.DataFrame(),
            away_matches=self.team_matches(away, ac, as_of, team_limit) if away else pd.DataFrame(),
            competition_matches=comp,
            h2h=self.h2h(home, away, codes, as_of) if home and away else pd.DataFrame(),
            elo_matches=elo_df,
        )
        # guarda final: nada no slice pode ser >= as_of
        mx = sl.max_date
        if mx is not None and mx >= pd.Timestamp(as_of):
            raise LeakageError(f"slice contém {mx} >= as_of {as_of}")
        return sl


__all__ = ["TemporalFeatureStore", "TemporalSlice", "LeakageError", "assert_before"]
