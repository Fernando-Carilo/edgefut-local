"""SuperbetEvidenceEngine — a Superbet como fonte de evidência (iteração 4, §8–§12, §25–§27).

Tudo aqui é **leitura** das tabelas append-only `odds_snapshot`, `closing_line`, `shadow_prediction`
e `event`. Nenhum snapshot é inventado: buckets de tempo até o kickoff só existem quando há coleta
nesse intervalo; overround só é calculado quando o mercado está completo no mesmo instante; CLV só
quando existe closing line própria da Superbet para a seleção recomendada.

Separação obrigatória de fontes: isto é **SUPERBET SHADOW VALIDATION** (odds próprias, com
carimbo de tempo). O replay histórico com odds football-data é **RESEARCH MARKET BENCHMARK** e
vive em `validation/replay.py` — os dois nunca se misturam num mesmo número.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ClosingLine, Event, OddsSnapshot, ShadowPrediction, ValidationRun
from .bootstrap import cluster_bootstrap_ci, effective_sample_size, sample_quality

log = logging.getLogger(__name__)

DATASET_VERSION = "superbet-shadow-v1"
CLASSIFICATION = "SUPERBET SHADOW VALIDATION"

# buckets de tempo até o kickoff (minutos) — só reportados quando existem coletas
TTK_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("T-15m", 0, 15), ("T-30m", 15, 30), ("T-1h", 30, 60), ("T-3h", 60, 180), ("T-6h", 180, 360), ("T-12h", 360, 720), ("T-24h", 720, 1440), (">24h", 1440, math.inf),
)
# nº de seleções que fecham um mercado (para overround/fair probability)
COMPLETE_MARKET = {
    "1X2": 3, "DOUBLE_CHANCE": 3, "DRAW_NO_BET": 2, "TOTAL_GOALS": 2, "BTTS": 2, "TEAM_TOTAL_HOME": 2, "TEAM_TOTAL_AWAY": 2,
    "HANDICAP": 2, "ASIAN_HANDICAP": 2, "TOTAL_CORNERS": 2, "TOTAL_CARDS": 2,
}
# soma das probabilidades justas de um mercado completo: dupla chance cobre cada resultado duas vezes → 2.0
BOOK_TOTAL = {"DOUBLE_CHANCE": 2.0}
FAMILY = {
    "1X2": "1X2", "DOUBLE_CHANCE": "1X2", "DRAW_NO_BET": "1X2", "HANDICAP": "1X2", "ASIAN_HANDICAP": "1X2",
    "TOTAL_GOALS": "OU", "TEAM_TOTAL_HOME": "OU", "TEAM_TOTAL_AWAY": "OU", "BTTS": "BTTS", "TOTAL_CORNERS": "CORNERS", "TOTAL_CARDS": "CARDS",
}
PRIMARY_MARKETS = ("1X2", "TOTAL_GOALS", "BTTS")
ODDS_BANDS = ((1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 1000.0))
MIN_GROUPS = 20      # mínimo de grupos completos para reportar um overround por segmento
MIN_BIAS_N = 50      # mínimo de seleções liquidadas para um teste de viés


def family(market_key: str) -> str:
    return FAMILY.get(market_key, "OTHER")


def ttk_bucket(minutes: float | None) -> str | None:
    if minutes is None or (isinstance(minutes, float) and math.isnan(minutes)) or minutes < 0:
        return None
    for name, lo, hi in TTK_BUCKETS:
        if lo <= minutes < hi:
            return name
    return None


def odds_band(o: float | None) -> str | None:
    if o is None or not (o > 1):
        return None
    for lo, hi in ODDS_BANDS:
        if lo <= o < hi:
            return f"{lo:.2f}-{hi:.2f}" if hi < 1000 else f"{lo:.2f}+"
    return None


# ---------------------------------------------------------------------------
# carga
# ---------------------------------------------------------------------------
def load_snapshots(session: Session, *, since: datetime | None = None) -> pd.DataFrame:
    """Snapshots de odds **pré-kickoff** (odds ao vivo ficam fora: não são evidência pré-jogo)."""
    q = (
        select(OddsSnapshot.id, OddsSnapshot.event_id, OddsSnapshot.market_key, OddsSnapshot.selection_key, OddsSnapshot.line, OddsSnapshot.price,
               OddsSnapshot.collected_at, Event.kickoff_utc, Event.competition_name, Event.category_name, Event.home_score, Event.away_score, Event.settlement_status)
        .join(Event, Event.id == OddsSnapshot.event_id)
        .where(OddsSnapshot.price > 1.0, Event.duplicate_of.is_(None))
    )
    if since is not None:
        q = q.where(OddsSnapshot.collected_at >= since)
    rows = session.execute(q).all()
    cols = ["snapshot_id", "event_id", "market_key", "selection_key", "line", "price", "collected_at", "kickoff_utc", "competition_name", "category_name", "home_score", "away_score", "settlement_status"]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    df["collected_at"] = pd.to_datetime(df["collected_at"])
    df["kickoff_utc"] = pd.to_datetime(df["kickoff_utc"])
    df["minutes_to_kickoff"] = (df["kickoff_utc"] - df["collected_at"]).dt.total_seconds() / 60.0
    df["pre_kickoff"] = df["minutes_to_kickoff"] >= 0
    df["ttk_bucket"] = df["minutes_to_kickoff"].map(ttk_bucket)
    df["family"] = df["market_key"].map(family)
    # instante de coleta arredondado ao minuto: as seleções de um mesmo sync partilham o grupo
    df["snap_ts"] = df["collected_at"].dt.floor("min")
    df["line_key"] = df["line"].fillna(-999.0)
    return df


def add_fair_probabilities(df: pd.DataFrame) -> pd.DataFrame:
    """Overround e probabilidade justa por grupo (evento, mercado, linha, instante). Só grupos completos."""
    if df.empty:
        df["overround"] = np.nan
        df["fair_probability"] = np.nan
        df["complete"] = False
        return df
    df = df.copy()
    df["implied"] = 1.0 / df["price"]
    g = df.groupby(["event_id", "market_key", "line_key", "snap_ts"], sort=False)
    df["n_sel"] = g["selection_key"].transform("nunique")
    df["implied_sum"] = g["implied"].transform("sum")
    need = df["market_key"].map(COMPLETE_MARKET)
    df["complete"] = (df["n_sel"] == need) & (g["selection_key"].transform("count") == need)
    # soma das probabilidades verdadeiras de um mercado completo: 1, exceto dupla chance (cada resultado
    # aparece em duas seleções → 2). Sem isto a margem da dupla chance sairia como ~118 %. A margem é
    # expressa relativa ao book total para ser comparável entre mercados.
    book_total = df["market_key"].map(BOOK_TOTAL).fillna(1.0)
    df["overround"] = np.where(df["complete"], df["implied_sum"] / book_total - 1.0, np.nan)
    df["fair_probability"] = np.where(df["complete"], df["implied"] / df["implied_sum"] * book_total, np.nan)
    return df


def load_closing(session: Session) -> pd.DataFrame:
    rows = session.execute(select(ClosingLine.event_id, ClosingLine.market_key, ClosingLine.selection_key, ClosingLine.line, ClosingLine.price, ClosingLine.collected_at, ClosingLine.minutes_before_kickoff)).all()
    df = pd.DataFrame(rows, columns=["event_id", "market_key", "selection_key", "line", "closing_price", "closing_collected_at", "closing_minutes_before"])
    if not df.empty:
        df["line_key"] = df["line"].fillna(-999.0)
    return df


def load_shadow(session: Session) -> pd.DataFrame:
    q = (
        select(ShadowPrediction, Event.settlement_status, Event.home_score, Event.away_score)
        .join(Event, Event.id == ShadowPrediction.event_id)
    )
    rows = session.execute(q).all()
    recs = []
    for sp, st, hs, as_ in rows:
        recs.append({
            "id": sp.id, "event_id": sp.event_id, "created_at": sp.created_at, "kickoff_utc": sp.kickoff_utc, "competition_name": sp.competition_name, "dataset_code": sp.dataset_code,
            "market_key": sp.market_key, "selection_key": sp.selection_key, "line": sp.line, "odd": sp.odd, "model_prob": sp.model_prob, "market_prob": sp.market_prob,
            "edge_pp": sp.edge_pp, "confidence_score": sp.confidence_score, "opportunity_score": sp.opportunity_score, "data_quality": sp.data_quality, "state": sp.state,
            "evidence": sp.evidence, "is_primary": sp.is_primary, "won": sp.won, "closing_odd": sp.closing_odd, "settled_at": sp.settled_at,
            "hybrid_prob": getattr(sp, "hybrid_prob", None), "residual_edge_pp": getattr(sp, "residual_edge_pp", None), "required_edge_pp": getattr(sp, "required_edge_pp", None),
            "minutes_to_kickoff": getattr(sp, "minutes_to_kickoff", None), "event_settlement_status": st, "home_score": hs, "away_score": as_,
        })
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["kickoff_utc"] = pd.to_datetime(df["kickoff_utc"])
    if df["minutes_to_kickoff"].isna().all():
        df["minutes_to_kickoff"] = (df["kickoff_utc"] - df["created_at"]).dt.total_seconds() / 60.0
    df["ttk_bucket"] = df["minutes_to_kickoff"].map(ttk_bucket)
    df["family"] = df["market_key"].map(family)
    df["line_key"] = df["line"].fillna(-999.0)
    return df


# ---------------------------------------------------------------------------
# §8 — cobertura e buckets
# ---------------------------------------------------------------------------
def coverage(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"snapshots_pre_kickoff": 0, "snapshots_live_excluded": 0, "events": 0, "selections": 0}
    pre = df[df["pre_kickoff"]]
    sel = pre.groupby(["event_id", "market_key", "selection_key", "line_key"]).size()
    cadence = None
    if len(pre) > 10:
        ts = pre.sort_values("collected_at").groupby(["event_id", "market_key", "selection_key", "line_key"])["collected_at"].diff().dt.total_seconds().dropna() / 60.0
        ts = ts[ts > 0]
        cadence = round(float(ts.median()), 1) if len(ts) else None
    return {
        "snapshots_pre_kickoff": int(len(pre)), "snapshots_live_excluded": int((~df["pre_kickoff"]).sum()),
        "events": int(pre["event_id"].nunique()), "selections": int(len(sel)), "snapshots_per_selection_median": float(sel.median()) if len(sel) else None,
        "cadence_minutes_median": cadence, "first_collected": pre["collected_at"].min().isoformat() if len(pre) else None, "last_collected": pre["collected_at"].max().isoformat() if len(pre) else None,
        "markets": {k: int(v) for k, v in pre["market_key"].value_counts().items()},
    }


def bucket_availability(df: pd.DataFrame) -> list[dict]:
    """Quantos eventos têm coleta em cada bucket (§8: só existe quando existe)."""
    if df.empty:
        return []
    pre = df[df["pre_kickoff"] & df["market_key"].eq("1X2")]
    total_events = pre["event_id"].nunique()
    out = []
    for name, lo, hi in TTK_BUCKETS:
        m = pre[pre["ttk_bucket"] == name]
        ev = int(m["event_id"].nunique())
        out.append({"bucket": name, "minutes": [lo, None if math.isinf(hi) else hi], "snapshots": int(len(m)), "events": ev, "events_share": round(ev / total_events, 3) if total_events else None, "exists": ev > 0})
    return out


# ---------------------------------------------------------------------------
# §10 — overround
# ---------------------------------------------------------------------------
def _overround_table(groups: pd.DataFrame, by: list[str], min_groups: int = MIN_GROUPS) -> list[dict]:
    out = []
    for keys, g in groups.groupby(by, dropna=False):
        if len(g) < min_groups:
            continue
        keys = keys if isinstance(keys, tuple) else (keys,)
        row = dict(zip(by, [None if (isinstance(k, float) and math.isnan(k)) else k for k in keys], strict=True))
        row.update({"groups": int(len(g)), "events": int(g["event_id"].nunique()), "overround_median_pct": round(float(g["overround"].median()) * 100, 2),
                    "overround_mean_pct": round(float(g["overround"].mean()) * 100, 2), "p10_pct": round(float(g["overround"].quantile(0.1)) * 100, 2), "p90_pct": round(float(g["overround"].quantile(0.9)) * 100, 2)})
        out.append(row)
    out.sort(key=lambda r: -r["groups"])
    return out


def overround_report(df: pd.DataFrame) -> dict:
    pre = df[df["pre_kickoff"] & df["complete"]]
    if pre.empty:
        return {"note": "sem mercados completos pré-kickoff", "by_market": [], "by_competition": [], "by_odds_band": [], "by_time_to_kickoff": []}
    groups = pre.groupby(["event_id", "market_key", "line_key", "snap_ts"], as_index=False).agg(overround=("overround", "first"), fav_price=("price", "min"), ttk_bucket=("ttk_bucket", "first"), category_name=("category_name", "first"), competition_name=("competition_name", "first"))
    groups["fav_band"] = groups["fav_price"].map(odds_band)
    groups["line"] = groups["line_key"].where(groups["line_key"] > -999.0)
    main = groups[groups["market_key"].isin(PRIMARY_MARKETS) & ((groups["market_key"] != "TOTAL_GOALS") | (groups["line"] == 2.5))]
    return {
        "by_market": _overround_table(groups, ["market_key"]),
        "by_market_line": _overround_table(groups[groups["market_key"].isin(("TOTAL_GOALS", "TEAM_TOTAL_HOME", "TEAM_TOTAL_AWAY", "TOTAL_CORNERS", "TOTAL_CARDS", "HANDICAP", "ASIAN_HANDICAP"))], ["market_key", "line"]),
        "by_competition": _overround_table(main[main["market_key"] == "1X2"], ["category_name", "competition_name"]),
        "by_odds_band": _overround_table(main, ["market_key", "fav_band"]),
        "by_time_to_kickoff": _overround_table(main, ["market_key", "ttk_bucket"], min_groups=10),
        "note": "Overround = Σ(1/odd) − 1 no mesmo instante, só com o mercado completo. Faixa = odd do favorito do grupo.",
    }


# ---------------------------------------------------------------------------
# §9/§27 — timeline por seleção: opening / closing / movimento
# ---------------------------------------------------------------------------
def selection_timelines(df: pd.DataFrame, closing: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por seleção: opening (primeiro pré-kickoff), closing (closing_line; senão último pré-kickoff)."""
    pre = df[df["pre_kickoff"]].sort_values("collected_at")
    if pre.empty:
        return pd.DataFrame()
    key = ["event_id", "market_key", "selection_key", "line_key"]
    first = pre.groupby(key, as_index=False).first()[key + ["price", "fair_probability", "collected_at", "minutes_to_kickoff", "kickoff_utc", "category_name", "competition_name", "home_score", "away_score"]]
    first = first.rename(columns={"price": "opening_price", "fair_probability": "opening_fair", "collected_at": "opening_at", "minutes_to_kickoff": "opening_minutes_before"})
    last = pre.groupby(key, as_index=False).last()[key + ["price", "fair_probability", "collected_at", "minutes_to_kickoff"]]
    last = last.rename(columns={"price": "last_price", "fair_probability": "last_fair", "collected_at": "last_at", "minutes_to_kickoff": "last_minutes_before"})
    n = pre.groupby(key).size().rename("n_snapshots").reset_index()
    tl = first.merge(last, on=key).merge(n, on=key)
    if not closing.empty:
        tl = tl.merge(closing[key + ["closing_price", "closing_minutes_before"]], on=key, how="left")
        tl["closing_source"] = np.where(tl["closing_price"].notna(), "closing_line", "last_pre_kickoff")
        tl["closing_price"] = tl["closing_price"].fillna(tl["last_price"])
        tl["closing_minutes_before"] = tl["closing_minutes_before"].fillna(tl["last_minutes_before"])
    else:
        tl["closing_price"], tl["closing_minutes_before"], tl["closing_source"] = tl["last_price"], tl["last_minutes_before"], "last_pre_kickoff"
    tl["move_pp"] = (1.0 / tl["closing_price"] - 1.0 / tl["opening_price"]) * 100.0   # implícita, + = encurtou
    tl["family"] = tl["market_key"].map(family)
    tl["line"] = tl["line_key"].where(tl["line_key"] > -999.0)
    return tl


