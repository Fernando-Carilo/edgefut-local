"""Normalização de nomes de times (Superbet PT-BR → datasets)."""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

from .countries import COUNTRIES_PT_EN

# Superbet (normalizado) → football-data
CLUB_ALIASES: dict[str, str] = {
    "manchester city": "Man City",
    "manchester united": "Man United",
    "manchester utd": "Man United",
    "nottingham forest": "Nott'm Forest",
    "wolverhampton": "Wolves",
    "wolverhampton wanderers": "Wolves",
    "tottenham hotspur": "Tottenham",
    "tottenham": "Tottenham",
    "newcastle united": "Newcastle",
    "west ham united": "West Ham",
    "brighton hove albion": "Brighton",
    "brighton": "Brighton",
    "leeds united": "Leeds",
    "leicester city": "Leicester",
    "sheffield united": "Sheffield United",
    "sheffield utd": "Sheffield United",
    "ipswich town": "Ipswich",
    "luton town": "Luton",
    "afc bournemouth": "Bournemouth",
    "atletico madrid": "Ath Madrid",
    "atletico de madrid": "Ath Madrid",
    "athletic bilbao": "Ath Bilbao",
    "athletic club": "Ath Bilbao",
    "real sociedad": "Sociedad",
    "real betis": "Betis",
    "rayo vallecano": "Vallecano",
    "espanyol": "Espanol",
    "rcd espanyol": "Espanol",
    "celta vigo": "Celta",
    "celta de vigo": "Celta",
    "deportivo alaves": "Alaves",
    "real valladolid": "Valladolid",
    "cadiz": "Cadiz",
    "ud las palmas": "Las Palmas",
    "cd leganes": "Leganes",
    "inter": "Inter",
    "inter de milao": "Inter",
    "internazionale": "Inter",
    "ac milan": "Milan",
    "milan": "Milan",
    "hellas verona": "Verona",
    "bayern de munique": "Bayern Munich",
    "bayern munchen": "Bayern Munich",
    "bayern munich": "Bayern Munich",
    "borussia dortmund": "Dortmund",
    "bayer leverkusen": "Leverkusen",
    "rb leipzig": "RB Leipzig",
    "eintracht frankfurt": "Ein Frankfurt",
    "borussia monchengladbach": "M'gladbach",
    "borussia mgladbach": "M'gladbach",
    "monchengladbach": "M'gladbach",
    "vfl wolfsburg": "Wolfsburg",
    "vfb stuttgart": "Stuttgart",
    "sc freiburg": "Freiburg",
    "tsg hoffenheim": "Hoffenheim",
    "1899 hoffenheim": "Hoffenheim",
    "mainz 05": "Mainz",
    "fsv mainz 05": "Mainz",
    "fc augsburg": "Augsburg",
    "union berlin": "Union Berlin",
    "1 fc union berlin": "Union Berlin",
    "werder bremen": "Werder Bremen",
    "vfl bochum": "Bochum",
    "1 fc heidenheim": "Heidenheim",
    "fc st pauli": "St Pauli",
    "st pauli": "St Pauli",
    "holstein kiel": "Holstein Kiel",
    "1 fc koln": "FC Koln",
    "fc koln": "FC Koln",
    "colonia": "FC Koln",
    "hamburgo": "Hamburg",
    "hamburger sv": "Hamburg",
    "paris saint germain": "Paris SG",
    "psg": "Paris SG",
    "paris sg": "Paris SG",
    "olympique de marseille": "Marseille",
    "olympique marseille": "Marseille",
    "olympique lyon": "Lyon",
    "olympique lyonnais": "Lyon",
    "as monaco": "Monaco",
    "losc lille": "Lille",
    "ogc nice": "Nice",
    "stade rennais": "Rennes",
    "rc lens": "Lens",
    "fc nantes": "Nantes",
    "rc strasbourg": "Strasbourg",
    "toulouse fc": "Toulouse",
    "montpellier hsc": "Montpellier",
    "stade brestois": "Brest",
    "stade de reims": "Reims",
    "le havre ac": "Le Havre",
    "aj auxerre": "Auxerre",
    "angers sco": "Angers",
    "saint etienne": "St Etienne",
    "as saint etienne": "St Etienne",
    "fc lorient": "Lorient",
    "fc metz": "Metz",
    "psv": "PSV Eindhoven",
    "psv eindhoven": "PSV Eindhoven",
    "az alkmaar": "AZ Alkmaar",
    "az": "AZ Alkmaar",
    "fc twente": "Twente",
    "fc utrecht": "Utrecht",
    "sc heerenveen": "Heerenveen",
    "fc groningen": "Groningen",
    "nec nijmegen": "Nijmegen",
    "nec": "Nijmegen",
    "sparta rotterdam": "Sparta Rotterdam",
    "pec zwolle": "Zwolle",
    "sporting cp": "Sp Lisbon",
    "sporting lisboa": "Sp Lisbon",
    "sporting": "Sp Lisbon",
    "sc braga": "Sp Braga",
    "braga": "Sp Braga",
    "sl benfica": "Benfica",
    "fc porto": "Porto",
    "vitoria guimaraes": "Guimaraes",
    "vitoria de guimaraes": "Guimaraes",
    "fc famalicao": "Famalicao",
    "gil vicente": "Gil Vicente",
    "rio ave": "Rio Ave",
    "estoril praia": "Estoril",
    "casa pia": "Casa Pia",
    "fc arouca": "Arouca",
    "moreirense": "Moreirense",
    "santa clara": "Santa Clara",
    "club brugge": "Club Brugge",
    "kaa gent": "Gent",
    "krc genk": "Genk",
    "rsc anderlecht": "Anderlecht",
    "royal antwerp": "Antwerp",
    "standard liege": "Standard",
    "union saint gilloise": "St. Gilloise",
    "union sg": "St. Gilloise",
    "galatasaray": "Galatasaray",
    "fenerbahce": "Fenerbahce",
    "besiktas": "Besiktas",
    "trabzonspor": "Trabzonspor",
    "istanbul basaksehir": "Buyuksehyr",
    "basaksehir": "Buyuksehyr",
    "celtic": "Celtic",
    "rangers": "Rangers",
    "heart of midlothian": "Hearts",
    "hearts": "Hearts",
    "hibernian": "Hibernian",
    "dundee united": "Dundee United",
    "st mirren": "St Mirren",
    "st johnstone": "St Johnstone",
    "ross county": "Ross County",
}

