"""Pipeline de análise por evento.

COLETA → NORMALIZAÇÃO → QUALIDADE → FEATURES → MODELOS → SIMULAÇÃO → PROBABILIDADE →
ODD JUSTA → COMPARAÇÃO → EDGE → RISCO → RECOMENDAÇÃO/NO BET → EXPLICAÇÃO
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..collectors import SuperbetSync, latest_odds_rows, odds_history_map
from ..collectors.sync import IdentityAssigner
from ..core import versions
from ..core.config import settings
from ..db.models import Competition, Event, Favorite, PredictionSnapshot, Setting
from ..domain.analysis import (
    CountDistribution,
    EventSummary,
    MatchAnalysis,
    TeamProfile,
    VenueInfo,
)
from ..domain import freshness as fresh
from ..domain.provenance import Provenance, SourceAttempt
from ..explanations import explain, explain_recommendation
from ..features.data_quality import compute_data_quality
from ..features.h2h import summarize_h2h
from ..features.strength import (
    attack_defense,
    build_windows,
    form_string,
    league_averages,
    recent_matches,
    strength_score,
    to_sides,
)
from ..features.venue import resolve_venue
from ..models.counts import cards_engine, corners_engine, shots_engine
from ..models.dixon_coles import dc_cache, dixon_coles_output, fit_dixon_coles
from ..models.bivariate_poisson import bivariate_output, bp_cache, fit_bivariate_poisson
from ..models.calibration import Calibrator, load_calibrators
from ..models.ensemble import compare, load_weights
from ..models.elo import elo_cache, elo_output, fit_elo
from ..models.poisson import poisson_model
from ..normalization import CompetitionProfile, classify_competition, is_womens
from ..odds import build_markets
from ..providers import SourceResolver
from ..providers.historical import get_store
from ..quality import conflicts_for_event, record_conflict
from ..recommendations import compute_confidence, evaluate
from ..simulation.monte_carlo import simulate

log = logging.getLogger(__name__)

_cache: dict[int, tuple[datetime, MatchAnalysis]] = {}
_cache_lock = threading.Lock()
CACHE_TTL = timedelta(minutes=5)


def cached_analysis(event_id: int) -> MatchAnalysis | None:
    with _cache_lock:
        item = _cache.get(event_id)
    if item and datetime.utcnow() - item[0] < CACHE_TTL:
        return item[1]
    return None


def all_cached() -> list[MatchAnalysis]:
    """Última análise conhecida de cada evento futuro (o radar as substitui a cada ciclo).

    Não expira por TTL: melhor mostrar uma análise de 30 min atrás, com carimbo de
    tempo, do que uma tela vazia enquanto o próximo ciclo roda.
    """
    now = datetime.utcnow()
    with _cache_lock:
        return [a for _, a in _cache.values() if a.event.kickoff_utc > now - timedelta(hours=2)]


_calibrators: dict[str, Calibrator] | None = None


def calibrators(session: Session) -> dict[str, Calibrator]:
    global _calibrators
    if _calibrators is None:
        try:
            _calibrators = load_calibrators(session)
        except Exception as exc:  # noqa: BLE001 — sem calibradores → probabilidades cruas
            log.warning("calibradores indisponíveis: %s", exc)
            _calibrators = {}
    return _calibrators


_ctx_cache: dict[str, tuple[datetime, object]] = {}
CTX_TTL = timedelta(seconds=60)


def _ctx(key: str, loader):
    """Contexto compartilhado por várias análises no mesmo ciclo (saúde do provider, performance)."""
    item = _ctx_cache.get(key)
    if item and datetime.utcnow() - item[0] < CTX_TTL:
        return item[1]
    value = loader()
    _ctx_cache[key] = (datetime.utcnow(), value)
    return value


def provider_status(session: Session) -> str | None:
    def load():
        try:
            from ..quality.health import superbet_health

            return superbet_health(session).status
        except Exception as exc:  # noqa: BLE001
            log.warning("saúde do provider indisponível: %s", exc)
            return None

    return _ctx("provider_status", load)  # type: ignore[return-value]


def historical_by_market(session: Session) -> dict[str, dict]:
    """ROI e N por mercado a partir do resumo de performance persistido (apostas settled reais)."""

    def load():
        row = session.get(Setting, "performance_summary")
        data = (row.value or {}) if row else {}
        out: dict[str, dict] = {}
        for mk, m in (data.get("by_market") or {}).items():
            out[mk] = {"roi": m.get("roi"), "n": int(m.get("bets") or 0), "status": m.get("sample_status")}
        return out

    return _ctx("historical_by_market", load)  # type: ignore[return-value]


def datasets_with_odds(store) -> set[str]:
    return _ctx("datasets_with_odds", lambda: store.datasets_with_odds())  # type: ignore[return-value]


def settled_by_competition(session: Session) -> dict[str, int]:
    def load():
        row = session.get(Setting, "performance_summary")
        data = (row.value or {}) if row else {}
        return {name: int(m.get("bets") or 0) for name, m in (data.get("by_competition") or {}).items()}

    return _ctx("settled_by_competition", load)  # type: ignore[return-value]


def evidence_level(session: Session, store, codes: list[str], competition_name: str | None) -> str:
    """SETTLED (N>=30 apostas liquidadas na competição) → BACKTEST_ODDS (dataset com odds) → MODEL_ONLY."""
    from ..backtesting.performance import MIN_SAMPLE_FOR_METRICS

    if competition_name and settled_by_competition(session).get(competition_name, 0) >= MIN_SAMPLE_FOR_METRICS:
        return "SETTLED"
    if codes and all(c in datasets_with_odds(store) for c in codes):
        return "BACKTEST_ODDS"
    return "MODEL_ONLY"


def invalidate_cache() -> None:
    global _calibrators
    with _cache_lock:
        _cache.clear()
    elo_cache.clear()
    dc_cache.clear()
    bp_cache.clear()
    _calibrators = None
    _ctx_cache.clear()


def event_summary(row: Event, session: Session | None = None, main_odds: dict[str, float] | None = None) -> EventSummary:
    fav = False
    if session is not None:
        fav = session.get(Favorite, row.id) is not None
    return EventSummary(
        id=row.id,
        home_name=row.home_name,
        away_name=row.away_name,
        kickoff_utc=row.kickoff_utc,
        competition_name=row.competition_name,
        category_name=row.category_name,
        status=row.status,
        market_count=row.market_count,
        event_url=row.event_url,
        venue_status=row.venue_status,  # type: ignore[arg-type]
        neutral_venue=row.neutral_venue,
        odds_collected_at=row.odds_collected_at,
        main_odds=main_odds,
        is_favorite=fav,
    )


def _profile_for(session: Session, row: Event) -> CompetitionProfile:
    comp = session.get(Competition, row.competition_id) if row.competition_id else None
    return classify_competition(
        row.competition_id,
        row.competition_name or (comp.name if comp else None),
        comp.category_id if comp else None,
        row.category_name,
        womens_hint=is_womens(row.home_name) or is_womens(row.away_name),
    )


def _team_profile(name: str, match, df: pd.DataFrame, la, is_home: bool, elo: float | None, prov_url: str | None) -> TeamProfile:
    if match.canonical is None or df is None or df.empty:
        return TeamProfile(
            name=name, canonical=match.canonical, dataset_code=match.dataset_code, match_method=match.method,
            match_confidence=match.confidence, is_national=match.is_national, elo=elo, sample_size=0,
        )
    sides = to_sides(df, match.canonical)
    windows = build_windows(sides)
    att, de = attack_defense(windows, la, is_home)
    collected = df["collected_at"].max() if "collected_at" in df and df["collected_at"].notna().any() else None
    return TeamProfile(
        name=name,
        canonical=match.canonical,
        dataset_code=match.dataset_code,
        match_method=match.method,
        match_confidence=match.confidence,
        is_national=match.is_national,
        elo=round(elo, 1) if elo is not None else None,
        strength_score=strength_score(elo, att, de),
        attack=att,
        defense=de,
        form=form_string(sides),
        recent=recent_matches(sides),
        windows=windows,
        sample_size=len(sides),
        provenance=Provenance(
            source=str(df["source"].iloc[0]) if "source" in df else "historical",
            source_url=prov_url or (str(df["source_url"].iloc[0]) if "source_url" in df else None),
            collected_at=collected.to_pydatetime() if collected is not None and not pd.isna(collected) else None,
            confidence=match.confidence,
            sample_size=len(sides),
            field="hg, ag, hs, hst, hc, hy, hr",
        ),
    )


def analyze_event(
    session: Session,
    event_id: int,
    *,
    simulations: int | None = None,
    force_odds: bool = False,
    save_snapshot: bool = True,
    use_cache: bool = True,
) -> MatchAnalysis:
    if use_cache and not force_odds:
        hit = cached_analysis(event_id)
        if hit is not None and (simulations is None or hit.simulation is None or hit.simulation.simulations == simulations):
            return hit

    sync = SuperbetSync()
    odds_status = sync.sync_odds(session, event_id, force=force_odds)
    # Libera o lock de escrita antes do ajuste dos modelos (pode levar segundos).
    session.commit()
    row = session.get(Event, event_id)
    if row is None:
        raise LookupError(f"evento {event_id} não encontrado")

    warnings: list[str] = []
    attempts: list[SourceAttempt] = []
    if not odds_status.get("ok"):
        warnings.append(f"Odds não atualizadas: {odds_status.get('error')}")
        attempts.append(SourceAttempt(provider="superbet", status="unavailable", detail=odds_status.get("error")))
    else:
        attempts.append(SourceAttempt(provider="superbet", status="cached" if odds_status.get("skipped") else "ok"))

    profile = _profile_for(session, row)
    if row.canonical_event_id is None:
        # evento visto antes da migração v2 ou fora da última janela de sync
        IdentityAssigner(session).assign(row, session.get(Competition, row.competition_id) if row.competition_id else None)
        session.commit()
    resolver = SourceResolver()
    resolved = resolver.resolve(profile, row.home_name, row.away_name)
    attempts.extend(resolved.attempts)
    store = get_store()

    # ---- histórico (as_of: nunca dados posteriores ao kickoff nem ao agora) ----
    as_of = min(datetime.utcnow(), row.kickoff_utc)
    codes = resolved.dataset_codes
    home_df = store.team_matches(resolved.home.canonical, resolved.home_datasets, before=as_of, limit=40) if resolved.home.canonical else pd.DataFrame()
    away_df = store.team_matches(resolved.away.canonical, resolved.away_datasets, before=as_of, limit=40) if resolved.away.canonical else pd.DataFrame()
    comp_df = store.competition_matches(codes, before=as_of) if codes else pd.DataFrame()
    if profile.is_national_teams and not comp_df.empty:
        comp_df = comp_df[comp_df["date"] >= pd.Timestamp(datetime.utcnow() - timedelta(days=3 * 365))]
    la = league_averages(comp_df)

    # ---- venue -------------------------------------------------------------
    override = None
    if row.venue_source == "user":
        override = VenueInfo(
            status=row.venue_status, neutral=row.neutral_venue, name=row.venue_name, city=row.venue_city,  # type: ignore[arg-type]
            country=row.venue_country, source="user", confidence=1.0, note="Definido manualmente pelo usuário.",
        )
    detected_venue = resolve_venue(
        profile=profile, home_canonical=resolved.home.canonical, away_canonical=resolved.away.canonical,
        kickoff=row.kickoff_utc, store=store, override=None,
    )
    venue = resolve_venue(
        profile=profile, home_canonical=resolved.home.canonical, away_canonical=resolved.away.canonical,
        kickoff=row.kickoff_utc, store=store, override=override,
    ) if override is not None else detected_venue
    _record_venue_conflicts(session, row, detected_venue, venue)
    if row.venue_source != "user":
        row.venue_status = venue.status
        row.neutral_venue = venue.neutral
        row.venue_city = venue.city
        row.venue_country = venue.country
        row.venue_source = venue.source
        row.venue_confidence = venue.confidence

    # ---- ELO -----------------------------------------------------------------
    elo_key = ("elo", tuple(codes), len(comp_df))
    if codes and not comp_df.empty:
        elo_df = store.competition_matches(codes, since=datetime(2000, 1, 1), before=as_of) if profile.is_national_teams else comp_df
        table = elo_cache.get(elo_key, lambda: fit_elo(elo_df, profile.is_national_teams))
    else:
        table = fit_elo(pd.DataFrame(), False)
    elo_out = elo_output(
        table, resolved.home.canonical, resolved.away.canonical, venue.home_advantage_weight,
        profile.is_national_teams, la.draw_rate if la else None,
    )
    elo_diff = (elo_out.home_elo - elo_out.away_elo) if elo_out.available else None  # type: ignore[operator]

    # ---- perfis -----------------------------------------------------------------
    src_url = None
    if codes:
        r0 = resolver.football_data.league_url(codes[0], "atual") if codes[0] != "INTL" else None
        src_url = r0
    home = _team_profile(row.home_name, resolved.home, home_df, la, True, table.get(resolved.home.canonical) if resolved.home.canonical else None, None)
    away = _team_profile(row.away_name, resolved.away, away_df, la, False, table.get(resolved.away.canonical) if resolved.away.canonical else None, None)

    # ---- H2H --------------------------------------------------------------------
    h2h = None
    if resolved.home.canonical and resolved.away.canonical and codes:
        h2h_df = store.h2h(resolved.home.canonical, resolved.away.canonical, codes, limit=10, before=as_of)
        h2h = summarize_h2h(h2h_df, resolved.home.canonical, resolved.away.canonical, str(h2h_df["source"].iloc[0]) if not h2h_df.empty else "historical", str(h2h_df["source_url"].iloc[0]) if not h2h_df.empty else None)

    # ---- modelos de gols ---------------------------------------------------------
    poisson = poisson_model(
        la=la, attack_home=home.attack, defense_home=home.defense, attack_away=away.attack, defense_away=away.defense,
        home_adv_weight=venue.home_advantage_weight, fit_matches=la.matches if la else 0,
    )
    dc_key = ("dc", tuple(codes), len(comp_df))
    dc_params = dc_cache.get(dc_key, lambda: fit_dixon_coles(comp_df)) if codes and not comp_df.empty else None
    dc = dixon_coles_output(dc_params, resolved.home.canonical, resolved.away.canonical, venue.home_advantage_weight)

    bp_key = ("bp", tuple(codes), len(comp_df))
    bp_params = bp_cache.get(bp_key, lambda: fit_bivariate_poisson(comp_df)) if codes and not comp_df.empty else None
    bp = bivariate_output(bp_params, resolved.home.canonical, resolved.away.canonical, venue.home_advantage_weight)

    # ---- comparação de modelos + consenso (ensemble-v1) ------------------------
    comparison, consensus, cons_matrix = compare(
        poisson=poisson, dixon_coles=dc, bivariate=bp, weights=load_weights(session, codes[0] if codes else None),
    )
    disagreement = comparison.max_disagreement_pp

    base = consensus if consensus is not None else (dc if dc.available else poisson)
    n_sim = simulations or settings.default_simulations
    sim = simulate(base, n_sim, seed=event_id, matrix=cons_matrix if consensus is not None else None) if base.available else None

    # ---- contagens ---------------------------------------------------------------
    count_prov = home.provenance
    corners = corners_engine(home.windows, away.windows, la, elo_diff, count_prov)
    cards = cards_engine(home.windows, away.windows, la, count_prov)
    shots = shots_engine(home.windows, away.windows, la, elo_diff, count_prov)

    # ---- odds --------------------------------------------------------------------
    rows, opening, collected_at, odds_url = latest_odds_rows(session, event_id)
    history = odds_history_map(session, event_id, before=row.kickoff_utc)
    markets = build_markets(rows, opening, collected_at, odds_url, history=history)
    has_1x2 = any(m.market_key == "1X2" for m in markets)

    # ---- freshness (nunca usar dado EXPIRED silenciosamente) ---------------------
    freshness = _freshness(collected_at, home, away, comp_df, venue, bool(markets))
    freshness_status = fresh.worst(freshness, {"odds", "form", "history"})
    stale_reason: str | None = None
    odds_f = next((f for f in freshness if f.kind == "odds"), None)
    hist_f = next((f for f in freshness if f.kind == "history"), None)
    if odds_f is not None and odds_f.status == "EXPIRED":
        stale_reason = f"Odds coletadas {fresh.describe_age(odds_f.age_seconds)} (limite {odds_f.valid_until:%d/%m %H:%M} UTC); Superbet indisponível para atualizar."
    elif hist_f is not None and hist_f.status == "EXPIRED":
        stale_reason = f"Histórico atualizado {fresh.describe_age(hist_f.age_seconds)}; acima do limite de validade."
    if stale_reason:
        warnings.append(f"STALE_DATA: {stale_reason}")

    # ---- qualidade / confiança ---------------------------------------------------
    dq = compute_data_quality(
        home=home, away=away, venue=venue, odds_collected_at=collected_at, has_1x2=has_1x2,
        has_corners_data=corners.available, has_cards_data=cards.available, has_shots_data=shots.available,
        dc_available=dc.available,
    )
    calibration, cal_n = market_calibration(session)
    n_models = sum(1 for m in (poisson, dc, bp) if m is not None and m.available)
    conf = compute_confidence(
        data_quality=dq, home=home, away=away, venue=venue, model_disagreement_pp=disagreement,
        market_calibration=calibration.get("1X2") if calibration else None, calibration_samples=cal_n,
        freshness=freshness, n_models=n_models,
    )

    unreliable = any(a.status in ("unavailable", "error") and a.provider != "superbet" for a in attempts)
    evidence = evidence_level(session, store, list(codes), row.competition_name)
    if consensus is not None:
        model_source = f"consenso de {n_models} modelos de gols (pesos {comparison.weights_source}) + Monte Carlo"
    elif dc.available:
        model_source = "Dixon-Coles + Monte Carlo"
    else:
        model_source = "Poisson por força + Monte Carlo"
    recs, verdict, event_why_not = evaluate(
        markets=markets, sim=sim, corners=corners, cards=cards, confidence=conf, data_quality=dq,
        supported=profile.supported, teams_resolved=resolved.ok, min_sample=min(home.sample_size, away.sample_size),
        model_disagreement_pp=disagreement, unreliable_source=unreliable, market_calibration=calibration,
        stale_data=stale_reason, calibrators=calibrators(session), competition=row.competition_name,
        odds_freshness=odds_f.status if odds_f is not None else None,
        odds_age_seconds=odds_f.age_seconds if odds_f is not None else None,
        provider_status=provider_status(session), n_models=n_models, model_source=model_source,
        historical=historical_by_market(session), evidence=evidence,
    )
    if not profile.supported and profile.reason:
        warnings.append(profile.reason)
    if resolved.home.canonical is None and profile.supported:
        warnings.append(f"Time não identificado no dataset: {row.home_name}")
    if resolved.away.canonical is None and profile.supported:
        warnings.append(f"Time não identificado no dataset: {row.away_name}")

    opp = max([r.opportunity_score for r in recs if r.status in ("RECOMMENDED", "WATCH")], default=0.0)

    session.flush()
    conflicts = conflicts_for_event(session, event_id)

    sources: list[Provenance] = []
    if markets:
        sources.append(Provenance(source="superbet", source_url=odds_url, collected_at=collected_at, confidence=1.0, sample_size=len(rows), field="odds (price) por mercado/seleção"))
    for t in (home, away):
        if t.provenance:
            sources.append(t.provenance)
    if h2h and h2h.provenance:
        sources.append(h2h.provenance)
    if venue.source:
        sources.append(Provenance(source=venue.source, confidence=venue.confidence, sample_size=0, field="venue/neutral", note=venue.note))

    analysis = MatchAnalysis(
        generated_at=datetime.utcnow(),
        pipeline_version=versions.PIPELINE,
        model_versions=dict(versions.ALL_MODELS),
        event=event_summary(row, session, _main_odds(markets)),
        venue=venue,
        home=home,
        away=away,
        h2h=h2h,
        elo=elo_out,
        poisson=poisson,
        dixon_coles=dc,
        bivariate_poisson=bp,
        consensus=consensus,
        model_comparison=comparison.model_dump(mode="json"),
        simulation=sim,
        corners=corners,
        cards=cards,
        shots=shots,
        markets=markets,
        recommendations=recs,
        no_bet=verdict,
        data_quality=dq,
        confidence=conf,
        opportunity_score=opp,
        model_disagreement_pp=disagreement,
        explanation="",
        sources=sources,
        source_attempts=attempts,
        warnings=warnings,
        freshness=freshness,
        freshness_status=freshness_status,
        conflicts=conflicts,
        conflicts_count=len(conflicts),
        canonical_event_id=row.canonical_event_id,
        why_not=event_why_not,
        quality_gate_passed=any(r.status == "RECOMMENDED" and r.quality_gate is not None and r.quality_gate.passed for r in recs),
        evidence=evidence,  # type: ignore[arg-type]
    )
    analysis.explanation = explain(analysis)
    for r in analysis.recommendations:
        if r.explanation is None:
            r.explanation = explain_recommendation(analysis, r)
    analysis.event.opportunity_score = opp
    analysis.event.confidence_grade = conf.grade
    analysis.event.data_quality = dq.score
    analysis.event.no_bet_reason = verdict.reason
    best = next((r for r in recs if r.status == "RECOMMENDED"), None)
    analysis.event.best_market = f"{best.market_label}: {best.selection_name}" if best else None

    if save_snapshot and row.kickoff_utc > datetime.utcnow():
        analysis.snapshot_id = _save_snapshot(session, row, analysis)
        session.commit()

    with _cache_lock:
        _cache[event_id] = (datetime.utcnow(), analysis)
    return analysis


def _freshness(odds_collected_at, home: TeamProfile, away: TeamProfile, comp_df: pd.DataFrame, venue: VenueInfo, has_odds: bool) -> list[fresh.Freshness]:
    out: list[fresh.Freshness] = []
    if has_odds or odds_collected_at is not None:
        out.append(fresh.assess("odds", odds_collected_at, source="superbet"))
    else:
        out.append(fresh.unavailable("odds", "Superbet não retornou mercados para este evento."))
    for side, t in (("mandante", home), ("visitante", away)):
        if t.provenance and t.provenance.collected_at:
            out.append(fresh.assess("form", t.provenance.collected_at, source=t.provenance.source, note=f"Forma do {side} ({t.sample_size} jogos)."))
        else:
            out.append(fresh.unavailable("form", f"Sem histórico resolvido para o {side}."))
    hist_at = None
    if comp_df is not None and not comp_df.empty and "collected_at" in comp_df and comp_df["collected_at"].notna().any():
        ts = comp_df["collected_at"].max()
        hist_at = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
    if hist_at is not None:
        out.append(fresh.assess("history", hist_at, source=str(comp_df["source"].iloc[0]) if "source" in comp_df else "historical", note=f"{len(comp_df)} jogos da competição."))
    else:
        out.append(fresh.unavailable("history", "Competição sem dataset histórico carregado."))
    if venue.source in ("international_results",) and hist_at is not None:
        out.append(fresh.assess("venue", hist_at, source=venue.source, note=venue.note))
    elif venue.source == "user":
        out.append(fresh.assess("venue", datetime.utcnow(), source="user", note="Definido manualmente."))
    else:
        out.append(fresh.assess("venue", datetime.utcnow(), source=venue.source, note="Regra de convenção (não é uma coleta)."))
    out.append(fresh.unavailable("lineup", "Nenhum provider público de escalações integrado (PLAYER DATA UNAVAILABLE)."))
    return out


def _record_venue_conflicts(session: Session, row: Event, detected: VenueInfo, chosen: VenueInfo) -> None:
    """Registra divergências de mando/venue entre a listagem Superbet, o dataset e o usuário."""
    try:
        if chosen.source == "user" and (detected.status != chosen.status or (detected.neutral is not None and detected.neutral != chosen.neutral)):
            record_conflict(
                session, event_id=row.id, field="neutral_venue", source_a=detected.source or "detected", value_a={"status": detected.status, "neutral": detected.neutral},
                source_b="user", value_b={"status": chosen.status, "neutral": chosen.neutral}, selected_value={"status": chosen.status, "neutral": chosen.neutral},
                selected_source="user", method="user_override", confidence=1.0, canonical_event_id=row.canonical_event_id,
            )
        if detected.status == "NEUTRAL" and detected.source == "international_results":
            record_conflict(
                session, event_id=row.id, field="neutral_venue", source_a="superbet", value_a={"home": row.home_name, "neutral": False},
                source_b="international_results", value_b={"neutral": True, "city": detected.city, "country": detected.country},
                selected_value={"neutral": True}, selected_source="international_results", method="dataset_flag", confidence=detected.confidence,
                canonical_event_id=row.canonical_event_id,
            )
        if detected.listing_swapped:
            record_conflict(
                session, event_id=row.id, field="home_team", source_a="superbet", value_a=row.home_name,
                source_b="international_results", value_b=row.away_name, selected_value=row.home_name, selected_source="superbet",
                method="listing_kept_unconfirmed", confidence=0.5, canonical_event_id=row.canonical_event_id,
            )
    except Exception as exc:  # noqa: BLE001 — registrar conflito nunca pode derrubar a análise
        log.warning("falha ao registrar conflito de venue (evento %s): %s", row.id, exc)


def _main_odds(markets) -> dict[str, float] | None:
    for m in markets:
        if m.market_key == "1X2":
            return {s.key: s.price for s in m.selections}
    return None


def _save_snapshot(session: Session, row: Event, a: MatchAnalysis) -> int | None:
    recent = session.execute(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.event_id == row.id, PredictionSnapshot.model_version == versions.PIPELINE)
        .order_by(PredictionSnapshot.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if recent and datetime.utcnow() - recent.created_at < timedelta(hours=6) and recent.result is None:
        return recent.id
    snap = PredictionSnapshot(
        event_id=row.id,
        kickoff_utc=row.kickoff_utc,
        home_name=row.home_name,
        away_name=row.away_name,
        competition_name=row.competition_name,
        model_version=versions.PIPELINE,
        model_versions=dict(versions.ALL_MODELS),
        features={
            "home": a.home.model_dump(mode="json", exclude={"recent"}),
            "away": a.away.model_dump(mode="json", exclude={"recent"}),
            "venue": a.venue.model_dump(mode="json"),
            "elo": a.elo.model_dump(mode="json"),
        },
        probabilities={
            "simulation": a.simulation.model_dump(mode="json") if a.simulation else None,
            "poisson": a.poisson.model_dump(mode="json"),
            "dixon_coles": a.dixon_coles.model_dump(mode="json"),
            "bivariate_poisson": a.bivariate_poisson.model_dump(mode="json") if a.bivariate_poisson else None,
            "consensus": a.consensus.model_dump(mode="json") if a.consensus else None,
            "model_comparison": a.model_comparison,
            "corners": a.corners.model_dump(mode="json"),
            "cards": a.cards.model_dump(mode="json"),
        },
        odds=[m.model_dump(mode="json") for m in a.markets],
        recommendations=[r.model_dump(mode="json") for r in a.recommendations if r.status != "NO_BET" or r.model_prob > 0][:40],
        confidence_grade=a.confidence.grade,
        data_quality=a.data_quality.score,
        no_bet_reason=a.no_bet.reason,
    )
    session.add(snap)
    session.flush()
    return snap.id


def market_calibration(session: Session) -> tuple[dict[str, float], int]:
    """Calibração por mercado a partir de snapshots settled: 1 - Brier normalizado (0.5 = neutro)."""
    snaps = session.execute(
        select(PredictionSnapshot).where(PredictionSnapshot.result.is_not(None)).order_by(PredictionSnapshot.created_at.desc()).limit(500)
    ).scalars().all()
    per_market: dict[str, list[float]] = {}
    n = 0
    for s in snaps:
        res = s.result or {}
        outcomes = res.get("outcomes") or {}
        for r in s.recommendations or []:
            key = f"{r['market_key']}|{r['selection_key']}|{r.get('line')}"
            if key not in outcomes:
                continue
            y = 1.0 if outcomes[key] else 0.0
            p = float(r["model_prob"])
            per_market.setdefault(r["market_key"], []).append((p - y) ** 2)
            n += 1
    out = {}
    for mk, briers in per_market.items():
        b = sum(briers) / len(briers)
        out[mk] = max(0.0, min(1.0, 1 - b / 0.25 * 0.5))  # brier 0.25 (aleatório) → 0.5
    return out, n
