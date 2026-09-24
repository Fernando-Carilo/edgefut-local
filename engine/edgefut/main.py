"""Ponto de entrada do engine (também usado pelo sidecar PyInstaller)."""

from __future__ import annotations

import argparse
import sys


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="edgefut-engine", description="EdgeFut AI — motor local de análise")
    parser.add_argument("--port", type=int, default=None, help="porta local (padrão 8765)")
    parser.add_argument("--no-bootstrap", action="store_true", help="não executar o primeiro boot automaticamente")
    parser.add_argument("--no-scheduler", action="store_true", help="desativar o agendador")
    parser.add_argument("--reload", action="store_true", help="recarregar em alterações (dev)")
    args = parser.parse_args(argv)

    from .core.config import settings

    if args.port:
        settings.port = args.port
    if args.no_bootstrap:
        settings.autostart_bootstrap = False
    if args.no_scheduler:
        settings.scheduler_enabled = False

    import uvicorn

    # Host fixo em loopback: nunca 0.0.0.0.
    uvicorn.run(
        "edgefut.api.app:app" if args.reload else _app(),
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


def _app():
    from .api.app import app

    return app


if __name__ == "__main__":
    run(sys.argv[1:])
