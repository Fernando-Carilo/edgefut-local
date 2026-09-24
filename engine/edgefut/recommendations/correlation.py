"""RecommendationCorrelationEngine — 7 recomendações não são 7 oportunidades.

Cada seleção pertence a um **cluster de tese** (`opportunity_cluster_id`): "o mandante vai
bem", "poucos gols", etc. Dentro de um cluster escolhe-se UMA seleção **primária** (maior
Opportunity Score entre as de melhor status; empate → ordem canônica do mercado) e as
demais viram **alternativas** (menor retorno / menor risco, mesma tese). Performance por
cluster conta só primárias — assim ROI/hit não são inflados por 5 variações do mesmo
palpite.

Clusters correlacionados entre si (ex.: HOME_TEAM_POSITIVE × HOME_GOALS_HIGH) partilham um
**grupo de tese**; a exposição do evento (Low/Medium/High) conta clusters acionáveis e
avisa quando dois deles são do mesmo grupo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.analysis import Recommendation

ACTIONABLE_STATES = ("VALUE", "VALUE_CANDIDATE", "RESEARCH_SIGNAL")

# ordem canônica para desempate na escolha da primária (mercado mais "puro" primeiro)
MARKET_PRIORITY = {
    "1X2": 0, "TOTAL_GOALS": 0, "BTTS": 1, "DRAW_NO_BET": 1, "DOUBLE_CHANCE": 2, "ASIAN_HANDICAP": 2, "HANDICAP": 3,
    "TEAM_TOTAL_HOME": 1, "TEAM_TOTAL_AWAY": 1, "TOTAL_CORNERS": 0, "TOTAL_CARDS": 0, "TOTAL_SHOTS": 0, "TOTAL_SHOTS_ON_TARGET": 1,
    "CORRECT_SCORE": 9, "FIRST_GOAL": 9,
}

CLUSTER_LABELS = {
    "HOME_TEAM_POSITIVE": "Mandante bem", "AWAY_TEAM_POSITIVE": "Visitante bem", "DRAW": "Empate", "NO_DRAW": "Sem empate",
    "GOALS_HIGH": "Muitos gols", "GOALS_LOW": "Poucos gols",
    "HOME_GOALS_HIGH": "Mandante marca", "HOME_GOALS_LOW": "Mandante marca pouco", "AWAY_GOALS_HIGH": "Visitante marca", "AWAY_GOALS_LOW": "Visitante marca pouco",
    "CORNERS_HIGH": "Muitos escanteios", "CORNERS_LOW": "Poucos escanteios", "CARDS_HIGH": "Muitos cartões", "CARDS_LOW": "Poucos cartões",
    "SHOTS_HIGH": "Muitas finalizações", "SHOTS_LOW": "Poucas finalizações", "MISC": "Outros",
}

# grupo de tese: clusters do mesmo grupo são correlacionados (mesma variável latente)
THESIS_GROUP = {
    "HOME_TEAM_POSITIVE": "RESULT", "AWAY_TEAM_POSITIVE": "RESULT", "DRAW": "RESULT", "NO_DRAW": "RESULT",
    "GOALS_HIGH": "GOALS", "GOALS_LOW": "GOALS", "HOME_GOALS_HIGH": "GOALS", "HOME_GOALS_LOW": "GOALS", "AWAY_GOALS_HIGH": "GOALS", "AWAY_GOALS_LOW": "GOALS",
    "CORNERS_HIGH": "CORNERS", "CORNERS_LOW": "CORNERS", "CARDS_HIGH": "CARDS", "CARDS_LOW": "CARDS", "SHOTS_HIGH": "SHOTS", "SHOTS_LOW": "SHOTS", "MISC": "MISC",
}
# clusters de grupos diferentes mas ainda correlacionados (mandante bem ⇄ mandante marca)
CROSS_CORRELATED = {
    frozenset({"HOME_TEAM_POSITIVE", "HOME_GOALS_HIGH"}), frozenset({"AWAY_TEAM_POSITIVE", "AWAY_GOALS_HIGH"}),
    frozenset({"HOME_TEAM_POSITIVE", "AWAY_GOALS_LOW"}), frozenset({"AWAY_TEAM_POSITIVE", "HOME_GOALS_LOW"}),
    frozenset({"GOALS_LOW", "DRAW"}), frozenset({"GOALS_HIGH", "NO_DRAW"}),
}


def cluster_for(market_key: str, selection_key: str, line: float | None) -> str:
    mk, sel = market_key, selection_key
    if mk == "1X2":
        return {"HOME": "HOME_TEAM_POSITIVE", "AWAY": "AWAY_TEAM_POSITIVE", "DRAW": "DRAW"}.get(sel, "MISC")
    if mk == "DOUBLE_CHANCE":
        return {"HOME_DRAW": "HOME_TEAM_POSITIVE", "DRAW_AWAY": "AWAY_TEAM_POSITIVE", "HOME_AWAY": "NO_DRAW"}.get(sel, "MISC")
    if mk == "DRAW_NO_BET":
        return {"HOME": "HOME_TEAM_POSITIVE", "AWAY": "AWAY_TEAM_POSITIVE"}.get(sel, "MISC")
    if mk in {"HANDICAP", "ASIAN_HANDICAP"}:
        # seleção do mandante com handicap: qualquer linha é aposta "mandante bem" (ou "visitante bem")
        if sel.startswith("HOME"):
            return "HOME_TEAM_POSITIVE"
        if sel.startswith("AWAY"):
            return "AWAY_TEAM_POSITIVE"
        return "MISC"
    if mk == "TOTAL_GOALS":
        return "GOALS_HIGH" if sel == "OVER" else "GOALS_LOW" if sel == "UNDER" else "MISC"
    if mk == "BTTS":
        return "GOALS_HIGH" if sel in {"YES", "SIM"} else "GOALS_LOW" if sel in {"NO", "NAO", "NÃO"} else "MISC"
    if mk == "TEAM_TOTAL_HOME":
        return "HOME_GOALS_HIGH" if sel == "OVER" else "HOME_GOALS_LOW"
    if mk == "TEAM_TOTAL_AWAY":
        return "AWAY_GOALS_HIGH" if sel == "OVER" else "AWAY_GOALS_LOW"
    if mk == "TOTAL_CORNERS":
        return "CORNERS_HIGH" if sel == "OVER" else "CORNERS_LOW"
    if mk == "TOTAL_CARDS":
        return "CARDS_HIGH" if sel == "OVER" else "CARDS_LOW"
    if mk in {"TOTAL_SHOTS", "TOTAL_SHOTS_ON_TARGET"}:
        return "SHOTS_HIGH" if sel == "OVER" else "SHOTS_LOW"
    return "MISC"


@dataclass
class ClusterView:
    cluster_id: str
    label: str
    thesis_group: str
    primary: Recommendation | None
    alternatives: list[Recommendation] = field(default_factory=list)
    state: str = "NO_BET"  # estado da primária

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id, "label": self.label, "thesis_group": self.thesis_group, "state": self.state,
            "primary": f"{self.primary.market_key}|{self.primary.selection_key}|{self.primary.line}" if self.primary else None,
            "alternatives": [f"{r.market_key}|{r.selection_key}|{r.line}" for r in self.alternatives],
        }


@dataclass
class ExposureView:
    level: str  # LOW | MEDIUM | HIGH
    actionable_clusters: list[str]
    correlated_pairs: list[list[str]]
    note: str

    def to_dict(self) -> dict:
        return {"level": self.level, "actionable_clusters": self.actionable_clusters, "correlated_pairs": self.correlated_pairs, "note": self.note}


_STATUS_RANK = {"RECOMMENDED": 0, "WATCH": 1, "NO_BET": 2}
_STATE_RANK = {"VALUE": 0, "VALUE_CANDIDATE": 1, "RESEARCH_SIGNAL": 2, "OBSERVATION": 3, "MODEL_ONLY": 4, "MARKET_OBSERVED": 5, "NO_BET": 6}


def _rank(r: Recommendation) -> tuple:
    return (_STATE_RANK.get(getattr(r, "state", None) or "NO_BET", 5), _STATUS_RANK.get(r.status, 2), -r.opportunity_score, MARKET_PRIORITY.get(r.market_key, 5), r.selection_key, r.line or 0)


def apply_clusters(recs: list[Recommendation], *, alternative_penalty: float = 0.85) -> tuple[list[ClusterView], ExposureView]:
    """Anota `cluster_id`, `is_primary`, `primary_of`; devolve clusters + exposição. Muta `recs`."""
    groups: dict[str, list[Recommendation]] = {}
    for r in recs:
        if r.market_key == "PLAYER_TO_SCORE":
            r.cluster_id = "MISC"
            r.is_primary = False
            continue
        r.cluster_id = cluster_for(r.market_key, r.selection_key, r.line)
        groups.setdefault(r.cluster_id, []).append(r)

    for members in groups.values():
        members.sort(key=_rank)
        primary = members[0]
        primary.is_primary = True
        primary.primary_of = None
        for alt in members[1:]:
            alt.is_primary = False
            alt.primary_of = f"{primary.market_key}|{primary.selection_key}|{primary.line}"
            if alt.status in ("RECOMMENDED", "WATCH"):
                alt.reasons = [*alt.reasons, f"ALTERNATIVE_OF:{primary.market_key}/{primary.selection_key}"]
                # Opportunity V3: penalidade de correlação — mesma tese, não é oportunidade extra
                alt.opportunity_adjustments = {**(alt.opportunity_adjustments or {}), "correlation_penalty": round(alt.opportunity_score * (alternative_penalty - 1.0), 1)}
                alt.opportunity_score = round(alt.opportunity_score * alternative_penalty, 1)
    return views_from(recs)


def views_from(recs: list[Recommendation]) -> tuple[list[ClusterView], ExposureView]:
    """Reconstrói clusters + exposição a partir de recomendações já anotadas (sem mutar)."""
    groups: dict[str, list[Recommendation]] = {}
    for r in recs:
        if r.market_key == "PLAYER_TO_SCORE" or not r.cluster_id:
            continue
        groups.setdefault(r.cluster_id, []).append(r)
    views: list[ClusterView] = []
    for cid, members in groups.items():
        primary = next((m for m in members if m.is_primary), None) or min(members, key=_rank)
        alts = [m for m in members if m is not primary]
        views.append(ClusterView(cluster_id=cid, label=CLUSTER_LABELS.get(cid, cid), thesis_group=THESIS_GROUP.get(cid, "MISC"), primary=primary, alternatives=alts, state=getattr(primary, "state", None) or "NO_BET"))
    views.sort(key=lambda v: (_STATE_RANK.get(v.state, 5), -(v.primary.opportunity_score if v.primary else 0)))

    actionable = [v.cluster_id for v in views if v.state in ACTIONABLE_STATES]
    pairs: list[list[str]] = []
    for i in range(len(actionable)):
        for j in range(i + 1, len(actionable)):
            a, b = actionable[i], actionable[j]
            if THESIS_GROUP.get(a) == THESIS_GROUP.get(b) or frozenset({a, b}) in CROSS_CORRELATED:
                pairs.append([a, b])
    n = len(actionable)
    level = "LOW" if n <= 1 else "MEDIUM" if n == 2 else "HIGH"
    if pairs and level == "LOW":
        level = "MEDIUM"
    note = (
        "Nenhuma oportunidade acionável." if n == 0 else
        f"{n} tese(s) acionável(is)" + (f"; {len(pairs)} par(es) correlacionado(s) — entradas simultâneas somam risco da mesma variável." if pairs else ".")
    )
    return views, ExposureView(level=level, actionable_clusters=actionable, correlated_pairs=pairs, note=note)


__all__ = ["apply_clusters", "views_from", "cluster_for", "ClusterView", "ExposureView", "CLUSTER_LABELS", "THESIS_GROUP", "ACTIONABLE_STATES"]
