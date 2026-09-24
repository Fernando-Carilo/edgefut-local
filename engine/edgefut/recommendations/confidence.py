"""Confidence Engine (confidence-v2): EDGEFUT CONFIDENCE 0-100 com breakdown por grupo.

Grupos: DATA_QUALITY · MODEL_AGREEMENT · CALIBRATION · HISTORICAL_SAMPLE · FRESHNESS · CONTEXT.
"""

from __future__ import annotations

from datetime import datetime

from ..core import versions
from ..domain.analysis import (
    ConfidenceBreakdown,
    ConfidenceComponent,
    DataQuality,
    Grade,
    TeamProfile,
    VenueInfo,
)
from ..domain.freshness import Freshness

GROUP_LABELS = {
    "DATA_QUALITY": "Qualidade dos dados",
    "MODEL_AGREEMENT": "Concordância entre modelos",
    "CALIBRATION": "Calibração",
    "HISTORICAL_SAMPLE": "Amostra histórica",
    "FRESHNESS": "Frescor dos dados",
    "CONTEXT": "Contexto da partida",
}


def grade_for(score: float) -> Grade:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_confidence(
    *,
    data_quality: DataQuality,
    home: TeamProfile,
    away: TeamProfile,
    venue: VenueInfo,
    model_disagreement_pp: float | None,
    market_calibration: float | None,  # 0-1 (None → 0.5 neutro)
    calibration_samples: int,
    lineups_available: bool = False,
    freshness: list[Freshness] | None = None,
    n_models: int = 1,
) -> ConfidenceBreakdown:
    comps: list[ConfidenceComponent] = []

    comps.append(ConfidenceComponent(name="Qualidade dos dados", weight=25, value=_clamp(data_quality.score / 100), group="DATA_QUALITY"))

    n_min = min(home.sample_size, away.sample_size)
    comps.append(
        ConfidenceComponent(
            name="Quantidade de jogos", weight=15, value=_clamp(n_min / 20), note=f"{n_min} jogos na menor amostra", group="HISTORICAL_SAMPLE"
        )
    )

    rec_dates = [m.date for m in home.recent[:10]] + [m.date for m in away.recent[:10]]
    if rec_dates:
        age_days = sum((datetime.utcnow() - d).days for d in rec_dates) / len(rec_dates)
        rec_val = _clamp(1 - (age_days - 60) / 300) if age_days > 60 else 1.0
        note = f"idade média da amostra: {age_days:.0f} dias"
    else:
        rec_val, note = 0.0, "sem jogos recentes"
    comps.append(ConfidenceComponent(name="Recência", weight=10, value=rec_val, note=note, group="HISTORICAL_SAMPLE"))

    cons_vals = []
    for team in (home, away):
        w = team.windows.get("all_10")
        if w and w.goals_for is not None and w.goals_against is not None and w.n >= 5:
            # consistência: quão próximo o desempenho 5 jogos está do 20 jogos
            w5, w20 = team.windows.get("all_5"), team.windows.get("all_20")
            if w5 and w20 and w5.goals_for is not None and w20.goals_for is not None:
                delta = abs((w5.goals_for - w5.goals_against) - (w20.goals_for - w20.goals_against))
                cons_vals.append(_clamp(1 - delta / 2.0))
    comps.append(
        ConfidenceComponent(
            name="Consistência", weight=10, value=sum(cons_vals) / len(cons_vals) if cons_vals else 0.3,
            note=None if cons_vals else "janelas insuficientes", group="HISTORICAL_SAMPLE",
        )
    )

    if market_calibration is None or calibration_samples < 30:
        cal_val, cal_note = 0.5, f"calibração neutra ({calibration_samples} amostras settled, mín. 30)"
    else:
        cal_val, cal_note = _clamp(market_calibration), f"{calibration_samples} previsões settled"
    comps.append(ConfidenceComponent(name="Calibração histórica", weight=10, value=cal_val, note=cal_note, group="CALIBRATION"))

    if model_disagreement_pp is None or n_models < 2:
        dis_val, dis_note = 0.5, "apenas um modelo de gols disponível"
    else:
        dis_val, dis_note = _clamp(1 - model_disagreement_pp / 10), f"maior divergência entre {n_models} modelos de gols: {model_disagreement_pp:.1f} pp"
    comps.append(ConfidenceComponent(name="Divergência entre modelos", weight=15, value=dis_val, note=dis_note, group="MODEL_AGREEMENT"))

    # Frescor: odds e forma/histórico. EXPIRED zera; UNAVAILABLE de odds também.
    fr_items = [f for f in (freshness or []) if f.kind in ("odds", "form", "history")]
    if fr_items:
        odds_f = [f for f in fr_items if f.kind == "odds"]
        other = [f for f in fr_items if f.kind != "odds"]
        odds_val = odds_f[0].penalty if odds_f else 0.0
        other_val = min((f.penalty for f in other), default=1.0)
        fr_val = 0.6 * odds_val + 0.4 * other_val
        fr_note = " · ".join(f"{f.label} {f.status}" for f in fr_items)
    else:
        fr_val, fr_note = 0.5, "frescor não avaliado"
    comps.append(ConfidenceComponent(name="Frescor dos dados", weight=10, value=_clamp(fr_val), note=fr_note, group="FRESHNESS"))

    venue_val = 1.0 if venue.status != "UNCONFIRMED" else 0.5
    comps.append(ConfidenceComponent(name="Campo / mandante", weight=3, value=venue_val, note=venue.status, group="CONTEXT"))

    comps.append(
        ConfidenceComponent(
            name="Disponibilidade de jogadores", weight=2, value=1.0 if lineups_available else 0.0,
            note="sem fonte pública de escalações", group="CONTEXT",
        )
    )

    total_w = sum(c.weight for c in comps)
    score = 100 * sum(c.weight * c.value for c in comps) / total_w
    groups: dict[str, float] = {}
    for g in GROUP_LABELS:
        gc = [c for c in comps if c.group == g]
        if gc:
            gw = sum(c.weight for c in gc)
            groups[g] = round(100 * sum(c.weight * c.value for c in gc) / gw, 1)
    return ConfidenceBreakdown(model_version=versions.CONFIDENCE, score=round(score, 1), grade=grade_for(score), components=comps, groups=groups)
