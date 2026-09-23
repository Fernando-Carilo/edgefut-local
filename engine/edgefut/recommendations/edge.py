"""Edge Engine: mapeia seleções de mercado para probabilidades do modelo e calcula EDGE/EV."""

from __future__ import annotations

from ..domain.analysis import CountDistribution, MarketOdds, SimulationOutput

CATEGORY_BY_MARKET = {
    "1X2": "RESULTADO",
    "DOUBLE_CHANCE": "RESULTADO",
    "DRAW_NO_BET": "RESULTADO",
    "HANDICAP": "RESULTADO",
    "ASIAN_HANDICAP": "RESULTADO",
    "TOTAL_GOALS": "GOLS",
    "BTTS": "GOLS",
    "TEAM_TOTAL_HOME": "GOLS",
    "TEAM_TOTAL_AWAY": "GOLS",
    "CORRECT_SCORE": "GOLS",
    "FIRST_GOAL": "GOLS",
    "TOTAL_CORNERS": "ESCANTEIOS",
    "TOTAL_CARDS": "CARTOES",
    "TOTAL_SHOTS": "FINALIZACOES",
    "TOTAL_SHOTS_ON_TARGET": "FINALIZACOES",
    "PLAYER_TO_SCORE": "JOGADOR",
}

# mercados exibidos com probabilidade mas nunca recomendados (aproximações ou alta variância)
WATCH_ONLY_MARKETS = {"CORRECT_SCORE", "FIRST_GOAL"}


def _line_key(line: float | None) -> str | None:
    return None if line is None else f"{line:.1f}"


def model_probability(
    market: MarketOdds,
    selection_key: str,
    sim: SimulationOutput | None,
    corners: CountDistribution,
    cards: CountDistribution,
) -> float | None:
    mk, line = market.market_key, market.line
    lk = _line_key(line)
    if mk in {"TOTAL_CORNERS", "TOTAL_CARDS"}:
        dist = corners if mk == "TOTAL_CORNERS" else cards
        if not dist.available or lk is None:
            return None
        table = dist.over if selection_key == "OVER" else dist.under if selection_key == "UNDER" else None
        return None if table is None else table.get(lk)
    if sim is None:
        return None
    if mk == "1X2":
        return {"HOME": sim.p_home, "DRAW": sim.p_draw, "AWAY": sim.p_away}.get(selection_key)
    if mk == "DOUBLE_CHANCE":
        return {"HOME_DRAW": sim.p_home_draw, "DRAW_AWAY": sim.p_draw_away, "HOME_AWAY": sim.p_home_away}.get(selection_key)
    if mk == "DRAW_NO_BET":
        return {"HOME": sim.p_dnb_home, "AWAY": sim.p_dnb_away}.get(selection_key)
    if mk == "TOTAL_GOALS":
        if lk is None:
            return None
        return sim.over.get(lk) if selection_key == "OVER" else sim.under.get(lk) if selection_key == "UNDER" else None
    if mk == "BTTS":
        return sim.btts if selection_key == "YES" else 1 - sim.btts if selection_key == "NO" else None
    if mk == "TEAM_TOTAL_HOME":
        if lk is None:
            return None
        p = sim.team_totals_home_over.get(lk)
        return None if p is None else (p if selection_key == "OVER" else 1 - p)
    if mk == "TEAM_TOTAL_AWAY":
        if lk is None:
            return None
        p = sim.team_totals_away_over.get(lk)
        return None if p is None else (p if selection_key == "OVER" else 1 - p)
    if mk in {"HANDICAP", "ASIAN_HANDICAP"}:
        if line is None or abs(line * 2 - round(line * 2)) > 1e-6 or float(line).is_integer():
            return None  # só linhas de meio gol (sem push)
        p_home_cover = sim.handicap_home.get(f"{line:+.1f}")
        if p_home_cover is None:
            return None
        return p_home_cover if selection_key == "HOME" else 1 - p_home_cover
    if mk == "FIRST_GOAL":
        return sim.first_goal.get(selection_key)
    if mk == "CORRECT_SCORE":
        for s, p in sim.top_scores:
            if s.replace("-", ":") == selection_key:
                return p
        return None
    return None


def edge_and_ev(model_prob: float, market_prob: float, odd: float) -> tuple[float, float]:
    edge_pp = (model_prob - market_prob) * 100
    ev_pct = (model_prob * odd - 1) * 100
    return round(edge_pp, 2), round(ev_pct, 2)
