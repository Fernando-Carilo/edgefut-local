"""Aplicação FastAPI do EdgeFut AI. Escuta apenas em 127.0.0.1 (ver core/config.py)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..collectors import flush_source_log, install_source_log_sink
from ..core import versions
from ..core.config import settings
from ..core.logging import setup_logging
from ..core.paths import get_paths
from ..db.immutability import install_snapshot_guard
from ..db.migrations import run_migrations
from ..db.session import get_engine
from ..providers import CircuitOpen, SourceBlocked, SourceError
from ..scheduler import jobs
from . import bootstrap
from .routers import events_router, radar_router, system_router, tools_router, validation_router

log = logging.getLogger(__name__)

# Origens permitidas: apenas a janela Tauri e o dev server Vite locais.
ALLOWED_ORIGINS = [
    "tauri://localhost",
    "https://tauri.localhost",
    "http://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    get_paths().ensure()
    run_migrations(get_engine())
    install_snapshot_guard()
    install_source_log_sink()
    from .routers.system import apply_settings, load_settings
    from ..db.session import session_scope
    from ..models.registry import sync_registry

    with session_scope() as s:
        apply_settings(load_settings(s))
        sync_registry(s)
    if settings.autostart_bootstrap:
        bootstrap.run_in_background(minimal=False)
    log.info("EdgeFut engine %s em http://%s:%s", versions.APP_VERSION, settings.host, settings.port)
    try:
        yield
    finally:
        jobs.stop()
        flush_source_log()


def create_app() -> FastAPI:
    app = FastAPI(
        title="EdgeFut AI Engine",
        version=versions.APP_VERSION,
        description="Motor local de análise quantitativa de futebol. Não realiza apostas.",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    @app.exception_handler(SourceBlocked)
    async def _blocked(request: Request, exc: SourceBlocked):
        return JSONResponse(status_code=503, content={"detail": f"Fonte bloqueou a coleta: {exc}", "code": "UNRELIABLE_SOURCE"})

    @app.exception_handler(CircuitOpen)
    async def _circuit(request: Request, exc: CircuitOpen):
        return JSONResponse(status_code=503, content={"detail": f"Fonte temporariamente indisponível: {exc}", "code": "SOURCE_UNAVAILABLE"})

    @app.exception_handler(SourceError)
    async def _source(request: Request, exc: SourceError):
        return JSONResponse(status_code=502, content={"detail": f"Erro de fonte: {exc}", "code": "SOURCE_ERROR"})

    app.include_router(system_router)
    app.include_router(events_router)
    app.include_router(radar_router)
    app.include_router(tools_router)
    app.include_router(validation_router)
    return app


app = create_app()
