"""Odds → probabilidade implícita, remoção de margem (multiplicativa e Shin) e movimento de linha.

Odds V2:
* `remove_margin` (multiplicativa) continua sendo o padrão; `remove_margin_shin` está
  disponível e o método efetivo é `settings.margin_method`. As duas probabilidades
  justas são sempre calculadas e gravadas (`fair_multiplicative`, `fair_shin`) — a
  odd bruta nunca é sobrescrita.
* `line_movement` resume abertura/atual/mínima/máxima e o movimento em odd, % e
  pontos percentuais de probabilidade implícita a partir do histórico de coletas.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from ..core.config import settings
from ..domain.analysis import LineMovement, MarketOdds, SelectionOdds
from ..providers.superbet.markets import COMPLETE_SELECTION_SETS, MARKET_LABELS

EXTREME_MOVEMENT_PCT = 15.0


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


def remove_margin_shin(prices: dict[str, float], fair_sum: float = 1.0, tol: float = 1e-10) -> tuple[dict[str, float], float, float]:
    """Método de Shin (1993): a margem é atribuída proporcionalmente mais aos azarões.

    Resolve `z` (fração de apostadores informados) por bissecção de modo que Σ p_i = 1, com
    p_i = (sqrt(z² + 4(1−z)·π_i²/β) − z) / (2(1−z)), π_i = 1/odd_i, β = Σ π_i.
    Só está definido para conjuntos mutuamente exclusivos (fair_sum = 1); para outros
    (ex.: Dupla Chance) devolve a normalização multiplicativa e z = 0.
    Retorna (fair_probs, overround, z).
    """
    implied = {k: implied_probability(v) for k, v in prices.items()}
    beta = sum(implied.values())
    if beta <= 0 or len(implied) < 2:
        return {k: 0.0 for k in prices}, 0.0, 0.0
    overround = beta / fair_sum - 1.0
    if fair_sum != 1.0 or beta <= 1.0:
        fair, _ = remove_margin(prices, fair_sum)
        return fair, overround, 0.0

    def probs(z: float) -> dict[str, float]:
        if z <= 0:
            return {k: v / beta for k, v in implied.items()}
        return {k: (math.sqrt(z * z + 4 * (1 - z) * (v * v) / beta) - z) / (2 * (1 - z)) for k, v in implied.items()}

    lo, hi = 0.0, 0.999
    # Σp(z) é decrescente em z; Σp(0) = 1 exatamente pela normalização, mas com z>0 a
    # soma de p_i usando π_i²/β desce abaixo de 1 → procuramos a raiz de Σp(z) − 1 com a
    # formulação padrão em que Σ_i p_i(z) = 1 define z. Como Σp(0⁺) > 1 (pois Σπ_i > 1), bisseção é válida.
    def f(z: float) -> float:
        return sum(probs(z).values()) - 1.0

    if f(1e-12) <= 0:
        fair, _ = remove_margin(prices, fair_sum)
        return fair, overround, 0.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    z = (lo + hi) / 2
    fair = probs(z)
    s = sum(fair.values())
    fair = {k: v / s for k, v in fair.items()}  # remove resíduo numérico
    return fair, overround, z


def movement(opening: float | None, current: float) -> tuple[float | None, str | None]:
    if opening is None or opening <= 0:
        return None, None
    pct = (current - opening) / opening * 100
    direction = "up" if pct > 0.5 else "down" if pct < -0.5 else "flat"
    return round(pct, 2), direction


def line_movement(points: list[tuple[datetime, float]], current: float | None = None) -> LineMovement | None:
    """Resumo do movimento a partir de coletas ordenadas por tempo [(collected_at, price)]."""
    pts = [(t, float(p)) for t, p in points if p and p > 1.0]
    if not pts:
        return None
    pts.sort(key=lambda x: x[0])
    opening = pts[0][1]
    cur = float(current) if current else pts[-1][1]
    prices = [p for _, p in pts] + [cur]
    pct, direction = movement(opening, cur)
    io, ic = implied_probability(opening), implied_probability(cur)
    return LineMovement(
        opening_odd=opening, current_odd=cur, lowest_odd=min(prices), highest_odd=max(prices),
        absolute_move=round(cur - opening, 3), percentage_move=pct or 0.0, implied_opening=round(io, 4), implied_current=round(ic, 4),
        implied_probability_move_pp=round((ic - io) * 100, 2), direction=direction or "flat", points=len(pts),
        first_seen_at=pts[0][0], last_seen_at=pts[-1][0], extreme=abs(pct or 0.0) > EXTREME_MOVEMENT_PCT,
    )


def build_markets(
    rows: list[dict],
    opening: dict[tuple[str, str, float | None], float] | None = None,
    collected_at: datetime | None = None,
    source_url: str | None = None,
    history: dict[tuple[str, str, float | None], list[tuple[datetime, float]]] | None = None,
    margin_method: str | None = None,
) -> list[MarketOdds]:
    """rows: dicts com market_key, market_name, selection_key, selection_name, line, price.

    `history` (opcional): coletas anteriores por seleção para o resumo de movimento.
    `margin_method`: MULTIPLICATIVE | SHIN (padrão: settings.margin_method).
    """
    grouped: dict[tuple[str, float | None], list[dict]] = defaultdict(list)
    for r in rows:
        grouped[(r["market_key"], r.get("line"))].append(r)

    method = (margin_method or settings.margin_method or "MULTIPLICATIVE").upper()
    if method not in ("MULTIPLICATIVE", "SHIN"):
        method = "MULTIPLICATIVE"
    markets: list[MarketOdds] = []
    opening = opening or {}
    history = history or {}
    for (mk, line), sels in grouped.items():
        prices = {s["selection_key"]: float(s["price"]) for s in sels}
        needed = COMPLETE_SELECTION_SETS.get(mk)
        complete = needed is not None and needed.issubset(prices.keys())
        fair_mult: dict[str, float] = {}
        fair_shin: dict[str, float] = {}
        overround = None
        z = None
        if complete:
            fair_mult, overround = remove_margin(prices, FAIR_SUM.get(mk, 1.0))
            fair_shin, _, z = remove_margin_shin(prices, FAIR_SUM.get(mk, 1.0))
        chosen = fair_shin if method == "SHIN" else fair_mult
        selections: list[SelectionOdds] = []
        for s in sels:
            price = float(s["price"])
            key = (mk, s["selection_key"], line)
            open_price = opening.get(key)
            mv, direction = movement(open_price, price)
            lm = line_movement(history.get(key, []), current=price) if history.get(key) else None
            selections.append(
                SelectionOdds(
                    key=s["selection_key"],
                    name=s["selection_name"],
                    price=price,
                    implied=round(implied_probability(price), 4),
                    fair=round(chosen[s["selection_key"]], 4) if complete else None,
                    fair_method=method if complete else None,  # type: ignore[arg-type]
                    fair_multiplicative=round(fair_mult[s["selection_key"]], 4) if complete else None,
                    fair_shin=round(fair_shin[s["selection_key"]], 4) if complete else None,
                    opening_price=open_price,
                    movement_pct=mv,
                    direction=direction,  # type: ignore[arg-type]
                    movement=lm,
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
                margin_method=method if complete else None,  # type: ignore[arg-type]
                shin_z=round(z, 4) if z is not None and complete else None,
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
