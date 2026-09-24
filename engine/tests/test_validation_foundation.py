"""Iteração 3 — fase B/C: reconciliação de settlement, órfãos do scheduler, shadow append-only,
TemporalFeatureStore anti-leakage e bootstrap/qualidade de amostra."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from edgefut.db.immutability import ImmutableSnapshotError, install_snapshot_guard
from edgefut.db.migrations import run_migrations
from edgefut.db.models import Event, JobRun, PredictionSnapshot, ShadowPrediction
from edgefut.db.session import get_engine, session_scope
from edgefut.features.temporal import LeakageError, TemporalFeatureStore
from edgefut.validation.bootstrap import Interval, bootstrap_ci, model_significance, paired_bootstrap_diff, sample_quality


@pytest.fixture(scope="module", autouse=True)
def _db():
    run_migrations(get_engine())
    install_snapshot_guard()


def _event(s, eid: int, kickoff: datetime, hg=None, ag=None) -> Event:
    ev = s.get(Event, eid)
    if ev is None:
        ev = Event(id=eid, home_name=f"H{eid}", away_name=f"A{eid}", kickoff_utc=kickoff, status="prematch", market_count=1)
        s.add(ev)
    ev.home_score, ev.away_score = hg, ag
    ev.settlement_status = None
    ev.settlement_attempts = 0
    return ev


# ---------------------------------------------------------------- reconciliação
def test_reconciliation_classifies_every_finished_event():
    from edgefut.backtesting.reconciliation import reconcile, summary

    now = datetime(2026, 9, 20, 12, 0)
    with session_scope() as s:
        _event(s, 880001, now - timedelta(hours=5), 2, 1)            # resultado → SETTLED
        _event(s, 880002, now - timedelta(hours=5))                  # sem resultado, recente → PENDING
        _event(s, 880003, now - timedelta(hours=100))                # sem resultado há 100 h → ERROR
        _event(s, 880004, now - timedelta(hours=8), 1, 1)            # resultado mas snapshot não liquidado → ERROR
        s.add(PredictionSnapshot(
            event_id=880004, kickoff_utc=now - timedelta(hours=8), home_name="x", away_name="y", competition_name="T",
            model_version="pipeline-v2", model_versions={}, features={}, probabilities={}, odds=[], recommendations=[],
        ))
        _event(s, 880005, now + timedelta(hours=2))                  # futuro → fora do escopo
    with session_scope() as s:
        res = reconcile(s, now=now, try_settle=False)
        assert res["ok"]
        states = {eid: s.get(Event, eid).settlement_status for eid in (880001, 880002, 880003, 880004)}
        assert states[880001] == "SETTLED"
        assert states[880002] == "SETTLEMENT_PENDING"
        assert states[880003] == "SETTLEMENT_ERROR"
        assert states[880004] == "SETTLEMENT_ERROR"
        assert s.get(Event, 880005).settlement_status is None
        assert s.get(Event, 880002).settlement_attempts == 1
        assert s.get(Event, 880001).settlement_attempts == 0
        assert res["divergences"] == 1  # o snapshot não liquidado conta como divergência
        sm = summary(s)
        assert sm["unsettled_finished"] >= 2 and sm["error"] >= 2
    # segunda passagem: pendente incrementa tentativas; nada muda de estado sem novos dados
    with session_scope() as s:
        reconcile(s, now=now + timedelta(hours=1), try_settle=False)
        assert s.get(Event, 880002).settlement_attempts == 2
        assert s.get(Event, 880002).settlement_status == "SETTLEMENT_PENDING"


def test_reconciliation_settles_shadow_rows_and_shadow_is_append_only():
    from edgefut.backtesting.reconciliation import reconcile

    now = datetime(2026, 9, 21, 12, 0)
    with session_scope() as s:
        _event(s, 880010, now - timedelta(hours=6), 3, 0)
        sp = ShadowPrediction(
            event_id=880010, kickoff_utc=now - timedelta(hours=6), market_key="1X2", selection_key="HOME", line=None, odd=1.8,
            model_prob=0.6, state="VALUE", model_version="pipeline-v2",
        )
        s.add(sp)
        s.commit()
        sid = sp.id
    with session_scope() as s:
        reconcile(s, now=now, try_settle=False)
        sp = s.get(ShadowPrediction, sid)
        assert sp.won is True and sp.result["hg"] == 3 and sp.settled_at is not None
    with session_scope() as s:
        sp = s.get(ShadowPrediction, sid)
        sp.model_prob = 0.9
        with pytest.raises(ImmutableSnapshotError):
            s.flush()
        s.rollback()
    with session_scope() as s:
        sp = s.get(ShadowPrediction, sid)
        assert sp.model_prob == 0.6
        s.delete(sp)
        with pytest.raises(ImmutableSnapshotError):
            s.flush()
        s.rollback()


def test_orphan_job_runs_are_marked_interrupted_on_boot():
    from edgefut.scheduler.jobs import mark_orphan_runs

    with session_scope() as s:
        jr = JobRun(job="sync_odds", correlation_id="test-orphan", status="running", started_at=datetime.utcnow() - timedelta(hours=3))
        s.add(jr)
        s.commit()
        jid = jr.id
    n = mark_orphan_runs()
    assert n >= 1
    with session_scope() as s:
        jr = s.get(JobRun, jid)
        assert jr.status == "interrupted" and jr.finished_at is not None


# ---------------------------------------------------------------- TemporalFeatureStore
def _frame() -> pd.DataFrame:
    rows = []
    d0 = datetime(2025, 8, 1)
    teams = ["A", "B", "C", "D"]
    for i in range(40):
        h, a = teams[i % 4], teams[(i + 1) % 4]
        rows.append({"dataset_code": "E0", "date": d0 + timedelta(days=i), "home": h, "away": a, "hg": i % 3, "ag": (i + 1) % 2, "source": "t", "source_url": None})
    return pd.DataFrame(rows)


def test_temporal_store_never_returns_rows_at_or_after_as_of():
    tfs = TemporalFeatureStore(frame=_frame())
    as_of = datetime(2025, 8, 21)
    sl = tfs.slice(home="A", away="B", codes=["E0"], as_of=as_of)
    assert sl.max_date is not None and sl.max_date < pd.Timestamp(as_of)
    assert len(sl.competition_matches) == 20  # dias 1..20
    assert (sl.home_matches["date"] < pd.Timestamp(as_of)).all()
    assert (sl.h2h["date"] < pd.Timestamp(as_of)).all()
    # partida exatamente em as_of não pode entrar (comparação estrita)
    assert not (sl.competition_matches["date"] == pd.Timestamp(as_of)).any()
    # nada no passado remoto além do since default
    early = tfs.slice(home="A", away="B", codes=["E0"], as_of=datetime(2025, 8, 1))
    assert early.competition_matches.empty and early.home_matches.empty and early.max_date is None


def test_temporal_store_raises_if_a_backend_leaks_future_rows():
    class LeakyStore:
        def team_matches(self, team, codes, before, limit):
            return _frame()  # devolve tudo, ignorando `before`

        def competition_matches(self, codes, since=None, before=None):
            return _frame()

        def h2h(self, a, b, codes, limit, before):
            return _frame()

    tfs = TemporalFeatureStore(store=LeakyStore())  # type: ignore[arg-type]
    with pytest.raises(LeakageError):
        tfs.competition_matches(["E0"], as_of=datetime(2025, 8, 10))
    with pytest.raises(LeakageError):
        tfs.slice(home="A", away="B", codes=["E0"], as_of=datetime(2025, 8, 10))


def test_temporal_store_uses_distinct_codes_per_team():
    f = _frame()
    f2 = f.copy()
    f2["dataset_code"] = "SP1"
    f2["home"] = f2["home"].str.lower()
    f2["away"] = f2["away"].str.lower()
    tfs = TemporalFeatureStore(frame=pd.concat([f, f2]))
    sl = tfs.slice(home="A", away="b", codes=["E0", "SP1"], as_of=datetime(2025, 9, 1), home_codes=["E0"], away_codes=["SP1"])
    assert set(sl.home_matches["dataset_code"]) == {"E0"}
    assert set(sl.away_matches["dataset_code"]) == {"SP1"}


# ---------------------------------------------------------------- bootstrap / amostra
def test_sample_quality_thresholds_and_bootstrap_ci():
    assert sample_quality(50) == "INSUFFICIENT"
    assert sample_quality(100) == "EARLY"
    assert sample_quality(300) == "MODERATE"
    assert sample_quality(1000) == "STRONG"

    rng = np.random.default_rng(1)
    # ROI real ~0: IC deve cruzar zero → inconclusivo
    profits = rng.choice([1.0, -1.0], size=400)
    ci = bootstrap_ci(profits, zero_test=True, seed=1)
    assert ci.n == 400 and ci.low is not None and ci.low < 0 < ci.high and ci.conclusive is False
    # sinal forte: IC não cruza zero
    ci2 = bootstrap_ci(rng.normal(0.5, 0.2, size=400), zero_test=True, seed=1)
    assert ci2.conclusive is True and ci2.low > 0
    # N pequeno: sem IC
    assert bootstrap_ci([1.0, 2.0], zero_test=True).low is None
    assert bootstrap_ci([]).n == 0

    # pareado: modelo com Brier sistematicamente menor
    base = rng.uniform(0.2, 0.3, size=500)
    model = base - 0.01 + rng.normal(0, 0.002, size=500)
    d = paired_bootstrap_diff(model, base, seed=2)
    assert d.high < 0 and d.conclusive is True
    assert model_significance(500, d) == "PROMISING"
    assert model_significance(1500, d, windows_positive=8, windows_total=8) == "CONSISTENT"
    assert model_significance(1500, d, windows_positive=3, windows_total=8) == "PROMISING"
    assert model_significance(50, d) == "INSUFFICIENT DATA"
    assert model_significance(500, Interval(0.001, -0.002, 0.004, 500, False)) == "NO CLEAR ADVANTAGE"
