"""Model drift monitor + auditoria de distribuição de probabilidades. SÓ ALERTA — nunca
altera modelos, pesos ou decisões (a mudança de campeão é decisão humana, ver
MODEL_GOVERNANCE.md).

Compara a janela recente (`recent_days`) com a janela de referência anterior nas
distribuições registradas pelo shadow mode: probabilidade média, fração de probabilidades
extremas (> 0,90), edge médio, share de MODEL_ONLY, Brier em seleções liquidadas e hit rate.
Um alerta nasce quando a diferença ultrapassa o limiar **e** as duas janelas têm N mínimo.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import PredictionSnapshot, ShadowPrediction, ValidationRun
from .bootstrap import sample_quality

log = logging.getLogger(__name__)

MIN_N = 50
BINS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
EXTREME_PROB = 0.90

THRESHOLDS = {
    "avg_prob": 0.05,          # deslocamento absoluto da probabilidade média
    "extreme_share": 0.05,     # +5 pp de probabilidades > 90%
    "avg_edge_pp": 2.0,        # edge médio (pp) — sinal de discordância com o mercado
    "model_only_share": 0.15,  # +15 pp de seleções sem preço
    "brier": 0.02,             # piora absoluta de Brier em liquidados
    "hit_minus_prob": 0.05,    # calibração grosseira: hit − prob média
}


def _stats(rows: list[ShadowPrediction]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    prob = np.array([float(r.model_prob) for r in rows])
    edges = np.array([float(r.edge_pp) for r in rows if r.edge_pp is not None])
    settled = [r for r in rows if r.won is not None]
    out = {
        "n": n,
        "avg_prob": round(float(prob.mean()), 4),
        "extreme_share": round(float((prob > EXTREME_PROB).mean()), 4),
        "avg_edge_pp": round(float(edges.mean()), 3) if edges.size else None,
        "model_only_share": round(sum(1 for r in rows if r.state == "MODEL_ONLY") / n, 4),
        "histogram": {f"{BINS[i]:.1f}-{BINS[i + 1]:.1f}": int(c) for i, c in enumerate(np.histogram(prob, bins=BINS)[0])},
        "settled_n": len(settled),
    }
    if settled:
        w = np.array([1.0 if r.won else 0.0 for r in settled])
        p = np.array([float(r.model_prob) for r in settled])
        out["brier"] = round(float(((p - w) ** 2).mean()), 4)
        out["hit_rate"] = round(float(w.mean()), 4)
        out["hit_minus_prob"] = round(float(w.mean() - p.mean()), 4)
    return out


def _snapshot_probability_audit(session: Session, since: datetime) -> dict:
    """Histograma das probabilidades 1X2 dos snapshots (independente do shadow)."""
    rows = session.execute(select(PredictionSnapshot.probabilities).where(PredictionSnapshot.created_at >= since).limit(5000)).scalars().all()
    vals: list[float] = []
    for pr in rows:
        if not isinstance(pr, dict):
            continue
        for k in ("home", "draw", "away", "p_home", "p_draw", "p_away"):
            v = pr.get(k)
            if isinstance(v, (int, float)):
                vals.append(float(v))
    if not vals:
        return {"n": 0}
    arr = np.array(vals)
    return {
        "n": int(arr.size),
        "snapshots": len(rows),
        "avg": round(float(arr.mean()), 4),
        "extreme_share": round(float((arr > EXTREME_PROB).mean()), 4),
        "histogram": {f"{BINS[i]:.1f}-{BINS[i + 1]:.1f}": int(c) for i, c in enumerate(np.histogram(arr, bins=BINS)[0])},
    }


def check_drift(session: Session, *, persist: bool = False, now: datetime | None = None, recent_days: int = 30, reference_days: int = 90) -> dict:
    t0 = time.perf_counter()
    now = now or datetime.utcnow()
    recent_start = now - timedelta(days=recent_days)
    ref_start = recent_start - timedelta(days=reference_days)

    recent = list(session.execute(select(ShadowPrediction).where(ShadowPrediction.created_at >= recent_start)).scalars())
    reference = list(session.execute(select(ShadowPrediction).where(ShadowPrediction.created_at >= ref_start, ShadowPrediction.created_at < recent_start)).scalars())
    rs, fs = _stats(recent), _stats(reference)

    alerts: list[dict] = []
    status = "OK"
    if rs["n"] < MIN_N or fs["n"] < MIN_N:
        status = "INSUFFICIENT DATA"
    else:
        for key, thr in THRESHOLDS.items():
            a, b = rs.get(key), fs.get(key)
            if a is None or b is None:
                continue
            delta = a - b
            if abs(delta) > thr:
                alerts.append({
                    "metric": key, "recent": a, "reference": b, "delta": round(float(delta), 4), "threshold": thr,
                    "severity": "WARN" if abs(delta) < 2 * thr else "ALERT",
                    "message": f"{key}: {b} → {a} (Δ {delta:+.4f}, limiar {thr})",
                })
        if alerts:
            status = "DRIFT" if any(a["severity"] == "ALERT" for a in alerts) else "WATCH"

    report = {
        "generated_at": now.isoformat(),
        "status": status,
        "windows": {"recent_days": recent_days, "reference_days": reference_days},
        "recent": rs,
        "reference": fs,
        "alerts": alerts,
        "sample_quality": {"recent": sample_quality(rs["n"]), "reference": sample_quality(fs["n"])},
        "probability_audit": _snapshot_probability_audit(session, recent_start),
        "action": "Nenhuma ação automática: drift é sinal para revisão humana (MODEL_GOVERNANCE.md).",
    }
    if persist:
        session.add(ValidationRun(kind="drift", summary={"status": status, "alerts": len(alerts), "recent_n": rs["n"], "reference_n": fs["n"]}, detail=report, duration_ms=int((time.perf_counter() - t0) * 1000)))
        session.commit()
    return report


def latest_drift(session: Session) -> dict | None:
    row = session.execute(select(ValidationRun).where(ValidationRun.kind == "drift").order_by(ValidationRun.created_at.desc()).limit(1)).scalar_one_or_none()
    return row.detail if row else None


__all__ = ["check_drift", "latest_drift", "THRESHOLDS", "EXTREME_PROB"]
