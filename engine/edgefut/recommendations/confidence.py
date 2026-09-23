"""Confidence Engine (confidence-v1)."""

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
) -> ConfidenceBreakdown:
    comps: list[ConfidenceComponent] = []

    comps.append(ConfidenceComponent(name="Qualidade dos dados", weight=25, value=_clamp(data_quality.score / 100)))

    n_min = min(home.sample_size, away.sample_size)
    comps.append(
        ConfidenceComponent(
            name="Quantidade de jogos", weight=15, value=_clamp(n_min / 20), note=f"{n_min} jogos na menor amostra"
        )
    )

    rec_dates = [m.date for m in home.recent[:10]] + [m.date for m in away.recent[:10]]
    if rec_dates:
        age_days = sum((datetime.utcnow() - d).days for d in rec_dates) / len(rec_dates)
        rec_val = _clamp(1 - (age_days - 60) / 300) if age_days > 60 else 1.0
        note = f"idade média da amostra: {age_days:.0f} dias"
    else:
        rec_val, note = 0.0, "sem jogos recentes"
    comps.append(ConfidenceComponent(name="Recência", weight=10, value=rec_val, note=note))

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
            note=None if cons_vals else "janelas insuficientes",
        )
    )

    if market_calibration is None or calibration_samples < 30:
        cal_val, cal_note = 0.5, f"calibração neutra ({calibration_samples} amostras settled, mín. 30)"
    else:
        cal_val, cal_note = _clamp(market_calibration), f"{calibration_samples} previsões settled"
    comps.append(ConfidenceComponent(name="Calibração histórica", weight=15, value=cal_val, note=cal_note))

    if model_disagreement_pp is None:
        dis_val, dis_note = 0.5, "apenas um modelo de gols disponível"
    else:
        dis_val, dis_note = _clamp(1 - model_disagreement_pp / 10), f"maior divergência entre modelos de gols: {model_disagreement_pp:.1f} pp"
    comps.append(ConfidenceComponent(name="Divergência entre modelos", weight=15, value=dis_val, note=dis_note))

    venue_val = 1.0 if venue.status != "UNCONFIRMED" else 0.5
    comps.append(ConfidenceComponent(name="Campo / mandante", weight=5, value=venue_val, note=venue.status))

    comps.append(
        ConfidenceComponent(
            name="Disponibilidade de jogadores", weight=5, value=1.0 if lineups_available else 0.0,
            note="sem fonte pública de escalações",
        )
    )

    total_w = sum(c.weight for c in comps)
    score = 100 * sum(c.weight * c.value for c in comps) / total_w
    return ConfidenceBreakdown(model_version=versions.CONFIDENCE, score=round(score, 1), grade=grade_for(score), components=comps)
