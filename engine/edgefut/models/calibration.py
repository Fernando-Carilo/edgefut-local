"""Calibration Engine — regressão isotônica (PAV) sobre previsões liquidadas.

Regras:
* Só existe calibrador quando o grupo (mercado × competição, ou mercado × GLOBAL)
  tem pelo menos `MIN_CALIBRATION_N` previsões liquidadas. Abaixo disso, o grupo é
  gravado como `reliable=False` e a recomendação usa a probabilidade CRUA.
* A UI mostra sempre RAW e CALIBRATED lado a lado; nunca substituímos um pelo
  outro em silêncio.
* O calibrador é ajustado sobre previsões gravadas ANTES do jogo (snapshots) —
  nunca sobre backtests com odds de fechamento.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import CalibrationModel, PredictionSnapshot

log = logging.getLogger(__name__)

MODEL_VERSION = "calibration-isotonic-v1"
MIN_CALIBRATION_N = 300
GLOBAL = "GLOBAL"


def pav(x: np.ndarray, y: np.ndarray) -> tuple[list[float], list[float]]:
    """Pool Adjacent Violators: devolve (x_blocos, y_blocos) — função escada não-decrescente."""
    order = np.argsort(x, kind="stable")
    xs, ys = x[order].astype(float), y[order].astype(float)
    # blocos: (soma_y, n, x_min, x_max)
    blocks: list[list[float]] = []
    for xi, yi in zip(xs, ys, strict=True):
        blocks.append([yi, 1.0, xi, xi])
        while len(blocks) >= 2 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            b = blocks.pop()
            a = blocks[-1]
            a[0] += b[0]
            a[1] += b[1]
            a[3] = b[3]
    thresholds: list[float] = []
    values: list[float] = []
    for s, n, lo, hi in blocks:
        v = s / n
        thresholds.extend([lo, hi])
        values.extend([v, v])
    return thresholds, values


def apply_isotonic(thresholds: list[float], values: list[float], p: float) -> float:
    if not thresholds:
        return p
    return float(np.clip(np.interp(p, thresholds, values), 0.0, 1.0))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2)) if len(p) else float("nan")


@dataclass
class Calibrator:
    group_key: str
    n: int
    reliable: bool
    thresholds: list[float]
    values: list[float]
    brier_raw: float | None
    brier_calibrated: float | None

    def __call__(self, p: float) -> float:
        return apply_isotonic(self.thresholds, self.values, p) if self.reliable else p


def group_key(market_key: str, group: str = GLOBAL) -> str:
    return f"{market_key}|{group}"


def _samples(session: Session) -> list[tuple[str, str, float, float]]:
    """(market_key, competition, p_raw, outcome) para toda recomendação liquidada."""
    snaps = session.execute(select(PredictionSnapshot).where(PredictionSnapshot.result.is_not(None))).scalars().all()
    out: list[tuple[str, str, float, float]] = []
    for s in snaps:
        outcomes = (s.result or {}).get("outcomes") or {}
        for r in s.recommendations or []:
            key = f"{r['market_key']}|{r['selection_key']}|{r.get('line')}"
            if key not in outcomes:
                continue
            out.append((r["market_key"], s.competition_name or GLOBAL, float(r["model_prob"]), 1.0 if outcomes[key] else 0.0))
    return out


def refit_all(session: Session) -> dict:
    samples = _samples(session)
    groups: dict[str, list[tuple[float, float]]] = {}
    for mk, comp, p, y in samples:
        groups.setdefault(group_key(mk), []).append((p, y))
        groups.setdefault(group_key(mk, comp), []).append((p, y))
    fitted = 0
    reliable = 0
    now = datetime.utcnow()
    for gk, pts in groups.items():
        p = np.array([a for a, _ in pts])
        y = np.array([b for _, b in pts])
        n = len(pts)
        ok = n >= MIN_CALIBRATION_N
        thresholds, values = pav(p, y) if ok else ([], [])
        b_raw = brier(p, y)
        b_cal = brier(np.array([apply_isotonic(thresholds, values, v) for v in p]), y) if ok else None
        row = session.get(CalibrationModel, gk)
        if row is None:
            row = CalibrationModel(group_key=gk)
            session.add(row)
        row.method = "isotonic"
        row.n = n
        row.thresholds = thresholds
        row.values = values
        row.brier_raw = round(b_raw, 5)
        row.brier_calibrated = round(b_cal, 5) if b_cal is not None else None
        row.reliable = ok
        row.fitted_at = now
        fitted += 1
        reliable += int(ok)
    session.commit()
    log.info("calibração: %s grupos, %s confiáveis (N>=%s), %s amostras", fitted, reliable, MIN_CALIBRATION_N, len(samples))
    return {"ok": True, "groups": fitted, "reliable": reliable, "samples": len(samples), "min_n": MIN_CALIBRATION_N}


def load_calibrators(session: Session) -> dict[str, Calibrator]:
    rows = session.execute(select(CalibrationModel)).scalars().all()
    return {
        r.group_key: Calibrator(
            group_key=r.group_key, n=r.n, reliable=bool(r.reliable), thresholds=list(r.thresholds or []),
            values=list(r.values or []), brier_raw=r.brier_raw, brier_calibrated=r.brier_calibrated,
        )
        for r in rows
    }


def pick(calibrators: dict[str, Calibrator], market_key: str, competition: str | None) -> Calibrator | None:
    """Calibrador da competição se confiável; senão o GLOBAL do mercado se confiável; senão None."""
    if competition:
        c = calibrators.get(group_key(market_key, competition))
        if c and c.reliable:
            return c
    c = calibrators.get(group_key(market_key))
    if c and c.reliable:
        return c
    return None


def reliability_diagram(session: Session, market_key: str | None = None, buckets: int = 10) -> dict:
    samples = _samples(session)
    if market_key:
        samples = [s for s in samples if s[0] == market_key]
    edges = np.linspace(0, 1, buckets + 1)
    out = []
    for i in range(buckets):
        lo, hi = edges[i], edges[i + 1]
        pts = [(p, y) for _, _, p, y in samples if (lo <= p < hi) or (i == buckets - 1 and p == 1.0)]
        n = len(pts)
        out.append(
            {
                "bucket": f"{int(lo * 100)}-{int(hi * 100)}",
                "lower": round(float(lo), 2),
                "upper": round(float(hi), 2),
                "predicted": round(float(np.mean([p for p, _ in pts])), 4) if n else None,
                "actual": round(float(np.mean([y for _, y in pts])), 4) if n else None,
                "sample": n,
            }
        )
    total = len(samples)
    p_all = np.array([p for _, _, p, _ in samples])
    y_all = np.array([y for _, _, _, y in samples])
    return {
        "market_key": market_key,
        "buckets": out,
        "sample": total,
        "brier": round(brier(p_all, y_all), 5) if total else None,
        "reliable": total >= MIN_CALIBRATION_N,
        "min_n": MIN_CALIBRATION_N,
        "model_version": MODEL_VERSION,
    }


def calibration_overview(session: Session) -> list[dict]:
    rows = session.execute(select(CalibrationModel).order_by(CalibrationModel.n.desc())).scalars().all()
    return [
        {
            "group_key": r.group_key, "market_key": r.group_key.split("|")[0], "group": r.group_key.split("|", 1)[1],
            "n": r.n, "reliable": bool(r.reliable), "brier_raw": r.brier_raw, "brier_calibrated": r.brier_calibrated,
            "fitted_at": r.fitted_at, "method": r.method, "min_n": MIN_CALIBRATION_N,
        }
        for r in rows
    ]