def line_movement_report(tl: pd.DataFrame) -> dict:
    if tl.empty:
        return {"n_selections": 0, "by_market": []}
    moved = tl[tl["n_snapshots"] >= 2]
    out = {"n_selections": int(len(tl)), "n_with_2plus_snapshots": int(len(moved)), "by_market": []}
    for mk, g in moved.groupby("market_key"):
        if len(g) < 30:
            continue
        mv = g["move_pp"].to_numpy(float)
        out["by_market"].append({
            "market_key": mk, "n": int(len(g)), "events": int(g["event_id"].nunique()), "share_moved_gt_0_5pp": round(float((np.abs(mv) > 0.5).mean()), 3),
            "abs_move_pp_median": round(float(np.median(np.abs(mv))), 2), "abs_move_pp_p90": round(float(np.quantile(np.abs(mv), 0.9)), 2),
            "net_move_pp_mean": round(float(mv.mean()), 3), "opening_minutes_before_median": round(float(g["opening_minutes_before"].median()), 0),
            "closing_minutes_before_median": round(float(g["closing_minutes_before"].median()), 1),
        })
    # favoritos encurtam? (1X2: correlação entre prob. de abertura e movimento)
    x = moved[(moved["market_key"] == "1X2") & moved["opening_fair"].notna()]
    if len(x) >= 60:
        c = float(np.corrcoef(x["opening_fair"], x["move_pp"])[0, 1])
        out["favourite_drift_corr_1x2"] = round(c, 3)
        out["favourite_drift_note"] = "corr > 0: favoritos encurtam até o kickoff (fluxo para o favorito); < 0: alongam."
    return out


