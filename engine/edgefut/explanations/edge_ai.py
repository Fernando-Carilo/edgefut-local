"""Edge AI — perguntas e respostas construídas SOMENTE a partir da análise estruturada.

Sem LLM: casamento de intenção por palavras-chave + templates com números.
Com Ollama (opcional): o LLM recebe os fatos estruturados e só pode reformular.
"""

from __future__ import annotations

import re

from ..domain.analysis import MatchAnalysis
from .templates import (
    NO_BET_LABELS,
    explain_recommendation,
    form_sentence,
    goals_sentence,
    num,
    pct,
    strength_sentence,
    venue_sentence,
)


def _has(q: str, *words: str) -> bool:
    return any(re.search(rf"\b{w}", q) for w in words)


def facts(a: MatchAnalysis) -> dict:
    """Fatos estruturados que alimentam qualquer resposta (template ou LLM)."""
    s = a.simulation
    return {
        "evento": f"{a.event.home_name} x {a.event.away_name}",
        "competicao": a.event.competition_name,
        "venue": a.venue.model_dump(),
        "elo": {a.home.name: a.home.elo, a.away.name: a.away.elo},
        "forca": {a.home.name: a.home.strength_score, a.away.name: a.away.strength_score},
        "forma": {a.home.name: a.home.form, a.away.name: a.away.form},
        "janelas_10": {
            a.home.name: a.home.windows.get("all_10").model_dump() if a.home.windows.get("all_10") else None,
            a.away.name: a.away.windows.get("all_10").model_dump() if a.away.windows.get("all_10") else None,
        },
        "simulacao": s.model_dump() if s else None,
        "escanteios": a.corners.model_dump(),
        "cartoes": a.cards.model_dump(),
        "finalizacoes": a.shots.model_dump(),
        "recomendacoes": [r.model_dump() for r in a.recommendations[:8]],
        "no_bet": a.no_bet.model_dump(),
        "confianca": a.confidence.model_dump(),
        "qualidade_dados": a.data_quality.score,
        "h2h": a.h2h.model_dump() if a.h2h else None,
    }


def answer(question: str, a: MatchAnalysis) -> str:
    q = question.lower().strip()
    h, w, s = a.home, a.away, a.simulation

    if _has(q, "casa", "mandante", "neutro", "estádio", "estadio", "onde"):
        return venue_sentence(a)

    if _has(q, "finaliza", "chute", "finalizações", "finalizacoes"):
        if not a.shots.available:
            return f"Não há dados de finalizações para estas equipes ({a.shots.note})."
        more = h.name if (a.shots.p_home_more or 0) >= (a.shots.p_away_more or 0) else w.name
        p = max(a.shots.p_home_more or 0, a.shots.p_away_more or 0)
        wh, ww = h.windows.get("all_10"), w.windows.get("all_10")
        hist = ""
        if wh and ww and wh.shots_for is not None and ww.shots_for is not None:
            hist = f"Nos últimos 10 jogos, {h.name} teve média ponderada de {wh.shots_for:.1f} finalizações contra {ww.shots_for:.1f} de {w.name}. "
        return (
            hist
            + f"O modelo espera {num(a.shots.expected_home, 1)} chutes ({num(a.shots.extra.get('sot_home'), 1)} no alvo) para {h.name} e "
            f"{num(a.shots.expected_away, 1)} ({num(a.shots.extra.get('sot_away'), 1)} no alvo) para {w.name}; "
            f"{pct(p)} de probabilidade de {more} finalizar mais."
        )

    if _has(q, "escanteio", "corner"):
        if not a.corners.available:
            return f"Não há dados de escanteios para estas equipes ({a.corners.note})."
        lo, hi = a.corners.likely_range or (None, None)
        lines = ", ".join(f"Over {k}: {pct(v)}" for k, v in list(a.corners.over.items())[:4])
        return (
            f"Escanteios esperados: {num(a.corners.expected_total, 1)} ({num(a.corners.expected_home, 1)} {h.name} + "
            f"{num(a.corners.expected_away, 1)} {w.name}); intervalo provável {lo:.0f}–{hi:.0f}. {lines}. "
            f"Amostra: {a.corners.sample_size} jogos."
        )

    if _has(q, "cart", "amarelo", "vermelho"):
        if not a.cards.available:
            return f"Não há dados de cartões para estas equipes ({a.cards.note})."
        lines = ", ".join(f"Over {k}: {pct(v)}" for k, v in list(a.cards.over.items())[:4])
        return f"Cartões esperados: {num(a.cards.expected_total, 1)} no total. {lines}. {a.cards.note or ''}".strip()

    if _has(q, "forte", "força", "forca", "melhor time", "favorito"):
        return strength_sentence(a) + " " + (
            f"Nas simulações: {h.name} {pct(s.p_home)}, empate {pct(s.p_draw)}, {w.name} {pct(s.p_away)}." if s else ""
        )

    if _has(q, "fase", "forma", "momento", "últimos", "ultimos"):
        return form_sentence(a)

    if _has(q, "confronto", "h2h", "histórico", "historico", "retrospecto"):
        if not a.h2h:
            return "Sem confrontos diretos no dataset histórico disponível."
        hh = a.h2h
        return (
            f"Últimos {hh.matches} confrontos: {hh.home_wins} vitórias de {h.name}, {hh.draws} empates, {hh.away_wins} de {w.name}; "
            f"gols {hh.home_goals}×{hh.away_goals} (média {num(hh.avg_goals)} por jogo), BTTS {pct(hh.btts_pct)}, Over 2.5 {pct(hh.over25_pct)}. "
            f"{hh.weight_note}"
        )

    if _has(q, "placar", "resultado exato", "score"):
        if not s:
            return "Sem simulação disponível para placares."
        top = ", ".join(f"{sc} ({pct(p, 1)})" for sc, p in s.top_scores[:5])
        return f"Placares mais frequentes em {s.simulations} simulações: {top}. Frequência de simulação, não certeza."

    if _has(q, "gol", "over", "under", "ambas", "btts"):
        return goals_sentence(a)

    if _has(q, "risco", "confian", "seguro", "confiável", "confiavel", "dados"):
        comps = "; ".join(f"{c.name} {c.value * 100:.0f}%" for c in a.confidence.components[:5])
        nb = f" NO BET: {NO_BET_LABELS.get(a.no_bet.reason or '', a.no_bet.reason)}." if a.no_bet.no_bet else ""
        return (
            f"Confiança {a.confidence.score:.0f}/100 (grade {a.confidence.grade}); qualidade dos dados {a.data_quality.score:.0f}%. "
            f"Componentes: {comps}.{nb}"
        )

    if _has(q, "vale", "entrar", "recomend", "melhor mercado", "aposta", "edge", "valor", "1x", "x2"):
        recs = [r for r in a.recommendations if r.status in ("RECOMMENDED", "WATCH")][:3]
        if a.no_bet.no_bet and not recs:
            return f"NÃO ENTRAR: {NO_BET_LABELS.get(a.no_bet.reason or '', a.no_bet.reason)}. {a.no_bet.detail or ''}".strip()
        # comparação explícita entre mercados citados
        cited = [r for r in a.recommendations if r.model_prob and (r.selection_name.lower() in q or r.market_label.lower() in q)]
        pool = cited if len(cited) >= 2 else recs
        if not pool:
            return f"Nenhuma seleção com edge suficiente. {a.no_bet.detail or ''}".strip()
        return " ".join(explain_recommendation(a, r) for r in pool)

    # fallback: resumo completo
    return a.explanation
