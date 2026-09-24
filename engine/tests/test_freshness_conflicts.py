"""Freshness (FRESH/AGING/STALE/EXPIRED), SourceConflict e Health — nunca usar dado expirado em silêncio."""

from datetime import datetime, timedelta

from sqlalchemy import select

from edgefut.db.models import Event, SourceConflict
from edgefut.db.session import get_engine, session_scope
from edgefut.domain import freshness as fresh
from edgefut.domain.analysis import ConfidenceBreakdown, DataQuality
from edgefut.quality import conflicts_for_event, record_conflict
from edgefut.recommendations.engine import evaluate

NOW = datetime(2026, 9, 23, 12, 0, 0)


def test_odds_freshness_ladder():
    assert fresh.assess("odds", NOW - timedelta(minutes=3), now=NOW).status == "FRESH"
    assert fresh.assess("odds", NOW - timedelta(minutes=20), now=NOW).status == "AGING"
    assert fresh.assess("odds", NOW - timedelta(hours=2), now=NOW).status == "STALE"
    f = fresh.assess("odds", NOW - timedelta(hours=7), now=NOW)
    assert f.status == "EXPIRED" and not f.usable and f.penalty == 0.0
    assert f.valid_until == NOW - timedelta(hours=7) + timedelta(hours=6)
    assert f.age_seconds == 7 * 3600


def test_history_policy_is_in_days_not_minutes():
    assert fresh.assess("history", NOW - timedelta(hours=20), now=NOW).status == "FRESH"
    assert fresh.assess("history", NOW - timedelta(days=3), now=NOW).status == "AGING"
    assert fresh.assess("history", NOW - timedelta(days=10), now=NOW).status == "STALE"
    assert fresh.assess("history", NOW - timedelta(days=30), now=NOW).status == "EXPIRED"


def test_unavailable_and_worst():
    items = [
        fresh.assess("odds", NOW - timedelta(minutes=1), now=NOW),
        fresh.assess("form", NOW - timedelta(days=5), now=NOW),
        fresh.unavailable("lineup", "sem provider"),
    ]
    assert items[2].status == "UNAVAILABLE" and items[2].collected_at is None
    assert fresh.worst(items) == "STALE"
    assert fresh.worst(items, {"odds"}) == "FRESH"
    assert fresh.worst([items[2]]) is None
    assert fresh.describe_age(18) == "há 18 s" and fresh.describe_age(3 * 3600) == "há 3 h"


def test_future_timestamp_is_clamped_to_fresh():
    f = fresh.assess("odds", NOW + timedelta(minutes=5), now=NOW)
    assert f.status == "FRESH" and f.age_seconds == 0


def test_expired_odds_block_recommendations():
    conf = ConfidenceBreakdown(model_version="t", score=90, grade="A", components=[])
    dq = DataQuality(score=90, checks=[])
    from edgefut.domain.analysis import CountDistribution

    empty = CountDistribution(model_version="t", available=False)
    recs, verdict, _ = evaluate(
        markets=[], sim=None, corners=empty, cards=empty, confidence=conf, data_quality=dq, supported=True,
        teams_resolved=True, min_sample=30, model_disagreement_pp=1.0, unreliable_source=False,
        stale_data="Odds coletadas há 7 h.",
    )
    assert verdict.no_bet and verdict.reason == "STALE_DATA"
    assert "7 h" in (verdict.detail or "")


def _event(session, eid=990001):
    row = session.get(Event, eid)
    if row is None:
        row = Event(
            id=eid, home_name="Iraque", away_name="Omã", kickoff_utc=NOW + timedelta(days=1), competition_name="Gulf Cup",
            status="prematch", market_count=10, canonical_event_id="20260924-intl-iraq-oman",
        )
        session.add(row)
        session.flush()
    return row


def test_record_conflict_is_idempotent_and_listed():
    from edgefut.db.migrations import run_migrations

    run_migrations(get_engine())
    with session_scope() as s:
        ev = _event(s)
        kwargs = dict(
            event_id=ev.id, field="neutral_venue", source_a="superbet", value_a={"neutral": False},
            source_b="international_results", value_b={"neutral": True, "city": "Kuwait City"}, selected_value={"neutral": True},
            selected_source="international_results", method="dataset_flag", confidence=0.95, canonical_event_id=ev.canonical_event_id,
        )
        c1 = record_conflict(s, **kwargs)
        c2 = record_conflict(s, **kwargs)
        assert c1.id == c2.id
        record_conflict(s, **{**kwargs, "field": "home_team", "value_a": "Iraque", "value_b": "Oman", "selected_value": "Iraque", "method": "listing_kept_unconfirmed", "confidence": 0.5})
        out = conflicts_for_event(s, ev.id)
        assert len(out) == 2
        fields = {c.field for c in out}
        assert fields == {"neutral_venue", "home_team"}
        labels = {c.resolution_label for c in out}
        assert "flag explícita do dataset histórico" in labels
        assert all(0 <= c.confidence <= 1 for c in out)
        n = s.execute(select(SourceConflict).where(SourceConflict.event_id == ev.id)).scalars().all()
        assert len(n) == 2


def test_system_health_reports_states_without_network():
    from edgefut.quality.health import system_health

    with session_scope() as s:
        h = system_health(s)
    names = {c.key for c in h.components}
    assert {"superbet", "fixtures", "historical", "results", "database", "duckdb", "scheduler", "ollama", "cache"} <= names
    for c in h.components:
        assert c.status in {"HEALTHY", "DEGRADED", "UNAVAILABLE", "STALE"}
    db = next(c for c in h.components if c.key == "database")
    assert db.status == "HEALTHY"
    sched = next(c for c in h.components if c.key == "scheduler")
    assert sched.status in {"UNAVAILABLE", "DEGRADED"}  # desligado nos testes: nunca reportado como HEALTHY
    assert h.overall in {"HEALTHY", "DEGRADED", "UNAVAILABLE", "STALE"}


def test_odds_collected_at_is_last_confirmation_not_last_price_change():
    """Snapshots idênticos não são duplicados; o frescor deve vir da última confirmação na fonte."""
    from edgefut.collectors.sync import latest_odds_rows
    from edgefut.db.models import OddsSnapshot

    from edgefut.db.migrations import run_migrations

    run_migrations(get_engine())
    eid = 990_002
    changed_at = datetime.utcnow() - timedelta(hours=3)
    confirmed_at = datetime.utcnow() - timedelta(minutes=2)
    with session_scope() as s:
        s.add(Event(id=eid, home_name="A", away_name="B", kickoff_utc=datetime.utcnow() + timedelta(days=1), status="prematch", market_count=1, odds_collected_at=confirmed_at))
        s.add(OddsSnapshot(event_id=eid, market_key="1X2", market_name="Resultado", selection_key="HOME", selection_name="1", line=None, price=2.0, status="active", source="superbet", collected_at=changed_at))
        s.flush()
        rows, opening, collected_at, _ = latest_odds_rows(s, eid)
        assert rows and rows[0]["price"] == 2.0 and opening[("1X2", "HOME", None)] == 2.0
        assert collected_at == confirmed_at
        assert fresh.assess("odds", collected_at).status == "FRESH"
        assert fresh.assess("odds", changed_at).status in ("STALE", "EXPIRED")
        s.rollback()
