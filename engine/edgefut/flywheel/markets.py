"""Canonicalização de mercados Superbet (§10).

Um `marketId` observado na oferta pública cai em exatamente uma de três situações:

- **MAPPED** — pertence a uma categoria canónica (MATCH_RESULT, TOTAL_GOALS, CORNERS_TOTAL…) e
  as seleções recebem um `canonical_market_id` estável (`TOTAL_GOALS_2_5`, `TEAM_CORNERS_HOME_4_5`)
  e um `selection_id` (`OVER`, `HOME`, `YES`, `player:12345`).
- **OUT_OF_SCOPE** — reconhecido pelo nome (1º tempo, combinações, placar correto…), registado
  com motivo. Fica na raw layer, não na normalizada.
- **UNKNOWN** — nem mapeado nem reconhecido: vai para o registo de mapeamento para revisão
  manual. Nunca é ignorado em silêncio; nunca é mapeado automaticamente se ambíguo.

Os IDs vêm dos payloads reais (`docs/MARKET_MAPPING.md`); o lado casa/fora dos mercados por
equipe é confirmado pelo nome do mercado, não só pelo ID.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

CATEGORY_LABELS: dict[str, str] = {
    "MATCH_RESULT": "Resultado final (1X2)",
    "DOUBLE_CHANCE": "Dupla chance",
    "DNB": "Empate anula",
    "TOTAL_GOALS": "Total de gols",
    "BTTS": "Ambas marcam",
    "TEAM_TOTAL": "Gols por equipe",
    "CORNERS_TOTAL": "Escanteios (total)",
    "TEAM_CORNERS": "Escanteios por equipe",
    "CARDS_TOTAL": "Cartões (total)",
    "TEAM_CARDS": "Cartões por equipe",
    "SHOTS": "Finalizações",
    "SHOTS_ON_TARGET": "Finalizações no alvo",
    "PLAYER_GOAL": "Jogador marca",
    "PLAYER_SHOTS": "Jogador — finalizações",
    "PLAYER_SOT": "Jogador — no alvo",
}
CATEGORY_ORDER = list(CATEGORY_LABELS)

# família de pesquisa (§21): mercados que partilham a mesma pergunta estatística
RESEARCH_FAMILY: dict[str, str] = {
    "MATCH_RESULT": "1X2", "DOUBLE_CHANCE": "1X2", "DNB": "1X2",
    "TOTAL_GOALS": "OU", "TEAM_TOTAL": "TEAM_TOTAL", "BTTS": "BTTS",
    "CORNERS_TOTAL": "CORNERS", "TEAM_CORNERS": "CORNERS", "CARDS_TOTAL": "CARDS", "TEAM_CARDS": "CARDS",
    "SHOTS": "SHOTS", "SHOTS_ON_TARGET": "SHOTS", "PLAYER_GOAL": "PLAYER", "PLAYER_SHOTS": "PLAYER", "PLAYER_SOT": "PLAYER",
}

# kind: como as seleções são interpretadas
#   3WAY (1/X/2), DC, 2WAY_TEAM (casa/fora pelo nome), OU (mais/menos + linha), YES_NO,
#   TEAM_OU (mais/menos por equipe; lado pelo nome do mercado, fallback pelo ID),
#   PLAYER_YES (uma seleção por jogador), PLAYER_OU (jogador + mais de X)
@dataclass(frozen=True)
class MarketSpec:
    category: str
    kind: str
    side: str | None = None  # HOME / AWAY para mercados por equipe (fallback)
    complete_n: int | None = None  # seleções que fecham o mercado (para fair/overround)


MAPPED: dict[int, MarketSpec] = {
    547: MarketSpec("MATCH_RESULT", "3WAY", complete_n=3),
    531: MarketSpec("DOUBLE_CHANCE", "DC", complete_n=3),
    555: MarketSpec("DNB", "2WAY_TEAM", complete_n=2),
    200734: MarketSpec("TOTAL_GOALS", "OU", complete_n=2),
    539: MarketSpec("BTTS", "YES_NO", complete_n=2),
    544: MarketSpec("TEAM_TOTAL", "TEAM_OU", side="HOME", complete_n=2),
    535: MarketSpec("TEAM_TOTAL", "TEAM_OU", side="AWAY", complete_n=2),
    704: MarketSpec("CORNERS_TOTAL", "OU", complete_n=2),
    713: MarketSpec("TEAM_CORNERS", "TEAM_OU", side="HOME", complete_n=2),
    733: MarketSpec("TEAM_CORNERS", "TEAM_OU", side="AWAY", complete_n=2),
    690: MarketSpec("CARDS_TOTAL", "OU", complete_n=2),
    700: MarketSpec("TEAM_CARDS", "TEAM_OU", side="HOME", complete_n=2),
    708: MarketSpec("TEAM_CARDS", "TEAM_OU", side="AWAY", complete_n=2),
    201590: MarketSpec("SHOTS", "OU", complete_n=2),
    201591: MarketSpec("SHOTS", "TEAM_OU", side="HOME", complete_n=2),
    201592: MarketSpec("SHOTS", "TEAM_OU", side="AWAY", complete_n=2),
    200702: MarketSpec("SHOTS_ON_TARGET", "OU", complete_n=2),
    200703: MarketSpec("SHOTS_ON_TARGET", "TEAM_OU", side="HOME", complete_n=2),
    200704: MarketSpec("SHOTS_ON_TARGET", "TEAM_OU", side="AWAY", complete_n=2),
    233475: MarketSpec("PLAYER_GOAL", "PLAYER_YES"),
    236226: MarketSpec("PLAYER_GOAL", "PLAYER_YES"),
    236218: MarketSpec("PLAYER_SHOTS", "PLAYER_OU"),
    232338: MarketSpec("PLAYER_SHOTS", "PLAYER_OU"),
    236220: MarketSpec("PLAYER_SOT", "PLAYER_OU"),
}

# Reconhecidos pelo nome → OUT_OF_SCOPE com motivo. Ordem importa (o primeiro que casa ganha).
OUT_OF_SCOPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("COMBO_MARKET", re.compile(r";")),  # bet-builder: várias pernas separadas por ';' (marketName varia por seleção)
    ("PERIOD_MARKET", re.compile(r"(1[ºo°]\s*tempo|2[ºo°]\s*tempo|intervalo|em cada tempo|qualquer um dos tempos|tempo com (o )?maior|nos dois tempos)", re.I)),
    ("TIME_WINDOW", re.compile(r"(primeiros?\s+\d+|pr[oó]ximos?\s+\d+|pr[oó]ximo minuto|de \d+:\d+ a|minutos)", re.I)),
    ("COMBO_MARKET", re.compile(r"(&|\bou\b.*(vence|empate|gols)|resultado final (e|ou)|dupla chance (e|&)|marcam? (e|ou) mais|vencer ou ambas)", re.I)),
    ("CORRECT_SCORE", re.compile(r"(resultado correto|placar correto|m[úu]ltiplos placares)", re.I)),
    ("HANDICAP", re.compile(r"handicap", re.I)),
    ("EXACT_OR_RANGE", re.compile(r"(n[úu]mero exato|faixa de|[íi]mpar/par|asi[áa]tico)", re.I)),
    ("SEQUENCE_MARKET", re.compile(r"(\d+[ºo°]\s*(gol|escanteio|cart[ãa]o|falta|impedimento|chute|finaliza)|1[ºo°] (gol|cart|impedimento|chute|finaliza|falta)|corrida|[úu]ltimo escanteio|a cobrar|m[ée]todo do|tempo do)", re.I)),
    ("TEAM_COMPARISON", re.compile(r"(equipe com mais|com mais (cart|escant|chutes|finaliza)|\(1x2\)|cada equipe|expuls[ãa]o em ambas)", re.I)),
    ("RED_CARDS_OR_WOODWORK", re.compile(r"(vermelh|trave)", re.I)),
    ("FOULS_OFFSIDES", re.compile(r"(faltas?|impedimento)", re.I)),
    ("OTHER_STAT", re.compile(r"(p[êe]naltis?|arremessos? lateral|tiros? de meta|[úu]ltimo gol|qualifica)", re.I)),
    ("PLAYER_EXOTIC", re.compile(r"(jogador|duelo|marcar (gol )?(de cabe|com o p[ée]|de fora)|marcar (o )?\d|marcar 2\+|marcar 3\+|assist[êe]ncia|desarme|receber|treinador|goleiro|acumular|cometer)", re.I)),
    ("BTTS_VARIANT", re.compile(r"ambas as equipes marcam", re.I)),  # variantes (2+ gols, em algum tempo…) — o BTTS puro é o 539
    ("DC_VARIANT", re.compile(r"(dupla chance|empate anula)", re.I)),  # variantes por período já apanhadas acima; restos aqui
    ("TOTAL_VARIANT", re.compile(r"total de gols", re.I)),  # ex.: 529 Total de Gols Asiático
)

_LINE_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)")


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def _line_token(line: float) -> str:
    return f"{line:g}".replace("-", "m").replace(".", "_")


def canonical_market_id(category: str, line: float | None = None, side: str | None = None) -> str:
    parts = [category]
    if side:
        parts.append(side)
    if line is not None:
        parts.append(_line_token(float(line)))
    return "_".join(parts)


def canonical_selection_id(category: str, selection_id: str, line: float | None = None, side: str | None = None) -> str:
    """Identificador plano para relatórios (ex.: TOTAL_GOALS_OVER_2_5, TEAM_CORNERS_HOME_UNDER_4_5)."""
    parts = [category]
    if side:
        parts.append(side)
    parts.append(selection_id)
    if line is not None:
        parts.append(_line_token(float(line)))
    return "_".join(parts)


def classify_market(market_id: int, market_name: str | None) -> tuple[str, str | None, str | None]:
    """→ (status, category, reason). status ∈ MAPPED | OUT_OF_SCOPE | UNKNOWN."""
    spec = MAPPED.get(int(market_id))
    if spec is not None:
        return "MAPPED", spec.category, None
    name = market_name or ""
    for reason, pat in OUT_OF_SCOPE_PATTERNS:
        if pat.search(name):
            return "OUT_OF_SCOPE", None, reason
    return "UNKNOWN", None, None


@dataclass(frozen=True)
class CanonicalOdd:
    superbet_market_id: int
    market_category: str
    canonical_market_id: str
    selection_id: str
    selection_name: str
    line: float | None
    odd: float
    status: str
    complete_n: int | None
    identity_confidence: str | None = None  # STRONG (player_id) | NAME_ONLY
    side_source: str | None = None  # NAME | ID


@dataclass(frozen=True)
class NormalizeIssue:
    market_id: int
    kind: str  # UNKNOWN_SELECTION | ODD_LE_1 | MISSING_LINE | MISSING_PRICE | PLAYER_IDENTITY_MISSING | MARKET_MAPPING_CONFLICT
    detail: str


def _parse_line(raw: dict, name: str) -> float | None:
    spec = raw.get("specifiers") or {}
    for k in ("total", "hcp", "handicap"):
        if k in spec:
            try:
                return float(str(spec[k]).replace(",", "."))
            except ValueError:
                pass
    m = _LINE_RE.search(name)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


def _team_side(market_name: str, home: str, away: str, fallback: str | None) -> tuple[str | None, str]:
    """Lado pelo nome do mercado ('Al Ahly - Total de Gols', 'Total de Finalizações Kosovo')."""
    n, h, a = _norm(market_name), _norm(home), _norm(away)
    hit_h = bool(h) and (n.startswith(h) or n.endswith(h) or f" {h} " in f" {n} ")
    hit_a = bool(a) and (n.startswith(a) or n.endswith(a) or f" {a} " in f" {n} ")
    if hit_h and not hit_a:
        return "HOME", "NAME"
    if hit_a and not hit_h:
        return "AWAY", "NAME"
    if hit_h and hit_a:
        # ambos aparecem (ex.: nomes contidos um no outro) → não decidir pelo nome
        return fallback, "ID" if fallback else "AMBIGUOUS"
    return fallback, "ID" if fallback else "AMBIGUOUS"


def normalize(raw: dict, home: str, away: str) -> CanonicalOdd | NormalizeIssue | None:
    """Uma odd Superbet → CanonicalOdd, NormalizeIssue (vai para quarentena) ou None (fora de escopo/unknown —
    o chamador regista o marketId no registo de mapeamento)."""
    try:
        mid = int(raw.get("marketId", -1))
    except (TypeError, ValueError):
        return NormalizeIssue(-1, "MARKET_MAPPING_CONFLICT", f"marketId inválido: {raw.get('marketId')!r}")
    spec = MAPPED.get(mid)
    if spec is None:
        return None
    price = raw.get("price")
    if price is None:
        return NormalizeIssue(mid, "MISSING_PRICE", "odd sem price")
    try:
        odd = float(price)
    except (TypeError, ValueError):
        return NormalizeIssue(mid, "MISSING_PRICE", f"price não numérico: {price!r}")
    status = str(raw.get("status") or "active").lower()
    if odd <= 1.0:
        if status != "active":
            return None  # seleção bloqueada/suspensa (a Superbet publica price=1, status=block): não é odd
        return NormalizeIssue(mid, "ODD_LE_1", f"odd {odd} ≤ 1 com status active")
    name = str(raw.get("name") or "").strip()
    market_name = str(raw.get("marketName") or "")
    code = str(raw.get("code") or "")
    lname = _norm(name)
    lhome, laway = _norm(home), _norm(away)
    specifiers = raw.get("specifiers") or {}

    sel: str | None = None
    line: float | None = None
    side: str | None = None
    side_source: str | None = None
    identity: str | None = None

    if spec.kind == "3WAY":
        sel = {"1": "HOME", "0": "DRAW", "x": "DRAW", "2": "AWAY"}.get(code) or {"1": "HOME", "x": "DRAW", "2": "AWAY"}.get(lname)
        if sel is None:
            sel = "HOME" if lname == lhome else "AWAY" if lname == laway else "DRAW" if lname in ("empate", "x") else None
    elif spec.kind == "DC":
        sel = {"10": "HOME_DRAW", "02": "DRAW_AWAY", "12": "HOME_AWAY"}.get(code)
        if sel is None:
            if "1 ou empate" in lname or lname == "1x":
                sel = "HOME_DRAW"
            elif "empate ou 2" in lname or lname == "x2":
                sel = "DRAW_AWAY"
            elif "1 ou 2" in lname or lname == "12":
                sel = "HOME_AWAY"
    elif spec.kind == "2WAY_TEAM":
        sel = {"1": "HOME", "2": "AWAY"}.get(code)
        if sel is None:
            sel = "HOME" if lname == lhome else "AWAY" if lname == laway else None
    elif spec.kind == "YES_NO":
        sel = {"1": "YES", "2": "NO"}.get(code) or {"sim": "YES", "nao": "NO"}.get(lname)
    elif spec.kind in ("OU", "TEAM_OU"):
        if lname.startswith("mais de") or lname.startswith("acima") or code.endswith("+"):
            sel = "OVER"
        elif lname.startswith("menos de") or lname.startswith("abaixo") or code.endswith("-"):
            sel = "UNDER"
        line = _parse_line(raw, name)
        if line is None:
            return NormalizeIssue(mid, "MISSING_LINE", f"sem linha em {name!r}")
        if spec.kind == "TEAM_OU":
            side, side_source = _team_side(market_name, home, away, spec.side)
            if side is None:
                return NormalizeIssue(mid, "MARKET_MAPPING_CONFLICT", f"lado indeterminado em {market_name!r}")
    elif spec.kind in ("PLAYER_YES", "PLAYER_OU"):
        pid = specifiers.get("player_id")
        pname = specifiers.get("player_name") or specifiers.get("player")
        if pid:
            sel, identity = f"player:{pid}", "STRONG"
        elif pname:
            sel, identity = f"player:{_norm(str(pname))}", "NAME_ONLY"
        else:
            return NormalizeIssue(mid, "PLAYER_IDENTITY_MISSING", f"sem specifier de jogador em {name!r}")
        if spec.kind == "PLAYER_OU":
            line = _parse_line(raw, name)
            if line is None:
                return NormalizeIssue(mid, "MISSING_LINE", f"sem linha em {name!r}")
            if not ("mais de" in lname or "acima" in lname):
                return NormalizeIssue(mid, "UNKNOWN_SELECTION", f"seleção de jogador sem 'mais de': {name!r}")
            sel = f"{sel}:OVER"

    if sel is None:
        return NormalizeIssue(mid, "UNKNOWN_SELECTION", f"seleção não reconhecida: {name!r} (code={code!r})")
    return CanonicalOdd(
        superbet_market_id=mid, market_category=spec.category,
        canonical_market_id=canonical_market_id(spec.category, line, side),
        selection_id=sel, selection_name=name, line=line, odd=odd, status=status, complete_n=spec.complete_n,
        identity_confidence=identity, side_source=side_source,
    )


__all__ = [
    "CATEGORY_LABELS", "CATEGORY_ORDER", "RESEARCH_FAMILY", "MAPPED", "MarketSpec", "CanonicalOdd", "NormalizeIssue",
    "classify_market", "normalize", "canonical_market_id", "canonical_selection_id",
]
