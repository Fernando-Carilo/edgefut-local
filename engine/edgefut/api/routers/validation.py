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
from ...validation.market_aware import (
    ABLATION,
    CHALLENGER_VERSIONS,
    FEATURE_GROUPS,
    FORBIDDEN_COLUMNS,
    MarketAwareRequest,
    frame_version,
    freeze,
    load_active_artifact,
    load_artifact,
    load_frame,
    run_discovery,
    run_holdout,
    save_artifact,
)
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


@router.get("/superbet")
def superbet_evidence(live: bool = False, session: Session = Depends(get_session)):
    """SUPERBET SHADOW VALIDATION (§8–§12, §25–§28): cobertura de snapshots, buckets T-24h…T-15m, overround,
    movimento de linha, viés da casa, Superbet fair como baseline, CLV próprio, tempo até o kickoff, painel shadow."""
    from ...validation.superbet import evidence_report as _ev
    from ...validation.superbet import latest_report as _latest_ev

    rep = None if live else _latest_ev(session)
    return rep or _ev(session, persist=False)


@router.get("/superbet/selection")
def superbet_selection(event_id: int, market_key: str, selection_key: str, line: float | None = None, session: Session = Depends(get_session)):
    from ...validation.superbet import selection_history

    return selection_history(session, event_id, market_key, selection_key, line)


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


# ---------------------------------------------------------------------------
# iteração 4 — market-aware (discovery → freeze → holdout)
# ---------------------------------------------------------------------------
class MarketAwareBody(BaseModel):
    frame_path: str | None = None          # default: frame do último replay de clubes
    base_model: str = "ensemble"
    markets: list[str] = Field(default_factory=lambda: ["1X2", "OU25"])
    test_window_days: int = Field(90, ge=30, le=180)
    validation_days: int = Field(180, ge=60, le=365)
    min_train: int = Field(1000, ge=200)
    holdout_days: int = Field(240, ge=60, le=730)
    ablation: bool = True


class HoldoutBody(BaseModel):
    run_id: int | None = None   # discovery run; default: o último
    force: bool = False         # repetir holdout para o mesmo (config_hash, dataset_version) — registrado como REPETIDO


def _latest_frame_path(session: Session) -> str | None:
    for r in session.execute(select(ValidationRun).where(ValidationRun.kind == "replay").order_by(ValidationRun.created_at.desc()).limit(30)).scalars():
        fr = (r.summary or {}).get("frame")
        if fr and fr.get("path") and not (r.summary or {}).get("international"):
            return fr["path"]
    return None


def _market_aware_job(body: MarketAwareBody, frame_path: str, cid: str) -> None:
    try:
        req = MarketAwareRequest(frame_path=frame_path, base_model=body.base_model, markets=tuple(body.markets), test_window_days=body.test_window_days,
                                 validation_days=body.validation_days, min_train=body.min_train, holdout_days=body.holdout_days, ablation=body.ablation)
        frame = load_frame(frame_path)
        version = frame_version(frame_path)
        rep = run_discovery(frame, req, dataset_version=version)
        artifact = freeze(rep, frame, req)
        path = save_artifact(artifact, activate=True)
        summary = {
            "config_hash": rep["config_hash"], "dataset_version": version, "frame_path": frame_path, "model_hash": artifact["model_hash"], "artifact_path": path,
            "verdict": rep["verdict"], "classification": rep["classification"], "holdout_start": rep["holdout_start"], "holdout_rows": rep["holdout_rows"],
            "discovery_rows": rep["discovery_rows"], "n": {m: (ev.get("n") or 0) for m, ev in rep["markets"].items()},
            "ranking": {m: ev.get("ranking_brier") for m, ev in rep["markets"].items()},
        }
        with session_scope() as s:
            s.add(ValidationRun(kind="market_aware", correlation_id=cid, request=req.to_dict(), summary=summary, detail=rep, duration_ms=rep.get("duration_ms")))
        log.info("market-aware discovery concluído (%s): %s", cid, rep["verdict"])
    except Exception as exc:  # noqa: BLE001
        log.exception("market-aware falhou: %s", exc)
        with session_scope() as s:
            s.add(ValidationRun(kind="market_aware", correlation_id=cid, request=body.model_dump(mode="json"), summary={"error": str(exc)}))
    finally:
        _running.discard("market_aware")


@router.post("/market-aware")
def start_market_aware(body: MarketAwareBody, session: Session = Depends(get_session)):
    """Fase DISCOVERY + freeze automático (o holdout é uma ação separada e única)."""
    if "market_aware" in _running:
        return {"ok": True, "skipped": True, "reason": "market-aware já em execução"}
    frame_path = body.frame_path or _latest_frame_path(session)
    if not frame_path:
        raise HTTPException(409, "nenhum frame de replay disponível — rode o replay de clubes primeiro (gera o frame por partida)")
    _running.add("market_aware")
    cid = new_correlation_id("market-aware")
    run_in_background(_market_aware_job, body, frame_path, cid)
    return {"ok": True, "started": True, "correlation_id": cid, "background": True, "frame_path": frame_path}


