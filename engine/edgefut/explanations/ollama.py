"""Ollama opcional: reformula/explica a partir de fatos estruturados. Nunca prevê."""

from __future__ import annotations

import json
import logging

import httpx

from ..core.config import settings

log = logging.getLogger(__name__)

SYSTEM = (
    "Você é o assistente EDGE AI de um app de análise de futebol. Responda em português do Brasil, de forma "
    "curta e citando números. REGRA ABSOLUTA: use somente os fatos JSON fornecidos; não invente estatísticas, "
    "jogadores, árbitros ou probabilidades. Se a informação não estiver nos fatos, diga que não está disponível. "
    "Nunca prometa ganho; fale em vantagem estatística estimada."
)


def is_available(timeout: float = 1.5) -> bool:
    if not settings.ollama_enabled:
        return False
    try:
        r = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=timeout)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def rephrase(question: str, facts: dict, template_answer: str, timeout: float = 30.0) -> str | None:
    if not is_available():
        return None
    prompt = (
        f"FATOS (JSON):\n{json.dumps(facts, ensure_ascii=False, default=str)[:12000]}\n\n"
        f"RESPOSTA-BASE DO MOTOR QUANTITATIVO:\n{template_answer}\n\n"
        f"PERGUNTA DO USUÁRIO: {question}\n\n"
        "Reescreva a resposta-base de forma clara, mantendo todos os números e sem acrescentar fatos."
    )
    try:
        r = httpx.post(
            f"{settings.ollama_base_url.rstrip('/')}/api/generate",
            json={"model": settings.ollama_model, "prompt": prompt, "system": SYSTEM, "stream": False},
            timeout=timeout,
        )
        r.raise_for_status()
        return (r.json().get("response") or "").strip() or None
    except (httpx.HTTPError, ValueError) as exc:
        log.info("ollama indisponível: %s", exc)
        return None
