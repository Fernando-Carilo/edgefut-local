"""Validação preditiva (iteração 3): replay histórico, decay, baselines, shadow, drift,
cobertura, governança champion/challenger. Tudo é leitura/medição — nenhum endpoint
altera decisões de produção, exceto `POST /validation/governance/promote` (ação
explícita do operador, registrada)."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.context import new_correlation_id
from ...db.models import ModelRegistry, ValidationRun
from ...db.session import get_session, session_scope
from ...providers.historical import get_store
from ...scheduler.jobs import run_in_background
from ...validation.bootstrap import sample_quality, sample_thresholds
from ...validation.drift import check_drift, latest_drift
from ...validation.governance import CONSENSUS_KEYS, current_champion, evaluate_promotion, promote
from ...validation.replay import BASELINES, MODEL_LABELS, MODELS, ReplayRequest, run_replay
from ...validation.shadow import daily_report, latest_report

log = logging.getLogger(__name__)
router = APIRouter(prefix="/validation", tags=["validation"])

_running: set[str] = set()


class ReplayBody(BaseModel):
    datasets: list[str] | None = None  # default: todos os datasets com odds (clubes)
    start: datetime | None = None
    end: datetime | None = None
    window_days: int = Field(30, ge=7, le=180)
    scheme: str = Field("expanding", pattern="^(expanding|rolling)$")
    rolling_years: float = 3.0
    models: list[str] | None = None
    international: bool = False  # replay do INTL (MODEL_ONLY: sem odds → sem ROI)
    bet_simulation: bool = True


class PromoteBody(BaseModel):
    consensus: str
    reason: str = Field(min_length=5, max_length=500)


def _latest(session: Session, kind: str) -> ValidationRun | None:
    return session.execute(select(ValidationRun).where(ValidationRun.kind == kind).order_by(ValidationRun.created_at.desc()).limit(1)).scalar_one_or_none()


def _replay_job(body: ReplayBody, cid: str) -> None:
    key = "replay:intl" if body.international else "replay:clubs"
    try:
        store = get_store()
        if body.international:
            datasets = ["INTL"]
        else:
            datasets = body.datasets or sorted(store.datasets_with_odds())
        req = ReplayRequest(
            datasets=datasets, start=body.start, end=body.end, window_days=body.window_days if not body.international else max(body.window_days, 90),
            scheme=body.scheme, rolling_years=body.rolling_years, models=tuple(body.models) if body.models else MODELS,
            is_national=body.international, friendly_weight=0.6 if body.international else None, bet_simulation=body.bet_simulation and not body.international,
        )
        with session_scope() as s:
            rep = run_replay(req, session=s, persist=True, correlation_id=cid)
            # avaliação de promoção registrada junto ao replay (nunca promove sozinha)
            champion = current_champion(s)
            evals = {c: evaluate_promotion(rep, c, champion) for c in ("ensemble_v2", "poisson_v2") if c in rep.get("overall", {})}
            run = _latest(s, "replay")
            if run is not None:
                run.summary = {**(run.summary or {}), "promotion": {k: v["verdict"] for k, v in evals.items()}, "international": body.international}
                run.detail = {**(run.detail or {}), "promotion_evaluation": evals}
        log.info("replay concluído (%s): %d partidas", cid, rep.get("matches", 0))
    except Exception as exc:  # noqa: BLE001
        log.exception("replay falhou: %s", exc)
        with session_scope() as s:
            s.add(ValidationRun(kind="replay", correlation_id=cid, request=body.model_dump(mode="json"), summary={"error": str(exc), "international": body.international}))
    finally:
        _running.discard(key)


@router.post("/replay")
def start_replay(body: ReplayBody):
    key = "replay:intl" if body.international else "replay:clubs"
    if key in _running:
        return {"ok": True, "skipped": True, "reason": "replay já em execução"}
    _running.add(key)
    cid = new_correlation_id("replay")
    run_in_background(_replay_job, body, cid)
    return {"ok": True, "started": True, "correlation_id": cid, "background": True}


@router.get("/replay/latest")
def replay_latest(international: bool = False, session: Session = Depends(get_session)):
    rows = session.execute(select(ValidationRun).where(ValidationRun.kind == "replay").order_by(ValidationRun.created_at.desc()).limit(20)).scalars().all()
    for r in rows:
        if bool((r.summary or {}).get("international")) == international and r.detail:
            return {"id": r.id, "created_at": r.created_at, "correlation_id": r.correlation_id, "summary": r.summary, "detail": r.detail, "running": ("replay:intl" if international else "replay:clubs") in _running}
    return {"id": None, "detail": None, "running": ("replay:intl" if international else "replay:clubs") in _running, "labels": MODEL_LABELS, "models": list(MODELS), "baselines": list(BASELINES)}


@router.get("/runs")
def runs(kind: str | None = Query(None), limit: int = Query(30, le=200), session: Session = Depends(get_session)):
    q = select(ValidationRun).order_by(ValidationRun.created_at.desc()).limit(limit)
    if kind:
        q = q.where(ValidationRun.kind == kind)
    rows = session.execute(q).scalars().all()
    return {"runs": [{"id": r.id, "kind": r.kind, "created_at": r.created_at, "correlation_id": r.correlation_id, "summary": r.summary, "duration_ms": r.duration_ms} for r in rows]}


@router.get("/runs/{run_id}")
def run_detail(run_id: int, session: Session = Depends(get_session)):
    r = session.get(ValidationRun, run_id)
    if r is None:
        raise HTTPException(404, "validation run não encontrado")
    return {"id": r.id, "kind": r.kind, "created_at": r.created_at, "request": r.request, "summary": r.summary, "detail": r.detail, "duration_ms": r.duration_ms}


@router.post("/decay")
def start_decay(session: Session = Depends(get_session)):
    if "decay" in _running:
        return {"ok": True, "skipped": True}
    _running.add("decay")

    def _job() -> None:
        try:
            from ...validation.decay import select_half_life

            with session_scope() as s:
                select_half_life(sorted(get_store().datasets_with_odds()), start=datetime(2022, 8, 1), window_days=30, session=s, persist=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("decay falhou: %s", exc)
        finally:
            _running.discard("decay")

    run_in_background(_job)
    return {"ok": True, "started": True, "background": True}


@router.get("/decay/latest")
def decay_latest(session: Session = Depends(get_session)):
    r = _latest(session, "decay")
    return {"id": r.id if r else None, "created_at": r.created_at if r else None, "detail": r.detail if r else None, "current_half_life_days": settings.strength_half_life_days, "running": "decay" in _running}


@router.get("/shadow")
def shadow(live: bool = False, session: Session = Depends(get_session)):
    rep = None if live else latest_report(session)
    return rep or daily_report(session, persist=False)


@router.get("/drift")
def drift(live: bool = False, session: Session = Depends(get_session)):
    rep = None if live else latest_drift(session)
    return rep or check_drift(session, persist=False)


@router.get("/coverage")
def coverage():
    """Competition Data Coverage map: % de partidas com resultado, odds, finalizações, escanteios, cartões; jogadores = 0 %."""
    df = get_store().datasets()
    rows = []
    for r in df.itertuples(index=False):
        n = int(r.rows or 0)
        def pct(v) -> float:
            return round(100.0 * float(v or 0) / n, 1) if n else 0.0
        rows.append({
            "dataset_code": r.dataset_code, "competition": r.competition, "competitions": int(r.competitions or 0), "matches": n,
            "first_date": str(r.first_date)[:10] if r.first_date is not None else None, "last_date": str(r.last_date)[:10] if r.last_date is not None else None,
            "results_pct": 100.0 if n else 0.0, "odds_pct": pct(r.rows_with_odds), "shots_pct": pct(r.rows_with_shots),
            "corners_pct": pct(r.rows_with_corners), "cards_pct": pct(r.rows_with_cards), "players_pct": 0.0,
            "sample_quality": sample_quality(int(r.rows_with_odds or 0)) if int(r.rows_with_odds or 0) else "INSUFFICIENT",
            "value_capable": int(r.rows_with_odds or 0) >= 200,
            "source": r.source,
        })
    rows.sort(key=lambda x: (-x["odds_pct"], -x["matches"]))
    return {"generated_at": datetime.utcnow(), "datasets": rows, "thresholds": sample_thresholds(), "note": "players_pct = 0: nenhum provider de jogadores público e permitido integrado (Player Engine bloqueado)."}


@router.get("/governance")
def governance(session: Session = Depends(get_session)):
    champion = current_champion(session)
    rows = session.execute(select(ModelRegistry).where(ModelRegistry.active.is_(True)).order_by(ModelRegistry.model_id)).scalars().all()
    latest = None
    for r in session.execute(select(ValidationRun).where(ValidationRun.kind == "replay").order_by(ValidationRun.created_at.desc()).limit(20)).scalars():
        if r.detail and not (r.summary or {}).get("international"):
            latest = r
            break
    promotions = session.execute(select(ValidationRun).where(ValidationRun.kind == "promotion").order_by(ValidationRun.created_at.desc()).limit(10)).scalars().all()
    return {
        "champion": champion, "consensus_options": list(CONSENSUS_KEYS),
        "roles": [{"model_id": r.model_id, "version": r.version, "role": r.role} for r in rows],
        "latest_replay_id": latest.id if latest else None,
        "promotion_evaluation": (latest.detail or {}).get("promotion_evaluation") if latest else None,
        "promotions": [{"id": p.id, "created_at": p.created_at, "request": p.request, "summary": p.summary} for p in promotions],
        "rule": [
            "Brier OOS melhor que o campeão (IC 95% da diferença pareada < 0)",
            "LogLoss não pior", "Calibração (ECE) não pior além de 0,005",
            f"Amostra ≥ {settings.sample_moderate_min} partidas pareadas", "Melhor em ≥ 60% das janelas",
            "ROI NÃO é critério de promoção",
        ],
    }


@router.post("/governance/promote")
def do_promote(body: PromoteBody, session: Session = Depends(get_session)):
    if body.consensus not in CONSENSUS_KEYS:
        raise HTTPException(400, f"consenso inválido: {body.consensus}")
    latest = None
    for r in session.execute(select(ValidationRun).where(ValidationRun.kind == "replay").order_by(ValidationRun.created_at.desc()).limit(20)).scalars():
        if r.detail and not (r.summary or {}).get("international"):
            latest = r
            break
    evaluation = ((latest.detail or {}).get("promotion_evaluation") or {}).get(body.consensus) if latest else None
    res = promote(session, body.consensus, reason=body.reason, evaluation=evaluation)
    from ...analysis.pipeline import invalidate_cache

    invalidate_cache()
    return {**res, "evaluation": evaluation, "warning": None if (evaluation or {}).get("eligible") else "Promoção sem regra cumprida no último replay — registrada como decisão do operador."}