# ---------------------------------------------------------------------------
# §11/§12 — viés da casa e Superbet como baseline de probabilidade
# ---------------------------------------------------------------------------
def _settle(mk: str, sk: str, line: float | None, hg: int, ag: int) -> bool | None:
    from ..backtesting.settlement import MatchResult, settle_selection

    return settle_selection(mk, sk, line, MatchResult(hg=int(hg), ag=int(ag), source="event"))


def _bias_row(label: str, won: np.ndarray, fair: np.ndarray, clusters: np.ndarray) -> dict:
    n = len(won)
    row: dict = {"segment": label, "n": n, "events": int(len(np.unique(clusters))), "sample_quality": sample_quality(n)}
    if n < MIN_BIAS_N:
        row["verdict"] = "INSUFFICIENT"
        return row
    diff = won - fair
    ci = cluster_bootstrap_ci(diff, clusters, zero_test=True)
    row.update({"observed_rate": round(float(won.mean()), 4), "fair_prob_mean": round(float(fair.mean()), 4), "bias_pp_ci": {k: (round(v * 100, 2) if isinstance(v, float) else v) for k, v in ci.to_dict().items()},
                "brier_fair": round(float(((fair - won) ** 2).mean()), 5), "effective_n": effective_sample_size(clusters, rho=1.0)["effective_n"]})
    row["verdict"] = "NO BIAS DETECTED" if not ci.conclusive else ("UNDERPRICED (paga mais do que implica)" if ci.low > 0 else "OVERPRICED (paga menos do que implica)")
    return row


