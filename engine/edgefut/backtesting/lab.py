"""Backtest Lab — walk-forward sobre histórico de liga com odds reais do CSV.

Nenhum dado futuro vaza: o modelo usado em cada partida só viu jogos anteriores.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pydantic import BaseModel

from ..core import versions
from ..models.dixon_coles import MIN_MATCHES, fit_dixon_coles
from ..models.goals_common import markets_from_matrix, score_matrix
from ..providers.historical import get_store
from .metrics import BetRecord, Metrics, compute_metrics
from .settlement import MatchResult, settle_selection


class BacktestRequest(BaseModel):
    dataset_code: str = "E0"
    market: str = "1X2"  # 1X2 | TOTAL_GOALS_2.5 | ALL
    model: str = "dixon_coles"  # dixon_coles (poisson via rho=0)
    min_odd: float = 1.30
    max_odd: float = 5.00
    min_edge_pp: float = 3.0
    min_team_games: int = 10  # proxy de confiança
    since: datetime | None = None
    until: datetime | None = None
    refit_every_days: int = 30
    use_closing_odds: bool = True
    stake: float = 1.0


@dataclass
class BacktestResult:
    request: dict
    model_version: str
    matches_evaluated: int
    bets: list[dict]
    metrics: dict
    by_selection: dict[str, dict]
    calibration_bins: list[dict]
    note: str | None = None
    equity_curve: list[float] = field(default_factory=list)


def _selections_for(market: str) -> list[tuple[str, str, float | None]]:
    if market == "1X2":
        return [("1X2", "HOME", None), ("1X2", "DRAW", None), ("1X2", "AWAY", None)]
    if market.startswith("TOTAL_GOALS_"):
        line = float(market.split("_")[-1])
        return [("TOTAL_GOALS", "OVER", line), ("TOTAL_GOALS", "UNDER", line)]
    return _selections_for("1X2") + _selections_for("TOTAL_GOALS_2.5")


def _odd_for(row, market_key, sel, line, closing: bool) -> float | None:
    if market_key == "1X2":
        col = {"HOME": "h", "DRAW": "d", "AWAY": "a"}[sel]
        v = row.get(f"odds_close_{col}") if closing else None
        if v is None or pd.isna(v):
            v = row.get(f"odds_{col}")
        return None if v is None or pd.isna(v) else float(v)
    if market_key == "TOTAL_GOALS" and line == 2.5:
        v = row.get("odds_o25" if sel == "OVER" else "odds_u25")
        return None if v is None or pd.isna(v) else float(v)
    return None


def run_backtest(req: BacktestRequest) -> BacktestResult:
    store = get_store()
    since = req.since or (datetime.utcnow() - timedelta(days=3 * 365))
    until = req.until or datetime.utcnow()
    df = store.competition_matches([req.dataset_code], since=since - timedelta(days=3 * 365), before=until)
    if df.empty:
        return BacktestResult(req.model_dump(mode="json"), versions.DIXON_COLES, 0, [], asdict(Metrics()), {}, [], "Dataset vazio.")
    df = df.sort_values("date").reset_index(drop=True)
    test = df[df["date"] >= pd.Timestamp(since)]
    if test.empty:
        return BacktestResult(req.model_dump(mode="json"), versions.DIXON_COLES, 0, [], asdict(Metrics()), {}, [], "Sem partidas no período.")

    selections = _selections_for(req.market)
    records: list[BetRecord] = []
    bets_out: list[dict] = []
    probs_all: list[tuple[float, int]] = []  # (p_home, won) para calibração 1X2 HOME
    evaluated = 0
    games_count: dict[str, int] = {}
    for t in df[df["date"] < pd.Timestamp(since)].itertuples():
        games_count[t.home] = games_count.get(t.home, 0) + 1
        games_count[t.away] = games_count.get(t.away, 0) + 1

    window_start = pd.Timestamp(since)
    params = None
    while window_start < pd.Timestamp(until):
        window_end = window_start + timedelta(days=req.refit_every_days)
        train = df[df["date"] < window_start]
        params = fit_dixon_coles(train, reference_date=window_start.to_pydatetime()) if len(train) >= MIN_MATCHES else None
        block = test[(test["date"] >= window_start) & (test["date"] < window_end)]
        for row in block.to_dict("records"):
            home, away = row["home"], row["away"]
            if params is not None and home in params.attack and away in params.attack:
                evaluated += 1
                lam, mu = params.lambdas(home, away, 1.0)
                rho = params.rho if req.model == "dixon_coles" else None
                m = score_matrix(max(0.15, min(5, lam)), max(0.15, min(5, mu)), rho)
                mk = markets_from_matrix(m)
                result = MatchResult(hg=int(row["hg"]), ag=int(row["ag"]))
                probs_all.append((mk["p_home"], 1 if result.hg > result.ag else 0))
                enough = min(games_count.get(home, 0), games_count.get(away, 0)) >= req.min_team_games
                for market_key, sel, line in selections:
                    odd = _odd_for(row, market_key, sel, line, req.use_closing_odds)
                    if odd is None or not (req.min_odd <= odd <= req.max_odd):
                        continue
                    if market_key == "1X2":
                        p = {"HOME": mk["p_home"], "DRAW": mk["p_draw"], "AWAY": mk["p_away"]}[sel]
                        implied = np.array([1 / (row.get("odds_close_h") or row["odds_h"]), 1 / (row.get("odds_close_d") or row["odds_d"]), 1 / (row.get("odds_close_a") or row["odds_a"])], dtype=float)
                        fair = float((1 / odd) / implied.sum()) if np.isfinite(implied).all() else 1 / odd
                    else:
                        p = mk["over"]["2.5"] if sel == "OVER" else mk["under"]["2.5"]
                        o, u = row.get("odds_o25"), row.get("odds_u25")
                        fair = float((1 / odd) / (1 / o + 1 / u)) if o and u and not pd.isna(o) and not pd.isna(u) else 1 / odd
                    edge_pp = (p - fair) * 100
                    if edge_pp < req.min_edge_pp or not enough:
                        continue
                    won = settle_selection(market_key, sel, line, result)
                    if won is None:
                        continue
                    label = f"{market_key}:{sel}" + (f"@{line}" if line is not None else "")
                    records.append(BetRecord(prob=p, odd=odd, won=won, stake=req.stake, edge_pp=edge_pp, market_key=market_key, label=label))
                    bets_out.append(
                        {
                            "date": row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"]),
                            "home": home, "away": away, "score": f"{result.hg}-{result.ag}", "selection": label,
                            "odd": round(odd, 2), "model_prob": round(p, 4), "market_prob": round(fair, 4),
                            "edge_pp": round(edge_pp, 2), "won": won,
                        }
                    )
            games_count[home] = games_count.get(home, 0) + 1
            games_count[away] = games_count.get(away, 0) + 1
        window_start = window_end

    metrics = compute_metrics(records)
    by_sel: dict[str, dict] = {}
    for label in sorted({r.label for r in records if r.label}):
        by_sel[label] = asdict(compute_metrics([r for r in records if r.label == label]))
        by_sel[label].pop("equity_curve", None)
    bins = _calibration_bins(probs_all)
    md = asdict(metrics)
    curve = md.pop("equity_curve", [])
    return BacktestResult(
        request=req.model_dump(mode="json"),
        model_version=versions.DIXON_COLES if req.model == "dixon_coles" else versions.POISSON,
        matches_evaluated=evaluated,
        bets=bets_out[-300:],
        metrics=md,
        by_selection=by_sel,
        calibration_bins=bins,
        equity_curve=curve,
        note=None if evaluated else "Histórico insuficiente para ajustar o modelo (mín. 150 jogos antes do período).",
    )


def _calibration_bins(pairs: list[tuple[float, int]]) -> list[dict]:
    if not pairs:
        return []
    edges = np.linspace(0, 1, 11)
    out = []
    arr = np.array(pairs)
    for lo, hi in zip(edges[:-1], edges[1:], strict=False):
        mask = (arr[:, 0] >= lo) & (arr[:, 0] < hi)
        if mask.sum() == 0:
            continue
        out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(mask.sum()), "predicted": round(float(arr[mask, 0].mean()), 3), "observed": round(float(arr[mask, 1].mean()), 3)})
    return out
