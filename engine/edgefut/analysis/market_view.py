"""MARKET vs EDGEFUT (§36–§37): probabilidade híbrida (market-aware) por seleção a partir do artefato
congelado, com o estado de validação **honesto** (discovery / holdout) — nunca a partir de um modelo
ajustado ao vivo.

* `hybrid_prob` = challenger selecionado no artefato (blend por defeito; logístico/residual quando
  as features de contexto existem) aplicado a p_mercado (justa, agora) e p_edgefut (agora);
* `disagreement_pp` = p_edgefut − p_mercado (divergência de modelo, **não** é edge);
* `residual_edge_pp` = p_híbrido − p_mercado; só é "validado" quando o holdout congelado confirmou o
  challenger neste mercado. Caso contrário é hipótese e é mostrado como tal.

Regra absoluta: closing line nunca entra aqui (o artefato não a conhece — ver
`test_market_aware_never_uses_closing_line`).
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ValidationRun
from ..validation.market_aware import load_active_artifact, predict_frozen

log = logging.getLogger(__name__)

NOT_VALIDATED = "NÃO VALIDADO"


def _latest_status(session: Session) -> dict:
    """Estado de validação por mercado: holdout primário (não repetido) > discovery > nada."""
    out: dict = {"source": None, "markets": {}, "model_hash": None}
    hold = None
    for r in session.execute(select(ValidationRun).where(ValidationRun.kind == "market_aware_holdout").order_by(ValidationRun.created_at.desc()).limit(20)).scalars():
        s = r.summary or {}
        if r.detail and not s.get("repeated") and not s.get("error"):
            hold = r
            break
    if hold is not None:
        out["source"] = "holdout"
        out["model_hash"] = (hold.summary or {}).get("model_hash")
        out["markets"] = {m: v.get("status") for m, v in ((hold.summary or {}).get("verdict") or {}).items()}
        return out
    disc = None
    for r in session.execute(select(ValidationRun).where(ValidationRun.kind == "market_aware").order_by(ValidationRun.created_at.desc()).limit(10)).scalars():
        if r.detail:
            disc = r
            break
    if disc is not None:
        out["source"] = "discovery"
        out["model_hash"] = (disc.summary or {}).get("model_hash")
        out["markets"] = {m: v.get("status") for m, v in ((disc.summary or {}).get("verdict") or {}).items()}
    return out


_status_cache: tuple[datetime, dict] | None = None


def validation_status(session: Session, max_age_s: int = 300) -> dict:
    global _status_cache
    now = datetime.utcnow()
    if _status_cache is not None and (now - _status_cache[0]).total_seconds() < max_age_s:
        return _status_cache[1]
    st = _latest_status(session)
    _status_cache = (now, st)
    return st


def invalidate_status_cache() -> None:
    global _status_cache
    _status_cache = None


def _feature_row(*, dataset: str | None, neutral: bool, elo_diff: float | None, att_diff: float | None, def_diff: float | None, home_advantage: float | None,
                 form5_home: float | None, form5_away: float | None, gd5_home: float | None, gd5_away: float | None, min_team_sample: int | None, agreement_pp: float | None, kickoff: datetime) -> pd.DataFrame:
    return pd.DataFrame([{
        "dataset": dataset or "UNKNOWN", "neutral": bool(neutral), "elo_diff": elo_diff, "att_diff": att_diff, "def_diff": def_diff, "home_advantage": home_advantage,
        "form5_home": form5_home, "form5_away": form5_away, "gd5_home": gd5_home, "gd5_away": gd5_away, "min_team_sample": min_team_sample, "agreement_pp": agreement_pp,
        "month": kickoff.month, "dow": kickoff.weekday(), "days_into_group": np.nan,
    }])


def market_view(
    session: Session,
    *,
    p_market_1x2: tuple[float, float, float] | None,
    p_edgefut_1x2: tuple[float, float, float] | None,
    p_market_over25: float | None,
    p_edgefut_over25: float | None,
    features: dict,
) -> dict | None:
    """Devolve o bloco MARKET vs EDGEFUT para o evento, ou None quando não há artefato congelado."""
    art = load_active_artifact()
    if not art:
        return None
    status = validation_status(session)
    feat = _feature_row(**features)
    imputed = [k for k, v in features.items() if v is None and k not in ("dataset",)]
    out: dict = {
        "model_hash": art.get("model_hash"), "config_hash": art.get("config_hash"), "dataset_version": art.get("dataset_version"), "frozen_at": art.get("frozen_at"),
        "validation_source": status.get("source"), "features_imputed": imputed, "markets": {},
        "note": "Probabilidade híbrida do artefato congelado (market-aware). Divergência ≠ edge. Residual edge só conta como validado após holdout congelado positivo neste mercado.",
    }
    for market, pm, pe in (("1X2", p_market_1x2, p_edgefut_1x2), ("OU25", p_market_over25, p_edgefut_over25)):
        spec = art["markets"].get(market)
        if not spec or pm is None or pe is None:
            continue
        if market == "1X2":
            PM, PE = np.array([pm], float), np.array([pe], float)
        else:
            PM, PE = np.array([[1 - pm, pm]], float), np.array([[1 - pe, pe]], float)
        try:
            preds = predict_frozen(art, market, PM, PE, feat)
        except Exception as exc:  # noqa: BLE001 — a análise nunca cai por causa do bloco híbrido
            log.warning("market_view: predict_frozen falhou (%s): %s", market, exc)
            preds = {"blend": predict_frozen(art, market, PM, PE, None)["blend"]}
        selected = spec.get("selected") or "blend"
        P = preds.get(selected, preds["blend"])[0]
        st = status["markets"].get(market)
        validated = st == "CHALLENGER BEATS MARKET (OOS)" and status.get("source") == "holdout"
        if market == "1X2":
            sels = {"HOME": 0, "DRAW": 1, "AWAY": 2}
            rows = {k: {"market": round(float(PM[0, i]), 4), "edgefut": round(float(PE[0, i]), 4), "hybrid": round(float(P[i]), 4),
                        "disagreement_pp": round(float(PE[0, i] - PM[0, i]) * 100, 2), "residual_edge_pp": round(float(P[i] - PM[0, i]) * 100, 2)} for k, i in sels.items()}
        else:
            rows = {"OVER": {"market": round(float(pm), 4), "edgefut": round(float(pe), 4), "hybrid": round(float(P[1]), 4), "disagreement_pp": round((pe - pm) * 100, 2), "residual_edge_pp": round(float(P[1] - pm) * 100, 2)},
                    "UNDER": {"market": round(1 - pm, 4), "edgefut": round(1 - pe, 4), "hybrid": round(float(P[0]), 4), "disagreement_pp": round((pm - pe) * 100, 2), "residual_edge_pp": round(float(P[0] - (1 - pm)) * 100, 2)}}
        out["markets"][market] = {
            "challenger": selected, "alpha": spec.get("alpha"), "all_challengers": {k: [round(float(x), 4) for x in v[0]] for k, v in preds.items()},
            "validation_status": st or NOT_VALIDATED, "residual_validated": validated, "selections": rows,
            "interpretation": _interpret(rows, validated, st),
        }
    return out


def _interpret(rows: dict, validated: bool, status: str | None) -> str:
    best = max(rows.items(), key=lambda kv: kv[1]["residual_edge_pp"])
    k, r = best
    if not validated:
        base = f"Market-aware {status or NOT_VALIDATED}: o híbrido é hipótese, não edge validado."
    else:
        base = "Challenger market-aware validado no holdout congelado neste mercado."
    if abs(r["disagreement_pp"]) < 2:
        return f"{base} EdgeFut e mercado praticamente concordam ({k}: {r['disagreement_pp']:+.1f} pp)."
    return f"{base} Maior divergência em {k}: EdgeFut {r['disagreement_pp']:+.1f} pp vs mercado; residual (híbrido − mercado) {r['residual_edge_pp']:+.1f} pp."


def selection_lookup(view: dict | None, market_key: str, selection_key: str, line: float | None) -> dict | None:
    """Linha do bloco híbrido para uma seleção (1X2 e TOTAL_GOALS 2,5 apenas)."""
    if not view:
        return None
    if market_key == "1X2":
        m = view["markets"].get("1X2")
        return {**m["selections"][selection_key], "validated": m["residual_validated"], "status": m["validation_status"]} if m and selection_key in m["selections"] else None
    if market_key == "TOTAL_GOALS" and line is not None and abs(line - 2.5) < 1e-9:
        m = view["markets"].get("OU25")
        return {**m["selections"][selection_key], "validated": m["residual_validated"], "status": m["validation_status"]} if m and selection_key in m["selections"] else None
    return None


__all__ = ["market_view", "selection_lookup", "validation_status", "invalidate_status_cache", "NOT_VALIDATED"]
