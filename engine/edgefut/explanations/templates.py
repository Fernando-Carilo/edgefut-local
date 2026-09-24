"""ExplanationEngine baseado em templates (PT-BR). Só cita números que existem na análise."""

from __future__ import annotations

from ..domain.analysis import MatchAnalysis, Recommendation

NO_BET_LABELS = {
    "LOW_DATA": "dados insuficientes",
    "LOW_CONFIDENCE": "confiança baixa",
    "NO_EDGE": "sem edge estatístico",
    "MODEL_DISAGREEMENT": "divergência entre modelos",
    "UNRELIABLE_SOURCE": "fonte não confiável",
    "SMALL_SAMPLE": "amostra pequena",
    "LINEUP_UNCERTAINTY": "escalação não confirmada",
    "EXTREME_ODDS_MOVEMENT": "movimento extremo de odds",
    "UNSUPPORTED_COMPETITION": "competição sem histórico mapeado",
    "STALE_DATA": "dados expirados (odds ou histórico desatualizados)",
    "QUALITY_GATE": "não passou no quality gate",
    "VALUE_DISABLED": "VALUE desativado até validação por mercado (research signal)",
}


def pct(x: float | None, nd: int = 0) -> str:
    return "n/d" if x is None else f"{100 * x:.{nd}f}%"


def num(x: float | None, nd: int = 2) -> str:
    return "n/d" if x is None else f"{x:.{nd}f}"


def venue_sentence(a: MatchAnalysis) -> str:
    v = a.venue
    if v.status == "NEUTRAL":
        loc = f" ({v.city}, {v.country})" if v.city else ""
        return f"A partida é em campo neutro{loc}; o modelo não aplica vantagem de mandante."
    if v.status == "CONFIRMED_HOME":
        return f"{a.event.home_name} joga em casa (mandante confirmado por {v.source})."
    return (
        f"Mandante não confirmado: a Superbet lista {a.event.home_name} primeiro, mas não há confirmação de "
        f"estádio; o modelo aplica {int(v.home_advantage_weight * 100)}% da vantagem de mandante."
    )


def strength_sentence(a: MatchAnalysis) -> str:
    h, w = a.home, a.away
    if h.elo is None or w.elo is None:
        return "Não há ELO disponível para as duas equipes."
    stronger = h if h.elo >= w.elo else w
    weaker = w if stronger is h else h
    return (
        f"Pelo ELO, {stronger.name} ({stronger.elo:.0f}) é mais forte que {weaker.name} ({weaker.elo:.0f}); "
        f"força relativa {num(stronger.strength_score, 0)} × {num(weaker.strength_score, 0)}."
    )


def form_sentence(a: MatchAnalysis) -> str:
    parts = []
    for t in (a.home, a.away):
        w = t.windows.get("all_10")
        if w and w.goals_for is not None:
            parts.append(
                f"{t.name}: forma {' '.join(t.form) or 'n/d'}, {w.points_per_game:.2f} pts/jogo, "
                f"{w.goals_for:.2f} gols pró e {w.goals_against:.2f} contra nos últimos {w.n} (ponderado)"
            )
    return "; ".join(parts) + "." if parts else "Sem forma recente disponível."


def goals_sentence(a: MatchAnalysis) -> str:
    s = a.simulation
    if s is None:
        return "Sem simulação de gols (modelo indisponível)."
    n_sim = f"{s.simulations:,}".replace(",", ".")
    return (
        f"Em {n_sim} simulações ({s.base_model}): {a.event.home_name} {pct(s.p_home)}, empate "
        f"{pct(s.p_draw)}, {a.event.away_name} {pct(s.p_away)}. Gols esperados {num(s.expected_goals_home)} × "
        f"{num(s.expected_goals_away)}; Over 2.5 {pct(s.over.get('2.5'))}, BTTS {pct(s.btts)}. "
        f"Placar mais frequente: {s.most_likely_score} ({pct(s.top_scores[0][1], 1) if s.top_scores else 'n/d'}) — "
        f"frequência, não certeza."
    )


def extras_sentence(a: MatchAnalysis) -> str:
    parts = []
    if a.shots.available:
        parts.append(
            f"finalizações esperadas {num(a.shots.expected_home, 1)} ({num(a.shots.extra.get('sot_home'), 1)} no alvo) × "
            f"{num(a.shots.expected_away, 1)} ({num(a.shots.extra.get('sot_away'), 1)} no alvo)"
        )
    if a.corners.available and a.corners.likely_range:
        lo, hi = a.corners.likely_range
        parts.append(f"escanteios esperados {num(a.corners.expected_total, 1)} (intervalo provável {lo:.0f}–{hi:.0f})")
    if a.cards.available:
        parts.append(f"cartões esperados {num(a.cards.expected_total, 1)}")
    return ("Além disso: " + "; ".join(parts) + ".") if parts else ""


def recommendation_sentence(a: MatchAnalysis) -> str:
    recs = [r for r in a.recommendations if r.status == "RECOMMENDED"]
    if a.no_bet.no_bet or not recs:
        reason = NO_BET_LABELS.get(a.no_bet.reason or "", a.no_bet.reason or "")
        return f"NENHUMA ENTRADA RECOMENDADA — {reason}. {a.no_bet.detail or ''}".strip()
    best = recs[0]
    return (
        f"Melhor mercado identificado: {best.market_label} — {best.selection_name} @ {best.odd:.2f}. "
        f"Modelo {pct(best.model_prob)} × mercado {pct(best.market_prob)} → edge {best.edge_pp:+.1f} pp, "
        f"EV {best.ev_pct:+.1f}%, confiança {best.confidence_grade}. Não é garantia: é uma vantagem estatística estimada."
    )


def explain(a: MatchAnalysis) -> str:
    sentences = [
        venue_sentence(a),
        strength_sentence(a),
        form_sentence(a),
        goals_sentence(a),
        extras_sentence(a),
        f"Qualidade dos dados {a.data_quality.score:.0f}% e confiança {a.confidence.score:.0f}/100 ({a.confidence.grade}).",
        recommendation_sentence(a),
    ]
    return " ".join(s for s in sentences if s)


def explain_recommendation(a: MatchAnalysis, r: Recommendation) -> str:
    base = (
        f"{r.market_label} — {r.selection_name}: o modelo estima {pct(r.model_prob, 1)} contra "
        f"{pct(r.market_prob, 1)} implícitos na odd {r.odd:.2f}"
        f"{' (margem removida)' if r.market_prob_is_fair else ' (margem não removida)'}. "
        f"Edge {r.edge_pp:+.1f} pp, EV {r.ev_pct:+.1f}%."
    )
    if r.status == "NO_BET":
        reasons = ", ".join(NO_BET_LABELS.get(x, x) for x in r.reasons)
        return base + f" NO BET: {reasons}."
    if r.status == "WATCH":
        return base + " Em observação: não atende a todos os limiares para recomendação."
    return base + f" Confiança {r.confidence_grade} ({r.confidence_score:.0f}/100), Opportunity Score {r.opportunity_score:.0f}."
