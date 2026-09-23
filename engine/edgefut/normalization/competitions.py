"""Mapeamento competição Superbet → dataset histórico."""

from __future__ import annotations

from dataclasses import dataclass

# tournamentId Superbet → código football-data
TOURNAMENT_TO_DATASET: dict[int, str] = {
    106: "E0",
    27: "E1",
    98: "SP1",
    191: "SP2",
    104: "I1",
    244: "I2",
    245: "D1",
    50: "D2",
    100: "F1",
    143: "F2",
    256: "N1",
    142: "P1",
    324: "B1",
    323: "T1",
    4: "SC0",
    1698: "BRA",
    897: "USA",
}

# categoryId Superbet que agrupa seleções
NATIONAL_TEAM_CATEGORY_IDS = {102}
# tournamentIds de seleções conhecidos (Amistoso Internacional etc.)
NATIONAL_TEAM_TOURNAMENT_IDS = {292}
NATIONAL_KEYWORDS = (
    "liga das nações", "nations league", "amistoso internacional", "copa do mundo",
    "eliminatórias", "qualificações", "euro 20", "copa américa", "copa africana",
    "copa asiática", "copa do golfo", "concacaf", "asean cup",
)
INTERNATIONAL_CLUB_CATEGORY_IDS = {31}


@dataclass
class CompetitionProfile:
    tournament_id: int | None
    name: str | None
    category_id: int | None
    category_name: str | None
    dataset_codes: list[str]
    is_national_teams: bool
    is_international_clubs: bool
    is_womens: bool
    supported: bool
    reason: str | None = None


def classify_competition(
    tournament_id: int | None,
    name: str | None,
    category_id: int | None,
    category_name: str | None,
    womens_hint: bool = False,
) -> CompetitionProfile:
    lname = (name or "").lower()
    womens = womens_hint or "(f)" in lname or "feminin" in lname
    is_national = (
        category_id in NATIONAL_TEAM_CATEGORY_IDS
        or tournament_id in NATIONAL_TEAM_TOURNAMENT_IDS
        or any(k in lname for k in NATIONAL_KEYWORDS)
    )
    is_intl_clubs = category_id in INTERNATIONAL_CLUB_CATEGORY_IDS

    if womens:
        return CompetitionProfile(
            tournament_id, name, category_id, category_name, [], is_national, is_intl_clubs, True,
            False, "Sem fonte pública de histórico para futebol feminino nesta iteração",
        )
    if is_national:
        return CompetitionProfile(
            tournament_id, name, category_id, category_name, ["INTL"], True, False, False, True
        )
    ds = TOURNAMENT_TO_DATASET.get(tournament_id or -1)
    if ds:
        return CompetitionProfile(
            tournament_id, name, category_id, category_name, [ds], False, False, False, True
        )
    if is_intl_clubs:
        # clubes de ligas diferentes: usamos os datasets de liga de cada time (resolvido por time)
        return CompetitionProfile(
            tournament_id, name, category_id, category_name,
            list(dict.fromkeys(TOURNAMENT_TO_DATASET.values())), False, True, False, True,
            "Competição internacional de clubes: força vem das ligas domésticas",
        )
    return CompetitionProfile(
        tournament_id, name, category_id, category_name, [], False, False, False, False,
        "Competição sem dataset histórico mapeado",
    )
