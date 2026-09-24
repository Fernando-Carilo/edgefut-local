"""international-strength-v1 — strength-v2 aplicado a seleções, com o contexto que o
futebol internacional exige e que o modelo de clubes ignora:

* tipo de partida: amistosos entram no ajuste com peso reduzido (`FRIENDLY_WEIGHT`) e a
  previsão de um amistoso recebe uma nota de menor confiança (elencos experimentais);
* campo neutro real (`neutral` do dataset) → HA = 1 no ajuste e `home_adv_weight = 0` na
  previsão, decidido pelo VenueResolver — nunca inventado aqui;
* vantagem de mandante por **tipo de torneio** (amistoso, eliminatórias, torneio final,
  Nations League, continental) com fallback para o conjunto quando N < 100;
* janela longa (o pipeline entrega 3 anos de INTL; o ELO usa desde 2000) com half-life do
  strength-v2 escolhido por walk-forward — seleções jogam pouco, cortar demais deixa
  equipes sem amostra.

Não há preço histórico no INTL → o replay mede só capacidade preditiva (Brier/LogLoss),
nunca ROI. Em produção o estado dessas previsões segue MODEL_ONLY até existir odd válida.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..core import versions
from ..domain.analysis import GoalsModelOutput
from .strength_v2 import StrengthV2Params, fit_strength_v2, strength_v2_output

FRIENDLY_WEIGHT = 0.6
MIN_TEAM_MATCHES = 8  # abaixo disso a previsão sai com nota de histórico curto

TOURNAMENT_TYPES = ("FRIENDLY", "QUALIFIER", "TOURNAMENT", "NATIONS_LEAGUE", "CONTINENTAL", "OTHER")


def tournament_type(name: str | None) -> str:
    c = (name or "").lower()
    if not c:
        return "OTHER"
    if "friendly" in c or "amistoso" in c:
        return "FRIENDLY"
    if "qualif" in c or "eliminat" in c:
        return "QUALIFIER"
    if "nations league" in c:
        return "NATIONS_LEAGUE"
    if "world cup" in c or "copa do mundo" in c:
        return "TOURNAMENT"
    if any(k in c for k in ("euro", "copa américa", "copa america", "african cup", "africa cup", "asian cup", "gold cup", "cosafa")):
        return "CONTINENTAL"
    return "OTHER"


def match_weights(df: pd.DataFrame, friendly_weight: float = FRIENDLY_WEIGHT) -> np.ndarray:
    col = df["competition"] if "competition" in df else df.get("tournament")
    if col is None:
        return np.ones(len(df))
    types = col.astype(str).map(tournament_type)
    return np.where(types == "FRIENDLY", friendly_weight, 1.0)


def fit_international(df: pd.DataFrame, *, reference_date: datetime | None = None, half_life_days=None, friendly_weight: float = FRIENDLY_WEIGHT) -> StrengthV2Params | None:
    if df is None or df.empty:
        return None
    d = df.copy()
    d["tournament_type"] = (d["competition"] if "competition" in d else d.get("tournament", pd.Series([""] * len(d)))).astype(str).map(tournament_type)
    kwargs = {} if half_life_days is None else {"half_life_days": half_life_days}
    return fit_strength_v2(d, reference_date=reference_date, match_weights=match_weights(d, friendly_weight), group_col="tournament_type", **kwargs)


def international_output(params: StrengthV2Params | None, home: str | None, away: str | None, home_adv_weight: float, competition_name: str | None) -> GoalsModelOutput:
    ttype = tournament_type(competition_name)
    ha, ha_src = params.group_home_advantage(ttype) if params is not None else (None, "")
    out = strength_v2_output(params, home, away, home_adv_weight, home_advantage=ha)
    out.model_version = versions.INTL_STRENGTH
    if not out.available or params is None:
        return out
    notes: list[str] = []
    if ttype == "FRIENDLY":
        notes.append("Amistoso: elencos experimentais; confiança reduzida.")
    if home and away:
        short = [t for t in (home, away) if params.ratings[t].matches < MIN_TEAM_MATCHES]
        if short:
            notes.append(f"Histórico curto ({', '.join(short)} < {MIN_TEAM_MATCHES} jogos); rating encolhido para a média.")
    notes.append(f"HA {ha:.3f} ({ha_src}).")
    out.note = " ".join(notes)
    return out


__all__ = ["fit_international", "international_output", "tournament_type", "match_weights", "FRIENDLY_WEIGHT", "TOURNAMENT_TYPES"]
