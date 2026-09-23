"""Rotas de sistema: health, bootstrap, fontes, modelos, configurações, ao vivo."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import versions
from ...core.config import settings
from ...core.paths import get_paths
from ...db.models import Competition, DatasetState, JobRun, Setting, SourceLog
from ...db.session import get_session
from ...explanations import ollama
from ...providers import get_http_client
from ...providers.historical.football_data import LEAGUE_NAMES
from ...providers.historical.store import get_store
from ...scheduler import jobs
from .. import bootstrap
from ..schemas import BootstrapStatus, HealthResponse, SettingsModel

router = APIRouter(tags=["system"])

SETTINGS_KEY = "app"

MODEL_DESCRIPTIONS = {
    "strength": "Força do time: janelas 5/10/20 com peso de recência; splits geral/casa/fora; ataque/defesa relativos à média da liga.",
    "elo": "ELO com atualização temporal, multiplicador por margem de gols e vantagem de mando ponderada pelo tipo de local.",
    "poisson": "Poisson independente: λ = média da liga × ataque × defesa × vantagem de mando.",
    "dixon_coles": "Dixon-Coles com decaimento temporal (ξ=0.0018/dia) e correção ρ para placares baixos; ajuste por L-BFGS-B.",
    "monte_carlo": "Monte Carlo (10k/25k/50k/100k) sobre a matriz de placares do modelo de gols; seed = eventId (reprodutível).",
    "corners": "Binomial Negativa para escanteios (k por método dos momentos), linhas 6.5–10.5.",
    "cards": "Binomial Negativa para cartões, linhas 2.5–5.5; árbitro só quando público.",
    "shots": "Finalizações e finalizações no alvo esperadas por time (média ponderada, taxa de conversão).",
    "confidence": "Confidence Engine: qualidade de dados, amostra, concordância de modelos, estabilidade de odds, calibração → A/B/C/D.",
    "opportunity": "Opportunity Score 0–100: 30% qualidade, 30% confiança, 25% edge, 10% estabilidade, 5% calibração. Nunca pela odd.",
    "pipeline": "Coleta → Normalização → Qualidade → Features → Modelos → Simulação → Probabilidade → Odd justa → Comparação → Edge → Risco → Recomendação/NO BET → Explicação.",
}


@router.get("/health", response_model=HealthResponse)
def health():
    st = jobs.state()
    try:
        n_datasets = len(get_store().datasets())
    except Exception:  # noqa: BLE001
        n_datasets = 0
    return HealthResponse(
        status="ok", version=versions.APP_VERSION, host=settings.host, port=settings.port,
        db_path=str(get_paths().sqlite), datasets=n_datasets,
        scheduler={k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in st.items()},
        superbet_enabled=settings.superbet_enabled, ollama_available=ollama.is_available(),
    )


@router.get("/health/system")
def health_system(session: Session = Depends(get_session)):
    """Diagnóstico por componente: HEALTHY / DEGRADED / UNAVAILABLE / STALE."""
    from ...quality.health import system_health

    return system_health(session)


@router.get("/jobs")
def jobs_history(limit: int = Query(100, le=500), job: str | None = None, session: Session = Depends(get_session)):
    q = select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)
    if job:
        q = q.where(JobRun.job == job)
    runs = session.execute(q).scalars().all()
    last_by_job: dict[str, dict] = {}
    for r in reversed(runs):
        last_by_job[r.job] = _job_run(r)
    return {
        "generated_at": datetime.utcnow(),
        "scheduler_running": jobs.is_running(),
        "scheduled": jobs.jobs_overview(),
        "labels": jobs.JOB_LABELS,
        "last_by_job": last_by_job,
        "runs": [_job_run(r) for r in runs],
        "state": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in jobs.state().items()},
    }


def _job_run(r: JobRun) -> dict:
    return {
        "id": r.id, "job": r.job, "label": jobs.JOB_LABELS.get(r.job, r.job), "correlation_id": r.correlation_id,
        "started_at": r.started_at, "finished_at": r.finished_at, "duration_ms": r.duration_ms, "status": r.status,
        "records_processed": r.records_processed, "errors": r.errors, "detail": r.detail,
    }


@router.post("/jobs/{job}/run")
def jobs_run(job: str):
    fn = {
        "events": jobs.job_sync_events, "odds": jobs.job_sync_odds, "history": jobs.job_sync_history, "settle": jobs.job_settle,
        "radar": jobs.job_refresh_radar, "closing_lines": jobs.job_closing_lines, "performance": jobs.job_update_performance,
        "calibration": jobs.job_update_calibration, "cache_cleanup": jobs.job_cache_cleanup, "alerts": jobs.job_alerts,
        "live_poll": jobs.job_live_poll,
    }.get(job)
    if fn is None:
        return JSONResponse(status_code=404, content={"detail": f"job desconhecido: {job}"})
    jobs.run_in_background(fn)
    return {"ok": True, "started": job, "background": True}


@router.get("/alerts")
def alerts(limit: int = Query(100, le=500), unread_only: bool = False, session: Session = Depends(get_session)):
    from ...alerts import KINDS, list_alerts

    items = list_alerts(session, limit=limit, unread_only=unread_only)
    return {"generated_at": datetime.utcnow(), "kinds": KINDS, "unread": sum(1 for a in items if a["read_at"] is None), "alerts": items}


@router.post("/alerts/read")
def alerts_read(ids: list[int] | None = None, session: Session = Depends(get_session)):
    from ...alerts import mark_read

    return {"ok": True, "marked": mark_read(session, ids)}


@router.get("/calibration")
def calibration(market_key: str | None = None, session: Session = Depends(get_session)):
    from ...models.calibration import MIN_CALIBRATION_N, calibration_overview, reliability_diagram

    return {
        "generated_at": datetime.utcnow(),
        "min_n": MIN_CALIBRATION_N,
        "groups": calibration_overview(session),
        "diagram": reliability_diagram(session, market_key),
        "note": (
            "Calibração isotônica ajustada apenas sobre previsões gravadas antes do jogo e liquidadas. "
            f"Grupos com menos de {MIN_CALIBRATION_N} previsões não são usados na recomendação (probabilidade crua)."
        ),
    }


@router.get("/bootstrap", response_model=BootstrapStatus)
def bootstrap_status():
    return BootstrapStatus(**bootstrap.status())


@router.post("/bootstrap", response_model=BootstrapStatus)
def bootstrap_run(minimal: bool = False):
    bootstrap.run_in_background(minimal)
    return BootstrapStatus(**bootstrap.status())


@router.get("/sources")
def sources(session: Session = Depends(get_session), log_limit: int = Query(50, le=500)):
    store = get_store()
    datasets = []
    try:
        df = store.datasets()
        for _, r in df.iterrows():
            code = str(r["dataset_code"])
            datasets.append(
                {
                    "code": code,
                    "label": "Seleções nacionais (martj42/international_results)" if code == "INTL" else LEAGUE_NAMES.get(code, code),
                    "competitions": int(r["competitions"]),
                    "rows": int(r["rows"]),
                    "first_date": str(r["first_date"])[:10] if r["first_date"] is not None else None,
                    "last_date": str(r["last_date"])[:10] if r["last_date"] is not None else None,
                    "collected_at": str(r["collected_at"]) if r["collected_at"] is not None else None,
                    "source": r["source"],
                    "source_url": r["source_url"],
                    "coverage": {
                        "corners_pct": round(100 * float(r["rows_with_corners"]) / max(1, int(r["rows"])), 1),
                        "shots_pct": round(100 * float(r["rows_with_shots"]) / max(1, int(r["rows"])), 1),
                        "cards_pct": round(100 * float(r["rows_with_cards"]) / max(1, int(r["rows"])), 1),
                    },
                }
            )
    except Exception as exc:  # noqa: BLE001
        datasets.append({"code": "ERRO", "label": str(exc), "rows": 0})

    states = [
        {
            "code": d.code, "provider": d.provider, "source_url": d.source_url, "rows": d.rows,
            "last_success_at": d.last_success_at, "last_error": d.last_error,
        }
        for d in session.execute(select(DatasetState).order_by(DatasetState.code)).scalars()
    ]
    since = datetime.utcnow() - timedelta(hours=24)
    logs = session.execute(
        select(SourceLog).where(SourceLog.collected_at >= since).order_by(SourceLog.collected_at.desc()).limit(log_limit)
    ).scalars().all()
    by_provider: dict[str, dict] = {}
    for row in session.execute(select(SourceLog).where(SourceLog.collected_at >= since)).scalars():
        p = by_provider.setdefault(row.provider, {"ok": 0, "cached": 0, "error": 0, "blocked": 0, "last": None})
        p[row.status if row.status in p else "error"] += 1
        if p["last"] is None or row.collected_at > p["last"]:
            p["last"] = row.collected_at
    competitions = session.execute(select(Competition).order_by(Competition.name)).scalars().all()
    providers = [
        {
            "key": "superbet", "name": "Superbet (oferta pública, JSON)", "kind": "odds", "enabled": settings.superbet_enabled,
            "url": settings.superbet_base_url, "dynamic": False,
            "notes": "Somente endpoints públicos da oferta. Sem login, sem credenciais, sem apostas automáticas. Páginas HTML protegidas por Cloudflare não são raspadas.",
            "stats": by_provider.get("superbet"),
        },
        {
            "key": "football_data", "name": "football-data.co.uk (CSV)", "kind": "historical", "enabled": True,
            "url": "https://www.football-data.co.uk/", "dynamic": False,
            "notes": "Resultados, finalizações, escanteios, cartões e odds de fechamento por liga/temporada.",
            "stats": by_provider.get("football-data.co.uk"),
        },
        {
            "key": "international_results", "name": "martj42/international_results (CSV)", "kind": "historical", "enabled": True,
            "url": "https://github.com/martj42/international_results", "dynamic": False,
            "notes": "Resultados de seleções desde 1872 com cidade, país e flag de campo neutro.",
            "stats": by_provider.get("international_results"),
        },
        {
            "key": "ollama", "name": "Ollama (local, opcional)", "kind": "explanation", "enabled": settings.ollama_enabled,
            "url": settings.ollama_base_url, "dynamic": False,
            "notes": "Apenas reescreve explicações a partir de fatos já calculados. Nunca gera estatísticas.",
            "stats": {"available": ollama.is_available()},
        },
    ]
    return {
        "generated_at": datetime.utcnow(),
        "providers": providers,
        "datasets": datasets,
        "dataset_states": states,
        "hosts": get_http_client().host_status(),
        "competitions": [
            {
                "id": c.id, "name": c.name, "category": c.category_name, "dataset_code": c.dataset_code,
                "is_national_teams": c.is_national_teams, "is_womens": c.is_womens,
                "supported": bool(c.dataset_code) or bool(c.is_national_teams and not c.is_womens),
            }
            for c in competitions
        ],
        "log": [
            {
                "provider": l.provider, "url": l.url, "status": l.status, "http_status": l.http_status,
                "latency_ms": l.latency_ms, "error": l.error, "collected_at": l.collected_at,
            }
            for l in logs
        ],
        "scheduler": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in jobs.state().items()},
        "paths": {"root": str(get_paths().root), "processed": str(get_paths().processed), "cache": str(get_paths().cache)},
    }


@router.post("/sources/refresh")
def sources_refresh(what: str = Query("events", pattern="^(events|odds|history|settle|radar|closing_lines|performance|calibration|alerts|live_poll)$"), force: bool = False):
    fn = {
        "events": jobs.job_sync_events,
        "odds": jobs.job_sync_odds,
        "history": lambda: jobs.job_sync_history(force=force),
        "settle": jobs.job_settle,
        "radar": jobs.job_refresh_radar,
        "closing_lines": jobs.job_closing_lines,
        "performance": jobs.job_update_performance,
        "calibration": jobs.job_update_calibration,
        "alerts": jobs.job_alerts,
        "live_poll": jobs.job_live_poll,
    }[what]
    if what in ("history", "radar", "performance", "calibration"):
        jobs.run_in_background(fn)
        return {"ok": True, "started": what, "background": True}
    return {"ok": True, "started": what, "background": False, "result": fn()}


@router.get("/models")
def models():
    from ...models.dixon_coles import dc_cache
    from ...models.elo import elo_cache

    return {
        "versions": versions.ALL_MODELS,
        "app_version": versions.APP_VERSION,
        "models": [
            {"key": k, "version": v, "description": MODEL_DESCRIPTIONS.get(k, "")} for k, v in versions.ALL_MODELS.items()
        ],
        "parameters": {
            "default_simulations": settings.default_simulations,
            "min_edge_pp": settings.min_edge_pp,
            "min_ev_pct": settings.min_ev_pct,
            "min_odd": settings.min_odd,
            "max_odd": settings.max_odd,
            "home_advantage_unconfirmed_weight": settings.home_advantage_unconfirmed_weight,
            "kelly_fraction_max": settings.kelly_fraction_max,
        },
        "fitted": {
            "elo": [str(k) for k in list(elo_cache._tables.keys())],
            "dixon_coles": [str(k) for k in list(dc_cache._fits.keys())],
        },
    }


def load_settings(session: Session) -> SettingsModel:
    row = session.get(Setting, SETTINGS_KEY)
    base = SettingsModel(
        default_simulations=settings.default_simulations, min_edge_pp=settings.min_edge_pp, min_ev_pct=settings.min_ev_pct,
        min_odd=settings.min_odd, max_odd=settings.max_odd, kelly_fraction_max=settings.kelly_fraction_max,
        ollama_enabled=settings.ollama_enabled, ollama_model=settings.ollama_model,
        events_refresh_min=settings.events_refresh_min, odds_refresh_min=settings.odds_refresh_min,
    )
    if row and row.value:
        return base.model_copy(update={k: v for k, v in row.value.items() if k in SettingsModel.model_fields})
    return base


def apply_settings(model: SettingsModel) -> None:
    settings.default_simulations = model.default_simulations
    settings.min_edge_pp = model.min_edge_pp
    settings.min_ev_pct = model.min_ev_pct
    settings.min_odd = model.min_odd
    settings.max_odd = model.max_odd
    settings.kelly_fraction_max = min(model.kelly_fraction_max, 0.25)
    settings.ollama_enabled = model.ollama_enabled
    settings.ollama_model = model.ollama_model
    settings.events_refresh_min = model.events_refresh_min
    settings.odds_refresh_min = model.odds_refresh_min


@router.get("/settings", response_model=SettingsModel)
def get_settings(session: Session = Depends(get_session)):
    return load_settings(session)


@router.put("/settings", response_model=SettingsModel)
def put_settings(model: SettingsModel, session: Session = Depends(get_session)):
    model.kelly_fraction_max = min(model.kelly_fraction_max, 0.25)
    if model.default_simulations not in (10_000, 25_000, 50_000, 100_000):
        model.default_simulations = 50_000
    row = session.get(Setting, SETTINGS_KEY)
    if row is None:
        row = Setting(key=SETTINGS_KEY, value=model.model_dump())
        session.add(row)
    else:
        row.value = model.model_dump()
        row.updated_at = datetime.utcnow()
    session.commit()
    apply_settings(model)
    from ...analysis.pipeline import invalidate_cache

    invalidate_cache()
    return model


@router.get("/live")
def live(poll: bool = False):
    """Jogos em andamento (Superbet offerState=live). Observação apenas — sem recomendações."""
    from ...collectors.live import live_snapshot, poll_live

    if poll:
        poll_live(force=True)
    return live_snapshot()