def bias_report(tl: pd.DataFrame) -> dict:
    """Frequência observada vs probabilidade justa **da Superbet** (closing/última pré-kickoff) em eventos
    liquidados. Testa favoritos/underdogs, casa/fora, faixas de odd, mercados, competições. Não assume viés."""
    if tl.empty:
        return {"n": 0, "rows": []}
    s = tl[tl["home_score"].notna() & tl["away_score"].notna() & tl["market_key"].isin(PRIMARY_MARKETS)].copy()
    if s.empty:
        return {"n": 0, "rows": [], "note": "sem eventos liquidados com odds pré-kickoff"}
    # fair no fechamento: se veio de closing_line não temos o grupo → usar last_fair (mesmo instante, mercado completo)
    s = s[s["last_fair"].notna()]
    s["won"] = [_settle(mk, sk, ln, hg, ag) for mk, sk, ln, hg, ag in zip(s["market_key"], s["selection_key"], s["line"], s["home_score"], s["away_score"], strict=True)]
    s = s[s["won"].notna()]
    if s.empty:
        return {"n": 0, "rows": []}
    s["won_f"] = s["won"].astype(float)
    s["band"] = s["closing_price"].map(odds_band)
    ou = s[(s["market_key"] == "TOTAL_GOALS") & (s["line"] == 2.5)]
    x = pd.concat([s[s["market_key"] != "TOTAL_GOALS"], ou])
    rows = []
    def add(label: str, m: pd.DataFrame) -> None:
        if len(m):
            rows.append(_bias_row(label, m["won_f"].to_numpy(), m["last_fair"].to_numpy(float), m["event_id"].to_numpy()))
    for mk in PRIMARY_MARKETS:
        # mercado inteiro: Σ fair = 1 e exatamente uma seleção ganha → viés médio é 0 por construção; só o Brier informa
        m = x[x["market_key"] == mk]
        if len(m):
            r = _bias_row(f"market:{mk}", m["won_f"].to_numpy(), m["last_fair"].to_numpy(float), m["event_id"].to_numpy())
            r.pop("bias_pp_ci", None)
            r["verdict"] = "N/A (fecha em 1 por construção) — ver Brier"
            rows.append(r)
    one = x[x["market_key"] == "1X2"]
    for sel in ("HOME", "DRAW", "AWAY"):
        add(f"1x2:{sel}", one[one["selection_key"] == sel])
    add("1x2:favourite", one[one["last_fair"] >= 0.5])
    add("1x2:underdog", one[one["last_fair"] < 0.25])
    for b in sorted(x["band"].dropna().unique()):
        add(f"odds_band:{b}", x[x["band"] == b])
    for comp, g in one.groupby("category_name"):
        if len(g) >= MIN_BIAS_N:
            add(f"competition:{comp}", g)
    return {"n": int(len(x)), "events": int(x["event_id"].nunique()), "rows": rows,
            "note": "Viés só é declarado quando o IC 95% (bootstrap por evento) da diferença observado − justo exclui zero. Amostras pequenas = INSUFFICIENT."}


