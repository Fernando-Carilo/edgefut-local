"""Team Strength Engine (strength-v1).

Recebe as partidas do time (mais recente primeiro) e produz janelas 5/10/20
com peso de recência, separando geral / mandante / visitante.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..domain.analysis import RecentMatch, WindowStats

RECENCY_LAMBDA = 0.08


def recency_weights(n: int, lam: float = RECENCY_LAMBDA) -> np.ndarray:
    if n <= 0:
        return np.array([])
    w = np.exp(-lam * np.arange(n))
    return w / w.sum()


@dataclass
class TeamSide:
    """Visão orientada ao time (for/against) de uma partida."""

    date: pd.Timestamp
    is_home: bool
    gf: int
    ga: int
    sf: float | None
    sa: float | None
    stf: float | None
    sta: float | None
    cf: float | None
    ca: float | None
    cards: float | None
    cards_against: float | None
    result: str
    competition: str | None
    neutral: bool | None
    opponent: str
    home: str
    away: str


def _num(v) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return float(v)


def to_sides(df: pd.DataFrame, team: str) -> list[TeamSide]:
    sides: list[TeamSide] = []
    for _, r in df.iterrows():
        is_home = r["home"] == team
        gf, ga = (int(r["hg"]), int(r["ag"])) if is_home else (int(r["ag"]), int(r["hg"]))
        res = "V" if gf > ga else "E" if gf == ga else "D"
        hy, ay, hr, ar = _num(r.get("hy")), _num(r.get("ay")), _num(r.get("hr")), _num(r.get("ar"))
        home_cards = None if hy is None else hy + (hr or 0)
        away_cards = None if ay is None else ay + (ar or 0)
        sides.append(
            TeamSide(
                date=r["date"],
                is_home=is_home,
                gf=gf,
                ga=ga,
                sf=_num(r.get("hs") if is_home else r.get("as_")),
                sa=_num(r.get("as_") if is_home else r.get("hs")),
                stf=_num(r.get("hst") if is_home else r.get("ast")),
                sta=_num(r.get("ast") if is_home else r.get("hst")),
                cf=_num(r.get("hc") if is_home else r.get("ac")),
                ca=_num(r.get("ac") if is_home else r.get("hc")),
                cards=home_cards if is_home else away_cards,
                cards_against=away_cards if is_home else home_cards,
                result=res,
                competition=str(r.get("competition")) if r.get("competition") is not None else None,
                neutral=bool(r["neutral"]) if r.get("neutral") is not None and not pd.isna(r.get("neutral")) else None,
                opponent=str(r["away"] if is_home else r["home"]),
                home=str(r["home"]),
                away=str(r["away"]),
            )
        )
    return sides


def _wmean(values: list[float | None], weights: np.ndarray) -> float | None:
    pairs = [(v, w) for v, w in zip(values, weights, strict=False) if v is not None]
    if not pairs:
        return None
    vs = np.array([p[0] for p in pairs], dtype=float)
    ws = np.array([p[1] for p in pairs], dtype=float)
    if ws.sum() == 0:
        return None
    return float((vs * ws).sum() / ws.sum())


def window_stats(sides: list[TeamSide], name: str, n: int) -> WindowStats | None:
    sub = sides[:n]
    if not sub:
        return None
    w = recency_weights(len(sub))
    gf = [float(s.gf) for s in sub]
    ga = [float(s.ga) for s in sub]
    totals = [s.gf + s.ga for s in sub]
    stf = _wmean([s.stf for s in sub], w)
    conv = None
    if stf and stf > 0:
        conv = (_wmean(gf, w) or 0.0) / stf
    wins = sum(1 for s in sub if s.result == "V")
    draws = sum(1 for s in sub if s.result == "E")
    losses = len(sub) - wins - draws
    return WindowStats(
        window=name,
        n=len(sub),
        wins=wins,
        draws=draws,
        losses=losses,
        points_per_game=round((3 * wins + draws) / len(sub), 3),
        goals_for=_r(_wmean(gf, w)),
        goals_against=_r(_wmean(ga, w)),
        shots_for=_r(_wmean([s.sf for s in sub], w)),
        shots_against=_r(_wmean([s.sa for s in sub], w)),
        sot_for=_r(stf),
        sot_against=_r(_wmean([s.sta for s in sub], w)),
        conversion=_r(conv),
        corners_for=_r(_wmean([s.cf for s in sub], w)),
        corners_against=_r(_wmean([s.ca for s in sub], w)),
        cards=_r(_wmean([s.cards for s in sub], w)),
        cards_against=_r(_wmean([s.cards_against for s in sub], w)),
        clean_sheet_pct=_r(_wmean([1.0 if s.ga == 0 else 0.0 for s in sub], w)),
        btts_pct=_r(_wmean([1.0 if (s.gf > 0 and s.ga > 0) else 0.0 for s in sub], w)),
        over15_pct=_r(_wmean([1.0 if t > 1.5 else 0.0 for t in totals], w)),
        over25_pct=_r(_wmean([1.0 if t > 2.5 else 0.0 for t in totals], w)),
        over35_pct=_r(_wmean([1.0 if t > 3.5 else 0.0 for t in totals], w)),
    )


def _r(v: float | None, nd: int = 3) -> float | None:
    return None if v is None else round(v, nd)


def build_windows(sides: list[TeamSide]) -> dict[str, WindowStats]:
    out: dict[str, WindowStats] = {}
    for n in (5, 10, 20):
        ws = window_stats(sides, f"all_{n}", n)
        if ws:
            out[ws.window] = ws
    home_sides = [s for s in sides if s.is_home and not s.neutral]
    away_sides = [s for s in sides if not s.is_home and not s.neutral]
    for n in (10,):
        ws = window_stats(home_sides, f"home_{n}", n)
        if ws:
            out[ws.window] = ws
        ws = window_stats(away_sides, f"away_{n}", n)
        if ws:
            out[ws.window] = ws
    return out


def form_string(sides: list[TeamSide], n: int = 5) -> list[str]:
    return [s.result for s in sides[:n]]


def recent_matches(sides: list[TeamSide], n: int = 10) -> list[RecentMatch]:
    out = []
    for s in sides[:n]:
        hg, ag = (s.gf, s.ga) if s.is_home else (s.ga, s.gf)
        out.append(
            RecentMatch(
                date=s.date.to_pydatetime(),
                home=s.home,
                away=s.away,
                hg=hg,
                ag=ag,
                competition=s.competition,
                result_for_team=s.result,  # type: ignore[arg-type]
                neutral=s.neutral,
            )
        )
    return out


@dataclass
class LeagueAverages:
    home_goals: float
    away_goals: float
    matches: int
    corners_home: float | None = None
    corners_away: float | None = None
    cards_total: float | None = None
    shots_home: float | None = None
    shots_away: float | None = None
    sot_rate: float | None = None
    draw_rate: float | None = None
    home_win_rate: float | None = None
    corners_dispersion_k: float | None = None
    cards_dispersion_k: float | None = None


def league_averages(df: pd.DataFrame) -> LeagueAverages | None:
    if df is None or df.empty:
        return None
    hg = pd.to_numeric(df["hg"], errors="coerce")
    ag = pd.to_numeric(df["ag"], errors="coerce")
    mask = hg.notna() & ag.notna()
    if mask.sum() == 0:
        return None
    hg, ag = hg[mask].astype(float), ag[mask].astype(float)
    la = LeagueAverages(
        home_goals=float(hg.mean()),
        away_goals=float(ag.mean()),
        matches=int(mask.sum()),
        draw_rate=float((hg == ag).mean()),
        home_win_rate=float((hg > ag).mean()),
    )
    for col_h, col_a, attr_h, attr_a in (
        ("hc", "ac", "corners_home", "corners_away"),
        ("hs", "as_", "shots_home", "shots_away"),
    ):
        if col_h in df and col_a in df:
            h = pd.to_numeric(df[col_h], errors="coerce").dropna()
            a = pd.to_numeric(df[col_a], errors="coerce").dropna()
            if len(h) >= 30 and len(a) >= 30:
                setattr(la, attr_h, float(h.mean()))
                setattr(la, attr_a, float(a.mean()))
    if "hc" in df and "ac" in df:
        tot = (pd.to_numeric(df["hc"], errors="coerce") + pd.to_numeric(df["ac"], errors="coerce")).dropna()
        if len(tot) >= 30:
            la.corners_dispersion_k = _nb_k(tot)
    if all(c in df for c in ("hy", "ay", "hr", "ar")):
        cards = (
            pd.to_numeric(df["hy"], errors="coerce")
            + pd.to_numeric(df["ay"], errors="coerce")
            + pd.to_numeric(df["hr"], errors="coerce").fillna(0)
            + pd.to_numeric(df["ar"], errors="coerce").fillna(0)
        ).dropna()
        if len(cards) >= 30:
            la.cards_total = float(cards.mean())
            la.cards_dispersion_k = _nb_k(cards)
    if all(c in df for c in ("hs", "as_", "hst", "ast")):
        shots = (pd.to_numeric(df["hs"], errors="coerce") + pd.to_numeric(df["as_"], errors="coerce")).dropna()
        sot = (pd.to_numeric(df["hst"], errors="coerce") + pd.to_numeric(df["ast"], errors="coerce")).dropna()
        if len(shots) >= 30 and shots.sum() > 0:
            la.sot_rate = float(sot.sum() / shots.sum())
    return la


def _nb_k(series: pd.Series) -> float | None:
    """Parâmetro de dispersão k da Binomial Negativa (método dos momentos)."""
    m, v = float(series.mean()), float(series.var())
    if v <= m or m <= 0:
        return None
    return float(m * m / (v - m))


def attack_defense(
    windows: dict[str, WindowStats], la: LeagueAverages | None, is_home: bool
) -> tuple[float | None, float | None]:
    """Multiplicadores de ataque/defesa relativos à liga (1.0 = média)."""
    if la is None:
        return None, None
    ws = windows.get("all_20") or windows.get("all_10") or windows.get("all_5")
    if ws is None or ws.goals_for is None or ws.goals_against is None:
        return None, None
    league_gf = (la.home_goals + la.away_goals) / 2
    if league_gf <= 0:
        return None, None
    # shrinkage para a média conforme o tamanho da amostra
    shrink = ws.n / (ws.n + 6)
    attack = 1 + shrink * (ws.goals_for / league_gf - 1)
    defense = 1 + shrink * (ws.goals_against / league_gf - 1)
    # mistura com desempenho na condição (casa/fora) quando houver amostra
    cond = windows.get("home_10" if is_home else "away_10")
    if cond and cond.n >= 5 and cond.goals_for is not None and cond.goals_against is not None:
        cond_gf = la.home_goals if is_home else la.away_goals
        cond_ga = la.away_goals if is_home else la.home_goals
        if cond_gf > 0 and cond_ga > 0:
            a2 = 1 + (cond.n / (cond.n + 6)) * (cond.goals_for / cond_gf - 1)
            d2 = 1 + (cond.n / (cond.n + 6)) * (cond.goals_against / cond_ga - 1)
            attack = 0.65 * attack + 0.35 * a2
            defense = 0.65 * defense + 0.35 * d2
    return round(max(0.3, attack), 3), round(max(0.3, defense), 3)


def strength_score(elo: float | None, attack: float | None, defense: float | None) -> float | None:
    """0–100: 60% ELO normalizado + 40% diferencial ofensivo/defensivo."""
    parts = []
    if elo is not None:
        parts.append((0.6, _clamp((elo - 1300) / 600)))
    if attack is not None and defense is not None:
        parts.append((0.4, _clamp(0.5 + (attack - defense) / 2)))
    if not parts:
        return None
    total_w = sum(w for w, _ in parts)
    return round(100 * sum(w * v for w, v in parts) / total_w, 1)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
