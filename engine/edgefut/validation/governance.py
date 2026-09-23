"""Champion / Challenger — governança de modelos.

* O **campeão** é o consenso que decide em produção (`model_registry.role = champion`
  para a chave `ensemble` ou `ensemble_v2`). Default: `ensemble` (v1).
* **Challengers** rodam em paralelo, aparecem na comparação de modelos e no replay, mas
  não entram no consenso.
* Regra de promoção (`evaluate_promotion`), avaliada sobre um relatório de replay
  out-of-sample — ROI **não** é critério:
    1. Brier OOS melhor que o campeão com IC 95% da diferença (bootstrap pareado) < 0;
    2. LogLoss não pior (IC da diferença não inteiramente > 0);
    3. calibração (ECE) não pior além de `ECE_TOLERANCE`;
    4. amostra ≥ `sample_moderate_min` partidas pareadas;
    5. estabilidade: melhor em ≥ 60 % das janelas.
* A promoção em si (`promote`) é uma ação explícita (endpoint / operador) registrada em
  `validation_run(kind="promotion")` — nunca automática.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db.models import ModelRegistry, ValidationRun
from .bootstrap import paired_bootstrap_diff

ECE_TOLERANCE = 0.005
STABILITY_MIN = 0.60
CONSENSUS_KEYS = ("ensemble", "ensemble_v2")


def current_champion(session: Session | None) -> str:
    if session is None:
        return "ensemble"
    try:
        rows = session.execute(select(ModelRegistry).where(ModelRegistry.model_id == "ensemble", ModelRegistry.active.is_(True))).scalars().all()
    except Exception:  # noqa: BLE001 — tabela ainda sem coluna/linha: campeão default
        return "ensemble"
    for r in rows:
        if r.parameters and r.parameters.get("champion_consensus") in CONSENSUS_KEYS:
            return str(r.parameters["champion_consensus"])
    return "ensemble"


def evaluate_promotion(report: dict, challenger: str, champion: str) -> dict:
    """Aplica a regra sobre um relatório de `run_replay` (usa `overall`, `per_window`)."""
    overall = report.get("overall") or {}
    a, b = overall.get(challenger), overall.get(champion)
    checks: dict[str, dict] = {}
    if not a or not b or not a.get("brier") or not b.get("brier"):
        return {"challenger": challenger, "champion": champion, "eligible": False, "verdict": "INSUFFICIENT DATA", "checks": checks, "reason": "modelo ausente do replay"}

    # diferenças pareadas por janela reconstruídas do relatório (per_window carrega Brier médio por janela por modelo)
    per_window = [w for w in report.get("per_window", []) if challenger in (w.get("brier") or {}) and champion in (w.get("brier") or {})]
    wins = sum(1 for w in per_window if w["brier"][challenger] < w["brier"][champion])
    n_w = len(per_window)
    n = min(a["n"], b["n"])
    # ICs da diferença: replay guarda vs_baseline só contra baselines; aqui usamos o IC de cada Brier
    # (conservador: exige separação dos ICs) quando não há diferença pareada explícita.
    paired = (report.get("pairwise") or {}).get(f"{challenger}|{champion}")
    if paired:
        d_brier, d_ll = paired["delta_brier_ci"], paired["delta_logloss_ci"]
        brier_ok = d_brier["high"] is not None and d_brier["high"] < 0
        ll_ok = not (d_ll["low"] is not None and d_ll["low"] > 0)
    else:
        brier_ok = a["brier"]["high"] is not None and b["brier"]["low"] is not None and a["brier"]["high"] < b["brier"]["low"]
        ll_ok = not (a["log_loss"]["low"] is not None and b["log_loss"]["high"] is not None and a["log_loss"]["low"] > b["log_loss"]["high"])
    ece_a, ece_b = a.get("ece"), b.get("ece")
    ece_ok = ece_a is None or ece_b is None or ece_a <= ece_b + ECE_TOLERANCE
    sample_ok = n >= settings.sample_moderate_min
    stable_ok = n_w > 0 and wins / n_w >= STABILITY_MIN
    checks = {
        "brier_better": {"ok": brier_ok, "challenger": a["brier"]["point"], "champion": b["brier"]["point"], "paired": bool(paired)},
        "logloss_not_worse": {"ok": ll_ok, "challenger": a["log_loss"]["point"], "champion": b["log_loss"]["point"]},
        "calibration_not_worse": {"ok": ece_ok, "challenger_ece": ece_a, "champion_ece": ece_b, "tolerance": ECE_TOLERANCE},
        "sample_adequate": {"ok": sample_ok, "n": n, "min": settings.sample_moderate_min},
        "stable_across_windows": {"ok": stable_ok, "windows_better": wins, "windows_total": n_w, "min_share": STABILITY_MIN},
    }
    eligible = all(c["ok"] for c in checks.values())
    if eligible:
        verdict = "PROMOTE"
    elif not sample_ok:
        verdict = "INSUFFICIENT DATA"
    elif brier_ok and not stable_ok:
        verdict = "PROMISING · UNSTABLE"
    else:
        verdict = "KEEP CHAMPION"
    return {"challenger": challenger, "champion": champion, "eligible": eligible, "verdict": verdict, "checks": checks, "roi_is_not_a_criterion": True}


def pairwise_from_preds(preds_by_model: dict[str, list], a: str, b: str) -> dict | None:
    """Usado pelo replay para gravar a diferença pareada challenger × campeão."""
    pa, pb = preds_by_model.get(a), preds_by_model.get(b)
    if not pa or not pb:
        return None
    bm = {(r.dataset, r.mid): r for r in pb}
    pairs = [(r, bm[(r.dataset, r.mid)]) for r in pa if (r.dataset, r.mid) in bm]
    if len(pairs) < 30:
        return None
    def brier(r):
        return float(sum((r.p1x2[k] - (1.0 if k == r.outcome else 0.0)) ** 2 for k in range(3)))
    def ll(r):
        return -float(np.log(max(1e-9, r.p1x2[r.outcome])))
    return {
        "n": len(pairs),
        "delta_brier_ci": paired_bootstrap_diff(np.array([brier(x) for x, _ in pairs]), np.array([brier(y) for _, y in pairs])).to_dict(),
        "delta_logloss_ci": paired_bootstrap_diff(np.array([ll(x) for x, _ in pairs]), np.array([ll(y) for _, y in pairs])).to_dict(),
    }


def promote(session: Session, consensus_key: str, *, reason: str, evaluation: dict | None = None, actor: str = "user") -> dict:
    if consensus_key not in CONSENSUS_KEYS:
        raise ValueError(f"consenso desconhecido: {consensus_key}")
    rows = session.execute(select(ModelRegistry).where(ModelRegistry.model_id == "ensemble", ModelRegistry.active.is_(True))).scalars().all()
    previous = current_champion(session)
    for r in rows:
        params = dict(r.parameters or {})
        params["champion_consensus"] = consensus_key
        params["champion_since"] = datetime.utcnow().isoformat()
        r.parameters = params
    for r in session.execute(select(ModelRegistry).where(ModelRegistry.model_id.in_(("poisson_v2", "strength_v2")))).scalars():
        r.role = "champion" if consensus_key == "ensemble_v2" else "challenger"
    session.add(ValidationRun(kind="promotion", request={"consensus": consensus_key, "actor": actor, "reason": reason}, summary={"from": previous, "to": consensus_key}, detail=evaluation))
    session.commit()
    return {"ok": True, "from": previous, "to": consensus_key}


__all__ = ["current_champion", "evaluate_promotion", "promote", "pairwise_from_preds", "CONSENSUS_KEYS"]
