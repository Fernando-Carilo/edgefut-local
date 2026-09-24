"""Iteração 4 — SuperbetEvidenceEngine: fair/overround só com mercado completo, buckets só quando existem,
odds ao vivo excluídas, closing backfill a partir de snapshots existentes, closing_odd na liquidação (CLV)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from edgefut.db.immutability import install_snapshot_guard
from edgefut.db.migrations import run_migrations
from edgefut.db.models import ClosingLine, Event, OddsSnapshot, ShadowPrediction
from edgefut.db.session import get_engine, session_scope
from edgefut.validation import superbet as sb


@pytest.fixture(scope="module", autouse=True)
def _db():
    run_migrations(get_engine())
    install_snapshot_guard()


def _snap_frame() -> pd.DataFrame:
    ko = datetime(2026, 9, 20, 18, 0)
    rows = []
    # evento 1: 1X2 completo em dois instantes (T-24h e T-1h) + uma coleta ao vivo (+10 min)
    for ts, prices in ((ko - timedelta(hours=23), (2.0, 3.4, 3.8)), (ko - timedelta(minutes=50), (1.9, 3.5, 4.0)), (ko + timedelta(minutes=10), (1.5, 4.0, 6.0))):
        for sel, p in zip(("HOME", "DRAW", "AWAY"), prices, strict=True):
            rows.append({"snapshot_id": len(rows) + 1, "event_id": 1, "market_key": "1X2", "selection_key": sel, "line": None, "price": p, "collected_at": ts, "kickoff_utc": ko,
                         "competition_name": "Liga X", "category_name": "País", "home_score": 1, "away_score": 0, "settlement_status": "SETTLED"})
    # evento 2: 1X2 INCOMPLETO (só HOME) → sem fair/overround; TOTAL_GOALS 2.5 completo
    rows.append({"snapshot_id": 99, "event_id": 2, "market_key": "1X2", "selection_key": "HOME", "line": None, "price": 2.2, "collected_at": ko - timedelta(hours=3), "kickoff_utc": ko,
                 "competition_name": "Liga X", "category_name": "País", "home_score": 2, "away_score": 2, "settlement_status": "SETTLED"})
    for sel, p in (("OVER", 1.85), ("UNDER", 1.95)):
        rows.append({"snapshot_id": 100 + len(rows), "event_id": 2, "market_key": "TOTAL_GOALS", "selection_key": sel, "line": 2.5, "price": p, "collected_at": ko - timedelta(hours=3), "kickoff_utc": ko,
                     "competition_name": "Liga X", "category_name": "País", "home_score": 2, "away_score": 2, "settlement_status": "SETTLED"})
    df = pd.DataFrame(rows)
    df["collected_at"] = pd.to_datetime(df["collected_at"])
    df["kickoff_utc"] = pd.to_datetime(df["kickoff_utc"])
    df["minutes_to_kickoff"] = (df["kickoff_utc"] - df["collected_at"]).dt.total_seconds() / 60.0
    df["pre_kickoff"] = df["minutes_to_kickoff"] >= 0
    df["ttk_bucket"] = df["minutes_to_kickoff"].map(sb.ttk_bucket)
    df["family"] = df["market_key"].map(sb.family)
    df["snap_ts"] = df["collected_at"].dt.floor("min")
    df["line_key"] = df["line"].fillna(-999.0)
    return df


def test_fair_probability_and_overround_only_for_complete_markets():
    df = sb.add_fair_probabilities(_snap_frame())
    e1 = df[(df["event_id"] == 1) & df["pre_kickoff"]]
    assert e1["complete"].all()
    grp = e1[e1["snap_ts"] == e1["snap_ts"].min()]
    assert abs(grp["fair_probability"].sum() - 1.0) < 1e-9
    assert abs(grp["overround"].iloc[0] - (1 / 2.0 + 1 / 3.4 + 1 / 3.8 - 1)) < 1e-9
    incomplete = df[(df["event_id"] == 2) & (df["market_key"] == "1X2")]
    assert not incomplete["complete"].any() and incomplete["fair_probability"].isna().all()
    ou = df[(df["event_id"] == 2) & (df["market_key"] == "TOTAL_GOALS")]
    assert ou["complete"].all()


def test_live_snapshots_excluded_and_buckets_only_when_present():
    df = sb.add_fair_probabilities(_snap_frame())
    cov = sb.coverage(df)
    assert cov["snapshots_live_excluded"] == 3 and cov["snapshots_pre_kickoff"] == 9
    buckets = {b["bucket"]: b for b in sb.bucket_availability(df)}
    assert buckets["T-24h"]["exists"] and buckets["T-1h"]["exists"]
    assert not buckets["T-15m"]["exists"] and buckets["T-15m"]["events"] == 0
    assert sb.ttk_bucket(-5) is None and sb.ttk_bucket(10) == "T-15m" and sb.ttk_bucket(1439) == "T-24h" and sb.ttk_bucket(5000) == ">24h"


def test_selection_timeline_opening_closing_and_movement():
    df = sb.add_fair_probabilities(_snap_frame())
    closing = pd.DataFrame(columns=["event_id", "market_key", "selection_key", "line", "closing_price", "closing_collected_at", "closing_minutes_before", "line_key"])
    tl = sb.selection_timelines(df, closing)
    home = tl[(tl["event_id"] == 1) & (tl["selection_key"] == "HOME")].iloc[0]
    assert home["opening_price"] == 2.0 and home["closing_price"] == 1.9   # o snapshot ao vivo (1.5) NÃO é o closing
    assert home["closing_source"] == "last_pre_kickoff" and home["n_snapshots"] == 2
    assert home["move_pp"] > 0  # encurtou
    # com closing_line própria, ela prevalece
    closing = pd.DataFrame([{"event_id": 1, "market_key": "1X2", "selection_key": "HOME", "line": None, "closing_price": 1.95, "closing_collected_at": None, "closing_minutes_before": 3.0}])
    closing["line_key"] = -999.0
    tl2 = sb.selection_timelines(df, closing)
    h2 = tl2[(tl2["event_id"] == 1) & (tl2["selection_key"] == "HOME")].iloc[0]
    assert h2["closing_price"] == 1.95 and h2["closing_source"] == "closing_line"


def test_bias_report_gates_small_samples_and_marks_market_level_trivial():
    df = sb.add_fair_probabilities(_snap_frame())
    tl = sb.selection_timelines(df, pd.DataFrame())
    rep = sb.bias_report(tl)
    assert rep["n"] > 0
    for r in rep["rows"]:
        if r["segment"].startswith("market:"):
            assert "bias_pp_ci" not in r
        else:
            assert r["verdict"] == "INSUFFICIENT"  # 1–2 eventos nunca viram "viés"


def test_clv_and_panel_from_shadow_frame():
    ko = pd.Timestamp("2026-09-20 18:00")
    shadow = pd.DataFrame([
        {"id": 1, "event_id": 1, "created_at": ko - pd.Timedelta(hours=5), "kickoff_utc": ko, "market_key": "1X2", "selection_key": "HOME", "line": None, "odd": 2.0, "model_prob": 0.55, "market_prob": 0.5,
         "state": "OBSERVATION", "won": True, "closing_odd": 1.9, "settled_at": ko + pd.Timedelta(hours=3), "event_settlement_status": "SETTLED", "hybrid_prob": None, "residual_edge_pp": None, "required_edge_pp": None, "minutes_to_kickoff": None,
         "competition_name": "x", "dataset_code": None, "edge_pp": 5.0, "confidence_score": 60, "opportunity_score": 50, "data_quality": 70, "evidence": "MODEL_ONLY", "is_primary": True, "home_score": 1, "away_score": 0},
        {"id": 2, "event_id": 3, "created_at": ko - pd.Timedelta(hours=5), "kickoff_utc": ko + pd.Timedelta(days=3), "market_key": "TOTAL_GOALS", "selection_key": "OVER", "line": 2.5, "odd": 1.9, "model_prob": 0.5, "market_prob": 0.5,
         "state": "NO_BET", "won": None, "closing_odd": None, "settled_at": None, "event_settlement_status": None, "hybrid_prob": None, "residual_edge_pp": None, "required_edge_pp": None, "minutes_to_kickoff": None,
         "competition_name": "x", "dataset_code": None, "edge_pp": 0.0, "confidence_score": 40, "opportunity_score": 20, "data_quality": 70, "evidence": "MODEL_ONLY", "is_primary": False, "home_score": None, "away_score": None},
    ])
    shadow["minutes_to_kickoff"] = (shadow["kickoff_utc"] - shadow["created_at"]).dt.total_seconds() / 60
    shadow["ttk_bucket"] = shadow["minutes_to_kickoff"].map(sb.ttk_bucket)
    shadow["family"] = shadow["market_key"].map(sb.family)
    shadow["line_key"] = shadow["line"].fillna(-999.0)
    clv = sb.clv_report(shadow, pd.DataFrame())
    assert clv["n_priced"] == 2 and clv["n_with_closing"] == 1
    assert abs(clv["all"]["clv_pct"]["point"] - (2.0 / 1.9 - 1) * 100) < 1e-3
    panel = sb.shadow_panel(shadow, now=datetime(2026, 9, 21, 12, 0))
    fam = {f["family"]: f for f in panel["families"]}
    assert fam["1X2"]["settled"] == 1 and fam["OU"]["upcoming"] == 1 and panel["total"]["events"] == 2


# ---------------------------------------------------------------- DB: backfill de closing + closing_odd na liquidação
def test_closing_backfill_from_existing_snapshots_and_shadow_clv():
    from edgefut.backtesting.reconciliation import _settle_shadow, backfill_shadow_closing
    from edgefut.odds.closing import backfill_closing_lines

    ko = datetime(2026, 1, 10, 20, 0)
    now = ko + timedelta(hours=6)  # app "esteve fechado" no kickoff: sem closing capturado
    with session_scope() as s:
        ev = Event(id=910001, home_name="H", away_name="A", kickoff_utc=ko, status="finished", market_count=1, home_score=2, away_score=1, settlement_status="SETTLED")
        s.add(ev)
        for ts, p in ((ko - timedelta(hours=20), 2.1), (ko - timedelta(minutes=40), 2.05), (ko + timedelta(minutes=30), 1.4)):
            s.add(OddsSnapshot(event_id=ev.id, market_key="1X2", market_name="1X2", selection_key="HOME", selection_name="H", line=None, price=p, collected_at=ts))
        s.add(ShadowPrediction(event_id=ev.id, kickoff_utc=ko, market_key="1X2", selection_key="HOME", line=None, odd=2.2, model_prob=0.52, market_prob=0.47, edge_pp=5.0, state="OBSERVATION", model_version="t", created_at=ko - timedelta(hours=3)))
    with session_scope() as s:
        n = backfill_closing_lines(s, now=now)
        assert n == 1
        cl = s.execute(__import__("sqlalchemy").select(ClosingLine).where(ClosingLine.event_id == 910001)).scalars().all()
        assert len(cl) == 1 and cl[0].price == 2.05 and 39 < cl[0].minutes_before_kickoff < 41  # última PRÉ-kickoff, nunca a odd ao vivo
    with session_scope() as s:
        settled = _settle_shadow(s, now)
        assert settled >= 1
    with session_scope() as s:
        sp = s.execute(__import__("sqlalchemy").select(ShadowPrediction).where(ShadowPrediction.event_id == 910001)).scalar_one()
        assert sp.won is True and sp.closing_odd == 2.05
        assert abs((sp.odd / sp.closing_odd - 1) * 100 - (2.2 / 2.05 - 1) * 100) < 1e-9
        assert backfill_shadow_closing(s) == 0  # nada pendente


def test_ttk_bucket_and_odds_band_edges():
    assert sb.odds_band(1.2) == "1.00-1.50" and sb.odds_band(1.5) == "1.50-2.00" and sb.odds_band(7.0) == "5.00+" and sb.odds_band(None) is None
    assert sb.family("TOTAL_CORNERS") == "CORNERS" and sb.family("CORRECT_SCORE") == "OTHER"
    assert np.isnan(float("nan")) and sb.ttk_bucket(float("nan")) is None