def superbet_baseline(shadow: pd.DataFrame) -> dict:
    """§12: mesmo evento/mercado/instante — Superbet fair (market_prob) vs EdgeFut (model_prob) vs híbrido
    (hybrid_prob, quando existe) em seleções shadow liquidadas com preço."""
    if shadow.empty:
        return {"n": 0}
    s = shadow[shadow["won"].notna() & shadow["market_prob"].notna() & shadow["odd"].notna()].copy()
    out: dict = {"n": int(len(s)), "events": int(s["event_id"].nunique()) if len(s) else 0, "by_market": []}
    if s.empty:
        return out
    s["won_f"] = s["won"].astype(float)
    def block(g: pd.DataFrame) -> dict:
        won = g["won_f"].to_numpy()
        cl = g["event_id"].to_numpy()
        res = {"n": int(len(g)), "events": int(g["event_id"].nunique()), "effective_n": effective_sample_size(cl, rho=1.0)["effective_n"], "sample_quality": sample_quality(int(g["event_id"].nunique()))}
        for name, col in (("superbet_fair", "market_prob"), ("edgefut", "model_prob"), ("hybrid", "hybrid_prob")):
            p = g[col].to_numpy(float)
            if np.isnan(p).all():
                continue
            m = ~np.isnan(p)
            eps = 1e-6
            res[name] = {"brier": cluster_bootstrap_ci((p[m] - won[m]) ** 2, cl[m]).to_dict(),
                         "log_loss": cluster_bootstrap_ci(-(won[m] * np.log(np.clip(p[m], eps, 1)) + (1 - won[m]) * np.log(np.clip(1 - p[m], eps, 1))), cl[m]).to_dict(), "n": int(m.sum())}
        if "edgefut" in res and "superbet_fair" in res:
            pm, pe = g["market_prob"].to_numpy(float), g["model_prob"].to_numpy(float)
            d = cluster_bootstrap_ci((pe - won) ** 2 - (pm - won) ** 2, cl, zero_test=True)
            res["edgefut_minus_superbet_brier"] = d.to_dict()
            res["winner"] = "INCONCLUSIVE" if not d.conclusive else ("EDGEFUT" if d.high < 0 else "SUPERBET")
        return res
    out["all"] = block(s)
    for fam, g in s.groupby("family"):
        out["by_market"].append({"family": fam, **block(g)})
    for mk, g in s[s["market_key"].isin(PRIMARY_MARKETS)].groupby("market_key"):
        out["by_market"].append({"market_key": mk, **block(g)})
    return out


