"""Settlement: liquida seleções a partir do resultado real. Nunca altera a previsão."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MatchResult:
    hg: int
    ag: int
    corners: int | None = None
    cards: int | None = None
    source: str = ""


def settle_selection(market_key: str, selection_key: str, line: float | None, r: MatchResult) -> bool | None:
    """True = ganhou, False = perdeu, None = void/não liquidável."""
    hg, ag, total, diff = r.hg, r.ag, r.hg + r.ag, r.hg - r.ag
    if market_key == "1X2":
        return {"HOME": diff > 0, "DRAW": diff == 0, "AWAY": diff < 0}.get(selection_key)
    if market_key == "DOUBLE_CHANCE":
        return {"HOME_DRAW": diff >= 0, "DRAW_AWAY": diff <= 0, "HOME_AWAY": diff != 0}.get(selection_key)
    if market_key == "DRAW_NO_BET":
        if diff == 0:
            return None
        return diff > 0 if selection_key == "HOME" else diff < 0
    if market_key == "TOTAL_GOALS" and line is not None:
        if total == line:
            return None
        return total > line if selection_key == "OVER" else total < line
    if market_key == "BTTS":
        both = hg > 0 and ag > 0
        return both if selection_key == "YES" else not both
    if market_key == "TEAM_TOTAL_HOME" and line is not None:
        return hg > line if selection_key == "OVER" else hg < line
    if market_key == "TEAM_TOTAL_AWAY" and line is not None:
        return ag > line if selection_key == "OVER" else ag < line
    if market_key in {"HANDICAP", "ASIAN_HANDICAP"} and line is not None:
        adj = diff + line
        if adj == 0:
            return None
        return adj > 0 if selection_key == "HOME" else adj < 0
    if market_key == "CORRECT_SCORE":
        return selection_key == f"{hg}:{ag}"
    if market_key == "TOTAL_CORNERS" and line is not None and r.corners is not None:
        if r.corners == line:
            return None
        return r.corners > line if selection_key == "OVER" else r.corners < line
    if market_key == "TOTAL_CARDS" and line is not None and r.cards is not None:
        if r.cards == line:
            return None
        return r.cards > line if selection_key == "OVER" else r.cards < line
    return None
