"""Performance real do modelo: snapshots settled → métricas por mercado."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Event, PredictionSnapshot
from ..providers import SourceError
from ..providers.historical import get_store
from ..providers.superbet import SuperbetProvider
from .metrics import BetRecord, compute_metrics
from .settlement import MatchResult, settle_selection

log = logging.getLogger(__name__)


def settle_pending(session: Session, max_events: int = 40) -> dict:
    """Busca resultados para eventos já encerrados e liquida snapshots (sem recalcular previsões)."""
    cutoff = datetime.utcnow() - timedelta(hours=2)
    pending = session.execute(
        select(Event).where(Event.kickoff_utc < cutoff, Event.settled_at.is_(None)).order_by(Event.kickoff_utc.desc()).limit(max_events)
    ).scalars().all()
    provider = SuperbetProvider()
    store = get_store()
    settled = 0
    for ev in pending:
        # Fetch antes de qualquer escrita; commit por evento mantém a transação curta.
        result = _result_from_superbet(provider, ev) or _result_from_history(store, ev, session)
        if result is None:
            continue
        ev.home_score, ev.away_score = result.hg, result.ag
        ev.result_source = result.source
        ev.settled_at = datetime.utcnow()
        snaps = session.execute(select(PredictionSnapshot).where(PredictionSnapshot.event_id == ev.id, PredictionSnapshot.result.is_(None))).scalars().all()
        for s in snaps:
            outcomes = {}
            for r in s.recommendations or []:
                won = settle_selection(r["market_key"], r["selection_key"], r.get("line"), result)
                if won is not None:
                    outcomes[f"{r['market_key']}|{r['selection_key']}|{r.get('line')}"] = won
            s.result = {"hg": result.hg, "ag": result.ag, "corners": result.corners, "cards": result.cards, "source": result.source, "outcomes": outcomes}
            s.settled_at = datetime.utcnow()
        settled += 1
        session.commit()
    return {"checked": len(pending), "settled": settled}


def _result_from_superbet(provider: SuperbetProvider, ev: Event) -> MatchResult | None:
    try:
        full = provider.fetch_event(ev.id)
    except SourceError:
        return None
    meta = full.raw.get("metadata") or {}
    if meta.get("status") != "FINISHED" or meta.get("homeTeamScore") is None:
        return None
    try:
        corners = None
        if meta.get("homeTeamCorners") is not None and meta.get("awayTeamCorners") is not None:
            corners = int(meta["homeTeamCorners"]) + int(meta["awayTeamCorners"])
        cards = None
        if meta.get("homeTeamYellowCards") is not None:
            cards = int(meta.get("homeTeamYellowCards", 0)) + int(meta.get("awayTeamYellowCards", 0)) + int(meta.get("homeTeamRedCards", 0)) + int(meta.get("awayTeamRedCards", 0))
        return MatchResult(hg=int(meta["homeTeamScore"]), ag=int(meta["awayTeamScore"]), corners=corners, cards=cards, source="superbet")
    except (TypeError, ValueError):
        return None


def _result_from_history(store, ev: Event, session: Session) -> MatchResult | None:
    from ..analysis.pipeline import _profile_for
    from ..providers.resolver import SourceResolver

    profile = _profile_for(session, ev)
    if not profile.supported:
        return None
    resolved = SourceResolver().resolve(profile, ev.home_name, ev.away_name)
    if not resolved.ok:
        return None
    df = store.find_result(resolved.home.canonical, resolved.away.canonical, ev.kickoff_utc, resolved.dataset_codes)
    if df.empty:
        return None
    r = df.iloc[0]
    flipped = r["home"] != resolved.home.canonical
    hg, ag = (int(r["ag"]), int(r["hg"])) if flipped else (int(r["hg"]), int(r["ag"]))
    corners = None
    if r.get("hc") is not None and r.get("ac") is not None and str(r.get("hc")) != "<NA>":
        try:
            corners = int(r["hc"]) + int(r["ac"])
        except (TypeError, ValueError):
            corners = None
    return MatchResult(hg=hg, ag=ag, corners=corners, source=str(r.get("source") or "historical"))


def performance_report(session: Session) -> dict:
    snaps = session.execute(select(PredictionSnapshot).where(PredictionSnapshot.result.is_not(None)).order_by(PredictionSnapshot.created_at.asc())).scalars().all()
    records: list[BetRecord] = []
    all_1x2: list[BetRecord] = []
    for s in snaps:
        outcomes = (s.result or {}).get("outcomes") or {}
        for r in s.recommendations or []:
            key = f"{r['market_key']}|{r['selection_key']}|{r.get('line')}"
            if key not in outcomes:
                continue
            rec = BetRecord(prob=float(r["model_prob"]), odd=float(r["odd"]), won=bool(outcomes[key]), edge_pp=r.get("edge_pp"), market_key=r["market_key"], label=r["market_label"])
            if r.get("status") == "RECOMMENDED":
                records.append(rec)
            if r["market_key"] == "1X2":
                all_1x2.append(rec)
    by_market: dict[str, dict] = {}
    for mk in sorted({r.market_key for r in records if r.market_key}):
        d = asdict(compute_metrics([r for r in records if r.market_key == mk]))
        d.pop("equity_curve", None)
        by_market[mk] = d
    overall = asdict(compute_metrics(records))
    curve = overall.pop("equity_curve", [])
    model_only = asdict(compute_metrics(all_1x2))
    model_only.pop("equity_curve", None)
    return {
        "settled_snapshots": len(snaps),
        "recommended_bets": len(records),
        "overall": overall,
        "equity_curve": curve,
        "by_market": by_market,
        "model_1x2_all_selections": model_only,
        "note": "Métricas calculadas apenas sobre snapshots gravados ANTES do jogo e liquidados com o resultado real.",
    }
