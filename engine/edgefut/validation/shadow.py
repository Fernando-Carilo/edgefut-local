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

from ..core.config import settings
from ..db.models import Event, PredictionSnapshot, ShadowPrediction, ValidationRun
from .bootstrap import (
    bootstrap_ci,
    cluster_bootstrap_ci,
    effective_sample_size,
    roi_stat,
    sample_quality,
)

log = logging.getLogger(__name__)

STATES = ("MODEL_ONLY", "MARKET_OBSERVED", "VALUE_CANDIDATE", "VALUE", "RESEARCH_SIGNAL", "OBSERVATION", "NO_BET")
PRICED_STATES = ("VALUE", "VALUE_CANDIDATE", "RESEARCH_SIGNAL", "OBSERVATION")
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


def _binary_scores(prob: np.ndarray, won: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    eps = 1e-6
    p = np.clip(prob, eps, 1 - eps)
    return (prob - won) ** 2, -(won * np.log(p) + (1 - won) * np.log(1 - p))


def market_aware_panel(rows: list[ShadowPrediction]) -> dict:
    """SUPERBET SHADOW REPORT v2 (§48): Superbet fair vs EdgeFut vs Híbrido nas seleções liquidadas
    **com preço e com probabilidade de mercado** registradas no instante da previsão.

    * IC 95 % por bootstrap de **clusters** (evento) — várias seleções do mesmo jogo não são independentes;
    * Raw N vs Effective N (§19);
    * o híbrido só entra nas linhas em que foi gravado (`hybrid_prob`, iteração 4+) — nunca é reconstruído;
    * CLV apenas onde há closing odd da Superbet."""
    priced = [r for r in rows if r.won is not None and r.odd and r.market_prob is not None]
    n = len(priced)
    clusters = np.array([r.event_id for r in priced])
    out: dict = {
        "raw_n": n,
        "events": int(len(set(clusters.tolist()))),
        "markets": sorted({r.market_key for r in priced}),
        "effective": effective_sample_size(clusters) if n else {"raw_n": 0, "clusters": 0, "effective_n": 0, "rho": 0.5},
        "sample_quality": sample_quality(int(len(set(clusters.tolist())))),
        "states": {s: sum(1 for r in priced if r.state == s) for s in STATES},
        "value_count": sum(1 for r in priced if r.state == "VALUE"),
        "no_bet_count": sum(1 for r in priced if r.state in ("NO_BET", "MARKET_OBSERVED")),
    }
    if n == 0:
        out["verdict"] = "INSUFFICIENT DATA"
        return out
    won = np.array([1.0 if r.won else 0.0 for r in priced])
    p_mkt = np.array([float(r.market_prob) for r in priced])
    p_ef = np.array([float(r.model_prob) for r in priced])
    b_m, l_m = _binary_scores(p_mkt, won)
    b_e, l_e = _binary_scores(p_ef, won)
    out["superbet_fair"] = {"brier": cluster_bootstrap_ci(b_m, clusters).to_dict(), "log_loss": cluster_bootstrap_ci(l_m, clusters).to_dict()}
    out["edgefut"] = {"brier": cluster_bootstrap_ci(b_e, clusters).to_dict(), "log_loss": cluster_bootstrap_ci(l_e, clusters).to_dict()}
    out["edgefut_vs_superbet"] = {
        "delta_brier": cluster_bootstrap_ci(b_e - b_m, clusters, zero_test=True).to_dict(),
        "delta_logloss": cluster_bootstrap_ci(l_e - l_m, clusters, zero_test=True).to_dict(),
    }
    hyb = [(r, float(r.hybrid_prob)) for r in priced if getattr(r, "hybrid_prob", None) is not None]
    if hyb:
        hw = np.array([1.0 if r.won else 0.0 for r, _ in hyb])
        hp = np.array([p for _, p in hyb])
        hm = np.array([float(r.market_prob) for r, _ in hyb])
        hc = np.array([r.event_id for r, _ in hyb])
        b_h, l_h = _binary_scores(hp, hw)
        b_hm, _ = _binary_scores(hm, hw)
        out["hybrid"] = {"n": len(hyb), "brier": cluster_bootstrap_ci(b_h, hc).to_dict(), "log_loss": cluster_bootstrap_ci(l_h, hc).to_dict(),
                         "delta_brier_vs_superbet": cluster_bootstrap_ci(b_h - b_hm, hc, zero_test=True).to_dict()}
    else:
        out["hybrid"] = {"n": 0, "note": "Nenhuma linha liquidada com hybrid_prob (coluna existe desde a iteração 4; linhas antigas não são reconstruídas)."}
    clv = [(r, (r.odd / r.closing_odd - 1) * 100) for r in priced if r.closing_odd]
    out["clv"] = {"n": len(clv), "events": len({r.event_id for r, _ in clv}),
                  "pct": cluster_bootstrap_ci(np.array([c for _, c in clv]), np.array([r.event_id for r, _ in clv]), zero_test=True).to_dict() if clv else None}
    d = out["edgefut_vs_superbet"]["delta_brier"]
    eff_n = out["effective"]["effective_n"]
    if eff_n < settings.sample_early_min:
        out["verdict"] = "INSUFFICIENT DATA"
    elif d.get("high") is not None and d["high"] < 0:
        out["verdict"] = "EDGEFUT BETTER THAN SUPERBET (SHADOW)"
    elif d.get("low") is not None and d["low"] > 0:
        out["verdict"] = "SUPERBET BETTER (SHADOW)"
    else:
        out["verdict"] = "NO CLEAR ADVANTAGE"
    return out


def _bucket_perf(rows: list[ShadowPrediction], label: str) -> dict:
    won = np.array([1.0 if r.won else 0.0 for r in rows])
    prob = np.array([float(r.model_prob) for r in rows])
    cl = np.array([r.event_id for r in rows])
    out = {"label": label, "n": len(rows), "events": int(len(set(cl.tolist()))), "avg_model_prob": round(float(prob.mean()), 4), "hit_rate": cluster_bootstrap_ci(won, cl).to_dict(),
           "brier": cluster_bootstrap_ci((prob - won) ** 2, cl).to_dict()}
    priced = [r for r in rows if r.odd]
    if priced:
        pw = np.array([1.0 if r.won else 0.0 for r in priced])
        po = np.array([float(r.odd) for r in priced])
        out["roi"] = cluster_bootstrap_ci(np.where(pw == 1, po - 1.0, -1.0), np.array([r.event_id for r in priced]), stat=roi_stat, zero_test=True).to_dict()
    return out


def confidence_validation(rows: list[ShadowPrediction]) -> dict:
    """§46 — o grau de confiança (A/B/C/D) separa acerto e calibração fora da amostra?
    Só linhas liquidadas com preço; o grau é reconstruído do score gravado na previsão."""
    from ..recommendations.confidence import grade_for

    settled = [r for r in rows if r.won is not None and r.confidence_score is not None and r.odd]
    groups: dict[str, list[ShadowPrediction]] = {}
    for r in settled:
        groups.setdefault(grade_for(float(r.confidence_score)), []).append(r)
    grades = {g: _bucket_perf(groups[g], g) for g in sorted(groups)}
    briers = [(g, grades[g]["brier"]["point"]) for g in sorted(grades) if grades[g]["n"] >= 30]
    monotonic = all(briers[i][1] <= briers[i + 1][1] for i in range(len(briers) - 1)) if len(briers) >= 2 else None
    eff = effective_sample_size(np.array([r.event_id for r in settled])) if settled else {"raw_n": 0, "effective_n": 0}
    return {"n": len(settled), "effective": eff, "grades": grades,
            "brier_monotonic_by_grade": monotonic,
            "verdict": "INSUFFICIENT DATA" if eff["effective_n"] < settings.sample_early_min or len(briers) < 2 else ("GRADE SEPARATES OOS" if monotonic else "GRADE DOES NOT SEPARATE OOS"),
            "note": "Grau reconstruído do confidence_score gravado no instante da previsão; comparação só entre graus com N ≥ 30."}


OPPORTUNITY_BINS = ((0, 40, "0-40"), (40, 55, "40-55"), (55, 70, "55-70"), (70, 101, "70+"))


def opportunity_validation(rows: list[ShadowPrediction]) -> dict:
    """§47 — Opportunity Score por faixa: hit, ROI, Brier OOS por bin. Se não houver relação, dizer."""
    settled = [r for r in rows if r.won is not None and r.opportunity_score is not None and r.odd]
    bins: dict[str, dict] = {}
    for lo, hi, label in OPPORTUNITY_BINS:
        rs = [r for r in settled if lo <= float(r.opportunity_score) < hi]
        if rs:
            bins[label] = _bucket_perf(rs, label)
    rois = [(k, v["roi"]["point"]) for k, v in bins.items() if v.get("roi") and v["n"] >= 30 and v["roi"].get("point") is not None]
    trend = all(rois[i][1] <= rois[i + 1][1] for i in range(len(rois) - 1)) if len(rois) >= 2 else None
    eff = effective_sample_size(np.array([r.event_id for r in settled])) if settled else {"raw_n": 0, "effective_n": 0}
    return {"n": len(settled), "effective": eff, "bins": bins, "roi_increasing_with_score": trend,
            "verdict": "INSUFFICIENT DATA" if eff["effective_n"] < settings.sample_early_min or len(rois) < 2 else ("SCORE RELATES TO ROI (OOS)" if trend else "NO RELATION SCORE→ROI (OOS)"),
            "note": "Bins fixos; ROI com IC por cluster de evento. Nunca escolher o bin com maior ROI como regra."}


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
        # iteração 4 (§46–§48): Superbet fair vs EdgeFut vs Híbrido, validação OOS de grau e opportunity
        "market_aware": market_aware_panel(all_settled),
        "confidence_validation": confidence_validation(all_settled),
        "opportunity_validation": opportunity_validation(all_settled),
        "notes": [
            "MODEL_ONLY nunca entra em ROI/Yield/CLV — não há preço.",
            "IC 95% por bootstrap; 'conclusive=false' significa que o IC cruza zero.",
            "market_aware/confidence/opportunity usam bootstrap por cluster de evento e N efetivo (§19–§20).",
        ],
    }
    if persist:
        skip = {"by_market", "performance", "market_aware", "confidence_validation", "opportunity_validation"}
        session.add(ValidationRun(kind="daily_report", summary={k: v for k, v in report.items() if k not in skip} | {"market_aware_verdict": report["market_aware"]["verdict"]}, detail=report, duration_ms=int((time.perf_counter() - t0) * 1000)))
        session.commit()
    return report


def latest_report(session: Session) -> dict | None:
    row = session.execute(select(ValidationRun).where(ValidationRun.kind == "daily_report").order_by(ValidationRun.created_at.desc()).limit(1)).scalar_one_or_none()
    return row.detail if row else None


__all__ = ["daily_report", "latest_report", "market_aware_panel", "confidence_validation", "opportunity_validation", "STATES", "PRICED_STATES"]
