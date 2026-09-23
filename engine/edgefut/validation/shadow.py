"""Shadow mode — leitura/relatório. A escrita acontece em `analysis/pipeline._save_snapshot`
(uma linha `shadow_prediction` por seleção avaliada, append-only) e a liquidação em
`backtesting/reconciliation._settle_shadow`.

`daily_report` responde: quantos eventos observamos, quantos foram analisados, quantos
ficaram em cada estado, quantos liquidaram hoje e qual o desempenho (com N e IC 95%) em
7d / 30d / 90d / tudo — SEMPRE separando MODEL_ONLY (sem preço) das seleções com odd.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Event, PredictionSnapshot, ShadowPrediction, ValidationRun
from .bootstrap import bootstrap_ci, roi_stat, sample_quality

log = logging.getLogger(__name__)

STATES = ("MODEL_ONLY", "MARKET_OBSERVED", "VALUE_CANDIDATE", "VALUE", "OBSERVATION", "NO_BET")
PRICED_STATES = ("VALUE", "VALUE_CANDIDATE", "OBSERVATION")
WINDOWS = {"7d": 7, "30d": 30, "90d": 90, "all": None}


def _perf(rows: list[ShadowPrediction]) -> dict:
    """Desempenho de seleções liquidadas **com odd** (ROI/Yield/CLV só fazem sentido com preço)."""
    priced = [r for r in rows if r.won is not None and r.odd]
    n = len(priced)
    out: dict = {"n": n, "sample_quality": sample_quality(n)}
    if n == 0:
        return out
    won = np.array([1.0 if r.won else 0.0 for r in priced])
    prob = np.array([float(r.model_prob) for r in priced])
    odd = np.array([float(r.odd) for r in priced])
    profit = np.where(won == 1, odd - 1.0, -1.0)
    brier = (prob - won) ** 2
    eps = 1e-6
    ll = -(won * np.log(np.clip(prob, eps, 1 - eps)) + (1 - won) * np.log(np.clip(1 - prob, eps, 1 - eps)))
    out["hit_rate"] = bootstrap_ci(won, min_n=10).to_dict()
    out["roi"] = bootstrap_ci(profit, stat=roi_stat, zero_test=True).to_dict()
    out["brier"] = bootstrap_ci(brier).to_dict()
    out["log_loss"] = bootstrap_ci(ll).to_dict()
    clv_rows = [(r.odd / r.closing_odd - 1) * 100 for r in priced if r.closing_odd]
    out["clv"] = bootstrap_ci(np.array(clv_rows), zero_test=True).to_dict() if clv_rows else None
    out["avg_odd"] = round(float(odd.mean()), 3)
    out["avg_model_prob"] = round(float(prob.mean()), 4)
    return out


def _model_only_perf(rows: list[ShadowPrediction]) -> dict:
    """MODEL_ONLY liquidado: só Brier/LogLoss/hit (capacidade preditiva), NUNCA ROI."""
    rs = [r for r in rows if r.won is not None and r.state == "MODEL_ONLY"]
    n = len(rs)
    out: dict = {"n": n, "sample_quality": sample_quality(n)}
    if n == 0:
        return out
    won = np.array([1.0 if r.won else 0.0 for r in rs])
    prob = np.array([float(r.model_prob) for r in rs])
    eps = 1e-6
    out["hit_rate"] = bootstrap_ci(won).to_dict()
    out["brier"] = bootstrap_ci((prob - won) ** 2).to_dict()
    out["log_loss"] = bootstrap_ci(-(won * np.log(np.clip(prob, eps, 1 - eps)) + (1 - won) * np.log(np.clip(1 - prob, eps, 1 - eps)))).to_dict()
    return out


def daily_report(session: Session, *, persist: bool = False, now: datetime | None = None) -> dict:
    t0 = time.perf_counter()
    now = now or datetime.utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    observed = session.execute(select(func.count()).select_from(Event).where(Event.duplicate_of.is_(None), Event.kickoff_utc >= day_start, Event.kickoff_utc < day_start + timedelta(days=1))).scalar() or 0
    analyzed = session.execute(
        select(func.count(func.distinct(PredictionSnapshot.event_id))).join(Event, Event.id == PredictionSnapshot.event_id)
        .where(Event.kickoff_utc >= day_start, Event.kickoff_utc < day_start + timedelta(days=1))
    ).scalar() or 0

    today_rows = list(session.execute(select(ShadowPrediction).where(ShadowPrediction.kickoff_utc >= day_start, ShadowPrediction.kickoff_utc < day_start + timedelta(days=1))).scalars())
    by_state = {s: 0 for s in STATES}
    events_by_state: dict[str, set[int]] = {s: set() for s in STATES}
    for r in today_rows:
        if r.state in by_state:
            by_state[r.state] += 1
            events_by_state[r.state].add(r.event_id)
    settled_today = session.execute(select(func.count()).select_from(ShadowPrediction).where(ShadowPrediction.settled_at >= day_start)).scalar() or 0

    all_settled = list(session.execute(select(ShadowPrediction).where(ShadowPrediction.settled_at.is_not(None))).scalars())
    perf: dict[str, dict] = {}
    for label, days in WINDOWS.items():
        rows = all_settled if days is None else [r for r in all_settled if r.kickoff_utc >= now - timedelta(days=days)]
        perf[label] = {
            "priced": _perf([r for r in rows if r.state in PRICED_STATES or (r.state == "NO_BET" and r.odd)]),
            "value_only": _perf([r for r in rows if r.state == "VALUE"]),
            "model_only": _model_only_perf(rows),
        }

    by_market: dict[str, dict] = {}
    for mk in sorted({r.market_key for r in all_settled}):
        by_market[mk] = _perf([r for r in all_settled if r.market_key == mk and r.state in PRICED_STATES])

    total_rows = session.execute(select(func.count()).select_from(ShadowPrediction)).scalar() or 0
    report = {
        "generated_at": now.isoformat(),
        "day": day_start.date().isoformat(),
        "events_observed": int(observed),
        "events_analyzed": int(analyzed),
        "selections_by_state": by_state,
        "events_by_state": {k: len(v) for k, v in events_by_state.items()},
        "settled_today": int(settled_today),
        "shadow_rows_total": int(total_rows),
        "shadow_rows_settled": len(all_settled),
        "performance": perf,
        "by_market": by_market,
        "notes": [
            "MODEL_ONLY nunca entra em ROI/Yield/CLV — não há preço.",
            "IC 95% por bootstrap; 'conclusive=false' significa que o IC cruza zero.",
        ],
    }
    if persist:
        session.add(ValidationRun(kind="daily_report", summary={k: v for k, v in report.items() if k not in {"by_market", "performance"}}, detail=report, duration_ms=int((time.perf_counter() - t0) * 1000)))
        session.commit()
    return report


def latest_report(session: Session) -> dict | None:
    row = session.execute(select(ValidationRun).where(ValidationRun.kind == "daily_report").order_by(ValidationRun.created_at.desc()).limit(1)).scalar_one_or_none()
    return row.detail if row else None


__all__ = ["daily_report", "latest_report", "STATES", "PRICED_STATES"]
