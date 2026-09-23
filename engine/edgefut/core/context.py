"""Correlation id por contexto (thread/task): ingestão de evento, análise, requisição a provider.

Uso:
    with correlation("analysis", event_id=123) as cid:
        ...  # todo log dentro do bloco carrega cid
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("edgefut_correlation_id", default=None)
_scope: contextvars.ContextVar[str | None] = contextvars.ContextVar("edgefut_scope", default=None)


def new_correlation_id(prefix: str = "") -> str:
    cid = uuid.uuid4().hex[:12]
    return f"{prefix}-{cid}" if prefix else cid


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def current_scope() -> str | None:
    return _scope.get()


@contextmanager
def correlation(scope: str, cid: str | None = None, **tags: object) -> Iterator[str]:
    """Abre um escopo de correlação. Reaproveita o id externo se já houver um ativo."""
    outer = _correlation_id.get()
    cid = cid or outer or new_correlation_id(scope[:3])
    label = scope + ("" if not tags else "[" + ",".join(f"{k}={v}" for k, v in tags.items()) + "]")
    t1 = _correlation_id.set(cid)
    t2 = _scope.set(label)
    try:
        yield cid
    finally:
        _correlation_id.reset(t1)
        _scope.reset(t2)