# ---------------------------------------------------------------------------
# §9/§28 — CLV Superbet e §26 tempo até o kickoff
# ---------------------------------------------------------------------------
def clv_report(shadow: pd.DataFrame, closing: pd.DataFrame) -> dict:
    if shadow.empty:
        return {"n": 0}
    s = shadow[shadow["odd"].notna()].copy()
    if not closing.empty:
        s = s.merge(closing[["event_id", "market_key", "selection_key", "line_key", "closing_price"]], on=["event_id", "market_key", "selection_key", "line_key"], how="left")
        s["closing_used"] = s["closing_odd"].fillna(s["closing_price"])
    else:
        s["closing_used"] = s["closing_odd"]
    with_close = s[s["closing_used"].notna() & (s["closing_used"] > 1)].copy()
    out: dict = {"n_priced": int(len(s)), "n_with_closing": int(len(with_close)), "events_with_closing": int(with_close["event_id"].nunique()) if len(with_close) else 0, "by_family": [], "by_state": []}
    if with_close.empty:
        out["note"] = "sem closing line própria da Superbet para as seleções recomendadas — CLV indisponível (não estimado)."
        return out
    with_close["clv_pct"] = (with_close["odd"] / with_close["closing_used"] - 1.0) * 100.0
    def block(g: pd.DataFrame) -> dict:
        cl = g["event_id"].to_numpy()
        return {"n": int(len(g)), "events": int(g["event_id"].nunique()), "clv_pct": cluster_bootstrap_ci(g["clv_pct"].to_numpy(float), cl, zero_test=True).to_dict(), "share_positive": round(float((g["clv_pct"] > 0).mean()), 3)}
    out["all"] = block(with_close)
    for fam, g in with_close.groupby("family"):
        out["by_family"].append({"family": fam, **block(g)})
    for st, g in with_close.groupby("state"):
        out["by_state"].append({"state": st, **block(g)})
    out["note"] = "CLV = odd recomendada / closing Superbet − 1. Positivo = batemos o fechamento da própria casa. Closing nunca entra na recomendação."
    return out


