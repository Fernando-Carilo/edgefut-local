"""Settlement V2 por mercado canónico (§11–§13).

Estados: WON · LOST · VOID (push na linha inteira) · UNSETTLED_DATA_MISSING (estatística ausente —
nunca se assume derrota) · UNSUPPORTED (sem regra: mercados de jogador) · ERROR.

Fontes de resultado, por campo: placar (Superbet FINISHED → `event`; senão histórico curado),
escanteios e cartões por equipe (metadata Superbet do payload FINISHED; senão football-data HC/AC,
HY/AY/HR/AR), finalizações (só football-data HS/AS/HST/AST). Cada campo regista a sua fonte.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Event, RawSuperbetSnapshot, SuperbetNormalized, SuperbetSettlement
from . import SETTLEMENT_VERSION
from .collector import raw_payload
from .markets import CATEGORY_ORDER

log = logging.getLogger(__name__)

FINISHED_AFTER = timedelta(hours=3)


@dataclass
class FullResult:
    hg: int | None = None
    ag: int | None = None
    home_corners: int | None = None
    away_corners: int | None = None
    home_cards: int | None = None  # amarelos + vermelhos
    away_cards: int | None = None
    home_shots: int | None = None
    away_shots: int | None = None
    home_sot: int | None = None
    away_sot: int | None = None
    sources: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("hg", "ag", "home_corners", "away_corners", "home_cards", "away_cards", "home_shots", "away_shots", "home_sot", "away_sot")} | {"sources": self.sources}


def _int(v) -> int | None:
    if v is None:
        return None
    try:
        s = str(v)
        if s in ("<NA>", "nan", "None", ""):
            return None
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _from_superbet_metadata(session: Session, event_id: int, res: FullResult) -> None:
    row = session.execute(select(RawSuperbetSnapshot).where(RawSuperbetSnapshot.event_id == event_id, RawSuperbetSnapshot.event_state == "finished").order_by(RawSuperbetSnapshot.fetched_at.desc()).limit(1)).scalar_one_or_none()
    if row is None:
        return
    payload = raw_payload(session, row.id)
    meta = (payload or {}).get("metadata") or {}
    if str(meta.get("status") or "").upper() != "FINISHED":
        return
    hg, ag = _int(meta.get("homeTeamScore")), _int(meta.get("awayTeamScore"))
    if hg is not None and ag is not None and res.hg is None:
        res.hg, res.ag = hg, ag
        res.sources["score"] = "superbet"
    hc, ac = _int(meta.get("homeTeamCorners")), _int(meta.get("awayTeamCorners"))
    if hc is not None and ac is not None:
        res.home_corners, res.away_corners = hc, ac
        res.sources["corners"] = "superbet"
    hy, ay, hr, ar = _int(meta.get("homeTeamYellowCards")), _int(meta.get("awayTeamYellowCards")), _int(meta.get("homeTeamRedCards")), _int(meta.get("awayTeamRedCards"))
    if hy is not None and ay is not None:
        res.home_cards, res.away_cards = hy + (hr or 0), ay + (ar or 0)
        res.sources["cards"] = "superbet"


def _from_history(session: Session, ev: Event, res: FullResult) -> None:
    try:
        from ..analysis.pipeline import _profile_for
        from ..providers.historical import get_store
        from ..providers.resolver import SourceResolver

        profile = _profile_for(session, ev)
        if not profile.supported:
            return
        resolved = SourceResolver().resolve(profile, ev.home_name, ev.away_name)
        if not resolved.ok:
            return
        df = get_store().find_result(resolved.home.canonical, resolved.away.canonical, ev.kickoff_utc, resolved.dataset_codes)
    except Exception as exc:  # noqa: BLE001
        log.debug("histórico indisponível para %s: %s", ev.id, exc)
        return
    if df is None or df.empty:
        return
    r = df.iloc[0]
    flipped = r["home"] != resolved.home.canonical

    def pair(h: str, a: str) -> tuple[int | None, int | None]:
        x, y = _int(r.get(h)), _int(r.get(a))
        return (y, x) if flipped else (x, y)

    src = str(r.get("source") or "football-data")
    if res.hg is None:
        res.hg, res.ag = pair("hg", "ag")
        if res.hg is not None:
            res.sources["score"] = src
    if res.home_corners is None:
        res.home_corners, res.away_corners = pair("hc", "ac")
        if res.home_corners is not None:
            res.sources["corners"] = src
    if res.home_cards is None:
        hy, ay = pair("hy", "ay")
        hr, ar = pair("hr", "ar")
        if hy is not None and ay is not None:
            res.home_cards, res.away_cards = hy + (hr or 0), ay + (ar or 0)
            res.sources["cards"] = src
    if res.home_shots is None:
        res.home_shots, res.away_shots = pair("hs", "as_")
        if res.home_shots is not None:
            res.sources["shots"] = src
    if res.home_sot is None:
        res.home_sot, res.away_sot = pair("hst", "ast")
        if res.home_sot is not None:
            res.sources["shots_on_target"] = src


def result_for_event(session: Session, ev: Event) -> FullResult:
    res = FullResult()
    if ev.home_score is not None and ev.away_score is not None:
        res.hg, res.ag = int(ev.home_score), int(ev.away_score)
        res.sources["score"] = ev.result_source or "event"
    _from_superbet_metadata(session, ev.id, res)
    _from_history(session, ev, res)
    return res


# ---------------------------------------------------------------------------
# regras
# ---------------------------------------------------------------------------
def _ou(total: int | None, line: float | None, sel: str, missing: str) -> tuple[str, list[str]]:
    if total is None:
        return "UNSETTLED_DATA_MISSING", [missing]
    if line is None:
        return "ERROR", ["line"]
    if abs(total - line) < 1e-9:
        return "VOID", []
    over = total > line
    if sel.endswith("OVER"):
        return ("WON" if over else "LOST"), []
    if sel.endswith("UNDER"):
        return ("LOST" if over else "WON"), []
    return "ERROR", ["selection"]


def settle_canonical(category: str, canonical_market_id: str, selection_id: str, line: float | None, r: FullResult) -> tuple[str, list[str]]:
    """→ (status, missing_fields)."""
    side = "HOME" if "_HOME_" in canonical_market_id or canonical_market_id.endswith("_HOME") else ("AWAY" if "_AWAY_" in canonical_market_id or canonical_market_id.endswith("_AWAY") else None)
    if category in ("MATCH_RESULT", "DOUBLE_CHANCE", "DNB", "TOTAL_GOALS", "BTTS", "TEAM_TOTAL"):
        if r.hg is None or r.ag is None:
            return "UNSETTLED_DATA_MISSING", ["score"]
        hg, ag = r.hg, r.ag
        if category == "MATCH_RESULT":
            out = "HOME" if hg > ag else "AWAY" if ag > hg else "DRAW"
            return ("WON" if selection_id == out else "LOST"), []
        if category == "DOUBLE_CHANCE":
            out = "HOME" if hg > ag else "AWAY" if ag > hg else "DRAW"
            wins = {"HOME_DRAW": ("HOME", "DRAW"), "DRAW_AWAY": ("DRAW", "AWAY"), "HOME_AWAY": ("HOME", "AWAY")}.get(selection_id)
            return ("WON" if wins and out in wins else "LOST"), ([] if wins else ["selection"])
        if category == "DNB":
            if hg == ag:
                return "VOID", []
            return ("WON" if (selection_id == "HOME") == (hg > ag) else "LOST"), []
        if category == "TOTAL_GOALS":
            return _ou(hg + ag, line, selection_id, "score")
        if category == "BTTS":
            both = hg > 0 and ag > 0
            return ("WON" if (selection_id == "YES") == both else "LOST"), []
        if category == "TEAM_TOTAL":
            return _ou(hg if side == "HOME" else ag, line, selection_id, "score")
    if category == "CORNERS_TOTAL":
        tot = None if r.home_corners is None or r.away_corners is None else r.home_corners + r.away_corners
        return _ou(tot, line, selection_id, "corners")
    if category == "TEAM_CORNERS":
        return _ou(r.home_corners if side == "HOME" else r.away_corners, line, selection_id, "corners")
    if category == "CARDS_TOTAL":
        tot = None if r.home_cards is None or r.away_cards is None else r.home_cards + r.away_cards
        return _ou(tot, line, selection_id, "cards")
    if category == "TEAM_CARDS":
        return _ou(r.home_cards if side == "HOME" else r.away_cards, line, selection_id, "cards")
    if category == "SHOTS":
        if side is None:
            tot = None if r.home_shots is None or r.away_shots is None else r.home_shots + r.away_shots
            return _ou(tot, line, selection_id, "shots")
        return _ou(r.home_shots if side == "HOME" else r.away_shots, line, selection_id, "shots")
    if category == "SHOTS_ON_TARGET":
        if side is None:
            tot = None if r.home_sot is None or r.away_sot is None else r.home_sot + r.away_sot
            return _ou(tot, line, selection_id, "shots_on_target")
        return _ou(r.home_sot if side == "HOME" else r.away_sot, line, selection_id, "shots_on_target")
    if category.startswith("PLAYER_"):
        return "UNSUPPORTED", ["player_stats"]  # §14: sem engine de jogador; odds capturadas, não liquidadas
    return "UNSUPPORTED", ["rule"]


# ---------------------------------------------------------------------------
# job
# ---------------------------------------------------------------------------
def settle_events(session: Session, *, now: datetime | None = None, limit_events: int = 200) -> dict:
    """Liquida (ou re-tenta) todas as seleções normalizadas de eventos terminados há ≥ 3 h.
    Re-tentativa: seleções UNSETTLED_DATA_MISSING são re-avaliadas quando a estatística aparece (histórico
    atualizado) — o registo anterior é substituído, com `settled_at` novo e a fonte por campo."""
    now = now or datetime.utcnow()
    cutoff = now - FINISHED_AFTER
    ev_ids = session.execute(
        select(SuperbetNormalized.event_id).where(SuperbetNormalized.kickoff_utc <= cutoff, SuperbetNormalized.event_state == "prematch").group_by(SuperbetNormalized.event_id)
    ).scalars().all()
    # eventos com pelo menos uma seleção pendente por dados voltam a ser tentados; os já fechados não
    pending_ids = set(session.execute(select(SuperbetSettlement.event_id).where(SuperbetSettlement.status.in_(("UNSETTLED_DATA_MISSING", "ERROR"))).group_by(SuperbetSettlement.event_id)).scalars().all())
    done_ids = set(session.execute(select(SuperbetSettlement.event_id).group_by(SuperbetSettlement.event_id)).scalars().all()) - pending_ids
    todo = [e for e in ev_ids if e not in done_ids][:limit_events]
    out = {"ok": True, "events": 0, "selections": 0, "by_status": {}, "settled": 0}
    for eid in todo:
        ev = session.get(Event, eid)
        if ev is None:
            continue
        r = result_for_event(session, ev)
        sels = session.execute(
            select(SuperbetNormalized.market_category, SuperbetNormalized.canonical_market_id, SuperbetNormalized.selection_id, SuperbetNormalized.line)
            .where(SuperbetNormalized.event_id == eid, SuperbetNormalized.event_state == "prematch").group_by(SuperbetNormalized.canonical_market_id, SuperbetNormalized.selection_id)
        ).all()
        existing = {(s.canonical_market_id, s.selection_id): s for s in session.execute(select(SuperbetSettlement).where(SuperbetSettlement.event_id == eid)).scalars()}
        for cat, mk, sel, line in sels:
            try:
                status, missing = settle_canonical(cat, mk, sel, line, r)
                err = None
            except Exception as exc:  # noqa: BLE001
                status, missing, err = "ERROR", [], str(exc)[:300]
            row = existing.get((mk, sel))
            if row is None:
                row = SuperbetSettlement(event_id=eid, market_category=cat, canonical_market_id=mk, selection_id=sel, line=line, status=status, missing_fields=missing or None, result_source=",".join(sorted(set(r.sources.values()))) or None, result=r.as_dict(), settlement_version=SETTLEMENT_VERSION, settled_at=now, error=err)
                session.add(row)
            elif row.status in ("UNSETTLED_DATA_MISSING", "ERROR"):
                row.status, row.missing_fields, row.result, row.settled_at, row.error = status, missing or None, r.as_dict(), now, err
                row.result_source = ",".join(sorted(set(r.sources.values()))) or None
            else:
                continue  # WON/LOST/VOID/UNSUPPORTED nunca são reescritos automaticamente
            out["selections"] += 1
            out["by_status"][status] = out["by_status"].get(status, 0) + 1
            if status in ("WON", "LOST", "VOID"):
                out["settled"] += 1
        out["events"] += 1
        session.flush()
    return out


def settlement_audit(session: Session, *, now: datetime | None = None) -> dict:
    """Dashboard de liquidação (§12) + MARKET DATA COVERAGE (§13)."""
    now = now or datetime.utcnow()
    cutoff = now - FINISHED_AFTER
    # eventos com seleções normalizadas pré-jogo
    ev_rows = session.execute(select(SuperbetNormalized.event_id, func.min(SuperbetNormalized.kickoff_utc)).where(SuperbetNormalized.event_state == "prematch").group_by(SuperbetNormalized.event_id)).all()
    finished = [eid for eid, ko in ev_rows if ko is not None and ko <= cutoff]
    st_rows = session.execute(select(SuperbetSettlement.event_id, SuperbetSettlement.market_category, SuperbetSettlement.status, func.count()).group_by(SuperbetSettlement.event_id, SuperbetSettlement.market_category, SuperbetSettlement.status)).all()
    by_event_status: dict[int, dict[str, int]] = {}
    per_market: dict[str, dict[str, int]] = {c: {"settled": 0, "missing": 0, "unsupported": 0, "error": 0, "void": 0} for c in CATEGORY_ORDER}
    market_events_settled: dict[str, set[int]] = {c: set() for c in CATEGORY_ORDER}
    for eid, cat, status, n in st_rows:
        by_event_status.setdefault(eid, {})[status] = by_event_status.get(eid, {}).get(status, 0) + n
        if cat not in per_market:
            continue
        if status in ("WON", "LOST"):
            per_market[cat]["settled"] += n
            market_events_settled[cat].add(eid)
        elif status == "VOID":
            per_market[cat]["void"] += n
            market_events_settled[cat].add(eid)
        elif status == "UNSETTLED_DATA_MISSING":
            per_market[cat]["missing"] += n
        elif status == "UNSUPPORTED":
            per_market[cat]["unsupported"] += n
        else:
            per_market[cat]["error"] += n
    settled_ev = pending_ev = missing_result = missing_stats = error_ev = 0
    for eid in finished:
        s = by_event_status.get(eid)
        if not s:
            pending_ev += 1
            continue
        if s.get("ERROR"):
            error_ev += 1
        miss = s.get("UNSETTLED_DATA_MISSING", 0)
        if miss and not (s.get("WON") or s.get("LOST") or s.get("VOID")):
            missing_result += 1  # nem o placar existe
        elif miss:
            missing_stats += 1  # placar sim, estatísticas secundárias não
        else:
            settled_ev += 1
    # cobertura por mercado: eventos com snapshots / eventos liquidados
    ev_market = session.execute(select(SuperbetNormalized.market_category, func.count(func.distinct(SuperbetNormalized.event_id)), func.count()).where(SuperbetNormalized.event_state == "prematch").group_by(SuperbetNormalized.market_category)).all()
    cov = {c: {"events": 0, "snapshots": 0} for c in CATEGORY_ORDER}
    for cat, ne, ns in ev_market:
        if cat in cov:
            cov[cat] = {"events": int(ne), "snapshots": int(ns)}
    miss_counter: dict[tuple[str, str], int] = {}
    for cat, fields in session.execute(select(SuperbetSettlement.market_category, SuperbetSettlement.missing_fields).where(SuperbetSettlement.status == "UNSETTLED_DATA_MISSING")).all():
        key = (cat, ",".join(sorted(fields)) if isinstance(fields, list) else str(fields or "?"))
        miss_counter[key] = miss_counter.get(key, 0) + 1
    missing_by_market = sorted(({"market_category": c, "missing_fields": f, "n": n} for (c, f), n in miss_counter.items()), key=lambda r: (-r["n"], r["market_category"]))
    return {
        "generated_at": now, "settlement_version": SETTLEMENT_VERSION,
        "events": {"finished": len(finished), "settled": settled_ev, "pending": pending_ev, "missing_result": missing_result, "missing_market_stats": missing_stats, "errors": error_ev},
        "match_result_vs_secondary": {
            "match_result": {"settled": per_market["MATCH_RESULT"]["settled"], "missing": per_market["MATCH_RESULT"]["missing"]},
            "secondary": {"settled": sum(per_market[c]["settled"] for c in ("CORNERS_TOTAL", "TEAM_CORNERS", "CARDS_TOTAL", "TEAM_CARDS", "SHOTS", "SHOTS_ON_TARGET")), "missing": sum(per_market[c]["missing"] for c in ("CORNERS_TOTAL", "TEAM_CORNERS", "CARDS_TOTAL", "TEAM_CARDS", "SHOTS", "SHOTS_ON_TARGET"))},
        },
        "market_data_coverage": [
            {"market_category": c, "events_with_snapshots": cov[c]["events"], "snapshots": cov[c]["snapshots"], "events_settled": len(market_events_settled[c]), **per_market[c]}
            for c in CATEGORY_ORDER
        ],
        "missing_by_market": missing_by_market,
        "note": "UNSETTLED_DATA_MISSING = estatística ausente na fonte (nunca se assume derrota). UNSUPPORTED = mercado sem regra de liquidação (jogador).",
    }


__all__ = ["settle_events", "settlement_audit", "settle_canonical", "result_for_event", "FullResult"]
