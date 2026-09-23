"""Odds → probabilidade implícita, remoção de margem e movimento."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ..domain.analysis import MarketOdds, SelectionOdds
from ..providers.superbet.markets import COMPLETE_SELECTION_SETS, MARKET_LABELS


def implied_probability(odd: float) -> float:
    return 1.0 / odd if odd > 0 else 0.0


# Soma "justa" das probabilidades de um conjunto completo. Em quase todos os mercados
# as seleções são mutuamente exclusivas (soma 1). Na Dupla Chance cada resultado é
# coberto por exatamente duas seleções, logo a soma sem margem é 2.
FAIR_SUM: dict[str, float] = {"DOUBLE_CHANCE": 2.0}


def remove_margin(prices: dict[str, float], fair_sum: float = 1.0) -> tuple[dict[str, float], float]:
    """Normalização multiplicativa. Retorna (fair_probs, overround)."""
    implied = {k: implied_probability(v) for k, v in prices.items()}
    s = sum(implied.values())
    if s <= 0:
        return {k: 0.0 for k in prices}, 0.0
    return {k: v * fair_sum / s for k, v in implied.items()}, s / fair_sum - 1.0


def movement(opening: float | None, current: float) -> tuple[float | None, str | None]:
    if opening is None or opening <= 0:
        return None, None
    pct = (current - opening) / opening * 100
    direction = "up" if pct > 0.5 else "down" if pct < -0.5 else "flat"
    return round(pct, 2), direction


def build_markets(
    rows: list[dict],
    opening: dict[tuple[str, str, float | None], float] | None = None,
    collected_at: datetime | None = None,
    source_url: str | None = None,
) -> list[MarketOdds]:
    """rows: dicts com market_key, market_name, selection_key, selection_name, line, price."""
    grouped: dict[tuple[str, float | None], list[dict]] = defaultdict(list)
    for r in rows:
        grouped[(r["market_key"], r.get("line"))].append(r)

    markets: list[MarketOdds] = []
    opening = opening or {}
    for (mk, line), sels in grouped.items():
        prices = {s["selection_key"]: float(s["price"]) for s in sels}
        needed = COMPLETE_SELECTION_SETS.get(mk)
        complete = needed is not None and needed.issubset(prices.keys())
        fair, overround = (remove_margin(prices, FAIR_SUM.get(mk, 1.0)) if complete else ({}, None))
        selections: list[SelectionOdds] = []
        for s in sels:
            price = float(s["price"])
            open_price = opening.get((mk, s["selection_key"], line))
            mv, direction = movement(open_price, price)
            selections.append(
                SelectionOdds(
                    key=s["selection_key"],
                    name=s["selection_name"],
                    price=price,
                    implied=round(implied_probability(price), 4),
                    fair=round(fair[s["selection_key"]], 4) if complete else None,
                    opening_price=open_price,
                    movement_pct=mv,
                    direction=direction,  # type: ignore[arg-type]
                )
            )
        selections.sort(key=lambda x: _sel_order(mk, x.key))
        markets.append(
            MarketOdds(
                market_key=mk,
                label=MARKET_LABELS.get(mk, sels[0].get("market_name", mk)),
                line=line,
                selections=selections,
                overround=round(overround, 4) if overround is not None else None,
                margin_removed=complete,
                collected_at=collected_at,
                source_url=source_url,
            )
        )
    markets.sort(key=lambda m: (_market_order(m.market_key), m.line if m.line is not None else 0))
    return markets


_MARKET_ORDER = [
    "1X2", "DOUBLE_CHANCE", "DRAW_NO_BET", "TOTAL_GOALS", "BTTS", "TEAM_TOTAL_HOME", "TEAM_TOTAL_AWAY",
    "HANDICAP", "ASIAN_HANDICAP", "TOTAL_CORNERS", "TOTAL_CARDS", "FIRST_GOAL", "CORRECT_SCORE",
    "PLAYER_TO_SCORE",
]
_SEL_ORDER = {"HOME": 0, "DRAW": 1, "AWAY": 2, "NONE": 1, "HOME_DRAW": 0, "DRAW_AWAY": 1, "HOME_AWAY": 2,
              "OVER": 0, "UNDER": 1, "YES": 0, "NO": 1}


def _market_order(mk: str) -> int:
    return _MARKET_ORDER.index(mk) if mk in _MARKET_ORDER else 99


def _sel_order(mk: str, key: str) -> tuple:
    return (_SEL_ORDER.get(key, 50), key)
