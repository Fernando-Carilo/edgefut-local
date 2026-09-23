"""Poisson independente (goals-poisson-v1)."""

from __future__ import annotations

import math

from ..core import versions
from ..domain.analysis import GoalsModelOutput
from ..features.strength import LeagueAverages
from .goals_common import markets_from_matrix, score_matrix


def poisson_model(
    *,
    la: LeagueAverages | None,
    attack_home: float | None,
    defense_home: float | None,
    attack_away: float | None,
    defense_away: float | None,
    home_adv_weight: float,
    fit_matches: int,
) -> GoalsModelOutput:
    if la is None or None in (attack_home, defense_home, attack_away, defense_away):
        return GoalsModelOutput(
            model_version=versions.POISSON, available=False,
            note="Poisson indisponível: força ofensiva/defensiva não calculável (dados insuficientes).",
        )
    league_mean = (la.home_goals + la.away_goals) / 2
    # vantagem de mandante da liga = razão gols casa / média; interpolada pelo peso do venue
    full_ha = la.home_goals / league_mean if league_mean > 0 else 1.0
    full_aa = la.away_goals / league_mean if league_mean > 0 else 1.0
    ha = math.exp(home_adv_weight * math.log(full_ha)) if full_ha > 0 else 1.0
    aa = math.exp(home_adv_weight * math.log(full_aa)) if full_aa > 0 else 1.0
    lam_home = league_mean * attack_home * defense_away * ha  # type: ignore[operator]
    lam_away = league_mean * attack_away * defense_home * aa  # type: ignore[operator]
    lam_home = max(0.15, min(5.0, lam_home))
    lam_away = max(0.15, min(5.0, lam_away))
    m = score_matrix(lam_home, lam_away)
    mk = markets_from_matrix(m)
    return GoalsModelOutput(
        model_version=versions.POISSON,
        available=True,
        lambda_home=round(lam_home, 3),
        lambda_away=round(lam_away, 3),
        p_home=round(mk["p_home"], 4),
        p_draw=round(mk["p_draw"], 4),
        p_away=round(mk["p_away"], 4),
        dist_home=[round(x, 4) for x in mk["dist_home"]],
        dist_away=[round(x, 4) for x in mk["dist_away"]],
        over={k: round(v, 4) for k, v in mk["over"].items()},
        under={k: round(v, 4) for k, v in mk["under"].items()},
        btts=round(mk["btts"], 4),
        top_scores=[(s, round(p, 4)) for s, p in mk["top_scores"][:6]],
        fit_matches=fit_matches,
    )
