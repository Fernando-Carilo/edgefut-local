"""Iteração 5 §68 — Collector V2: append-only, deduplicação, canonicalização, cobertura por alvo,
closing = último snapshot pré-kickoff, lacunas de coleta, quarentena e mudança de schema."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from edgefut.db.migrations import run_migrations
from edgefut.db.models import (
    CollectorGap,
    DataQuarantine,
    Event,
    MarketMappingRegistry,
    RawSuperbetSnapshot,
    SuperbetNormalized,
)
from edgefut.db.session import get_engine, session_scope
from edgefut.flywheel import collector, coverage, markets
from edgefut.flywheel.research import load_normalized, timelines

KO = datetime(2030, 6, 1, 18, 0)  # futuro distante: nada de "finished" por acidente
EV = 7100001


@pytest.fixture(scope="module", autouse=True)
def _db():
    run_migrations(get_engine())
    with session_scope() as s:
        for eid in (EV, EV + 1, EV + 2, EV + 3):
            if s.get(Event, eid) is None:
                s.add(Event(id=eid, home_name="Alpha FC", away_name="Beta United", kickoff_utc=KO, competition_name="Liga Teste", category_name="Teste"))


def _odd(mid: int, mname: str, name: str, price: float, code: str = "", status: str = "active", spec: dict | None = None) -> dict:
    return {"marketId": mid, "marketName": mname, "name": name, "price": price, "code": code, "status": status, "specifiers": spec or {}}


def _payload(event_id: int = EV, home_odd: float = 2.0, draw: float = 3.4, away: float = 3.8, extra: list[dict] | None = None, status: str = "NOT_STARTED") -> dict:
    odds = [
        _odd(547, "Resultado Final", "Alpha FC", home_odd, "1"),
        _odd(547, "Resultado Final", "Empate", draw, "0"),
        _odd(547, "Resultado Final", "Beta United", away, "2"),
        _odd(200734, "Total de Gols", "Mais de 2.5", 1.9, "2.5+", spec={"total": "2.5"}),
        _odd(200734, "Total de Gols", "Menos de 2.5", 1.9, "2.5-", spec={"total": "2.5"}),
    ]
    odds.extend(extra or [])
    return {"eventId": event_id, "matchName": "Alpha FC·Beta United", "utcDate": KO.strftime("%Y-%m-%dT%H:%M:%SZ"), "marketCount": 5, "metadata": {"status": status}, "odds": odds}


def _ingest(s, payload: dict, fetched_at: datetime, event_id: int = EV, now: datetime | None = None):
    return collector.ingest_event_payload(s, event_id=event_id, payload=payload, fetched_at=fetched_at, source_url=f"https://x/events/{event_id}", http_status=200, now=now or fetched_at + timedelta(seconds=1))


# ---------------------------------------------------------------------------
# append-only + dedup
# ---------------------------------------------------------------------------
def test_raw_is_append_only_and_confirmations_reference_payload():
    with session_scope() as s:
        t0 = KO - timedelta(hours=47)
        r1 = _ingest(s, _payload(), t0)
        assert r1.ok and r1.stored_payload and not r1.confirmation and r1.snapshot_target == "T-48h"
        # mesmo payload 5 min depois → confirmação (sem blob), aponta para o raw anterior
        r2 = _ingest(s, _payload(), t0 + timedelta(minutes=5))
        assert r2.ok and r2.confirmation and not r2.stored_payload
        row2 = s.get(RawSuperbetSnapshot, r2.raw_id)
        assert row2.payload is None and row2.payload_ref_id == r1.raw_id
        assert collector.raw_payload(s, r2.raw_id)["eventId"] == EV  # resolve via referência
        # preço mudou → novo blob; o anterior continua intacto
        r3 = _ingest(s, _payload(home_odd=2.1), t0 + timedelta(minutes=10))
        assert r3.ok and r3.stored_payload and not r3.confirmation
        assert collector.raw_payload(s, r1.raw_id)["odds"][0]["price"] == 2.0
        s.flush()
        with pytest.raises(DBAPIError, match="append-only"):
            s.execute(text("UPDATE raw_superbet_snapshot SET payload_hash = 'tampered' WHERE id = :i"), {"i": r1.raw_id})
    with session_scope() as s:
        with pytest.raises(DBAPIError, match="append-only"):
            s.execute(text("DELETE FROM raw_superbet_snapshot WHERE id = :i"), {"i": r1.raw_id})
    with session_scope() as s:
        with pytest.raises(DBAPIError, match="append-only"):
            s.execute(text("DELETE FROM superbet_normalized_v1 WHERE event_id = :e"), {"e": EV})


def test_duplicate_snapshot_goes_to_quarantine_not_overwrite():
    with session_scope() as s:
        t = KO - timedelta(hours=46)
        r1 = _ingest(s, _payload(), t)
        assert r1.ok
        before = s.execute(select(func.count()).select_from(RawSuperbetSnapshot).where(RawSuperbetSnapshot.event_id == EV)).scalar_one()
        r2 = _ingest(s, _payload(home_odd=9.9), t)  # mesmo instante, payload diferente
        assert not r2.ok and r2.skipped_reason == "DUPLICATE_SNAPSHOT"
        after = s.execute(select(func.count()).select_from(RawSuperbetSnapshot).where(RawSuperbetSnapshot.event_id == EV)).scalar_one()
        assert after == before
        q = s.execute(select(DataQuarantine).where(DataQuarantine.reason == "DUPLICATE_SNAPSHOT", DataQuarantine.event_id == EV)).scalars().all()
        assert q and q[-1].raw_snapshot_id == r1.raw_id


def test_unknown_event_and_future_timestamp_are_quarantined():
    with session_scope() as s:
        t = KO - timedelta(hours=40)
        r = _ingest(s, _payload(event_id=EV + 500), t)  # payload de outro evento
        assert not r.ok and r.skipped_reason == "UNKNOWN_EVENT"
        r = collector.ingest_event_payload(s, event_id=EV, payload=_payload(), fetched_at=t + timedelta(hours=2), source_url="u", http_status=200, now=t)
        assert not r.ok and r.skipped_reason == "NEGATIVE_TIMESTAMP"
        reasons = {q.reason for q in s.execute(select(DataQuarantine).where(DataQuarantine.event_id == EV)).scalars()}
        assert {"UNKNOWN_EVENT", "NEGATIVE_TIMESTAMP"} <= reasons


# ---------------------------------------------------------------------------
# canonicalização
# ---------------------------------------------------------------------------
def test_canonicalization_sides_lines_blocked_and_player_identity():
    home, away = "Al Ahly", "Inter Miami"
    # 1X2 por code e por nome
    c = markets.normalize(_odd(547, "Resultado Final", "Al Ahly", 2.0, "1"), home, away)
    assert isinstance(c, markets.CanonicalOdd) and c.selection_id == "HOME" and c.canonical_market_id == "MATCH_RESULT"
    c = markets.normalize(_odd(547, "Resultado Final", "Empate", 3.3), home, away)
    assert c.selection_id == "DRAW"
    # total por equipe: lado pelo nome do mercado vence o fallback do id (535 = AWAY por padrão)
    c = markets.normalize(_odd(535, "Al Ahly - Total de Gols", "Mais de 1.5", 2.2, spec={"total": "1.5"}), home, away)
    assert c.market_category == "TEAM_TOTAL" and c.canonical_market_id == "TEAM_TOTAL_HOME_1_5" and c.line == 1.5 and c.side_source == "NAME"
    c = markets.normalize(_odd(535, "Total de Gols da Equipe", "Menos de 1.5", 1.7, spec={"total": "1.5"}), home, away)
    assert c.canonical_market_id == "TEAM_TOTAL_AWAY_1_5" and c.side_source == "ID" and c.selection_id == "UNDER"
    # linha lida do nome quando não há specifier; vírgula decimal aceita
    c = markets.normalize(_odd(704, "Total de Escanteios", "Mais de 9,5", 1.85), home, away)
    assert c.line == 9.5 and c.canonical_market_id == "CORNERS_TOTAL_9_5"
    # bloqueada (price=1, status=block) → None (não é odd, não é erro)
    assert markets.normalize(_odd(547, "Resultado Final", "Al Ahly", 1.0, "1", status="block"), home, away) is None
    # odd ≤ 1 ativa → quarentena
    r = markets.normalize(_odd(547, "Resultado Final", "Al Ahly", 1.0, "1"), home, away)
    assert isinstance(r, markets.NormalizeIssue) and r.kind == "ODD_LE_1"
    # sem linha → MISSING_LINE
    r = markets.normalize(_odd(200734, "Total de Gols", "Mais de", 1.9), home, away)
    assert isinstance(r, markets.NormalizeIssue) and r.kind == "MISSING_LINE"
    # identidade de jogador: id > nome > nada
    c = markets.normalize(_odd(233475, "Marcador", "Messi", 2.5, spec={"player_id": "77"}), home, away)
    assert c.selection_id == "player:77" and c.identity_confidence == "STRONG"
    c = markets.normalize(_odd(233475, "Marcador", "Messi", 2.5, spec={"player_name": "Lionel Messi"}), home, away)
    assert c.selection_id == "player:lionel messi" and c.identity_confidence == "NAME_ONLY"
    r = markets.normalize(_odd(233475, "Marcador", "Messi", 2.5), home, away)
    assert isinstance(r, markets.NormalizeIssue) and r.kind == "PLAYER_IDENTITY_MISSING"
    # classificação: mapeado / fora de escopo com motivo / desconhecido
    assert markets.classify_market(547, "Resultado Final") == ("MAPPED", "MATCH_RESULT", None)
    st, cat, reason = markets.classify_market(999999, "Resultado Final 1º Tempo")
    assert st == "OUT_OF_SCOPE" and cat is None and reason == "PERIOD_MARKET"
    assert markets.classify_market(999998, "Mercado Misterioso")[0] == "UNKNOWN"


def test_ingest_registers_unknown_markets_and_never_maps_them_silently():
    with session_scope() as s:
        t = KO - timedelta(hours=23)
        extra = [_odd(999998, "Mercado Misterioso", "Sim", 1.8), _odd(999998, "Mercado Misterioso", "Não", 1.9), _odd(999997, "Handicap Asiático", "Alpha FC -0.5", 1.95)]
        r = _ingest(s, _payload(event_id=EV + 1, extra=extra), t, event_id=EV + 1)
        assert r.ok and r.odds_unknown == 2 and r.odds_out_of_scope == 1 and r.odds_mapped == 5
        reg = {m.superbet_market_id: m for m in s.execute(select(MarketMappingRegistry).where(MarketMappingRegistry.superbet_market_id.in_([999998, 999997, 547]))).scalars()}
        assert reg[999998].status == "UNKNOWN" and reg[999998].occurrences >= 2 and "Sim" in (reg[999998].sample_selections or [])
        assert reg[999997].status == "OUT_OF_SCOPE" and reg[999997].reason == "HANDICAP"
        assert reg[547].status == "MAPPED" and reg[547].market_category == "MATCH_RESULT"
        # nada do desconhecido chegou à camada normalizada
        assert s.execute(select(func.count()).select_from(SuperbetNormalized).where(SuperbetNormalized.event_id == EV + 1, SuperbetNormalized.superbet_market_id == 999998)).scalar_one() == 0
        # fair/overround só quando o mercado está completo, no mesmo instante
        rows = s.execute(select(SuperbetNormalized).where(SuperbetNormalized.event_id == EV + 1, SuperbetNormalized.market_category == "MATCH_RESULT")).scalars().all()
        assert len(rows) == 3 and all(r.fair_prob is not None for r in rows) and abs(sum(r.fair_prob for r in rows) - 1.0) < 1e-9
        assert all(abs(r.overround - (1 / 2.0 + 1 / 3.4 + 1 / 3.8 - 1)) < 1e-9 for r in rows)


# ---------------------------------------------------------------------------
# cobertura por alvo + closing = último pré-kickoff
# ---------------------------------------------------------------------------
def test_targets_coverage_and_closing_is_last_pre_kickoff():
    eid = EV + 2
    with session_scope() as s:
        # sequência realista: T-24h, T-6h, T-1h, T-5m, +1 min (ao vivo) — T-12h/T-3h/… ficam por cobrir
        seq = [(timedelta(hours=24), 2.0), (timedelta(hours=6), 2.05), (timedelta(hours=1), 2.1), (timedelta(minutes=5), 2.2), (timedelta(minutes=-1), 1.5)]
        for delta, home in seq:
            r = _ingest(s, _payload(event_id=eid, home_odd=home, status="STARTED" if delta < timedelta(0) else "NOT_STARTED"), KO - delta, event_id=eid)
            assert r.ok
        targets = [t for t in s.execute(select(RawSuperbetSnapshot.snapshot_target).where(RawSuperbetSnapshot.event_id == eid).order_by(RawSuperbetSnapshot.fetched_at)).scalars()]
        assert targets == ["T-24h", "T-6h", "T-1h", "T-5m", None]
        # o mesmo alvo nunca é atribuído duas vezes
        r = _ingest(s, _payload(event_id=eid, home_odd=2.06), KO - timedelta(hours=5, minutes=50), event_id=eid)
        assert r.ok and r.snapshot_target is None
        s.flush()
        cov = coverage.coverage_report(s, now=KO + timedelta(hours=1), days=3650)
        row = next(r for r in cov["per_event"] if r["event_id"] == eid)
        # LAST_PRE_KO conta como observado porque há snapshot pré-kickoff e o jogo já começou
        assert set(row["observed"]) == {"T-24h", "T-6h", "T-1h", "T-5m", "LAST_PRE_KO"}
        missing = [t for t in row["expected"] if t not in row["observed"]]
        assert {"T-12h", "T-3h", "T-2h", "T-30m", "T-15m"} <= set(missing)  # lacunas reais, nunca interpoladas
        assert row["coverage_pct"] is not None and row["coverage_pct"] < 100
        assert cov["observed"] <= cov["expected"]
        assert next(t for t in cov["by_target"] if t["target"] == "T-12h")["observed"] < next(t for t in cov["by_target"] if t["target"] == "T-12h")["expected"]
        # closing: último snapshot com minutes_to_kickoff ≥ 0 (o ao vivo é excluído)
        norm = load_normalized(s)
        tl = timelines(norm[norm["event_id"] == eid])
        home_tl = tl[(tl["market_category"] == "MATCH_RESULT") & (tl["selection_id"] == "HOME")].iloc[0]
        assert home_tl["opening_odd"] == 2.0 and home_tl["closing_odd"] == 2.2 and home_tl["closing_minutes"] >= 0
        assert int(home_tl["n_obs"]) == 5  # 2.0, 2.05, 2.1, 2.2 + a confirmação 2.06 → 5 linhas de preço; o ao vivo não conta


# ---------------------------------------------------------------------------
# lacunas de coleta (§48)
# ---------------------------------------------------------------------------
def test_gap_recorded_once_after_downtime_and_not_for_short_pauses():
    with session_scope() as s:
        last = s.execute(select(func.max(RawSuperbetSnapshot.fetched_at))).scalar_one()
        assert last is not None
        # pausa curta (abaixo do limiar) → nada
        assert collector.record_gap_if_needed(s, now=last + timedelta(minutes=10), expected_cadence_min=5) is None
        # downtime → gap único, com o intervalo real
        g = collector.record_gap_if_needed(s, now=last + timedelta(hours=3), expected_cadence_min=5)
        assert g is not None and g.started_at == last and abs(g.minutes - 180) < 0.5 and g.reason == "DOWNTIME"
        assert collector.record_gap_if_needed(s, now=last + timedelta(hours=4), expected_cadence_min=5) is None  # já registada
        assert s.execute(select(func.count()).select_from(CollectorGap).where(CollectorGap.started_at == last)).scalar_one() == 1
        # nenhum snapshot foi fabricado dentro da lacuna
        assert s.execute(select(func.count()).select_from(RawSuperbetSnapshot).where(RawSuperbetSnapshot.fetched_at > last)).scalar_one() == 0


# ---------------------------------------------------------------------------
# quarentena + schema change (§53–§54)
# ---------------------------------------------------------------------------
def test_schema_change_is_recorded_on_raw_and_quarantined_without_losing_the_payload():
    eid = EV + 3
    with session_scope() as s:
        p = _payload(event_id=eid)
        del p["marketCount"]
        for o in p["odds"]:
            o.pop("marketName", None)  # 100 % das odds sem campo obrigatório
        p["odds"] = p["odds"] * 5  # ≥ 20 odds para ativar parse_rate_low
        r = _ingest(s, p, KO - timedelta(hours=20), event_id=eid)
        assert r.ok and r.raw_id is not None
        assert "missing:marketCount" in r.schema_issues and any(i.startswith("parse_rate_low") for i in r.schema_issues)
        raw = s.get(RawSuperbetSnapshot, raw_id := r.raw_id)
        assert raw.schema_issues and raw.parse_failures == len(p["odds"])
        q = s.execute(select(DataQuarantine).where(DataQuarantine.reason == "SCHEMA_CHANGE", DataQuarantine.raw_snapshot_id == raw_id)).scalar_one()
        assert q.resolver_status == "OPEN" and "keys" in (q.payload_excerpt or {})
        assert collector.raw_payload(s, raw_id)["eventId"] == eid  # o payload cru ficou guardado, intacto


def test_kickoff_inconsistent_payload_is_kept_raw_but_not_normalized():
    eid = EV + 3
    with session_scope() as s:
        p = _payload(event_id=eid)
        p["utcDate"] = (KO + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        before = s.execute(select(func.count()).select_from(SuperbetNormalized).where(SuperbetNormalized.event_id == eid)).scalar_one()
        r = _ingest(s, p, KO - timedelta(hours=19), event_id=eid)
        assert r.ok and r.normalized_rows == 0
        after = s.execute(select(func.count()).select_from(SuperbetNormalized).where(SuperbetNormalized.event_id == eid)).scalar_one()
        assert after == before
        assert s.execute(select(func.count()).select_from(DataQuarantine).where(DataQuarantine.reason == "KICKOFF_INCONSISTENT", DataQuarantine.raw_snapshot_id == r.raw_id)).scalar_one() == 1
        ev = s.get(Event, eid)
        assert ev.kickoff_utc == KO  # o evento não foi sobrescrito pelo payload divergente


def test_target_windows_do_not_overlap_and_cover_names():
    wins = [w for _, _, w in collector.SNAPSHOT_TARGETS]
    for (lo1, _hi1), (_lo2, hi2) in zip(wins, wins[1:], strict=False):
        assert lo1 >= hi2, "janelas de alvo não podem sobrepor-se"
    assert collector.target_for_minutes(2880) == "T-48h" and collector.target_for_minutes(5) == "T-5m"
    assert collector.target_for_minutes(1000) is None and collector.target_for_minutes(-1) is None and collector.target_for_minutes(None) is None