def _holdout_for(session: Session, config_hash: str, dataset_version: str) -> list[ValidationRun]:
    rows = session.execute(select(ValidationRun).where(ValidationRun.kind == "market_aware_holdout").order_by(ValidationRun.created_at.desc()).limit(50)).scalars().all()
    return [r for r in rows if (r.summary or {}).get("config_hash") == config_hash and (r.summary or {}).get("dataset_version") == dataset_version and not (r.summary or {}).get("error")]


@router.get("/market-aware/latest")
def market_aware_latest(session: Session = Depends(get_session)):
    disc = None
    for r in session.execute(select(ValidationRun).where(ValidationRun.kind == "market_aware").order_by(ValidationRun.created_at.desc()).limit(10)).scalars():
        if r.detail:
            disc = r
            break
    out: dict = {"running": "market_aware" in _running, "holdout_running": "market_aware_holdout" in _running,
                 "meta": {"challengers": CHALLENGER_VERSIONS, "feature_groups": list(FEATURE_GROUPS), "forbidden": list(FORBIDDEN_COLUMNS), "ablation": {k: list(v) for k, v in ABLATION.items()}},
                 "frame_available": _latest_frame_path(session) is not None}
    if disc is None:
        return {**out, "discovery": None, "holdout": None}
    holds = _holdout_for(session, disc.summary["config_hash"], disc.summary["dataset_version"])
    primary = next((h for h in reversed(holds) if not (h.summary or {}).get("repeated")), None)  # o PRIMEIRO holdout é o que conta
    out["discovery"] = {"id": disc.id, "created_at": disc.created_at, "summary": disc.summary, "detail": disc.detail}
    out["holdout"] = {"id": primary.id, "created_at": primary.created_at, "summary": primary.summary, "detail": primary.detail} if primary else None
    out["holdout_repeats"] = len([h for h in holds if (h.summary or {}).get("repeated")])
    out["holdout_consumed"] = primary is not None
    active = load_active_artifact()
    out["active_artifact"] = {k: active.get(k) for k in ("model_hash", "config_hash", "dataset_version", "frozen_at", "holdout_start", "base_model")} | {
        "markets": {m: {"alpha": s["alpha"], "selected": s["selected"], "train_rows": s["train_rows"], "coefficients": s["coefficients"]} for m, s in active["markets"].items()}} if active else None
    return out


@router.post("/market-aware/holdout")
def start_holdout(body: HoldoutBody, session: Session = Depends(get_session)):
    """Fase CONFIRMATION: avalia o artefato congelado no holdout. Uma vez por (config_hash, dataset_version);
    repetir exige `force` e fica marcado como REPETIDO (não conta como confirmação)."""
    if "market_aware_holdout" in _running:
        return {"ok": True, "skipped": True, "reason": "holdout já em execução"}
    disc = session.get(ValidationRun, body.run_id) if body.run_id else None
    if disc is None:
        for r in session.execute(select(ValidationRun).where(ValidationRun.kind == "market_aware").order_by(ValidationRun.created_at.desc()).limit(10)).scalars():
            if r.detail:
                disc = r
                break
    if disc is None or not disc.detail or not (disc.summary or {}).get("artifact_path"):
        raise HTTPException(409, "nenhum discovery market-aware com artefato congelado")
    summ = disc.summary
    previous = _holdout_for(session, summ["config_hash"], summ["dataset_version"])
    if previous and not body.force:
        raise HTTPException(409, f"holdout já executado para config {summ['config_hash']} / dataset {summ['dataset_version']} (run {previous[-1].id}). Repetir exige force=true e não conta como confirmação.")
    _running.add("market_aware_holdout")
    cid = new_correlation_id("holdout")
    repeated = bool(previous)
    disc_id, disc_req = disc.id, dict(disc.request or {})

    def _job() -> None:
        try:
            artifact = load_artifact(summ["artifact_path"])
            frame = load_frame(summ["frame_path"])
            if frame_version(summ["frame_path"]) != summ["dataset_version"]:
                raise RuntimeError("o frame em disco mudou desde o discovery (dataset_version diferente) — holdout inválido")
            req = MarketAwareRequest(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in disc_req.items()})
            rep = run_holdout(artifact, frame, req)
            rep["repeated"] = repeated
            with session_scope() as s:
                s.add(ValidationRun(kind="market_aware_holdout", correlation_id=cid, request={"discovery_run_id": disc_id, "force": body.force},
                                    summary={"config_hash": summ["config_hash"], "dataset_version": summ["dataset_version"], "model_hash": artifact["model_hash"], "repeated": repeated,
                                             "verdict": rep["verdict"], "holdout_rows": rep["holdout_rows"], "run_timestamp": rep["run_timestamp"],
                                             "ranking": {m: ev.get("ranking_brier") for m, ev in rep["markets"].items()}},
                                    detail=rep, duration_ms=rep.get("duration_ms")))
        except Exception as exc:  # noqa: BLE001
            log.exception("holdout falhou: %s", exc)
            with session_scope() as s:
                s.add(ValidationRun(kind="market_aware_holdout", correlation_id=cid, request={"discovery_run_id": disc_id}, summary={"error": str(exc), "config_hash": summ.get("config_hash"), "dataset_version": summ.get("dataset_version"), "repeated": True}))
        finally:
            _running.discard("market_aware_holdout")

    run_in_background(_job)
    return {"ok": True, "started": True, "correlation_id": cid, "background": True, "repeated": repeated}


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
