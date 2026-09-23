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
    """Busca resultados para eventos já encerrados e liquida snapshots (sem recalcular previsões).

    Prioriza eventos com snapshots pendentes (é isso que alimenta Performance e Calibração).
    Placar já coletado pelo `sync_odds` (status FINISHED) é reutilizado sem novo request;
    quando o dataset curado também tem o resultado e discorda, registramos um
    `source_conflict(field=score)`.
    """
    from ..quality import record_conflict

    cutoff = datetime.utcnow() - timedelta(hours=2)
    snap_event_ids = [
        int(i) for i in session.execute(
            select(PredictionSnapshot.event_id).where(PredictionSnapshot.result.is_(None), PredictionSnapshot.kickoff_utc < cutoff).distinct()
        ).scalars().all()
    ]
    pending: list[Event] = []
    if snap_event_ids:
        pending = session.execute(
            select(Event).where(Event.id.in_(snap_event_ids)).order_by(Event.kickoff_utc.desc()).limit(max_events)
        ).scalars().all()
    if len(pending) < max_events:
        seen = {e.id for e in pending}
        extra = session.execute(
            select(Event).where(Event.kickoff_utc < cutoff, Event.settled_at.is_(None), Event.duplicate_of.is_(None))
            .order_by(Event.kickoff_utc.desc()).limit(max_events - len(pending))
        ).scalars().all()
        pending.extend(e for e in extra if e.id not in seen)
    provider = SuperbetProvider()
    store = get_store()
    settled = 0
    conflicts = 0
    for ev in pending:
        # Fetch antes de qualquer escrita; commit por evento mantém a transação curta.
        feed = _result_from_event(ev) or _result_from_superbet(provider, ev)
        history = _result_from_history(store, ev, session)
        result = history or feed
        if result is None:
            continue
        if feed is not None and history is not None and (feed.hg, feed.ag) != (history.hg, history.ag):
            record_conflict(
                session, event_id=ev.id, field="score", source_a="superbet", value_a=f"{feed.hg}-{feed.ag}",
                source_b=history.source, value_b=f"{history.hg}-{history.ag}", selected_value=f"{result.hg}-{result.ag}",
                selected_source=result.source, method="prefer_curated_history", confidence=0.8, canonical_event_id=ev.canonical_event_id,
            )
            conflicts += 1
        if history is not None and feed is not None and result.corners is None:
            result = MatchResult(hg=result.hg, ag=result.ag, corners=feed.corners, cards=feed.cards, source=result.source)
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
    return {"checked": len(pending), "settled": settled, "score_conflicts": conflicts}


def _result_from_event(ev: Event) -> MatchResult | None:
    """Placar já persistido pelo sync_odds (Superbet status FINISHED)."""
    if ev.home_score is None or ev.away_score is None or ev.result_source != "superbet":
        return None
    return MatchResult(hg=int(ev.home_score), ag=int(ev.away_score), source="superbet")


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


MIN_SAMPLE_FOR_METRICS = 30  # abaixo disso a métrica é exibida como INSUFFICIENT SAMPLE


def _group_metrics(records: list[BetRecord]) -> dict:
    d = asdict(compute_metrics(records))
    d.pop("equity_curve", None)
    d["sample_status"] = "OK" if len(records) >= MIN_SAMPLE_FOR_METRICS else "INSUFFICIENT_SAMPLE"
    d["min_sample"] = MIN_SAMPLE_FOR_METRICS
    return d


def performance_report(session: Session) -> dict:
    snaps = session.execute(select(PredictionSnapshot).where(PredictionSnapshot.result.is_not(None)).order_by(PredictionSnapshot.created_at.asc())).scalars().all()
    records: list[BetRecord] = []
    all_1x2: list[BetRecord] = []
    by_comp_records: dict[str, list[BetRecord]] = {}
    for s in snaps:
        outcomes = (s.result or {}).get("outcomes") or {}
        closing = s.closing_odds or {}
        for r in s.recommendations or []:
            key = f"{r['market_key']}|{r['selection_key']}|{r.get('line')}"
            if key not in outcomes:
                continue
            rec = BetRecord(
                prob=float(r["model_prob"]), odd=float(r["odd"]), won=bool(outcomes[key]), edge_pp=r.get("edge_pp"),
                market_key=r["market_key"], label=r["market_label"], closing_odd=_closing_for(closing, key),
            )
            if r.get("status") == "RECOMMENDED":
                records.append(rec)
                by_comp_records.setdefault(s.competition_name or "—", []).append(rec)
            if r["market_key"] == "1X2":
                all_1x2.append(rec)
    by_market = {mk: _group_metrics([r for r in records if r.market_key == mk]) for mk in sorted({r.market_key for r in records if r.market_key})}
    by_competition = {c: _group_metrics(recs) for c, recs in sorted(by_comp_records.items(), key=lambda kv: -len(kv[1]))}
    overall = asdict(compute_metrics(records))
    curve = overall.pop("equity_curve", [])
    overall["sample_status"] = "OK" if len(records) >= MIN_SAMPLE_FOR_METRICS else "INSUFFICIENT_SAMPLE"
    model_only = _group_metrics(all_1x2)
    with_clv = sum(1 for r in records if r.closing_odd)
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "settled_snapshots": len(snaps),
        "recommended_bets": len(records),
        "bets_with_closing_line": with_clv,
        "overall": overall,
        "equity_curve": curve,
        "by_market": by_market,
        "by_competition": by_competition,
        "model_1x2_all_selections": model_only,
        "min_sample": MIN_SAMPLE_FOR_METRICS,
        "note": (
            "Métricas calculadas apenas sobre snapshots gravados ANTES do jogo e liquidados com o resultado real. "
            "CLV usa a closing line capturada imediatamente antes do kickoff (nunca usada para recomendar)."
        ),
    }


def _closing_for(closing: dict, key: str) -> float | None:
    v = closing.get(key)
    if isinstance(v, dict):
        v = v.get("price")
    try:
        return float(v) if v else None
    except (TypeError, ValueError):
        return None


PERFORMANCE_SUMMARY_KEY = "performance_summary"


def refresh_performance_summary(session: Session) -> dict:
    """Recalcula e persiste o resumo de performance (job `performance`)."""
    from ..db.models import Setting

    report = performance_report(session)
    summary = {k: v for k, v in report.items() if k != "equity_curve"}
    row = session.get(Setting, PERFORMANCE_SUMMARY_KEY)
    if row is None:
        session.add(Setting(key=PERFORMANCE_SUMMARY_KEY, value=summary))
    else:
        row.value = summary
        row.updated_at = datetime.utcnow()
    session.commit()
    return {"ok": True, "settled_snapshots": report["settled_snapshots"], "recommended_bets": report["recommended_bets"]}
