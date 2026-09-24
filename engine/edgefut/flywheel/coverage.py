"""Snapshot coverage (§6): alvos esperados vs observados, por evento e por mercado.

"Esperado" só conta alvos que o coletor **podia** ter observado: a janela do alvo tem de ser posterior
ao momento em que o evento foi descoberto (`first_seen_at`) e anterior a `now`. Um alvo não observado
é uma falha real de cobertura (downtime, fonte indisponível, evento sem esse mercado) — nunca é preenchido.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Event, RawSuperbetSnapshot
from .collector import SNAPSHOT_TARGETS
from .markets import CATEGORY_ORDER


def _expected_targets(first_seen: datetime | None, kickoff: datetime, now: datetime) -> list[str]:
    out = []
    for name, _, (lo, _hi) in SNAPSHOT_TARGETS:
        win_end = kickoff - timedelta(minutes=lo)
        if win_end <= now and (first_seen is None or first_seen <= win_end):
            out.append(name)
    if kickoff <= now and (first_seen is None or first_seen < kickoff):
        out.append("LAST_PRE_KO")
    return out


def load_raw_index(session: Session, since: datetime | None = None) -> pd.DataFrame:
    q = select(RawSuperbetSnapshot.id, RawSuperbetSnapshot.event_id, RawSuperbetSnapshot.fetched_at, RawSuperbetSnapshot.minutes_to_kickoff, RawSuperbetSnapshot.event_state,
               RawSuperbetSnapshot.snapshot_target, RawSuperbetSnapshot.markets_present, RawSuperbetSnapshot.kickoff_utc, RawSuperbetSnapshot.payload_bytes, RawSuperbetSnapshot.payload.is_(None).label("confirmation"))
    if since is not None:
        q = q.where(RawSuperbetSnapshot.fetched_at >= since)
    rows = session.execute(q).all()
    df = pd.DataFrame(rows, columns=["raw_id", "event_id", "fetched_at", "minutes_to_kickoff", "event_state", "snapshot_target", "markets_present", "kickoff_utc", "payload_bytes", "confirmation"])
    if not df.empty:
        df["fetched_at"] = pd.to_datetime(df["fetched_at"])
        df["kickoff_utc"] = pd.to_datetime(df["kickoff_utc"])
    return df


def coverage_report(session: Session, *, now: datetime | None = None, days: int = 30, per_event_limit: int = 300) -> dict:
    now = now or datetime.utcnow()
    since = now - timedelta(days=days)
    raw = load_raw_index(session, since=since - timedelta(days=3))
    events = session.execute(select(Event.id, Event.kickoff_utc, Event.first_seen_at, Event.home_name, Event.away_name, Event.competition_name).where(Event.kickoff_utc >= since, Event.kickoff_utc <= now + timedelta(hours=60), Event.duplicate_of.is_(None))).all()
    per_event: list[dict] = []
    tot_exp = tot_obs = 0
    market_exp: dict[str, int] = {c: 0 for c in CATEGORY_ORDER}
    market_obs: dict[str, int] = {c: 0 for c in CATEGORY_ORDER}
    target_exp: dict[str, int] = {}
    target_obs: dict[str, int] = {}
    by_event = {eid: g for eid, g in raw.groupby("event_id")} if not raw.empty else {}
    # antes do primeiro snapshot raw não existia Collector V2: nada era "esperado" (lacunas anteriores não são inventadas)
    collector_start = raw["fetched_at"].min().to_pydatetime() if not raw.empty else now
    for eid, ko, first_seen, home, away, comp in events:
        g = by_event.get(eid)
        seen_from = max(first_seen, collector_start) if first_seen else collector_start
        expected = _expected_targets(seen_from, ko, now)
        if not expected and (g is None or g.empty):
            continue
        observed: set[str] = set()
        markets_by_target: dict[str, set[str]] = {}
        markets_any: set[str] = set()
        if g is not None and not g.empty:
            for r in g.itertuples():
                if r.snapshot_target:
                    observed.add(r.snapshot_target)
                    markets_by_target[r.snapshot_target] = set(r.markets_present or [])
                markets_any.update(r.markets_present or [])
            pre = g[(g["minutes_to_kickoff"].notna()) & (g["minutes_to_kickoff"] >= 0)]
            if len(pre) and ko <= now:
                observed.add("LAST_PRE_KO")
                last_row = pre.sort_values("fetched_at").iloc[-1]
                markets_by_target["LAST_PRE_KO"] = set(last_row["markets_present"] or [])
        obs = [t for t in expected if t in observed]
        tot_exp += len(expected)
        tot_obs += len(obs)
        for t in expected:
            target_exp[t] = target_exp.get(t, 0) + 1
            if t in observed:
                target_obs[t] = target_obs.get(t, 0) + 1
        # por mercado: só mercados que o evento chegou a oferecer alguma vez
        for cat in markets_any:
            if cat in market_exp:
                market_exp[cat] += len(expected)
                market_obs[cat] += sum(1 for t in obs if cat in markets_by_target.get(t, set()))
        if len(per_event) < per_event_limit:
            per_event.append({
                "event_id": eid, "label": f"{home} × {away}", "competition": comp, "kickoff_utc": ko, "first_seen_at": first_seen,
                "expected": expected, "observed": sorted(observed, key=lambda t: ([x[0] for x in SNAPSHOT_TARGETS] + ["LAST_PRE_KO"]).index(t) if t in [x[0] for x in SNAPSHOT_TARGETS] + ["LAST_PRE_KO"] else 99),
                "coverage_pct": round(100 * len(obs) / len(expected), 1) if expected else None, "raw_snapshots": int(len(g)) if g is not None else 0,
                "markets": sorted(markets_any),
            })
    per_event.sort(key=lambda e: e["kickoff_utc"], reverse=True)
    return {
        "generated_at": now, "window_days": days, "events": len(per_event), "expected": tot_exp, "observed": tot_obs, "coverage_pct": round(100 * tot_obs / tot_exp, 1) if tot_exp else None,
        "by_target": [{"target": t, "expected": target_exp.get(t, 0), "observed": target_obs.get(t, 0), "pct": round(100 * target_obs.get(t, 0) / target_exp[t], 1) if target_exp.get(t) else None} for t in [x[0] for x in SNAPSHOT_TARGETS] + ["LAST_PRE_KO"]],
        "by_market": [{"market_category": c, "expected": market_exp[c], "observed": market_obs[c], "pct": round(100 * market_obs[c] / market_exp[c], 1) if market_exp[c] else None} for c in CATEGORY_ORDER],
        "per_event": per_event,
        "note": "Esperado = alvos cuja janela já passou e era posterior à descoberta do evento. Nada é interpolado: alvo não observado = lacuna real.",
    }


__all__ = ["coverage_report", "load_raw_index"]
