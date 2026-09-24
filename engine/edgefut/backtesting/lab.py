"""Backtest Lab — walk-forward sobre histórico de liga com odds reais do CSV.

Anti-leakage (iteração 2):
* Cada partida tem um `as_of` = data do jogo. O modelo usado para ela só viu jogos
  com `date < as_of` (guarda `assert_no_leakage`, que levanta `LeakageError`).
* A decisão de apostar usa SEMPRE a odd pré-fechamento (`odds_h/d/a`, `odds_o25/u25`)
  — a odd de fechamento (`odds_close_*`) só entra como referência de CLV depois da
  decisão. Nunca há "recomendação retroativa" com odd de fechamento.
* Janelas walk-forward (expanding ou rolling), nunca split aleatório. Resultados por
  janela são devolvidos em `windows`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from ..core import versions
from ..models.dixon_coles import MIN_MATCHES, fit_dixon_coles
from ..models.goals_common import markets_from_matrix, score_matrix
from ..providers.historical import get_store
from .metrics import BetRecord, Metrics, compute_metrics
from .settlement import MatchResult, settle_selection


class LeakageError(RuntimeError):
    """Dados com data >= as_of chegaram ao treino."""


def assert_no_leakage(train: pd.DataFrame, as_of: datetime | pd.Timestamp) -> None:
    if train is None or train.empty:
        return
    mx = train["date"].max()
    if pd.Timestamp(mx) >= pd.Timestamp(as_of):
        raise LeakageError(f"treino contém partida em {mx} >= as_of {as_of}")


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
    refit_every_days: int = Field(30, ge=7, le=365)  # tamanho da janela de teste (walk-forward)
    scheme: Literal["expanding", "rolling"] = "expanding"
    train_window_days: int | None = Field(None, ge=90)  # só para scheme=rolling
    closing_for_clv: bool = True  # odd de fechamento apenas para CLV (nunca para decidir)
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
    windows: list[dict] = field(default_factory=list)
    leakage_checks: int = 0
    decision_odds: str = "pre-closing (odds_h/d/a, odds_o25/u25)"


def _selections_for(market: str) -> list[tuple[str, str, float | None]]:
    if market == "1X2":
        return [("1X2", "HOME", None), ("1X2", "DRAW", None), ("1X2", "AWAY", None)]
    if market.startswith("TOTAL_GOALS_"):
        line = float(market.split("_")[-1])
        return [("TOTAL_GOALS", "OVER", line), ("TOTAL_GOALS", "UNDER", line)]
    return _selections_for("1X2") + _selections_for("TOTAL_GOALS_2.5")


def _val(row, col) -> float | None:
    v = row.get(col)
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return float(v)


def _decision_odd(row, market_key, sel, line) -> float | None:
    """Odd disponível ANTES do jogo (pré-fechamento). Nunca odds_close_*."""
    if market_key == "1X2":
        return _val(row, f"odds_{ {'HOME': 'h', 'DRAW': 'd', 'AWAY': 'a'}[sel] }")
    if market_key == "TOTAL_GOALS" and line == 2.5:
        return _val(row, "odds_o25" if sel == "OVER" else "odds_u25")
    return None


def _closing_odd(row, market_key, sel, line) -> float | None:
    if market_key == "1X2":
        return _val(row, f"odds_close_{ {'HOME': 'h', 'DRAW': 'd', 'AWAY': 'a'}[sel] }")
    return None  # CSV não traz fechamento para totais


def _fair_prob(row, market_key, sel, odd) -> float:
    """Probabilidade de mercado sem margem (multiplicativa) a partir das odds pré-fechamento."""
    if market_key == "1X2":
        trio = [_val(row, "odds_h"), _val(row, "odds_d"), _val(row, "odds_a")]
        if all(v and v > 1 for v in trio):
            return float((1 / odd) / sum(1 / v for v in trio))  # type: ignore[operator]
        return 1 / odd
    o, u = _val(row, "odds_o25"), _val(row, "odds_u25")
    if o and u and o > 1 and u > 1:
        return float((1 / odd) / (1 / o + 1 / u))
    return 1 / odd


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
    probs_all: list[tuple[float, int]] = []
    evaluated = 0
    leakage_checks = 0
    windows_out: list[dict] = []
    games_count: dict[str, int] = {}
    for t in df[df["date"] < pd.Timestamp(since)].itertuples():
        games_count[t.home] = games_count.get(t.home, 0) + 1
        games_count[t.away] = games_count.get(t.away, 0) + 1

    window_start = pd.Timestamp(since)
    while window_start < pd.Timestamp(until):
        window_end = window_start + timedelta(days=req.refit_every_days)
        train = df[df["date"] < window_start]
        if req.scheme == "rolling" and req.train_window_days:
            train = train[train["date"] >= window_start - timedelta(days=req.train_window_days)]
        assert_no_leakage(train, window_start)
        leakage_checks += 1
        params = fit_dixon_coles(train, reference_date=window_start.to_pydatetime()) if len(train) >= MIN_MATCHES else None
        block = test[(test["date"] >= window_start) & (test["date"] < window_end)]
        train_max = pd.Timestamp(train["date"].max()) if not train.empty else None
        win_records: list[BetRecord] = []
        win_eval = 0
        for row in block.to_dict("records"):
            as_of = pd.Timestamp(row["date"])
            # Guarda por partida: o modelo desta janela foi ajustado só com dados < window_start <= as_of.
            if train_max is not None and train_max >= as_of:
                raise LeakageError(f"modelo viu {train_max} para partida em {as_of}")
            home, away = row["home"], row["away"]
            if params is None or home not in params.attack or away not in params.attack:
                games_count[home] = games_count.get(home, 0) + 1
                games_count[away] = games_count.get(away, 0) + 1
                continue
            evaluated += 1
            win_eval += 1
            lam, mu = params.lambdas(home, away, 1.0)
            rho = params.rho if req.model == "dixon_coles" else None
            m = score_matrix(max(0.15, min(5, lam)), max(0.15, min(5, mu)), rho)
            mk = markets_from_matrix(m)
            result = MatchResult(hg=int(row["hg"]), ag=int(row["ag"]))
            probs_all.append((mk["p_home"], 1 if result.hg > result.ag else 0))
            enough = min(games_count.get(home, 0), games_count.get(away, 0)) >= req.min_team_games
            for market_key, sel, line in selections:
                odd = _decision_odd(row, market_key, sel, line)
                if odd is None or not (req.min_odd <= odd <= req.max_odd):
                    continue
                if market_key == "1X2":
                    p = {"HOME": mk["p_home"], "DRAW": mk["p_draw"], "AWAY": mk["p_away"]}[sel]
                else:
                    p = mk["over"]["2.5"] if sel == "OVER" else mk["under"]["2.5"]
                fair = _fair_prob(row, market_key, sel, odd)
                edge_pp = (p - fair) * 100
                if edge_pp < req.min_edge_pp or not enough:
                    continue
                won = settle_selection(market_key, sel, line, result)
                if won is None:
                    continue
                closing = _closing_odd(row, market_key, sel, line) if req.closing_for_clv else None
                label = f"{market_key}:{sel}" + (f"@{line}" if line is not None else "")
                rec = BetRecord(prob=p, odd=odd, won=won, stake=req.stake, edge_pp=edge_pp, market_key=market_key, label=label, closing_odd=closing)
                records.append(rec)
                win_records.append(rec)
                bets_out.append(
                    {
                        "date": row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"]),
                        "home": home, "away": away, "score": f"{result.hg}-{result.ag}", "selection": label,
                        "odd": round(odd, 2), "closing_odd": round(closing, 2) if closing else None,
                        "clv_pct": round((odd / closing - 1) * 100, 2) if closing else None,
                        "model_prob": round(p, 4), "market_prob": round(fair, 4),
                        "edge_pp": round(edge_pp, 2), "won": won, "as_of": as_of.isoformat(),
                    }
                )
            games_count[home] = games_count.get(home, 0) + 1
            games_count[away] = games_count.get(away, 0) + 1
        wm = asdict(compute_metrics(win_records))
        wm.pop("equity_curve", None)
        windows_out.append(
            {
                "train_start": train["date"].min().isoformat() if not train.empty else None,
                "train_end": train["date"].max().isoformat() if not train.empty else None,
                "test_start": window_start.isoformat(), "test_end": min(window_end, pd.Timestamp(until)).isoformat(),
                "n_train": int(len(train)), "n_test": int(len(block)), "evaluated": win_eval, "fitted": params is not None,
                "metrics": wm,
            }
        )
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
        windows=windows_out,
        leakage_checks=leakage_checks,
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
