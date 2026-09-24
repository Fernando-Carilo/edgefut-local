"""Iteração 5 §68 — liquidação por mercado (nunca assumir loss), CLV V2 descritivo, máquina de estados
MARKET_EDGE (VALUE nunca automático; staking desligado), backup/retenção/restore."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import select

from edgefut.core.paths import get_paths
from edgefut.db.migrations import run_migrations
from edgefut.db.models import Event, ManualCorrection, MarketEdgeState, SuperbetSettlement
from edgefut.db.session import get_engine, session_scope
from edgefut.flywheel import collector, governance, storage
from edgefut.flywheel.research import clv_v2, load_normalized, timelines
from edgefut.flywheel.settlement import (
    FullResult,
    settle_canonical,
    settle_events,
    settlement_audit,
)

pytestmark = pytest.mark.value_disabled  # regras reais de produção: VALUE_ENABLED=false

KO = datetime(2026, 3, 1, 15, 0)  # passado: jogos "terminados"
EV = 7200001


@pytest.fixture(scope="module", autouse=True)
def _db():
    run_migrations(get_engine())
    with session_scope() as s:
        for eid, (hs, as_) in ((EV, (2, 1)), (EV + 1, (None, None)), (EV + 2, (1, 1))):
            if s.get(Event, eid) is None:
                s.add(Event(id=eid, home_name="Gamma", away_name="Delta", kickoff_utc=KO, competition_name="Liga Teste", category_name="Teste", status="finished", home_score=hs, away_score=as_, result_source="test" if hs is not None else None))


def _odd(mid, mname, name, price, code="", spec=None):
    return {"marketId": mid, "marketName": mname, "name": name, "price": price, "code": code, "status": "active", "specifiers": spec or {}}


def _payload(eid: int, home: float, draw: float, away: float, over: float, under: float, status="NOT_STARTED", scores: dict | None = None):
    meta = {"status": status} | (scores or {})
    return {"eventId": eid, "matchName": "Gamma·Delta", "utcDate": KO.strftime("%Y-%m-%dT%H:%M:%SZ"), "marketCount": 3, "metadata": meta, "odds": [
        _odd(547, "Resultado Final", "Gamma", home, "1"), _odd(547, "Resultado Final", "Empate", draw, "0"), _odd(547, "Resultado Final", "Delta", away, "2"),
        _odd(200734, "Total de Gols", "Mais de 2.5", over, "2.5+", {"total": "2.5"}), _odd(200734, "Total de Gols", "Menos de 2.5", under, "2.5-", {"total": "2.5"}),
        _odd(704, "Total de Escanteios", "Mais de 9.5", 1.9, spec={"total": "9.5"}), _odd(704, "Total de Escanteios", "Menos de 9.5", 1.9, spec={"total": "9.5"}),
    ]}


def _ingest(s, eid, payload, at):
    return collector.ingest_event_payload(s, event_id=eid, payload=payload, fetched_at=at, source_url=f"https://x/events/{eid}", http_status=200, now=at + timedelta(seconds=1))


# ---------------------------------------------------------------------------
# regras puras
# ---------------------------------------------------------------------------
def test_settle_canonical_rules_never_assume_loss():
    r = FullResult(hg=2, ag=1)
    assert settle_canonical("MATCH_RESULT", "MATCH_RESULT", "HOME", None, r) == ("WON", [])
    assert settle_canonical("MATCH_RESULT", "MATCH_RESULT", "DRAW", None, r) == ("LOST", [])
    assert settle_canonical("DOUBLE_CHANCE", "DOUBLE_CHANCE", "DRAW_AWAY", None, r) == ("LOST", [])
    assert settle_canonical("DNB", "DNB", "AWAY", None, r) == ("LOST", [])
    assert settle_canonical("DNB", "DNB", "AWAY", None, FullResult(hg=1, ag=1)) == ("VOID", [])
    assert settle_canonical("TOTAL_GOALS", "TOTAL_GOALS_2_5", "OVER", 2.5, r) == ("WON", [])
    assert settle_canonical("TOTAL_GOALS", "TOTAL_GOALS_3", "UNDER", 3.0, r) == ("VOID", [])  # linha inteira igual ao total
    assert settle_canonical("BTTS", "BTTS", "YES", None, r) == ("WON", [])
    assert settle_canonical("TEAM_TOTAL", "TEAM_TOTAL_AWAY_0_5", "OVER", 0.5, r) == ("WON", [])
    # dado ausente → UNSETTLED_DATA_MISSING com o campo em falta; nunca LOST
    assert settle_canonical("CORNERS_TOTAL", "CORNERS_TOTAL_9_5", "OVER", 9.5, r) == ("UNSETTLED_DATA_MISSING", ["corners"])
    assert settle_canonical("CARDS_TOTAL", "CARDS_TOTAL_4_5", "UNDER", 4.5, r) == ("UNSETTLED_DATA_MISSING", ["cards"])
    assert settle_canonical("MATCH_RESULT", "MATCH_RESULT", "HOME", None, FullResult()) == ("UNSETTLED_DATA_MISSING", ["score"])
    # com estatística → liquida
    assert settle_canonical("CORNERS_TOTAL", "CORNERS_TOTAL_9_5", "OVER", 9.5, FullResult(hg=2, ag=1, home_corners=6, away_corners=5)) == ("WON", [])
    assert settle_canonical("TEAM_CORNERS", "TEAM_CORNERS_HOME_4_5", "UNDER", 4.5, FullResult(home_corners=6, away_corners=5)) == ("LOST", [])
    # jogador: capturado, nunca liquidado (§14)
    assert settle_canonical("PLAYER_GOAL", "PLAYER_GOAL", "player:77", None, r) == ("UNSUPPORTED", ["player_stats"])
    assert settle_canonical("MISTERIO", "X", "Y", None, r)[0] == "UNSUPPORTED"


# ---------------------------------------------------------------------------
# job de liquidação + auditoria
# ---------------------------------------------------------------------------
def test_settle_events_by_market_and_missing_market_stats_are_retried_not_lost():
    with session_scope() as s:
        for eid in (EV, EV + 1, EV + 2):
            assert _ingest(s, eid, _payload(eid, 2.0, 3.4, 3.8, 1.9, 1.9), KO - timedelta(hours=6)).ok
            assert _ingest(s, eid, _payload(eid, 1.9, 3.5, 4.0, 1.85, 1.95), KO - timedelta(minutes=30)).ok
        # evento 3 (1-1): a Superbet publicou escanteios no payload FINISHED → corners liquidáveis
        fin = _payload(EV + 2, 1.9, 3.5, 4.0, 1.85, 1.95, status="FINISHED", scores={"homeTeamScore": 1, "awayTeamScore": 1, "homeTeamCorners": 7, "awayTeamCorners": 4})
        assert _ingest(s, EV + 2, fin, KO + timedelta(hours=2)).ok
        s.flush()
        out = settle_events(s, now=KO + timedelta(hours=4))
        assert out["events"] == 3
        rows = {(r.event_id, r.canonical_market_id, r.selection_id): r for r in s.execute(select(SuperbetSettlement).where(SuperbetSettlement.event_id.in_([EV, EV + 1, EV + 2]))).scalars()}
        # placar 2-1: 1X2 e total liquidam; escanteios ficam por dados (nunca LOST)
        assert rows[(EV, "MATCH_RESULT", "HOME")].status == "WON" and rows[(EV, "MATCH_RESULT", "AWAY")].status == "LOST"
        assert rows[(EV, "TOTAL_GOALS_2_5", "OVER")].status == "WON"
        assert rows[(EV, "CORNERS_TOTAL_9_5", "OVER")].status == "UNSETTLED_DATA_MISSING" and rows[(EV, "CORNERS_TOTAL_9_5", "OVER")].missing_fields == ["corners"]
        # sem placar em lado nenhum → tudo pendente por dados, nada assumido
        assert {r.status for k, r in rows.items() if k[0] == EV + 1} == {"UNSETTLED_DATA_MISSING"}
        # 1-1 com metadata Superbet: escanteios 11 > 9.5 → OVER WON; fonte registada
        c = rows[(EV + 2, "CORNERS_TOTAL_9_5", "OVER")]
        assert c.status == "WON" and "superbet" in (c.result_source or "") and c.result["home_corners"] == 7
        assert rows[(EV + 2, "MATCH_RESULT", "DRAW")].status == "WON"
        # re-execução: WON/LOST não são reescritos; pendentes voltam a ser tentados (sem novos dados → continuam pendentes)
        again = settle_events(s, now=KO + timedelta(hours=5))
        assert again["events"] == 2  # EV (corners pendentes) e EV+1 (tudo pendente); EV+2 está fechado
        assert rows[(EV, "MATCH_RESULT", "HOME")].status == "WON"
        # o placar aparece depois → liquida no re-try (retorno ao normal, não "perda")
        ev1 = s.get(Event, EV + 1)
        ev1.home_score, ev1.away_score, ev1.result_source = 0, 3, "test"
        s.flush()
        settle_events(s, now=KO + timedelta(hours=6))
        r = s.execute(select(SuperbetSettlement).where(SuperbetSettlement.event_id == EV + 1, SuperbetSettlement.canonical_market_id == "MATCH_RESULT", SuperbetSettlement.selection_id == "AWAY")).scalar_one()
        assert r.status == "WON"
        audit = settlement_audit(s, now=KO + timedelta(hours=6))
        cov = {c["market_category"]: c for c in audit["market_data_coverage"]}
        assert cov["MATCH_RESULT"]["settled"] >= 9 and cov["MATCH_RESULT"]["missing"] == 0
        assert cov["CORNERS_TOTAL"]["missing"] >= 4 and cov["CORNERS_TOTAL"]["settled"] >= 2
        assert audit["match_result_vs_secondary"]["secondary"]["missing"] >= 4
        assert any(m["market_category"] == "CORNERS_TOTAL" and m["missing_fields"] == "corners" for m in audit["missing_by_market"])


# ---------------------------------------------------------------------------
# CLV V2 (descritivo, preço da casa)
# ---------------------------------------------------------------------------
def test_clv_v2_measures_house_price_vs_closing_per_target():
    with session_scope() as s:
        norm = load_normalized(s)
        norm = norm[norm["event_id"].isin([EV, EV + 1, EV + 2])]
        tl = timelines(norm)
        home = tl[(tl["event_id"] == EV) & (tl["selection_id"] == "HOME") & (tl["market_category"] == "MATCH_RESULT")].iloc[0]
        assert home["opening_odd"] == 2.0 and home["closing_odd"] == 1.9  # closing = último pré-kickoff (T-30m), não o FINISHED
        assert home["closing_minutes"] >= 0
        # CLV manual para HOME em T-6h: 2.0/1.9 − 1 = +5.26 %
        key = ["event_id", "canonical_market_id", "selection_id"]
        x = norm[norm["snapshot_target"] == "T-6h"].merge(tl[key + ["closing_odd", "closing_at"]], on=key)
        row = x[(x["event_id"] == EV) & (x["selection_id"] == "HOME") & (x["market_category"] == "MATCH_RESULT")].iloc[0]
        assert abs((row["odd"] / row["closing_odd"] - 1) * 100 - 5.263) < 0.01
        out = clv_v2(norm, tl)
        rows = {(r["market_category"], r["target"]): r for r in out["rows"]}
        # com 3 eventos a amostra é INSUFFICIENT: o verdict diz isso em vez de inventar um IC
        assert rows[("MATCH_RESULT", "T-6h")]["verdict"] == "INSUFFICIENT" and rows[("MATCH_RESULT", "T-6h")]["events"] == 3
        assert "clv_raw_pct" not in rows[("MATCH_RESULT", "T-6h")]
        assert "PREÇO DA CASA" in out["note"]


# ---------------------------------------------------------------------------
# governança: VALUE nunca automático; staking desligado
# ---------------------------------------------------------------------------
def test_edge_state_machine_and_manual_value_toggle():
    now = datetime(2026, 9, 1)
    disc_young = [{"market_category": "TOTAL_GOALS", "label": "Total", "raw_n": 40, "unique_events": 20, "effective_n": 25, "maturity": "EARLY"}]
    exps = {"hypotheses": []}
    with session_scope() as s:
        governance.update_edge_states(s, disc_young, exps, now=now)
        st = s.get(MarketEdgeState, "TOTAL_GOALS")
        assert st.state == "COLLECTING" and not st.value_enabled and not st.enablement_candidate
        assert len(st.evidence["rules_failed"]) >= 4
        # evidência "perfeita" em tudo menos no período de confirmação → PROMISING, mas ainda NÃO candidato
        strong = {"market_category": "TOTAL_GOALS", "label": "Total", "raw_n": 2000, "unique_events": 900, "effective_n": 800, "maturity": "MATURE",
                  "edgefut_minus_fair_brier": {"point": -0.004, "low": -0.006, "high": -0.001, "n": 800, "conclusive": True},
                  "clv_near_close": {"point": 0.8, "low": 0.1, "high": 1.5, "n": 800, "conclusive": True},
                  "edgefut": {"calibration_bias_pp": {"point": 0.1, "low": -0.9, "high": 1.1, "n": 800, "conclusive": False}}}
        exps = {"hypotheses": [{"hypothesis_id": "H1", "market_category": "TOTAL_GOALS", "status": "SUPPORTED", "survives_fdr": True, "confirmation_start": (now - timedelta(days=5)).isoformat()}]}
        governance.update_edge_states(s, [strong], exps, now=now)
        st = s.get(MarketEdgeState, "TOTAL_GOALS")
        assert st.state == "PROMISING" and not st.enablement_candidate and not st.value_enabled
        assert any("confirmação" in f for f in st.evidence["rules_failed"])
        with pytest.raises(PermissionError):
            governance.set_value_enabled(s, "TOTAL_GOALS", True, reason="quero ligar", now=now)
        # 30 dias depois: candidato — e mesmo assim VALUE continua desligado até ação humana
        later = now + timedelta(days=31)
        governance.update_edge_states(s, [strong], exps, now=later)
        st = s.get(MarketEdgeState, "TOTAL_GOALS")
        assert st.enablement_candidate and not st.value_enabled and st.state == "PROMISING"
        assert governance.value_enabled_for("TOTAL_GOALS") is False
        assert governance.staking_enabled()[0] is False
        with pytest.raises(ValueError):
            governance.set_value_enabled(s, "TOTAL_GOALS", True, reason="ok", now=later)  # motivo curto
        row = governance.set_value_enabled(s, "TOTAL_GOALS", True, reason="validação manual em teste", now=later)
        assert row["state"] == "VALIDATED" and row["value_enabled"]
        mc = s.execute(select(ManualCorrection).where(ManualCorrection.entity == "market_edge_state", ManualCorrection.entity_id == "TOTAL_GOALS")).scalars().all()
        assert mc and mc[-1].after["value_enabled"] is True and mc[-1].before["value_enabled"] is False
        s.flush()
        governance.invalidate_cache()
        governance._refresh_cache(s)
        assert governance.value_enabled_for("TOTAL_GOALS") is True and governance.value_enabled_for("1X2") is False
        assert governance.staking_enabled()[0] is True
        # o job de estados não retira VALIDATED sozinho; o utilizador desliga, com trilha
        governance.update_edge_states(s, disc_young, {"hypotheses": []}, now=later + timedelta(days=1))
        assert s.get(MarketEdgeState, "TOTAL_GOALS").state == "VALIDATED"
        row = governance.set_value_enabled(s, "TOTAL_GOALS", False, reason="desligar após teste", now=later + timedelta(days=1))
        assert row["value_enabled"] is False and row["state"] != "VALIDATED"
        governance._refresh_cache(s)
        assert governance.value_enabled_for("TOTAL_GOALS") is False
        ok, reason = governance.staking_enabled()
        assert ok is False and "STAKING DISABLED" in reason


def test_freeze_registers_frozen_models_and_forbidden_families():
    with session_scope() as s:
        f = governance.register_freeze(s)
        st = governance.freeze_status(s)
        fr = st["freeze"]
        assert st["status"] == "INTACT" and st["drifted_fields"] == [] and fr["model_hash"] == st["current"]["model_hash"]
        assert "ensemble" in fr["frozen_models"] and fr["frozen_models"] == f["frozen_models"] and fr["iteration"] == 5
        assert "XGBoost" in fr["forbidden_families"] and any("PAUSED" in v for v in fr["paused"].values())
        assert len(fr["frozen_models"]) == len(set(fr["frozen_models"]))
        # registar de novo não reescreve o freeze original (a data fica)
        assert governance.register_freeze(s, now=datetime(2031, 1, 1))["freeze_date"] == f["freeze_date"]


# ---------------------------------------------------------------------------
# backup / retenção / restore (§49–§51)
# ---------------------------------------------------------------------------
def test_backup_manifest_retention_and_restore_with_safety_copy():
    bdir = storage.backups_dir()
    for f in bdir.glob("*"):
        f.unlink()
    t0 = datetime(2026, 9, 24, 3, 0)
    with session_scope() as s:
        n_events_before = s.execute(select(Event)).scalars().all()
    res = storage.create_backup(reason="test", now=t0)
    assert res["ok"] and res["integrity"] == "ok" and res["counts"]["event"] == len(n_events_before) and res["schema_version"] >= 5
    man = json.loads((bdir / res["file"]).with_suffix(".json").read_text(encoding="utf-8"))
    assert man["sha256"] == res["sha256"] and man["versions"]["settlement"]
    # retenção: 60 backups diários consecutivos → ficam 7 diários + 4 semanais + 3 mensais (com sobreposição), nunca 60
    for i in range(1, 60):
        d = t0 - timedelta(days=i)
        (bdir / f"{storage.BACKUP_PREFIX}{d.strftime('%Y%m%d-%H%M%S')}{storage.BACKUP_SUFFIX}").write_bytes(b"x")
    removed = storage.apply_retention(now=t0)
    kept = storage.list_backups()
    assert removed and 7 <= len(kept) <= 14
    tiers = {b["tier"] for b in kept if b["tier"]}
    assert "daily" in tiers
    assert (bdir / res["file"]).exists()  # o backup real (mais recente) é sempre mantido
    kept_dates = sorted(datetime.strptime(b["file"][len(storage.BACKUP_PREFIX):-len(storage.BACKUP_SUFFIX)], "%Y%m%d-%H%M%S") for b in kept)
    assert (t0 - kept_dates[0]).days >= 28  # há pelo menos um mensal antigo
    # restore: recusa backup corrompido, guarda cópia pre-restore e substitui a base
    bad = bdir / f"{storage.BACKUP_PREFIX}19990101-000000{storage.BACKUP_SUFFIX}"
    bad.write_bytes(b"not a database")
    out = storage.restore_backup(bad.name, now=t0)
    assert not out["ok"]
    bad.unlink()
    db = get_paths().sqlite
    with session_scope() as s:
        s.add(Event(id=EV + 77, home_name="Depois", away_name="Do Backup", kickoff_utc=KO, competition_name="Liga Teste", category_name="Teste"))
    get_engine().dispose()
    out = storage.restore_backup(res["file"], now=t0 + timedelta(minutes=1))
    assert out["ok"] and out["restored_from"] == res["file"] and out["safety_copy"].startswith("pre-restore-")
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM event WHERE id = ?", (EV + 77,)).fetchone()[0] == 0  # base voltou ao estado do backup
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(bdir / out["safety_copy"]) as c:
        assert c.execute("SELECT COUNT(*) FROM event WHERE id = ?", (EV + 77,)).fetchone()[0] == 1  # nada se perdeu: está na cópia de segurança
    get_engine().dispose()
    run_migrations(get_engine())


def test_storage_dashboard_states_raw_is_never_deleted_automatically():
    with session_scope() as s:
        d = storage.storage_dashboard(s)
    assert d["raw_payloads"]["snapshots"] >= 1 and d["raw_payloads"]["compressed_bytes"] > 0
    assert "nunca" in d["raw_retention_policy"].lower()
    assert d["growth"]["projection_365d_bytes"] >= d["growth"]["projection_30d_bytes"] >= 0
    assert isinstance(pd.Timestamp(d["generated_at"]), pd.Timestamp)
