"""Mapeamento marketId Superbet → mercados EdgeFut.

Nunca inventamos mercado: só entram aqui IDs observados na oferta pública.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# market_key EdgeFut → rótulo PT-BR
MARKET_LABELS: dict[str, str] = {
    "1X2": "Resultado Final",
    "DOUBLE_CHANCE": "Dupla Chance",
    "DRAW_NO_BET": "Empate Anula Aposta",
    "TOTAL_GOALS": "Total de Gols",
    "BTTS": "Ambas Marcam",
    "HANDICAP": "Handicap",
    "ASIAN_HANDICAP": "Handicap Asiático",
    "TOTAL_CORNERS": "Total de Escanteios",
    "TOTAL_CARDS": "Total de Cartões",
    "PLAYER_TO_SCORE": "Jogador Marca",
    "FIRST_GOAL": "Primeiro Gol",
    "CORRECT_SCORE": "Placar Correto",
    "TEAM_TOTAL_HOME": "Total de Gols (Mandante)",
    "TEAM_TOTAL_AWAY": "Total de Gols (Visitante)",
    "TOTAL_SHOTS": "Total de Finalizações",
    "TOTAL_SHOTS_ON_TARGET": "Finalizações no Alvo",
}

# marketId Superbet → market_key
SUPERBET_MARKET_IDS: dict[int, str] = {
    547: "1X2",
    531: "DOUBLE_CHANCE",
    555: "DRAW_NO_BET",
    200734: "TOTAL_GOALS",
    539: "BTTS",
    200736: "HANDICAP",
    530: "ASIAN_HANDICAP",
    704: "TOTAL_CORNERS",
    690: "TOTAL_CARDS",
    233475: "PLAYER_TO_SCORE",
    538: "FIRST_GOAL",
    200741: "CORRECT_SCORE",
    544: "TEAM_TOTAL_HOME",
    535: "TEAM_TOTAL_AWAY",
}

# Mercados cujo conjunto de seleções é completo (permite remover a margem)
COMPLETE_SELECTION_SETS: dict[str, set[str]] = {
    "1X2": {"HOME", "DRAW", "AWAY"},
    "DOUBLE_CHANCE": {"HOME_DRAW", "DRAW_AWAY", "HOME_AWAY"},
    "DRAW_NO_BET": {"HOME", "AWAY"},
    "TOTAL_GOALS": {"OVER", "UNDER"},
    "BTTS": {"YES", "NO"},
    "HANDICAP": {"HOME", "AWAY"},
    "ASIAN_HANDICAP": {"HOME", "AWAY"},
    "TOTAL_CORNERS": {"OVER", "UNDER"},
    "TOTAL_CARDS": {"OVER", "UNDER"},
    "FIRST_GOAL": {"HOME", "NONE", "AWAY"},
    "TEAM_TOTAL_HOME": {"OVER", "UNDER"},
    "TEAM_TOTAL_AWAY": {"OVER", "UNDER"},
}


@dataclass(frozen=True)
class NormalizedOdd:
    market_key: str
    market_name: str
    selection_key: str
    selection_name: str
    line: float | None
    price: float
    status: str


_LINE_RE = re.compile(r"(-?\d+(?:\.\d+)?)")


def _parse_line(raw: dict) -> float | None:
    spec = raw.get("specifiers") or {}
    for k in ("total", "hcp", "handicap"):
        if k in spec:
            try:
                return float(str(spec[k]).replace(",", "."))
            except ValueError:
                pass
    sbv = raw.get("specialBetValue")
    if sbv is not None:
        m = _LINE_RE.search(str(sbv))
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def normalize_odd(raw: dict, home_name: str, away_name: str) -> NormalizedOdd | None:
    market_key = SUPERBET_MARKET_IDS.get(int(raw.get("marketId", -1)))
    if market_key is None:
        return None
    status = str(raw.get("status", "active"))
    price = raw.get("price")
    if price is None:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price <= 1.0:
        return None

    name = str(raw.get("name", ""))
    code = str(raw.get("code") or "")
    lname = _norm(name)
    lhome, laway = _norm(home_name), _norm(away_name)
    line = _parse_line(raw)
    sel: str | None = None

    if market_key == "1X2":
        sel = {"1": "HOME", "0": "DRAW", "2": "AWAY"}.get(code) or {
            "1": "HOME", "x": "DRAW", "2": "AWAY"
        }.get(lname)
    elif market_key == "DOUBLE_CHANCE":
        sel = {"10": "HOME_DRAW", "02": "DRAW_AWAY", "12": "HOME_AWAY"}.get(code)
        if sel is None:
            if "1 ou empate" in lname or lname == "1x":
                sel = "HOME_DRAW"
            elif "empate ou 2" in lname or lname == "x2":
                sel = "DRAW_AWAY"
            elif "1 ou 2" in lname or lname == "12":
                sel = "HOME_AWAY"
    elif market_key == "DRAW_NO_BET":
        sel = {"1": "HOME", "2": "AWAY"}.get(code)
        if sel is None:
            sel = "HOME" if lname == lhome else "AWAY" if lname == laway else None
    elif market_key in {
        "TOTAL_GOALS", "TOTAL_CORNERS", "TOTAL_CARDS", "TEAM_TOTAL_HOME", "TEAM_TOTAL_AWAY"
    }:
        if lname.startswith("mais de") or code.endswith("+"):
            sel = "OVER"
        elif lname.startswith("menos de") or code.endswith("-"):
            sel = "UNDER"
        if line is None:
            m = _LINE_RE.search(name)
            line = float(m.group(1)) if m else None
        if line is None:
            return None
    elif market_key == "BTTS":
        sel = {"1": "YES", "2": "NO"}.get(code) or {"sim": "YES", "não": "NO", "nao": "NO"}.get(lname)
    elif market_key in {"HANDICAP", "ASIAN_HANDICAP"}:
        if code == "1" or lname.startswith(lhome):
            sel = "HOME"
        elif code == "2" or lname.startswith(laway):
            sel = "AWAY"
        # linha do ponto de vista do mandante
        m = _LINE_RE.search(name)
        if m:
            side_line = float(m.group(1))
            line = side_line if sel == "HOME" else -side_line
        if line is None:
            return None
    elif market_key == "FIRST_GOAL":
        sel = {"1": "HOME", "0": "NONE", "2": "AWAY"}.get(code)
        if sel is None:
            sel = "HOME" if lname == lhome else "AWAY" if lname == laway else "NONE"
    elif market_key == "CORRECT_SCORE":
        m = re.match(r"^(\d+):(\d+)$", name.strip())
        if not m:
            return None
        sel = f"{m.group(1)}:{m.group(2)}"
    elif market_key == "PLAYER_TO_SCORE":
        sel = name.strip()

    if sel is None:
        return None
    return NormalizedOdd(
        market_key=market_key,
        market_name=str(raw.get("marketName", MARKET_LABELS.get(market_key, market_key))),
        selection_key=sel,
        selection_name=name.strip(),
        line=line,
        price=price,
        status=status,
    )
