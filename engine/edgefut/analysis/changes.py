"""WHY MODEL CHANGED (§39) — compara a análise atual com o último snapshot do evento.

Nada aqui altera a análise: é uma explicação. Drivers possíveis (ordenados por relevância):

* ``ODDS_MOVED``        — o preço da Superbet mudou → edge/estado mudam sem o modelo mudar;
* ``NEW_MATCHES``       — entrou jogo novo na amostra de um dos times;
* ``RATINGS_CHANGED``   — ataque/defesa strength-v2 ou ELO mudaram (mesmo sem jogo novo,
                           o decay temporal e o pool da competição mexem nas forças);
* ``MODEL_VERSION``     — versão de algum modelo/pipeline mudou;
* ``CHAMPION_CHANGED``  — o consenso campeão foi trocado (governança);
* ``CALIBRATION``       — a probabilidade calibrada divergiu da RAW de forma diferente.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import PredictionSnapshot
from ..domain.analysis import MatchAnalysis

MIN_DELTA_PP = 1.0  # abaixo disso não reportamos mudança de probabilidade
RATING_DELTA = 0.02  # variação relativa de attack/defense considerada relevante
ELO_DELTA = 5.0

DRIVER_TEXT = {
    "ODDS_MOVED": "Odds da Superbet mudaram desde a última análise: edge e estado mudam mesmo com o modelo parado.",
    "NEW_MATCHES": "Novo(s) jogo(s) entraram na amostra de pelo menos um dos times.",
    "RATINGS_CHANGED": "Forças ataque/defesa (strength-v2) ou ELO mudaram — decay temporal e pool da competição reestimados.",
    "MODEL_VERSION": "Versão de modelo/pipeline diferente da usada no snapshot anterior.",
    "CHAMPION_CHANGED": "O consenso campeão mudou (governança champion/challenger).",
    "CALIBRATION": "Calibração histórica aplicada de forma diferente (RAW → calibrada).",
    "NONE": "Sem mudança relevante: probabilidades e preços estáveis desde o snapshot anterior.",
}


def _key(r: dict) -> str:
    return f"{r.get('market_key')}|{r.get('selection_key')}|{r.get('line')}"


def _ratings_delta(prev_feat: dict | None, team) -> bool:
    if not prev_feat:
        return False
    pv2, cv2 = prev_feat.get("ratings_v2") or {}, team.ratings_v2 or {}
    for k in ("attack", "defense"):
        a, b = pv2.get(k), cv2.get(k)
        if a and b and abs(float(a) - float(b)) / max(abs(float(a)), 1e-9) > RATING_DELTA:
            return True
    pe, ce = prev_feat.get("elo"), team.elo
    return pe is not None and ce is not None and abs(float(pe) - float(ce)) > ELO_DELTA


def why_model_changed(session: Session, event_id: int, a: MatchAnalysis) -> dict:
    prev = session.execute(
        select(PredictionSnapshot).where(PredictionSnapshot.event_id == event_id).order_by(PredictionSnapshot.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if prev is None:
        return {"status": "FIRST_ANALYSIS", "previous_snapshot_id": None, "drivers": [], "selections": [], "text": "Primeira análise deste evento: não há snapshot anterior para comparar."}

    feats = prev.features or {}
    probs = prev.probabilities or {}
    prev_recs = {_key(r): r for r in (prev.recommendations or [])}
    prev_odds: dict[str, float | None] = {}
    for m in prev.odds or []:
        for s in m.get("selections") or []:
            prev_odds[f"{m.get('market_key')}|{s.get('key')}|{m.get('line')}"] = s.get("price")

    drivers: list[str] = []
    odds_moved = 0
    sel_changes: list[dict] = []
    for r in a.recommendations:
        if r.market_key == "PLAYER_TO_SCORE":
            continue
        k = f"{r.market_key}|{r.selection_key}|{r.line}"
        p = prev_recs.get(k)
        odd_before = prev_odds.get(k, p.get("odd") if p else None)
        if odd_before and r.odd and abs(float(odd_before) - r.odd) >= 0.01:
            odds_moved += 1
        if p is None:
            continue
        prob_before = float(p.get("model_prob") or 0.0)
        delta_pp = round(100.0 * (r.model_prob - prob_before), 1)
        state_before = p.get("state") or p.get("status")
        if abs(delta_pp) >= MIN_DELTA_PP or state_before != r.state or (odd_before and r.odd and abs(float(odd_before) - r.odd) >= 0.05):
            sel_changes.append({
                "key": k, "market_label": r.market_label, "selection_name": r.selection_name, "line": r.line,
                "prob_before": round(prob_before, 4), "prob_after": r.model_prob, "delta_pp": delta_pp,
                "odd_before": odd_before, "odd_after": r.odd,
                "state_before": state_before, "state_after": r.state,
                "edge_before": p.get("edge_pp"), "edge_after": r.edge_pp,
            })

    if odds_moved:
        drivers.append("ODDS_MOVED")
    for side, team in (("home", a.home), ("away", a.away)):
        f = feats.get(side) or {}
        if f.get("sample_size") is not None and int(f["sample_size"]) != team.sample_size:
            if "NEW_MATCHES" not in drivers:
                drivers.append("NEW_MATCHES")
        elif _ratings_delta(f, team) and "RATINGS_CHANGED" not in drivers:
            drivers.append("RATINGS_CHANGED")
    if (prev.model_versions or {}) != a.model_versions or prev.model_version != a.pipeline_version:
        drivers.append("MODEL_VERSION")
    prev_champ = (probs.get("model_comparison") or {}).get("champion")
    if prev_champ and a.champion and prev_champ != a.champion:
        drivers.append("CHAMPION_CHANGED")
    if any(p.get("calibration_reliable") != r.calibration_reliable for r in a.recommendations for p in [prev_recs.get(f"{r.market_key}|{r.selection_key}|{r.line}")] if p):
        drivers.append("CALIBRATION")
    if not drivers and not sel_changes:
        drivers.append("NONE")

    sel_changes.sort(key=lambda c: -abs(c["delta_pp"]))
    biggest = sel_changes[0] if sel_changes else None
    parts = [DRIVER_TEXT[d] for d in drivers]
    if biggest:
        parts.append(f"Maior mudança: {biggest['market_label']} · {biggest['selection_name']} {biggest['prob_before']:.1%} → {biggest['prob_after']:.1%} ({biggest['delta_pp']:+.1f} pp), estado {biggest['state_before']} → {biggest['state_after']}.")
    age_h = (datetime.utcnow() - prev.created_at).total_seconds() / 3600 if prev.created_at else None
    return {
        "status": "COMPARED",
        "previous_snapshot_id": prev.id,
        "previous_at": prev.created_at.isoformat() if prev.created_at else None,
        "previous_age_hours": round(age_h, 1) if age_h is not None else None,
        "drivers": drivers,
        "odds_moved_selections": odds_moved,
        "selections": sel_changes[:12],
        "text": " ".join(parts),
    }


__all__ = ["why_model_changed", "DRIVER_TEXT"]
