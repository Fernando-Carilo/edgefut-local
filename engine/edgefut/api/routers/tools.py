"""Simulador, gestão de banca, múltiplas, Edge AI, busca, backtest, performance, histórico."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...analysis.pipeline import analyze_event, cached_analysis
from ...backtesting.lab import BacktestRequest, run_backtest
from ...backtesting.performance import performance_report, settle_pending
from ...core.config import settings
from ...db.models import Competition, Event, PredictionSnapshot
from ...db.session import get_session
from ...explanations import answer, facts
from ...explanations import ollama as ollama_mod
from ...odds import implied_probability
from ...providers.historical import get_store
from ...recommendations.multiples import Leg, combine, joint_probability_same_event
from ..schemas import (
    ChatRequest,
    ChatResponse,
    MultipleRequest,
    MultipleResponse,
    SearchResponse,
    SimulatorRequest,
    SimulatorResponse,
    StakeRequest,
    StakeResponse,
)
from .events import main_odds_for
from .radar import event_summary

router = APIRouter(tags=["tools"])


@router.post("/simulator", response_model=SimulatorResponse)
def simulator(body: SimulatorRequest):
    gross = body.stake * body.odd
    implied = implied_probability(body.odd)
    ev = edge = None
    if body.model_prob is not None:
        ev = round((body.model_prob * body.odd - 1) * 100, 2)
        edge = round((body.model_prob - implied) * 100, 2)
    return SimulatorResponse(
        stake=body.stake, odd=body.odd, gross_return=round(gross, 2), profit=round(gross - body.stake, 2),
        implied_probability=round(implied, 4), model_probability=body.model_prob, ev_pct=ev, edge_pp=edge,
    )


@router.post("/bankroll/stake", response_model=StakeResponse)
def stake(body: StakeRequest):
    b = body.odd - 1
    kelly_full = max(0.0, (body.model_prob * b - (1 - body.model_prob)) / b) if b > 0 else 0.0
    frac = min(body.kelly_fraction, settings.kelly_fraction_max)
    warning = None
    if body.method == "fixed":
        st = body.fixed_value or 0.0
    elif body.method == "percent":
        st = body.bankroll * (body.percent or 1.0) / 100
    else:
        st = body.bankroll * kelly_full * frac
        if body.kelly_fraction > settings.kelly_fraction_max:
            warning = f"Fração de Kelly limitada a {settings.kelly_fraction_max:.2f} (configuração de segurança)."
    pct = st / body.bankroll * 100 if body.bankroll else 0.0
    if pct > body.max_stake_pct:
        warning = (warning + " " if warning else "") + f"Stake {pct:.1f}% acima do limite configurado ({body.max_stake_pct:.1f}%)."
    if kelly_full == 0 and body.method == "kelly":
        warning = (warning + " " if warning else "") + "Kelly negativo: sem valor esperado positivo, stake 0."
    return StakeResponse(method=body.method, stake=round(st, 2), stake_pct=round(pct, 2), kelly_full_pct=round(kelly_full * 100, 2), kelly_fraction_used=frac if body.method == "kelly" else 0.0, warning=warning)


@router.post("/multiples/evaluate", response_model=MultipleResponse)
def multiples(body: MultipleRequest, session: Session = Depends(get_session)):
    by_event: dict[int, list[Leg]] = {}
    warnings: list[str] = []
    correlations: list[str] = []
    for leg in body.legs:
        a = cached_analysis(leg.event_id) or analyze_event(session, leg.event_id)
        prob = None
        for r in a.recommendations:
            if r.market_key == leg.market_key and r.selection_key == leg.selection_key and (r.line == leg.line or (r.line is None and leg.line is None)):
                prob = r.model_prob
                break
        if prob is None:
            for m in a.markets:
                if m.market_key == leg.market_key and m.line == leg.line:
                    for s in m.selections:
                        if s.key == leg.selection_key and s.model_prob is not None:
                            prob = s.model_prob
        if prob is None:
            warnings.append(f"Sem probabilidade do modelo para {leg.market_key}/{leg.selection_key} no evento {leg.event_id}.")
            prob = 0.0
        by_event.setdefault(leg.event_id, []).append(
            Leg(leg.event_id, leg.market_key, leg.selection_key, leg.line, leg.odd, prob, leg.label or f"{leg.market_key} {leg.selection_key}")
        )
    naive = 1.0
    combined_odd = 1.0
    joints: list[float] = []
    per_event = []
    joint_ok = True
    for eid, legs in by_event.items():
        a = cached_analysis(eid) or analyze_event(session, eid)
        for lg in legs:
            naive *= lg.model_prob
            combined_odd *= lg.odd
        if len(legs) > 1:
            base = a.dixon_coles if a.dixon_coles.available else a.poisson
            joint, correlated = joint_probability_same_event(legs, base, seed=eid)
            if joint is None:
                joint_ok = False
                warnings.append(f"Evento {eid}: pernas não combináveis pela simulação; usada independência.")
                joint = float(__import__("numpy").prod([lg.model_prob for lg in legs]))
            elif correlated:
                correlations.append(f"CORRELAÇÃO DETECTADA no evento {eid}: " + " + ".join(lg.label for lg in legs))
            joints.append(joint)
            per_event.append({"event_id": eid, "legs": [lg.label for lg in legs], "joint_probability": round(joint, 4), "naive_probability": round(float(__import__("numpy").prod([lg.model_prob for lg in legs])), 4), "correlated": correlated})
        else:
            joints.append(legs[0].model_prob)
            per_event.append({"event_id": eid, "legs": [legs[0].label], "joint_probability": round(legs[0].model_prob, 4), "naive_probability": round(legs[0].model_prob, 4), "correlated": False})
    joint_total = combine(joints)
    if len(body.legs) > 3:
        warnings.append("Múltiplas com mais de 3 seleções reduzem fortemente a probabilidade conjunta; prefira 2–3 pernas.")
    ev = round((joint_total * combined_odd - 1) * 100, 2) if joint_total > 0 else None
    return MultipleResponse(
        legs=len(body.legs), combined_odd=round(combined_odd, 3), naive_probability=round(naive, 4),
        joint_probability=round(joint_total, 4) if joint_ok or joints else None, implied_probability=round(1 / combined_odd, 4),
        ev_pct=ev, correlations=correlations, warnings=warnings, per_event=per_event,
    )


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, session: Session = Depends(get_session)):
    try:
        a = cached_analysis(body.event_id) or analyze_event(session, body.event_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    base = answer(body.question, a)
    if settings.ollama_enabled:
        rephrased = ollama_mod.rephrase(body.question, facts(a), base)
        if rephrased:
            return ChatResponse(answer=rephrased, engine="ollama", event_id=body.event_id)
    return ChatResponse(answer=base, engine="templates", event_id=body.event_id)


@router.get("/search", response_model=SearchResponse)
def search(q: str = Query(min_length=2), session: Session = Depends(get_session)):
    like = f"%{q}%"
    rows = session.execute(
        select(Event).where(Event.kickoff_utc > datetime.utcnow(), Event.duplicate_of.is_(None), or_(Event.home_name.ilike(like), Event.away_name.ilike(like), Event.competition_name.ilike(like)))
        .order_by(Event.kickoff_utc.asc()).limit(20)
    ).scalars().all()
    odds = main_odds_for(session, [r.id for r in rows])
    comps = session.execute(select(Competition).where(Competition.name.ilike(like)).limit(10)).scalars().all()
    teams: dict[str, dict] = {}
    for r in rows:
        for name in (r.home_name, r.away_name):
            if q.lower() in name.lower():
                teams.setdefault(name, {"name": name, "next_event_id": r.id, "next_kickoff_utc": r.kickoff_utc.isoformat(), "competition": r.competition_name})
    store = get_store()
    if store.has_data():
        ds = store.datasets()
        codes = ds["dataset_code"].tolist() if not ds.empty else []
        for name in store.team_names(codes):
            if q.lower() in name.lower() and name not in teams and len(teams) < 15:
                teams[name] = {"name": name, "next_event_id": None, "dataset": True}
    return SearchResponse(
        query=q, events=[event_summary(r, None, odds.get(r.id)) for r in rows], teams=list(teams.values()),
        competitions=[{"id": c.id, "name": c.name, "category": c.category_name, "dataset_code": c.dataset_code} for c in comps],
    )


@router.post("/backtest")
def backtest(body: BacktestRequest):
    r = run_backtest(body)
    return r.__dict__


@router.get("/performance")
def performance(session: Session = Depends(get_session)):
    return performance_report(session)


@router.post("/performance/settle")
def settle(session: Session = Depends(get_session)):
    return settle_pending(session)


@router.get("/history")
def history(limit: int = Query(100, le=500), session: Session = Depends(get_session)):
    rows = session.execute(select(PredictionSnapshot).order_by(PredictionSnapshot.created_at.desc()).limit(limit)).scalars().all()
    out = []
    for s in rows:
        recs = [r for r in (s.recommendations or []) if r.get("status") == "RECOMMENDED"]
        out.append(
            {
                "id": s.id, "event_id": s.event_id, "created_at": s.created_at, "kickoff_utc": s.kickoff_utc,
                "home_name": s.home_name, "away_name": s.away_name, "competition_name": s.competition_name,
                "model_version": s.model_version, "confidence_grade": s.confidence_grade, "data_quality": s.data_quality,
                "no_bet_reason": s.no_bet_reason, "recommended": len(recs),
                "best": recs[0] if recs else None, "result": s.result, "settled_at": s.settled_at,
            }
        )
    return {"snapshots": out, "total": len(out)}


@router.get("/history/{snapshot_id}")
def history_detail(snapshot_id: int, session: Session = Depends(get_session)):
    s = session.get(PredictionSnapshot, snapshot_id)
    if s is None:
        raise HTTPException(404, "snapshot não encontrado")
    return {
        "id": s.id, "event_id": s.event_id, "created_at": s.created_at, "kickoff_utc": s.kickoff_utc,
        "home_name": s.home_name, "away_name": s.away_name, "competition_name": s.competition_name,
        "model_version": s.model_version, "model_versions": s.model_versions, "features": s.features,
        "probabilities": s.probabilities, "odds": s.odds, "recommendations": s.recommendations,
        "confidence_grade": s.confidence_grade, "data_quality": s.data_quality, "no_bet_reason": s.no_bet_reason,
        "result": s.result, "settled_at": s.settled_at,
    }