def ttk_performance(shadow: pd.DataFrame) -> list[dict]:
    if shadow.empty:
        return []
    s = shadow[shadow["won"].notna() & shadow["market_prob"].notna()].copy()
    out = []
    for name, _, _ in TTK_BUCKETS:
        g = s[s["ttk_bucket"] == name]
        n = int(len(g))
        row = {"bucket": name, "n": n, "events": int(g["event_id"].nunique()), "sample_quality": sample_quality(int(g["event_id"].nunique()))}
        if n >= 30:
            won = g["won"].astype(float).to_numpy()
            cl = g["event_id"].to_numpy()
            pe, pm = g["model_prob"].to_numpy(float), g["market_prob"].to_numpy(float)
            row["brier_edgefut"] = round(float(((pe - won) ** 2).mean()), 5)
            row["brier_superbet"] = round(float(((pm - won) ** 2).mean()), 5)
            row["delta_ci"] = cluster_bootstrap_ci((pe - won) ** 2 - (pm - won) ** 2, cl, zero_test=True).to_dict()
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# §25 — painel de liquidação shadow por mercado
# ---------------------------------------------------------------------------
def shadow_panel(shadow: pd.DataFrame, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    if shadow.empty:
        return {"families": [], "total": {"events": 0, "predictions": 0, "settled": 0, "pending": 0, "errors": 0, "upcoming": 0}}
    s = shadow.copy()
    finished = s["kickoff_utc"] < pd.Timestamp(now) - pd.Timedelta(hours=2)
    s["bucket"] = np.select([s["settled_at"].notna(), finished & (s["event_settlement_status"] == "SETTLEMENT_ERROR"), finished], ["settled", "error", "pending"], default="upcoming")
    def block(g: pd.DataFrame) -> dict:
        b = g["bucket"].value_counts()
        return {"events": int(g["event_id"].nunique()), "predictions": int(len(g)), "settled": int(b.get("settled", 0)), "pending": int(b.get("pending", 0)), "errors": int(b.get("error", 0)), "upcoming": int(b.get("upcoming", 0)),
                "settled_events": int(g[g["bucket"] == "settled"]["event_id"].nunique()), "priced": int(g["odd"].notna().sum()), "value": int((g["state"] == "VALUE").sum()), "observation": int((g["state"] == "OBSERVATION").sum())}
    order = ["1X2", "OU", "BTTS", "CORNERS", "CARDS", "OTHER"]
    fams = [{"family": f, **block(s[s["family"] == f])} for f in order if (s["family"] == f).any()]
    return {"families": fams, "total": block(s), "by_market": [{"market_key": mk, **block(g)} for mk, g in s.groupby("market_key")]}


# ---------------------------------------------------------------------------
# §34/§35 — dataset processado versionado
# ---------------------------------------------------------------------------
def export_dataset(shadow: pd.DataFrame, tl: pd.DataFrame, out_dir=None) -> dict:
    from pathlib import Path

    from ..core.paths import get_paths

    d = Path(out_dir) if out_dir else get_paths().processed / "superbet_shadow"
    d.mkdir(parents=True, exist_ok=True)
    tag = datetime.utcnow().strftime("%Y%m%d")
    paths = {}
    for name, df in (("shadow", shadow), ("timelines", tl)):
        if df is None or df.empty:
            continue
        p = d / f"{DATASET_VERSION}_{name}_{tag}.parquet"
        df.to_parquet(p, index=False)
        paths[name] = {"path": str(p), "rows": int(len(df)), "sha16": hashlib.sha256(p.read_bytes()).hexdigest()[:16]}
    return {"dataset_version": DATASET_VERSION, "files": paths}


# ---------------------------------------------------------------------------
# relatório completo
# ---------------------------------------------------------------------------
def evidence_report(session: Session, *, persist: bool = False, export: bool = False) -> dict:
    t0 = time.perf_counter()
    snaps = add_fair_probabilities(load_snapshots(session))
    closing = load_closing(session)
    shadow = load_shadow(session)
    tl = selection_timelines(snaps, closing)
    report = {
        "classification": CLASSIFICATION, "dataset_version": DATASET_VERSION, "generated_at": datetime.utcnow().isoformat(),
        "coverage": coverage(snaps), "buckets": bucket_availability(snaps), "closing_lines": {"rows": int(len(closing)), "events": int(closing["event_id"].nunique()) if len(closing) else 0},
        "overround": overround_report(snaps), "line_movement": line_movement_report(tl), "bias": bias_report(tl),
        "baseline": superbet_baseline(shadow), "clv": clv_report(shadow, closing), "time_to_kickoff": ttk_performance(shadow), "shadow_panel": shadow_panel(shadow),
        "notes": [
            "Fonte: odds Superbet coletadas pelo próprio app (carimbo de tempo real). Odds ao vivo (pós-kickoff) excluídas.",
            "Nada aqui usa football-data: esse é o RESEARCH MARKET BENCHMARK (replay), reportado separadamente.",
            "N pequeno é reportado como pequeno; nenhum bucket ou segmento sem dados é estimado.",
        ],
    }
    if export:
        report["export"] = export_dataset(shadow, tl)
    report["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    if persist:
        session.add(ValidationRun(kind="superbet_evidence", request={}, summary={
            "events": report["coverage"].get("events"), "snapshots": report["coverage"].get("snapshots_pre_kickoff"), "shadow_settled": report["shadow_panel"]["total"]["settled"],
            "clv_n": report["clv"].get("n_with_closing"), "baseline_n": report["baseline"].get("n")}, detail=report, duration_ms=report["duration_ms"]))
    return report


def latest_report(session: Session) -> dict | None:
    r = session.execute(select(ValidationRun).where(ValidationRun.kind == "superbet_evidence").order_by(ValidationRun.created_at.desc()).limit(1)).scalar_one_or_none()
    return r.detail if r and r.detail else None


def selection_history(session: Session, event_id: int, market_key: str, selection_key: str, line: float | None) -> dict:
    """Timeline de uma seleção para a página do jogo: opening / snapshots / closing, com fair e overround."""
    df = add_fair_probabilities(load_snapshots(session))
    if df.empty:
        return {"points": []}
    lk = -999.0 if line is None else float(line)
    m = df[(df["event_id"] == event_id) & (df["market_key"] == market_key) & (df["selection_key"] == selection_key) & (df["line_key"] == lk) & df["pre_kickoff"]].sort_values("collected_at")
    pts = [{"collected_at": r.collected_at.isoformat(), "minutes_to_kickoff": round(float(r.minutes_to_kickoff), 1), "bucket": r.ttk_bucket, "odd": float(r.price),
            "fair_probability": None if pd.isna(r.fair_probability) else round(float(r.fair_probability), 4), "overround": None if pd.isna(r.overround) else round(float(r.overround), 4), "source_snapshot_id": int(r.snapshot_id)} for r in m.itertuples()]
    cl = session.execute(select(ClosingLine).where(ClosingLine.event_id == event_id, ClosingLine.market_key == market_key, ClosingLine.selection_key == selection_key)).scalars().all()
    cl = [c for c in cl if (c.line is None and line is None) or (c.line is not None and line is not None and abs(c.line - line) < 1e-9)]
    return {"points": pts, "opening": pts[0] if pts else None, "closing": {"odd": cl[0].price, "minutes_before_kickoff": cl[0].minutes_before_kickoff, "collected_at": cl[0].collected_at.isoformat()} if cl else None,
            "line_state": "CLOSED" if cl else ("OPEN" if pts else "NO_DATA")}


__all__ = ["evidence_report", "latest_report", "selection_history", "load_snapshots", "add_fair_probabilities", "selection_timelines", "bias_report", "clv_report",
           "shadow_panel", "ttk_bucket", "family", "TTK_BUCKETS", "DATASET_VERSION", "CLASSIFICATION"]
