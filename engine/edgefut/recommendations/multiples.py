"""Multiple Builder com detecção de correlação.

Seleções do mesmo evento são avaliadas em conjunto pela simulação Monte Carlo
(probabilidade conjunta real), não pelo produto ingênuo. Entre eventos
distintos assume-se independência.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..domain.analysis import GoalsModelOutput
from ..models.goals_common import MAX_GOALS, score_matrix

GOAL_MARKETS = {
    "1X2", "DOUBLE_CHANCE", "DRAW_NO_BET", "TOTAL_GOALS", "BTTS", "TEAM_TOTAL_HOME", "TEAM_TOTAL_AWAY",
    "HANDICAP", "ASIAN_HANDICAP", "CORRECT_SCORE",
}


@dataclass
class Leg:
    event_id: int
    market_key: str
    selection_key: str
    line: float | None
    odd: float
    model_prob: float
    label: str


def _leg_mask(leg: Leg, hg: np.ndarray, ag: np.ndarray) -> np.ndarray | None:
    total = hg + ag
    mk, s, line = leg.market_key, leg.selection_key, leg.line
    if mk == "1X2":
        return {"HOME": hg > ag, "DRAW": hg == ag, "AWAY": hg < ag}.get(s)
    if mk == "DOUBLE_CHANCE":
        return {"HOME_DRAW": hg >= ag, "DRAW_AWAY": hg <= ag, "HOME_AWAY": hg != ag}.get(s)
    if mk == "TOTAL_GOALS" and line is not None:
        return total > line if s == "OVER" else total < line
    if mk == "BTTS":
        both = (hg > 0) & (ag > 0)
        return both if s == "YES" else ~both
    if mk == "TEAM_TOTAL_HOME" and line is not None:
        return hg > line if s == "OVER" else hg < line
    if mk == "TEAM_TOTAL_AWAY" and line is not None:
        return ag > line if s == "OVER" else ag < line
    if mk in {"HANDICAP", "ASIAN_HANDICAP"} and line is not None:
        cover = (hg - ag + line) > 0
        return cover if s == "HOME" else ~cover
    if mk == "CORRECT_SCORE":
        try:
            h, a = (int(x) for x in s.split(":"))
        except ValueError:
            return None
        return (hg == h) & (ag == a)
    return None


def joint_probability_same_event(legs: list[Leg], model: GoalsModelOutput, n: int = 50_000, seed: int = 7) -> tuple[float | None, bool]:
    """Retorna (prob conjunta, correlacionado?). None se alguma perna não é modelável pelo placar."""
    if not model.available or model.lambda_home is None or model.lambda_away is None:
        return None, False
    m = score_matrix(model.lambda_home, model.lambda_away, model.rho)
    rng = np.random.default_rng(seed)
    draws = rng.choice(m.size, size=n, p=m.ravel() / m.sum())
    hg, ag = draws // MAX_GOALS, draws % MAX_GOALS
    mask = np.ones(n, dtype=bool)
    naive = 1.0
    for leg in legs:
        lm = _leg_mask(leg, hg, ag)
        if lm is None:
            return None, False
        mask &= lm
        naive *= float(lm.mean())
    joint = float(mask.mean())
    correlated = abs(joint - naive) > 0.02
    return joint, correlated


def combine(joint_by_event: list[float]) -> float:
    return float(np.prod(joint_by_event)) if joint_by_event else 0.0
