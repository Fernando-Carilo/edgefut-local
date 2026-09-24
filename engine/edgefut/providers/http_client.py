"""Cliente HTTP compartilhado por todos os providers.

Garantias:
- cache em disco com TTL (data/cache);
- rate limit por host (intervalo mínimo entre requisições);
- retry com backoff exponencial (3 tentativas) apenas para erros transitórios;
- timeout;
- circuit breaker por host: abre após N falhas consecutivas e reabre após cooldown;
- registro em `source_log` (provider, url, status, latência).

Nunca tenta contornar 403/429/CAPTCHA: esses códigos abrem o circuito e
propagam `SourceBlocked` para que o SourceResolver escolha outra fonte.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..core.config import settings
from ..core.paths import get_paths

log = logging.getLogger(__name__)


class SourceError(Exception):
    pass


class SourceBlocked(SourceError):
    """403/429/anti-bot: não insistimos."""


class CircuitOpen(SourceError):
    pass


@dataclass
class _HostState:
    min_interval_s: float = 2.0
    last_request_at: float = 0.0
    consecutive_failures: int = 0
    opened_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class FetchResult:
    content: bytes
    url: str
    status: str  # ok | cached
    http_status: int | None
    collected_at: datetime
    latency_ms: int

    def json(self):
        return json.loads(self.content.decode("utf-8"))

    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class HttpClient:
    FAILURE_THRESHOLD = 5
    COOLDOWN_S = 300

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or get_paths().cache
        self._hosts: dict[str, _HostState] = {}
        self._hosts_lock = threading.Lock()
        self._client = httpx.Client(
            timeout=settings.http_timeout_s,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "application/json, text/csv, text/plain, */*",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
            },
            follow_redirects=True,
        )
        self._log_sink = None  # callable(provider, url, status, http_status, latency_ms, error)

    def set_log_sink(self, sink) -> None:
        self._log_sink = sink

    # -- host state --------------------------------------------------------
    def _host(self, url: str, min_interval_s: float) -> _HostState:
        host = urlparse(url).netloc
        with self._hosts_lock:
            st = self._hosts.get(host)
            if st is None:
                st = _HostState(min_interval_s=min_interval_s)
                self._hosts[host] = st
            else:
                st.min_interval_s = min(st.min_interval_s, min_interval_s) or min_interval_s
            return st

    def host_status(self) -> dict[str, dict]:
        out = {}
        for host, st in self._hosts.items():
            out[host] = {
                "consecutive_failures": st.consecutive_failures,
                "circuit_open": self._is_open(st),
                "opened_at": st.opened_at,
            }
        return out

    def _is_open(self, st: _HostState) -> bool:
        if st.opened_at is None:
            return False
        if time.monotonic() - st.opened_at > self.COOLDOWN_S:
            return False  # meia-abertura: permite tentar
        return True

    # -- cache -------------------------------------------------------------
    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha1(url.encode()).hexdigest()
        return self.cache_dir / f"{h}.json"

    def _read_cache(self, url: str, ttl_s: float) -> FetchResult | None:
        p = self._cache_path(url)
        if not p.exists():
            return None
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
            collected = datetime.fromisoformat(meta["collected_at"])
            if datetime.utcnow() - collected > timedelta(seconds=ttl_s):
                return None
            body_path = p.with_suffix(".body")
            if not body_path.exists():
                return None
            return FetchResult(
                content=body_path.read_bytes(),
                url=url,
                status="cached",
                http_status=meta.get("http_status"),
                collected_at=collected,
                latency_ms=0,
            )
        except (OSError, ValueError, KeyError):
            return None

    def _write_cache(self, url: str, res: FetchResult) -> None:
        p = self._cache_path(url)
        try:
            p.with_suffix(".body").write_bytes(res.content)
            p.write_text(
                json.dumps(
                    {
                        "url": url,
                        "collected_at": res.collected_at.isoformat(),
                        "http_status": res.http_status,
                    }
                ),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover
            log.warning("cache write falhou: %s", exc)

    # -- fetch -------------------------------------------------------------
    def get(
        self,
        url: str,
        *,
        provider: str,
        ttl_s: float = 600,
        min_interval_s: float = 2.0,
        retries: int = 3,
        force: bool = False,
    ) -> FetchResult:
        if not force:
            cached = self._read_cache(url, ttl_s)
            if cached is not None:
                self._emit(provider, url, "cached", cached.http_status, 0, None)
                return cached

        st = self._host(url, min_interval_s)
        if self._is_open(st):
            self._emit(provider, url, "blocked", None, None, "circuit open")
            raise CircuitOpen(f"circuito aberto para {urlparse(url).netloc}")

        last_exc: Exception | None = None
        for attempt in range(retries):
            with st.lock:
                wait = st.min_interval_s - (time.monotonic() - st.last_request_at)
                if wait > 0:
                    time.sleep(wait)
                st.last_request_at = time.monotonic()
            t0 = time.perf_counter()
            try:
                resp = self._client.get(url)
                latency = int((time.perf_counter() - t0) * 1000)
                if resp.status_code in (401, 403, 429) or "cf-mitigated" in resp.headers:
                    st.consecutive_failures += 1
                    if st.consecutive_failures >= self.FAILURE_THRESHOLD:
                        st.opened_at = time.monotonic()
                    self._emit(provider, url, "blocked", resp.status_code, latency, "anti-bot/limite")
                    raise SourceBlocked(f"{resp.status_code} em {url}")
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError("5xx", request=resp.request, response=resp)
                if resp.status_code >= 400:
                    self._emit(provider, url, "error", resp.status_code, latency, resp.reason_phrase)
                    raise SourceError(f"{resp.status_code} em {url}")
                st.consecutive_failures = 0
                st.opened_at = None
                res = FetchResult(
                    content=resp.content,
                    url=url,
                    status="ok",
                    http_status=resp.status_code,
                    collected_at=datetime.utcnow(),
                    latency_ms=latency,
                )
                self._write_cache(url, res)
                self._emit(provider, url, "ok", resp.status_code, latency, None)
                return res
            except SourceBlocked:
                raise
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                st.consecutive_failures += 1
                if st.consecutive_failures >= self.FAILURE_THRESHOLD:
                    st.opened_at = time.monotonic()
                backoff = 1.5 * (2**attempt)
                log.warning("%s tentativa %s falhou (%s); backoff %.1fs", url, attempt + 1, exc, backoff)
                time.sleep(backoff)
        self._emit(provider, url, "error", None, None, str(last_exc))
        raise SourceError(f"falha após {retries} tentativas: {url}") from last_exc

    def _emit(self, provider, url, status, http_status, latency_ms, error) -> None:
        if self._log_sink is None:
            return
        try:
            self._log_sink(provider, url, status, http_status, latency_ms, error)
        except Exception:  # pragma: no cover
            log.exception("log sink falhou")


_client: HttpClient | None = None
_client_lock = threading.Lock()


def get_http_client() -> HttpClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = HttpClient()
        return _client
