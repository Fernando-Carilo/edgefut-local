"""Backtest anti-leakage: as_of por partida, odds de fechamento só para CLV, janelas walk-forward."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from edgefut.backtesting import lab
from edgefut.backtesting.lab import BacktestRequest, LeakageError, assert_no_leakage, run_backtest


def _synthetic(seed: int = 3, seasons: int = 2, n_teams: int = 12) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(n_teams)]
    strength = {t: rng.normal(0, 0.25) for t in teams}
    rows = []
    start = datetime(2024, 8, 10)
    day = 0
    for s in range(seasons):
        for i, h in enumerate(teams):
            for j, a in enumerate(teams):
                if h == a:
                    continue
                lam = 1.4 * np.exp(strength[h] - strength[a] + 0.2)
                mu = 1.1 * np.exp(strength[a] - strength[h])
                hg, ag = rng.poisson(lam), rng.poisson(mu)
                date = start + timedelta(days=day + 365 * s)
                day = (day + 1) % 280
                ph, pd_, pa = 0.45 + 0.2 * (strength[h] - strength[a]), 0.27, 0.28 - 0.2 * (strength[h] - strength[a])
                probs = np.clip([ph, pd_, pa], 0.05, 0.9)
                probs = probs / probs.sum() * 1.05  # margem 5%
                rows.append(
                    {
                        "dataset_code": "E0", "date": pd.Timestamp(date), "home": h, "away": a, "hg": hg, "ag": ag,
                        "odds_h": 1 / probs[0], "odds_d": 1 / probs[1], "odds_a": 1 / probs[2],
                        # fechamento absurdamente generoso: se fosse usado na decisão, geraria edge em todo jogo
                        "odds_close_h": 9.0, "odds_close_d": 9.0, "odds_close_a": 9.0,
                        "odds_o25": 1.9, "odds_u25": 1.9, "neutral": False, "competition": "E0",
                    }
                )
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


class _FakeStore:
    def __init__(self, df):
        self.df = df

    def competition_matches(self, codes, since=None, before=None):
        d = self.df[self.df["dataset_code"].isin(codes)]
        if since is not None:
            d = d[d["date"] >= pd.Timestamp(since)]
        if before is not None:
            d = d[d["date"] < pd.Timestamp(before)]
        return d.copy()


@pytest.fixture
def fake_store(monkeypatch):
    df = _synthetic()
    monkeypatch.setattr(lab, "get_store", lambda: _FakeStore(df))
    return df


def test_assert_no_leakage_raises_on_future_rows():
    df = pd.DataFrame({"date": [pd.Timestamp("2025-01-01"), pd.Timestamp("2025-03-01")]})
    assert_no_leakage(df, datetime(2025, 3, 2))
    with pytest.raises(LeakageError):
        assert_no_leakage(df, datetime(2025, 3, 1))  # igual ao as_of também é vazamento
    with pytest.raises(LeakageError):
        assert_no_leakage(df, datetime(2025, 2, 1))
    assert_no_leakage(pd.DataFrame(), datetime(2020, 1, 1))


def test_walk_forward_windows_never_see_future(fake_store):
    df = fake_store
    since = df["date"].iloc[len(df) // 2].to_pydatetime()
    res = run_backtest(BacktestRequest(dataset_code="E0", since=since, until=df["date"].max().to_pydatetime() + timedelta(days=1), refit_every_days=30))
    assert res.windows, "walk-forward deve produzir janelas"
    assert res.leakage_checks == len(res.windows)
    for w in res.windows:
        if w["train_end"] is not None:
            assert pd.Timestamp(w["train_end"]) < pd.Timestamp(w["test_start"])
        assert pd.Timestamp(w["test_start"]) < pd.Timestamp(w["test_end"])
    # janelas são contíguas e ordenadas (nunca aleatórias)
    starts = [pd.Timestamp(w["test_start"]) for w in res.windows]
    assert starts == sorted(starts)
    assert res.matches_evaluated > 0
    assert res.decision_odds.startswith("pre-closing")


def test_closing_odds_never_drive_the_decision(fake_store):
    df = fake_store
    since = df["date"].iloc[len(df) // 2].to_pydatetime()
    res = run_backtest(BacktestRequest(dataset_code="E0", since=since, until=df["date"].max().to_pydatetime() + timedelta(days=1), min_edge_pp=3.0, max_odd=8.0))
    # odds_close_* = 9.0 em todas as linhas: se a decisão as usasse, toda aposta teria odd 9.0.
    assert all(b["odd"] < 8.0 for b in res.bets)
    assert all(b["closing_odd"] == 9.0 for b in res.bets if b["selection"].startswith("1X2"))
    # CLV é calculado contra o fechamento e é fortemente negativo (odd tomada << fechamento)
    if res.metrics["bets"]:
        assert res.metrics["clv_pct"] is not None and res.metrics["clv_pct"] < 0
    for b in res.bets:
        assert "as_of" in b


def test_rolling_scheme_limits_training_window(fake_store):
    df = fake_store
    since = df["date"].iloc[int(len(df) * 0.75)].to_pydatetime()
    until = df["date"].max().to_pydatetime() + timedelta(days=1)
    exp = run_backtest(BacktestRequest(dataset_code="E0", since=since, until=until, refit_every_days=60, scheme="expanding"))
    roll = run_backtest(BacktestRequest(dataset_code="E0", since=since, until=until, refit_every_days=60, scheme="rolling", train_window_days=200))
    assert exp.windows and roll.windows
    for we, wr in zip(exp.windows, roll.windows, strict=True):
        assert wr["n_train"] <= we["n_train"]
        if wr["train_start"]:
            assert pd.Timestamp(wr["train_start"]) >= pd.Timestamp(wr["test_start"]) - timedelta(days=200)


def test_bet_is_never_made_without_pre_match_odd(fake_store):
    df = fake_store.copy()
    df["odds_h"] = np.nan
    df["odds_d"] = np.nan
    df["odds_a"] = np.nan
    lab.get_store = lambda: _FakeStore(df)  # type: ignore[assignment]
    since = df["date"].iloc[len(df) // 2].to_pydatetime()
    res = run_backtest(BacktestRequest(dataset_code="E0", market="1X2", since=since, until=df["date"].max().to_pydatetime() + timedelta(days=1)))
    assert res.metrics["bets"] == 0  # fechamento existe (9.0) mas não pode substituir a odd pré-jogo
