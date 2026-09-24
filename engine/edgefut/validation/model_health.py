"""MODEL HEALTH — resumo discreto para o Dashboard (iteração 3, §47).

Agrega, sem recalcular nada pesado, o que já foi medido e persistido em
`validation_run`: último replay de clubes (campeão vs baseline de mercado), status de
drift, reconciliação de settlement, volume do shadow mode e o decay em uso.
Nunca lança: qualquer falha vira `status = UNAVAILABLE` com o motivo.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..backtesting import reconciliation
from ..core.config import settings
from ..db.models import ShadowPrediction, ValidationRun
from .bootstrap import effective_sample_size, sample_quality
from .governance import current_champion


def _latest_replay(session: Session, *, international: bool) -> ValidationRun | None:
    rows = session.execute(select(ValidationRun).where(ValidationRun.kind == "replay").order_by(ValidationRun.created_at.desc()).limit(20)).scalars().all()
    for r in rows:
        if r.detail and bool((r.summary or {}).get("international")) == international and "error" not in (r.summary or {}):
            return r
    return None


def _replay_block(run: ValidationRun | None, champion: str) -> dict | None:
    if run is None:
        return None
    detail, summary = run.detail or {}, run.summary or {}
    overall = detail.get("overall") or {}
    vs = (detail.get("vs_baseline") or {}).get(champion) or {}
    vs_market = vs.get("market") or {}
    vs_naive = vs.get("naive") or {}
    champ = overall.get(champion) or {}
    market = overall.get("market") or {}
    return {
        "run_id": run.id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "matches": detail.get("matches"),
        "matches_with_odds": detail.get("matches_with_odds"),
        "windows": detail.get("windows"),
        "sample_quality": sample_quality(int(detail.get("matches_with_odds") or detail.get("matches") or 0)),
        "champion_brier": (champ.get("brier") or {}).get("point"),
        "market_brier": (market.get("brier") or {}).get("point"),
        "vs_market": {"significance": vs_market.get("significance"), "lift_pct": vs_market.get("lift_pct"), "ci": vs_market.get("delta_brier_ci")},
        "vs_naive": {"significance": vs_naive.get("significance"), "lift_pct": vs_naive.get("lift_pct")},
        "ranking_brier": summary.get("ranking_brier") or detail.get("ranking_brier"),
        "promotion": summary.get("promotion"),
    }


def _status(replay: dict | None, drift: dict | None, recon: dict, shadow_n: int) -> tuple[str, list[str]]:
    notes: list[str] = []
    status = "OK"
    if replay is None:
        status = "UNVALIDATED"
        notes.append("Nenhum replay histórico concluído: capacidade preditiva não medida.")
    else:
        sig = (replay.get("vs_market") or {}).get("significance")
        if sig in (None, "INSUFFICIENT DATA"):
            status = "UNVALIDATED"
            notes.append("Replay sem amostra suficiente contra o mercado.")
        elif sig == "NO CLEAR ADVANTAGE":
            status = "WATCH"
            notes.append("Campeão NÃO supera o baseline de mercado (Brier) — valor depende de nichos, não do 1X2 geral.")
        else:
            notes.append(f"Campeão vs mercado: {sig}.")
    if drift and drift.get("status") == "DRIFT":
        status = "DRIFT"
        notes.append("Drift detectado na performance recente — revisão humana recomendada.")
    elif drift and drift.get("status") == "WATCH" and status == "OK":
        status = "WATCH"
        notes.append("Sinais fracos de drift.")
    if recon.get("unsettled_finished", 0) > 0:
        notes.append(f"{recon['unsettled_finished']} eventos terminados sem liquidação.")
    if shadow_n < settings.sample_early_min:
        notes.append(f"Shadow mode com {shadow_n} previsões (< {settings.sample_early_min}): performance real ainda não mensurável.")
    return status, notes


def _latest_run(session: Session, kind: str, *, primary_only: bool = False) -> ValidationRun | None:
    for r in session.execute(select(ValidationRun).where(ValidationRun.kind == kind).order_by(ValidationRun.created_at.desc()).limit(20)).scalars():
        s = r.summary or {}
        if r.detail and not s.get("error") and not (primary_only and s.get("repeated")):
            return r
    return None


def football_prediction_status(replay: dict | None) -> dict:
    """FOOTBALL PREDICTION STATUS (§30): o modelo prevê futebol? Brier vs naive e vs mercado no replay."""
    if replay is None:
        return {"status": "UNVALIDATED", "text": "Sem replay histórico concluído."}
    vs_naive = (replay.get("vs_naive") or {}).get("significance")
    vs_market = (replay.get("vs_market") or {}).get("significance")
    if vs_naive in (None, "INSUFFICIENT DATA"):
        status = "UNVALIDATED"
    elif vs_naive == "NO CLEAR ADVANTAGE":
        status = "WEAK"
    else:
        status = "PREDICTIVE (vs naive)"
    return {
        "status": status, "vs_naive": vs_naive, "vs_market": vs_market,
        "champion_brier": replay.get("champion_brier"), "market_brier": replay.get("market_brier"), "matches": replay.get("matches_with_odds"),
        "text": f"Campeão vs naive: {vs_naive or 'n/d'}; vs mercado: {vs_market or 'n/d'}. Prever futebol ≠ bater o mercado.",
    }


def market_edge_status(session: Session) -> dict:
    """MARKET EDGE STATUS (§31/§49): o modelo tem informação incremental além do preço? Holdout congelado > discovery.
    Só sai de UNPROVEN quando `evaluate_market_aware_promotion` aprovar (todos os critérios + CLV ≥ 0)."""
    from .governance import evaluate_market_aware_promotion
    from .shadow import latest_report as latest_daily

    hold = _latest_run(session, "market_aware_holdout", primary_only=True)
    disc = _latest_run(session, "market_aware")
    src_run, source = (hold, "holdout") if hold else ((disc, "discovery") if disc else (None, None))
    if src_run is None:
        return {"status": "UNPROVEN", "source": None, "markets": {}, "text": "Nenhum experimento market-aware executado."}
    summary = src_run.summary or {}
    daily = latest_daily(session) or {}
    clv = ((daily.get("market_aware") or {}).get("clv") or {}).get("pct")
    markets: dict[str, dict] = {}
    any_proven = False
    for m, v in (summary.get("verdict") or {}).items():
        promo = evaluate_market_aware_promotion(src_run.detail if source == "holdout" else None, m, clv=clv)
        markets[m] = {"verdict": v.get("status"), "challenger": v.get("challenger"), "promotion": promo["verdict"], "value_layer_allowed": promo["value_layer_allowed"]}
        any_proven = any_proven or promo["value_layer_allowed"]
    status = "PROVEN (HOLDOUT + CLV ≥ 0)" if any_proven else ("UNPROVEN" if source == "holdout" else "UNPROVEN (DISCOVERY ONLY)")
    return {
        "status": status, "source": source, "run_id": src_run.id, "run_timestamp": summary.get("run_timestamp") or (src_run.created_at.isoformat() if src_run.created_at else None),
        "model_hash": summary.get("model_hash"), "config_hash": summary.get("config_hash"), "dataset_version": summary.get("dataset_version"),
        "classification": (src_run.detail or {}).get("classification"), "markets": markets,
        "text": "Modelo market-aware NÃO demonstrou informação incremental além do preço (holdout congelado)." if not any_proven else "Challenger bate o mercado no holdout congelado e CLV não negativo.",
    }


def model_health_v2(session: Session, club: dict | None) -> dict:
    """MODEL HEALTH V2 (§49): cinco linhas discretas, cada uma com o seu N e a sua fonte."""
    ev = _latest_run(session, "superbet_evidence")
    evs = (ev.summary or {}) if ev else {}
    evd = (ev.detail or {}) if ev else {}
    settled_rows = session.execute(select(ShadowPrediction.event_id).where(ShadowPrediction.won.is_not(None))).scalars().all()
    settled_n = len(settled_rows)
    settled_events = len(set(settled_rows))
    eff = effective_sample_size(list(settled_rows)) if settled_rows else {"raw_n": 0, "clusters": 0, "effective_n": 0}
    edge = market_edge_status(session)
    buckets = evd.get("buckets") or {}
    return {
        "football_model": football_prediction_status(club),
        "market_model": {"status": edge["status"], "source": edge.get("source"), "markets": {m: v["verdict"] for m, v in edge.get("markets", {}).items()}, "model_hash": edge.get("model_hash")},
        "superbet_evidence": {
            "status": "COLLECTING" if evs.get("events") else "NONE", "events": evs.get("events"), "snapshots_pre_kickoff": evs.get("snapshots"),
            "closing_events": ((evd.get("closing_lines") or {}).get("events")), "buckets_present": [b["bucket"] for b in buckets if b.get("exists")] if isinstance(buckets, list) else None,
            "clv_n": evs.get("clv_n"), "generated_at": evd.get("generated_at"),
        },
        "shadow_settled": {"raw_n": settled_n, "events": settled_events, "effective_n": eff["effective_n"], "sample_quality": sample_quality(settled_events),
                           "status": "INSUFFICIENT" if settled_events < settings.sample_early_min else "MEASURABLE"},
        "market_edge": {"status": "UNPROVEN" if not edge["status"].startswith("PROVEN") else "PROVEN", "detail": edge["status"], "text": edge["text"]},
    }


def model_health_summary(session: Session) -> dict:
    try:
        champion = current_champion(session)
        club = _replay_block(_latest_replay(session, international=False), champion)
        intl = _replay_block(_latest_replay(session, international=True), champion)
        drift_row = session.execute(select(ValidationRun).where(ValidationRun.kind == "drift").order_by(ValidationRun.created_at.desc()).limit(1)).scalar_one_or_none()
        drift = drift_row.detail if drift_row else None
        recon = reconciliation.summary(session)
        shadow_total = int(session.execute(select(func.count()).select_from(ShadowPrediction)).scalar() or 0)
        shadow_settled = int(session.execute(select(func.count()).select_from(ShadowPrediction).where(ShadowPrediction.won.is_not(None))).scalar() or 0)
        decay = session.execute(select(ValidationRun).where(ValidationRun.kind == "decay").order_by(ValidationRun.created_at.desc()).limit(1)).scalar_one_or_none()
        status, notes = _status(club, drift, recon, shadow_total)
        v2 = model_health_v2(session, club)
        if v2["market_edge"]["status"] == "UNPROVEN":
            notes.append("MARKET EDGE UNPROVEN: nenhum challenger market-aware bateu o mercado no holdout congelado com todos os critérios.")
        return {
            "status": status,
            "generated_at": datetime.utcnow().isoformat(),
            "champion": champion,
            "replay": club,
            "replay_international": intl,
            "drift": {"status": drift.get("status"), "alerts": len(drift.get("alerts") or []), "generated_at": drift.get("generated_at")} if drift else None,
            "settlement": recon,
            "shadow": {"total": shadow_total, "settled": shadow_settled, "sample_quality": sample_quality(shadow_settled)},
            "decay": {"half_life_days": settings.strength_half_life_days, "selected_by_walk_forward": (decay.summary or {}).get("recommended_half_life_days") if decay else None, "tie_with_runner_up": (decay.summary or {}).get("tie_with_runner_up") if decay else None, "run_id": decay.id if decay else None},
            "v2": v2,
            "notes": notes,
        }
    except Exception as exc:  # noqa: BLE001 — dashboard nunca cai por causa do resumo
        return {"status": "UNAVAILABLE", "reason": str(exc), "generated_at": datetime.utcnow().isoformat()}


__all__ = ["model_health_summary", "model_health_v2", "market_edge_status", "football_prediction_status"]
