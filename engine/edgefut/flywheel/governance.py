"""Governança da iteração 5 (§2, §31–§34, §62–§66).

- MODEL FREEZE: registo único com model_hash / config_hash / dataset_version / freeze_date; qualquer
  divergência posterior é reportada como DRIFTED (não corrigida em silêncio).
- VALUE_ENABLED por mercado: `False` por defeito; só muda por ação explícita do utilizador, e só quando
  a máquina de estados marcou VALUE_ENABLEMENT_CANDIDATE. Enquanto isso, VALUE/VALUE_CANDIDATE viram
  RESEARCH_SIGNAL nas recomendações.
- MARKET_EDGE state machine por mercado: UNPROVEN → COLLECTING → PROMISING → (candidato) → VALIDATED | REJECTED.
- Staking/Kelly: DISABLED enquanto nenhum mercado estiver VALIDATED com VALUE ativo.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import versions
from ..core.config import settings
from ..db.models import DatasetState, ManualCorrection, MarketEdgeState, Setting
from .markets import CATEGORY_LABELS, CATEGORY_ORDER
from .research import LEGACY_TO_CANONICAL, market_confidence, required_edge_v2

FREEZE_KEY = "model_freeze"
FROZEN_MODELS = list(dict.fromkeys(["ensemble", "ensemble_v2", "market-model-blend", "market-logistic-stack", "market-residual-v1", *sorted(versions.ALL_MODELS)]))
PAUSED_RESEARCH = {"1X2 market-aware modeling": "PAUSED — iteração 5 constrói evidência Superbet; nenhum novo challenger (§66)"}
FORBIDDEN_MODEL_FAMILIES = ["deep learning", "XGBoost", "LightGBM", "neural networks", "transformers"]
CONFIRMATION_MIN_DAYS = 30
VALIDATION_MIN_EFFECTIVE_N = 500


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _model_hash() -> str:
    from ..models.registry import SPECS

    return _hash({"versions": versions.ALL_MODELS, "specs": SPECS})


def _config_hash() -> str:
    keys = ("min_edge_pp", "min_ev_pct", "min_odd", "max_odd", "margin_method", "strength_half_life_days", "value_min_oos_bets", "gate_min_data_quality", "gate_min_confidence", "gate_min_sample", "gate_max_disagreement_pp", "gate_max_edge_pp_uncalibrated", "kelly_fraction_max")
    return _hash({k: getattr(settings, k) for k in keys})


def _dataset_version(session: Session) -> str:
    rows = session.execute(select(DatasetState.code, DatasetState.rows, DatasetState.last_success_at).order_by(DatasetState.code)).all()
    return "history:" + _hash([(c, r, str(t)[:10] if t else None) for c, r, t in rows])


def register_freeze(session: Session, *, force: bool = False, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    row = session.get(Setting, FREEZE_KEY)
    if row is not None and row.value and not force:
        return row.value
    value = {
        "freeze_date": now.isoformat(), "model_hash": _model_hash(), "config_hash": _config_hash(), "dataset_version": _dataset_version(session),
        "frozen_models": FROZEN_MODELS, "paused": PAUSED_RESEARCH, "forbidden_families": FORBIDDEN_MODEL_FAMILIES,
        "iteration": 5, "note": "Modelos congelados: a iteração 5 não treina, não ajusta e não promove modelos. Só constrói evidência Superbet por mercado.",
    }
    if row is None:
        session.add(Setting(key=FREEZE_KEY, value=value))
    else:
        row.value, row.updated_at = value, now
    session.flush()
    return value


def freeze_status(session: Session) -> dict:
    frozen = register_freeze(session)
    cur = {"model_hash": _model_hash(), "config_hash": _config_hash(), "dataset_version": _dataset_version(session)}
    drift = [k for k in ("model_hash", "config_hash") if frozen.get(k) != cur[k]]
    return {"freeze": frozen, "current": cur, "status": "INTACT" if not drift else "DRIFTED", "drifted_fields": drift,
            "dataset_changed": frozen.get("dataset_version") != cur["dataset_version"],
            "note": "DRIFTED = versões/parâmetros de modelo ou configuração mudaram depois do freeze (o histórico crescer não é drift)."}


# ---------------------------------------------------------------------------
# VALUE_ENABLED por mercado + cache para o motor de recomendações
# ---------------------------------------------------------------------------
_cache: dict = {"at": 0.0, "enabled": {}, "validated": set()}
_lock = threading.Lock()


def _refresh_cache(session: Session | None = None) -> None:
    from ..db.session import session_scope

    def load(s: Session) -> None:
        rows = s.execute(select(MarketEdgeState)).scalars().all()
        _cache["enabled"] = {r.market_category: bool(r.value_enabled) for r in rows}
        _cache["validated"] = {r.market_category for r in rows if r.state == "VALIDATED" and r.value_enabled}
        _cache["at"] = time.monotonic()

    if session is not None:
        load(session)
        return
    try:
        with session_scope() as s:
            load(s)
    except Exception:  # noqa: BLE001 — sem tabela ainda: tudo desligado
        _cache["enabled"], _cache["validated"], _cache["at"] = {}, set(), time.monotonic()


def value_enabled_for(market_key: str) -> bool:
    """`market_key` legado (1X2, TOTAL_GOALS…) → categoria canónica → VALUE_ENABLED. Default False."""
    with _lock:
        if time.monotonic() - _cache["at"] > 60:
            _refresh_cache()
        cat = LEGACY_TO_CANONICAL.get(market_key, (None, None))[0]
        return bool(_cache["enabled"].get(cat, False)) if cat else False


def staking_enabled() -> tuple[bool, str | None]:
    with _lock:
        if time.monotonic() - _cache["at"] > 60:
            _refresh_cache()
        if _cache["validated"]:
            return True, None
    return False, "STAKING DISABLED: nenhum mercado com MARKET_EDGE = VALIDATED e VALUE ativo. Kelly/stake só voltam com validação por mercado (iteração 5, §64)."


def invalidate_cache() -> None:
    with _lock:
        _cache["at"] = 0.0


# ---------------------------------------------------------------------------
# máquina de estados MARKET_EDGE por mercado (§65)
# ---------------------------------------------------------------------------
def _rules(row: dict, experiments: dict, now: datetime) -> tuple[list[str], list[str]]:
    """→ (regras cumpridas, regras falhadas) para VALUE_ENABLEMENT_CANDIDATE."""
    ok, fail = [], []
    eff = int(row.get("effective_n") or 0)
    (ok if eff >= VALIDATION_MIN_EFFECTIVE_N else fail).append(f"N efetivo ≥ {VALIDATION_MIN_EFFECTIVE_N} ({eff})")
    d = row.get("edgefut_minus_fair_brier") or {}
    (ok if d.get("conclusive") and (d.get("high") or 0) < 0 else fail).append("EdgeFut bate a fair da Superbet (ΔBrier IC < 0)")
    clv = row.get("clv_near_close") or {}
    (ok if clv and (clv.get("low") is not None) and clv.get("low", -1) >= 0 else fail).append("CLV não negativo (IC inferior ≥ 0)")
    cal = ((row.get("edgefut") or {}).get("calibration_bias_pp") or {})
    (ok if cal and cal.get("conclusive") is False else fail).append("Calibração EdgeFut sem viés detetável (IC inclui 0)")
    supported = [h for h in experiments.get("hypotheses", []) if h["market_category"] == row["market_category"] and h["status"] == "SUPPORTED" and h.get("survives_fdr")]
    (ok if supported else fail).append("Hipótese pré-registada SUPPORTED após BH-FDR")
    starts = [datetime.fromisoformat(h["confirmation_start"]) for h in experiments.get("hypotheses", []) if h["market_category"] == row["market_category"] and h.get("confirmation_start")]
    days = (now - min(starts)).days if starts else 0
    (ok if days >= CONFIRMATION_MIN_DAYS else fail).append(f"Período de confirmação ≥ {CONFIRMATION_MIN_DAYS} dias ({days})")
    return ok, fail


def update_edge_states(session: Session, discovery: list[dict], experiments: dict, settlement_audit: dict | None = None, *, now: datetime | None = None) -> list[dict]:
    now = now or datetime.utcnow()
    rows = {r.market_category: r for r in session.execute(select(MarketEdgeState)).scalars()}
    cov = {r["market_category"]: r for r in (settlement_audit or {}).get("market_data_coverage", [])}
    out = []
    for row in discovery:
        cat = row["market_category"]
        st = rows.get(cat)
        if st is None:
            st = MarketEdgeState(market_category=cat, state="UNPROVEN", value_enabled=False, enablement_candidate=False, history=[])
            session.add(st)
        ok, fail = _rules(row, experiments, now)
        raw_n = int(row.get("raw_n") or 0)
        d = row.get("edgefut_minus_fair_brier") or {}
        if st.state == "VALIDATED" and st.value_enabled:
            new = "VALIDATED"  # só o utilizador tira daqui (com trilha)
        elif raw_n == 0:
            new = "UNPROVEN"
        elif row.get("maturity") in ("COLLECTING", "EARLY"):
            new = "COLLECTING"
        elif d.get("conclusive") and (d.get("low") or 0) > 0 and row.get("maturity") == "MATURE":
            new = "REJECTED"  # mercado claramente melhor que o EdgeFut com amostra madura
        elif d.get("conclusive") and (d.get("high") or 0) < 0:
            new = "PROMISING"
        else:
            new = "COLLECTING"
        candidate = not fail
        if new != st.state or candidate != bool(st.enablement_candidate):
            hist = list(st.history or [])
            hist.append({"at": now.isoformat(), "from": st.state, "to": new, "candidate": candidate})
            st.history = hist[-50:]
        st.state, st.enablement_candidate, st.updated_at = new, candidate, now
        c = cov.get(cat, {})
        settled_share = (c.get("events_settled") / c["events_with_snapshots"]) if c.get("events_with_snapshots") else None
        st.evidence = {"rules_ok": ok, "rules_failed": fail, "maturity": row.get("maturity"), "effective_n": row.get("effective_n"), "raw_n": raw_n, "unique_events": row.get("unique_events"),
                       "required_edge_v2": required_edge_v2(row), "confidence": market_confidence(row, settled_share), "settled_share": round(settled_share, 3) if settled_share is not None else None}
        out.append(edge_state_row(st))
    session.flush()
    invalidate_cache()
    return out


def edge_state_row(st: MarketEdgeState) -> dict:
    return {"market_category": st.market_category, "label": CATEGORY_LABELS.get(st.market_category, st.market_category), "state": st.state, "value_enabled": bool(st.value_enabled),
            "enablement_candidate": bool(st.enablement_candidate), "evidence": st.evidence, "updated_at": st.updated_at, "history": st.history or []}


def edge_states(session: Session) -> list[dict]:
    rows = {r.market_category: r for r in session.execute(select(MarketEdgeState)).scalars()}
    out = []
    for cat in CATEGORY_ORDER:
        st = rows.get(cat)
        if st is None:
            out.append({"market_category": cat, "label": CATEGORY_LABELS[cat], "state": "UNPROVEN", "value_enabled": False, "enablement_candidate": False, "evidence": None, "updated_at": None, "history": []})
        else:
            out.append(edge_state_row(st))
    return out


def set_value_enabled(session: Session, market_category: str, enabled: bool, *, reason: str, now: datetime | None = None) -> dict:
    """Ação explícita do utilizador. Ativar exige `enablement_candidate`; desativar é sempre permitido. Fica trilha."""
    now = now or datetime.utcnow()
    st = session.get(MarketEdgeState, market_category)
    if st is None:
        raise ValueError(f"mercado desconhecido: {market_category}")
    if enabled and not st.enablement_candidate:
        raise PermissionError(f"{market_category}: VALUE não pode ser ativado — regras de validação não cumpridas ({', '.join((st.evidence or {}).get('rules_failed', []))})")
    if not reason or len(reason.strip()) < 5:
        raise ValueError("motivo obrigatório (≥ 5 caracteres)")
    before = {"value_enabled": bool(st.value_enabled), "state": st.state}
    st.value_enabled = enabled
    st.state = "VALIDATED" if enabled else ("PROMISING" if st.enablement_candidate else st.state)
    hist = list(st.history or [])
    hist.append({"at": now.isoformat(), "from": before["state"], "to": st.state, "value_enabled": enabled, "manual": True})
    st.history, st.updated_at = hist[-50:], now
    session.add(ManualCorrection(entity="market_edge_state", entity_id=market_category, field="value_enabled", before=before, after={"value_enabled": enabled, "state": st.state}, reason=reason.strip()[:400]))
    session.flush()
    invalidate_cache()
    return edge_state_row(st)


__all__ = ["register_freeze", "freeze_status", "value_enabled_for", "staking_enabled", "update_edge_states", "edge_states", "set_value_enabled", "invalidate_cache", "FROZEN_MODELS", "PAUSED_RESEARCH"]