_STRIP_TOKENS = {
    "fc", "sc", "cf", "afc", "ac", "as", "ss", "us", "sv", "fk", "bk", "if", "cd", "ud", "rcd", "sd",
    "club", "clube", "de", "do", "da", "del", "the", "1", "1899", "05",
}
_SUFFIX_RE = re.compile(
    r"\s*\((f|w|fem|feminino|women|sub[- ]?\d+|u\d+|res|reservas|ii|b)\)\s*$", re.IGNORECASE
)
_WOMEN_RE = re.compile(r"\((f|w|fem|feminino|women)\)", re.IGNORECASE)
_YOUTH_RE = re.compile(r"\((sub[- ]?\d+|u\d+|res|reservas|ii|b)\)|\bsub[- ]?\d+\b|\bu\d{2}\b", re.IGNORECASE)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def is_womens(name: str) -> bool:
    return bool(_WOMEN_RE.search(name))


def is_youth_or_reserve(name: str) -> bool:
    return bool(_YOUTH_RE.search(name))


def base_name(name: str) -> str:
    """Remove sufixos (F), (Sub-21) etc. preservando o nome-base para lookup."""
    return _SUFFIX_RE.sub("", name).strip()


def normalize(name: str) -> str:
    s = strip_accents(base_name(name)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = [t for t in s.split() if t not in _STRIP_TOKENS]
    return " ".join(tokens) if tokens else s.strip()


@dataclass
class TeamMatch:
    query: str
    canonical: str | None
    dataset_code: str | None
    confidence: float
    method: str  # curated | exact | fuzzy | unmatched
    is_national: bool = False
    is_womens: bool = False


def resolve_country(name: str) -> str | None:
    b = base_name(name)
    if b in COUNTRIES_PT_EN:
        return COUNTRIES_PT_EN[b]
    nb = normalize(name)
    for pt, en in COUNTRIES_PT_EN.items():
        if normalize(pt) == nb or normalize(en) == nb:
            return en
    return None


def resolve_team(
    name: str,
    dataset_code: str | None,
    candidates: list[str],
    *,
    is_national: bool = False,
    threshold: float = 0.88,
) -> TeamMatch:
    womens = is_womens(name)
    if is_national:
        en = resolve_country(name)
        if en and (not candidates or en in candidates):
            return TeamMatch(name, en, dataset_code, 0.98, "curated", True, womens)
        return TeamMatch(name, None, dataset_code, 0.0, "unmatched", True, womens)

    nq = normalize(name)
    alias = CLUB_ALIASES.get(nq)
    if alias and (not candidates or alias in candidates):
        return TeamMatch(name, alias, dataset_code, 0.97, "curated", False, womens)

    if not candidates:
        return TeamMatch(name, None, dataset_code, 0.0, "unmatched", False, womens)

    norm_candidates = {normalize(c): c for c in candidates}
    if nq in norm_candidates:
        return TeamMatch(name, norm_candidates[nq], dataset_code, 0.95, "exact", False, womens)

    best, best_score = None, 0.0
    for nc, original in norm_candidates.items():
        score = difflib.SequenceMatcher(None, nq, nc).ratio()
        # bônus quando um é prefixo/token do outro (ex.: "newcastle united" vs "newcastle")
        if nq.startswith(nc) or nc.startswith(nq):
            score = max(score, 0.90)
        if score > best_score:
            best, best_score = original, score
    if best is not None and best_score >= threshold:
        return TeamMatch(name, best, dataset_code, round(best_score * 0.9, 3), "fuzzy", False, womens)
    return TeamMatch(name, None, dataset_code, 0.0, "unmatched", False, womens)
