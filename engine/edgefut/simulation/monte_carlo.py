"""Match Simulation Engine (mc-v1).

Amostra placares da matriz do modelo de gols (Dixon-Coles se disponível, senão
Poisson) com semente fixa por evento. Deriva mercados a partir da amostra.
"""

from __future__ import annotations

import numpy as np

from ..core import versions
from ..domain.analysis import GoalsModelOutput, SimulationOutput
from ..models.goals_common import MAX_GOALS, score_matrix

ALLOWED_SIMULATIONS = (10_000, 25_000, 50_000, 100_000)


def simulate(model: GoalsModelOutput, n: int, seed: int) -> SimulationOutput | None:
    if not model.available or model.lambda_home is None or model.lambda_away is None:
        return None
    if n not in ALLOWED_SIMULATIONS:
        n = 50_000
    m = score_matrix(model.lambda_home, model.lambda_away, model.rho)
    rng = np.random.default_rng(seed)
    flat = m.ravel()
    draws = rng.choice(flat.size, size=n, p=flat / flat.sum())
    hg = draws // MAX_GOALS
    ag = draws % MAX_GOALS
    total = hg + ag

    p_home = float((hg > ag).mean())
    p_draw = float((hg == ag).mean())
    p_away = float((hg < ag).mean())
    over, under = {}, {}
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        o = float((total > line).mean())
        over[f"{line:.1f}"] = round(o, 4)
        under[f"{line:.1f}"] = round(1 - o, 4)
    btts = float(((hg > 0) & (ag > 0)).mean())
    tot_dist = [float((total == k).mean()) for k in range(6)]
    tot_dist.append(max(0.0, 1 - sum(tot_dist)))

    scores: dict[str, int] = {}
    for h, a in zip(hg.tolist(), ag.tolist(), strict=False):
        key = f"{h}-{a}"
        scores[key] = scores.get(key, 0) + 1
    top = sorted(scores.items(), key=lambda kv: -kv[1])[:8]
    top_scores = [(k, round(v / n, 4)) for k, v in top]

    team_h_over = {f"{line:.1f}": round(float((hg > line).mean()), 4) for line in (0.5, 1.5, 2.5)}
    team_a_over = {f"{line:.1f}": round(float((ag > line).mean()), 4) for line in (0.5, 1.5, 2.5)}

    # primeiro gol: aproximado pela proporção de gols esperados (ordem dentro do jogo
    # não é modelada explicitamente pelo placar final)
    lh, la = model.lambda_home, model.lambda_away
    p_no_goal = float((total == 0).mean())
    share_h = lh / (lh + la) if (lh + la) > 0 else 0.5
    first_goal = {
        "HOME": round((1 - p_no_goal) * share_h, 4),
        "AWAY": round((1 - p_no_goal) * (1 - share_h), 4),
        "NONE": round(p_no_goal, 4),
    }
    diff = hg - ag
    handicap = {f"{line:+.1f}": round(float(((diff + line) > 0).mean()), 4) for line in (-2.5, -1.5, -0.5, 0.5, 1.5, 2.5)}

    dnb_den = p_home + p_away
    return SimulationOutput(
        model_version=versions.MONTE_CARLO,
        simulations=n,
        seed=seed,
        base_model=model.model_version,
        p_home=round(p_home, 4),
        p_draw=round(p_draw, 4),
        p_away=round(p_away, 4),
        p_home_draw=round(p_home + p_draw, 4),
        p_draw_away=round(p_draw + p_away, 4),
        p_home_away=round(p_home + p_away, 4),
        p_dnb_home=round(p_home / dnb_den, 4) if dnb_den else 0.5,
        p_dnb_away=round(p_away / dnb_den, 4) if dnb_den else 0.5,
        over=over,
        under=under,
        btts=round(btts, 4),
        expected_goals_home=round(float(hg.mean()), 3),
        expected_goals_away=round(float(ag.mean()), 3),
        total_goals_dist=[round(x, 4) for x in tot_dist],
        top_scores=top_scores,
        most_likely_score=top_scores[0][0] if top_scores else "-",
        team_totals_home_over=team_h_over,
        team_totals_away_over=team_a_over,
        first_goal=first_goal,
        handicap_home=handicap,
    )
