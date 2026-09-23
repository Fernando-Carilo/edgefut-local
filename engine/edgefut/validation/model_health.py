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
from .bootstrap import sample_quality
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
            "notes": notes,
        }
    except Exception as exc:  # noqa: BLE001 — dashboard nunca cai por causa do resumo
        return {"status": "UNAVAILABLE", "reason": str(exc), "generated_at": datetime.utcnow().isoformat()}


__all__ = ["model_health_summary"]
