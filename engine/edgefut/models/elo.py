"""ELO (elo-v1) com atualização cronológica e ajuste de margem."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from ..core import versions
from ..domain.analysis import EloOutput

INITIAL = 1500.0
HOME_ADV_CLUB = 100.0
HOME_ADV_NATIONAL = 80.0


def _k_factor(competition: str | None, is_national: bool) -> float:
    if not is_national:
        return 20.0
    c = (competition or "").lower()
    if "friendly" in c or "amistoso" in c:
        return 15.0
    if "world cup" in c or "copa" in c or "euro" in c or "nations" in c:
        return 30.0
    return 25.0


def _margin_mult(diff: int) -> float:
    d = abs(diff)
    if d <= 1:
        return 1.0
    if d == 2:
        return 1.5
    return (11 + d) / 8


def expected_home(r_home: float, r_away: float, home_adv: float) -> float:
    return 1.0 / (1.0 + 10 ** ((r_away - r_home - home_adv) / 400.0))


@dataclass
class EloTable:
    ratings: dict[str, float] = field(default_factory=dict)
    games: dict[str, int] = field(default_factory=dict)
    matches_used: int = 0
    last_date: datetime | None = None

    def get(self, team: str) -> float | None:
        return self.ratings.get(team)


def fit_elo(df: pd.DataFrame, is_national: bool) -> EloTable:
    """df ordenado por data ascendente com colunas home, away, hg, ag, neutral, competition."""
    return update_elo(EloTable(), df, is_national)


def update_elo(table: EloTable, df: pd.DataFrame, is_national: bool) -> EloTable:
    """Atualiza `table` in-place com novas partidas (ordem cronológica) — usado pelo replay."""
    if df is None or df.empty:
        return table
    ha_base = HOME_ADV_NATIONAL if is_national else HOME_ADV_CLUB
    r = table.ratings
    g = table.games
    for row in df.itertuples(index=False):
        h, a = row.home, row.away
        try:
            hg, ag = int(row.hg), int(row.ag)
        except (TypeError, ValueError):
            continue
        neutral = bool(getattr(row, "neutral", False)) if getattr(row, "neutral", None) is not None and not pd.isna(getattr(row, "neutral", None)) else False
        rh, ra = r.get(h, INITIAL), r.get(a, INITIAL)
        ha = 0.0 if neutral else ha_base
        e_h = expected_home(rh, ra, ha)
        s_h = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        k = _k_factor(getattr(row, "competition", None), is_national) * _margin_mult(hg - ag)
        delta = k * (s_h - e_h)
        r[h] = rh + delta
        r[a] = ra - delta
        g[h] = g.get(h, 0) + 1
        g[a] = g.get(a, 0) + 1
        table.matches_used += 1
        table.last_date = row.date.to_pydatetime() if hasattr(row.date, "to_pydatetime") else row.date
    return table


def elo_probabilities(
    r_home: float, r_away: float, home_adv_points: float, draw_rate_base: float = 0.26
) -> tuple[float, float, float]:
    """1X2 a partir do ELO: separa empate com taxa-base decaindo com |ΔELO|."""
    diff = r_home + home_adv_points - r_away
    p_home_side = expected_home(r_home, r_away, home_adv_points)  # P(vitória) + 0.5·P(empate)
    p_draw = draw_rate_base * math.exp(-abs(diff) / 600.0)
    p_home = max(0.0, p_home_side - p_draw / 2)
    p_away = max(0.0, 1 - p_home - p_draw)
    s = p_home + p_draw + p_away
    return p_home / s, p_draw / s, p_away / s


def elo_output(
    table: EloTable, home: str | None, away: str | None, home_adv_weight: float, is_national: bool,
    draw_rate_base: float | None,
) -> EloOutput:
    if not home or not away or table.get(home) is None or table.get(away) is None:
        return EloOutput(
            model_version=versions.ELO, available=False, matches_used=table.matches_used,
            note="ELO indisponível: time sem histórico no dataset.",
        )
    rh, ra = table.get(home), table.get(away)
    ha = (HOME_ADV_NATIONAL if is_national else HOME_ADV_CLUB) * home_adv_weight
    ph, pd_, pa = elo_probabilities(rh, ra, ha, draw_rate_base or 0.26)  # type: ignore[arg-type]
    return EloOutput(
        model_version=versions.ELO,
        available=True,
        home_elo=round(rh, 1),  # type: ignore[arg-type]
        away_elo=round(ra, 1),  # type: ignore[arg-type]
        p_home=round(ph, 4),
        p_draw=round(pd_, 4),
        p_away=round(pa, 4),
        home_advantage_points=round(ha, 1),
        matches_used=table.matches_used,
        note=f"{table.games.get(home, 0)} jogos de {home} e {table.games.get(away, 0)} de {away} no ajuste.",
    )


class EloCache:
    """Cache em memória por conjunto de datasets (recalcula quando o parquet muda)."""

    def __init__(self) -> None:
        self._tables: dict[tuple, EloTable] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple, builder) -> EloTable:
        with self._lock:
            t = self._tables.get(key)
            if t is None:
                t = builder()
                self._tables[key] = t
            return t

    def clear(self) -> None:
        with self._lock:
            self._tables.clear()


elo_cache = EloCache()
