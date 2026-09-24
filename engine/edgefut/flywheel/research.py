"""Research engines do Data Flywheel (§15–§30, §35–§36).

Tudo é **descritivo e pré-registado**: nenhum número aqui vira recomendação. As funções recebem o
frame derivado (`derived_frame`) construído a partir de `superbet_normalized_v1` (pré-kickoff) +
`superbet_settlement_v1` + `shadow_prediction` (probabilidade EdgeFut na mesma seleção, gravada
antes do jogo). Onde o N efetivo é pequeno, o resultado é INSUFFICIENT — não "tendência".
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db.models import ShadowPrediction, SuperbetNormalized, SuperbetSettlement
from ..validation.bootstrap import (
    benjamini_hochberg,
    bootstrap_p_value,
    cluster_bootstrap_ci,
    effective_sample_size,
)
from .collector import SNAPSHOT_TARGETS
from .markets import CATEGORY_LABELS, CATEGORY_ORDER, RESEARCH_FAMILY, canonical_market_id

TARGET_ORDER = [t[0] for t in SNAPSHOT_TARGETS]
ODDS_BANDS = ((1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 1000.0))
MIN_N_METRIC = 30
MATURITY = (("MATURE", 500), ("TESTABLE", 200), ("EARLY", 50), ("COLLECTING", 0))
STEAM_PP = 3.0
DRIFT_PP = 1.5

# shadow_prediction.market_key (legado) → categoria canónica (+ lado)
LEGACY_TO_CANONICAL: dict[str, tuple[str, str | None]] = {
    "1X2": ("MATCH_RESULT", None), "DOUBLE_CHANCE": ("DOUBLE_CHANCE", None), "DRAW_NO_BET": ("DNB", None), "TOTAL_GOALS": ("TOTAL_GOALS", None), "BTTS": ("BTTS", None),
    "TEAM_TOTAL_HOME": ("TEAM_TOTAL", "HOME"), "TEAM_TOTAL_AWAY": ("TEAM_TOTAL", "AWAY"), "TOTAL_CORNERS": ("CORNERS_TOTAL", None), "TOTAL_CARDS": ("CARDS_TOTAL", None), "TOTAL_SHOTS": ("SHOTS", None),
}


def odds_band(o: float | None) -> str | None:
    if o is None or not (o > 1):
        return None
    for lo, hi in ODDS_BANDS:
        if lo <= o < hi:
            return f"{lo:.2f}-{hi:.2f}" if hi < 1000 else f"{lo:.2f}+"
    return None


def maturity(effective_n: int) -> str:
    for name, thr in MATURITY:
        if effective_n >= thr:
            return name
    return "COLLECTING"


# ---------------------------------------------------------------------------
# carga
# ---------------------------------------------------------------------------
def load_normalized(session: Session, *, since: datetime | None = None, categories: list[str] | None = None) -> pd.DataFrame:
    q = select(
        SuperbetNormalized.id, SuperbetNormalized.raw_snapshot_id, SuperbetNormalized.event_id, SuperbetNormalized.competition_name, SuperbetNormalized.market_category,
        SuperbetNormalized.canonical_market_id, SuperbetNormalized.selection_id, SuperbetNormalized.line, SuperbetNormalized.odd, SuperbetNormalized.implied_prob, SuperbetNormalized.fair_prob,
        SuperbetNormalized.overround, SuperbetNormalized.fetched_at, SuperbetNormalized.kickoff_utc, SuperbetNormalized.minutes_to_kickoff, SuperbetNormalized.snapshot_target, SuperbetNormalized.identity_confidence,
    ).where(SuperbetNormalized.event_state == "prematch")
    if since is not None:
        q = q.where(SuperbetNormalized.fetched_at >= since)
    if categories:
        q = q.where(SuperbetNormalized.market_category.in_(categories))
    rows = session.execute(q).all()
    cols = ["id", "raw_snapshot_id", "event_id", "competition_name", "market_category", "canonical_market_id", "selection_id", "line", "odd", "implied_prob", "fair_prob", "overround", "fetched_at", "kickoff_utc", "minutes_to_kickoff", "snapshot_target", "identity_confidence"]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    df["fetched_at"] = pd.to_datetime(df["fetched_at"])
    df["kickoff_utc"] = pd.to_datetime(df["kickoff_utc"])
    df["family"] = df["market_category"].map(RESEARCH_FAMILY)
    df["band"] = df["odd"].map(odds_band)
    return df


def load_settlement(session: Session) -> pd.DataFrame:
    rows = session.execute(select(SuperbetSettlement.event_id, SuperbetSettlement.canonical_market_id, SuperbetSettlement.selection_id, SuperbetSettlement.status, SuperbetSettlement.result_source)).all()
    df = pd.DataFrame(rows, columns=["event_id", "canonical_market_id", "selection_id", "status", "result_source"])
    if df.empty:
        df["won"] = pd.Series(dtype=float)
        return df
    df["won"] = np.select([df["status"] == "WON", df["status"] == "LOST"], [1.0, 0.0], default=np.nan)
    return df


def load_edgefut(session: Session) -> pd.DataFrame:
    """Probabilidade EdgeFut por seleção canónica, gravada ANTES do jogo (shadow). Uma linha por (evento, seleção): a última pré-kickoff."""
    rows = session.execute(select(ShadowPrediction.event_id, ShadowPrediction.market_key, ShadowPrediction.selection_key, ShadowPrediction.line, ShadowPrediction.model_prob, ShadowPrediction.created_at, ShadowPrediction.kickoff_utc, ShadowPrediction.state)).all()
    recs = []
    for eid, mk, sk, line, p, created, ko, state in rows:
        m = LEGACY_TO_CANONICAL.get(mk)
        if m is None or p is None:
            continue
        cat, side = m
        recs.append({"event_id": eid, "canonical_market_id": canonical_market_id(cat, line, side), "selection_id": sk, "edgefut_prob": float(p), "edgefut_at": created, "kickoff_utc": ko, "edgefut_state": state})
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    df["edgefut_at"] = pd.to_datetime(df["edgefut_at"])
    df["kickoff_utc"] = pd.to_datetime(df["kickoff_utc"])
    df = df[df["edgefut_at"] <= df["kickoff_utc"]].sort_values("edgefut_at")
    return df.groupby(["event_id", "canonical_market_id", "selection_id"], as_index=False).last().drop(columns=["kickoff_utc"])


def timelines(norm: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por seleção: opening, closing (último pré-kickoff), fair em cada alvo T-x, nº de observações."""
    if norm.empty:
        return pd.DataFrame()
    key = ["event_id", "market_category", "canonical_market_id", "selection_id"]
    n = norm.sort_values("fetched_at")
    first = n.groupby(key, as_index=False).first()[key + ["competition_name", "line", "odd", "fair_prob", "fetched_at", "minutes_to_kickoff", "kickoff_utc"]].rename(columns={"odd": "opening_odd", "fair_prob": "opening_fair", "fetched_at": "opening_at", "minutes_to_kickoff": "opening_minutes"})
    last = n.groupby(key, as_index=False).last()[key + ["odd", "fair_prob", "fetched_at", "minutes_to_kickoff", "overround"]].rename(columns={"odd": "closing_odd", "fair_prob": "closing_fair", "fetched_at": "closing_at", "minutes_to_kickoff": "closing_minutes", "overround": "closing_overround"})
    cnt = n.groupby(key).size().rename("n_obs").reset_index()
    tl = first.merge(last, on=key).merge(cnt, on=key)
    tg = n[n["snapshot_target"].notna()]
    if not tg.empty:
        piv = tg.pivot_table(index=key, columns="snapshot_target", values="fair_prob", aggfunc="first")
        piv.columns = [f"fair@{c}" for c in piv.columns]
        tl = tl.merge(piv.reset_index(), on=key, how="left")
        pivo = tg.pivot_table(index=key, columns="snapshot_target", values="odd", aggfunc="first")
        pivo.columns = [f"odd@{c}" for c in pivo.columns]
        tl = tl.merge(pivo.reset_index(), on=key, how="left")
    tl["move_pp"] = (tl["closing_fair"] - tl["opening_fair"]) * 100.0
    tl["family"] = tl["market_category"].map(RESEARCH_FAMILY)
    tl["band"] = tl["closing_odd"].map(odds_band)
    return tl


def derived_frame(session: Session, *, since: datetime | None = None) -> dict[str, pd.DataFrame]:
    norm = load_normalized(session, since=since)
    tl = timelines(norm)
    st = load_settlement(session)
    ef = load_edgefut(session)
    if not tl.empty and not st.empty:
        tl = tl.merge(st[["event_id", "canonical_market_id", "selection_id", "status", "won", "result_source"]], on=["event_id", "canonical_market_id", "selection_id"], how="left")
    elif not tl.empty:
        tl["status"], tl["won"], tl["result_source"] = None, np.nan, None
    if not tl.empty and not ef.empty:
        tl = tl.merge(ef, on=["event_id", "canonical_market_id", "selection_id"], how="left")
    elif not tl.empty:
        tl["edgefut_prob"], tl["edgefut_at"], tl["edgefut_state"] = np.nan, pd.NaT, None
    return {"normalized": norm, "timelines": tl, "settlement": st, "edgefut": ef}


# ---------------------------------------------------------------------------
# §15 — MARGIN LAB
# ---------------------------------------------------------------------------
def _overround_rows(g: pd.DataFrame, by: list[str], min_groups: int = 20) -> list[dict]:
    out = []
    for keys, x in g.groupby(by, dropna=False):
        if len(x) < min_groups:
            continue
        keys = keys if isinstance(keys, tuple) else (keys,)
        row = dict(zip(by, [None if isinstance(k, float) and math.isnan(k) else k for k in keys], strict=True))
        ov = x["overround"].to_numpy(float) * 100
        row.update({"groups": int(len(x)), "events": int(x["event_id"].nunique()), "overround_median_pct": round(float(np.median(ov)), 2), "overround_mean_pct": round(float(ov.mean()), 2), "p10_pct": round(float(np.quantile(ov, 0.1)), 2), "p90_pct": round(float(np.quantile(ov, 0.9)), 2)})
        out.append(row)
    out.sort(key=lambda r: -r["groups"])
    return out


def margin_lab(norm: pd.DataFrame) -> dict:
    if norm.empty or norm["overround"].notna().sum() == 0:
        return {"by_market": [], "by_competition": [], "by_odds_band": [], "by_time_to_kickoff": [], "note": "sem mercados completos ainda"}
    g = norm[norm["overround"].notna()].groupby(["event_id", "canonical_market_id", "raw_snapshot_id"], as_index=False).agg(
        market_category=("market_category", "first"), overround=("overround", "first"), fav_odd=("odd", "min"), competition_name=("competition_name", "first"), snapshot_target=("snapshot_target", "first"), line=("line", "first"))
    g["fav_band"] = g["fav_odd"].map(odds_band)
    return {
        "by_market": _overround_rows(g, ["market_category"]),
        "by_market_line": _overround_rows(g[g["line"].notna()], ["market_category", "line"]),
        "by_competition": _overround_rows(g[g["market_category"] == "MATCH_RESULT"], ["competition_name"]),
        "by_odds_band": _overround_rows(g, ["market_category", "fav_band"]),
        "by_time_to_kickoff": _overround_rows(g[g["snapshot_target"].notna()], ["market_category", "snapshot_target"], min_groups=10),
        "note": "Overround = Σ(1/odd)/book_total − 1 no mesmo payload, só com mercado completo. Faixa = odd do favorito do grupo.",
    }


# ---------------------------------------------------------------------------
# §16 — eficiência de preço por tempo até o kickoff
# ---------------------------------------------------------------------------
def _score_block(p: np.ndarray, y: np.ndarray, clusters: np.ndarray) -> dict:
    eps = 1e-6
    brier = (p - y) ** 2
    ll = -(y * np.log(np.clip(p, eps, 1)) + (1 - y) * np.log(np.clip(1 - p, eps, 1)))
    ess = effective_sample_size(clusters, rho=1.0)
    out = {"n": int(len(y)), "events": ess["clusters"], "effective_n": ess["effective_n"], "brier": cluster_bootstrap_ci(brier, clusters).to_dict(), "log_loss": cluster_bootstrap_ci(ll, clusters).to_dict()}
    out["calibration_bias_pp"] = cluster_bootstrap_ci((y - p) * 100, clusters, zero_test=True).to_dict()
    return out


def price_efficiency_by_time(norm: pd.DataFrame, st: pd.DataFrame) -> dict:
    if norm.empty or st.empty:
        return {"rows": [], "note": "sem seleções liquidadas"}
    x = norm[norm["snapshot_target"].notna() & norm["fair_prob"].notna()].merge(st[["event_id", "canonical_market_id", "selection_id", "won"]], on=["event_id", "canonical_market_id", "selection_id"])
    x = x[x["won"].notna()]
    rows = []
    for (cat, tgt), g in x.groupby(["market_category", "snapshot_target"]):
        row = {"market_category": cat, "target": tgt, "n": int(len(g)), "events": int(g["event_id"].nunique())}
        if len(g) >= MIN_N_METRIC and g["event_id"].nunique() >= 10:
            row.update(_score_block(g["fair_prob"].to_numpy(float), g["won"].to_numpy(float), g["event_id"].to_numpy()))
            row["verdict"] = "OK"
        else:
            row["verdict"] = "INSUFFICIENT"
        rows.append(row)
    rows.sort(key=lambda r: (CATEGORY_ORDER.index(r["market_category"]) if r["market_category"] in CATEGORY_ORDER else 99, TARGET_ORDER.index(r["target"]) if r["target"] in TARGET_ORDER else 99))
    return {"rows": rows, "note": "Brier/LogLoss da probabilidade justa da Superbet no alvo T-x contra o resultado. Por construção, um mercado completo tem viés médio ~0; o Brier compara instantes."}


# ---------------------------------------------------------------------------
# §17 — CLV ENGINE V2
# ---------------------------------------------------------------------------
def clv_v2(norm: pd.DataFrame, tl: pd.DataFrame) -> dict:
    """Para cada observação num alvo T-x: CLV bruto = odd_T / odd_closing − 1; CLV justo = fair_closing − fair_T (pp).
    Descritivo — mede quanto o preço de T-x diferia do fechamento, por mercado e por instante."""
    if norm.empty or tl.empty:
        return {"rows": [], "note": "sem timelines"}
    key = ["event_id", "canonical_market_id", "selection_id"]
    x = norm[norm["snapshot_target"].notna()].merge(tl[key + ["closing_odd", "closing_fair", "closing_at"]], on=key)
    x = x[x["fetched_at"] < x["closing_at"]]
    if x.empty:
        return {"rows": [], "note": "nenhuma observação anterior ao fechamento"}
    x["clv_raw_pct"] = (x["odd"] / x["closing_odd"] - 1.0) * 100.0
    x["clv_fair_pp"] = (x["closing_fair"] - x["fair_prob"]) * 100.0
    rows = []
    for (cat, tgt), g in x.groupby(["market_category", "snapshot_target"]):
        cl = g["event_id"].to_numpy()
        row = {"market_category": cat, "target": tgt, "n": int(len(g)), "events": int(g["event_id"].nunique()), "effective_n": effective_sample_size(cl, rho=1.0)["effective_n"]}
        if len(g) >= MIN_N_METRIC and g["event_id"].nunique() >= 10:
            row["clv_raw_pct"] = cluster_bootstrap_ci(g["clv_raw_pct"].to_numpy(float), cl, zero_test=True).to_dict()
            fair = g[g["clv_fair_pp"].notna()]
            row["clv_fair_pp"] = cluster_bootstrap_ci(fair["clv_fair_pp"].to_numpy(float), fair["event_id"].to_numpy(), zero_test=True).to_dict() if len(fair) >= MIN_N_METRIC else None
            row["abs_move_pp_median"] = round(float(np.nanmedian(np.abs(g["clv_fair_pp"].to_numpy(float)))), 2) if fair.size else None
            row["verdict"] = "OK"
        else:
            row["verdict"] = "INSUFFICIENT"
        rows.append(row)
    rows.sort(key=lambda r: (CATEGORY_ORDER.index(r["market_category"]) if r["market_category"] in CATEGORY_ORDER else 99, TARGET_ORDER.index(r["target"]) if r["target"] in TARGET_ORDER else 99))
    return {"rows": rows, "note": "CLV aqui é do PREÇO DA CASA em T-x vs fechamento — não de apostas. Sem causalidade."}


# ---------------------------------------------------------------------------
# §18 — classificação de movimento (descritiva)
# ---------------------------------------------------------------------------
def classify_move(move_pp: float | None, fair_t3: float | None, opening_fair: float | None, closing_fair: float | None) -> str | None:
    if move_pp is None or (isinstance(move_pp, float) and math.isnan(move_pp)):
        return None
    a = abs(move_pp)
    if a < DRIFT_PP:
        return "STABLE"
    if a >= STEAM_PP and fair_t3 is not None and not (isinstance(fair_t3, float) and math.isnan(fair_t3)) and closing_fair is not None and opening_fair is not None:
        late = (closing_fair - fair_t3) * 100.0
        if abs(late) >= 0.6 * a and np.sign(late) == np.sign(move_pp):
            return "STEAM"  # ≥60 % do movimento nas últimas ~3 h, na mesma direção
    return "DRIFT"


def line_movement(tl: pd.DataFrame, top: int = 40) -> dict:
    if tl.empty:
        return {"by_market": [], "top": []}
    x = tl[(tl["n_obs"] >= 2) & tl["move_pp"].notna()].copy()
    if x.empty:
        return {"by_market": [], "top": [], "note": "sem seleções com ≥2 observações e fair definido"}
    f3 = x["fair@T-3h"] if "fair@T-3h" in x.columns else pd.Series(np.nan, index=x.index)
    x["move_class"] = [classify_move(m, t3, o, c) for m, t3, o, c in zip(x["move_pp"], f3, x["opening_fair"], x["closing_fair"], strict=True)]
    by = []
    for cat, g in x.groupby("market_category"):
        vc = g["move_class"].value_counts()
        mv = g["move_pp"].to_numpy(float)
        by.append({"market_category": cat, "n": int(len(g)), "events": int(g["event_id"].nunique()), "steam": int(vc.get("STEAM", 0)), "drift": int(vc.get("DRIFT", 0)), "stable": int(vc.get("STABLE", 0)),
                   "abs_move_pp_median": round(float(np.median(np.abs(mv))), 2), "abs_move_pp_p90": round(float(np.quantile(np.abs(mv), 0.9)), 2), "opening_minutes_median": round(float(g["opening_minutes"].median()), 0)})
    x["abs"] = x["move_pp"].abs()
    topm = x.sort_values("abs", ascending=False).head(top)
    return {
        "by_market": by,
        "top": [{"event_id": int(r.event_id), "competition": r.competition_name, "market": r.canonical_market_id, "selection": r.selection_id, "opening_odd": r.opening_odd, "closing_odd": r.closing_odd, "move_pp": round(float(r.move_pp), 2), "move_class": r.move_class, "n_obs": int(r.n_obs), "kickoff_utc": r.kickoff_utc} for r in topm.itertuples()],
        "note": f"STABLE |Δ|<{DRIFT_PP} pp · DRIFT ≥{DRIFT_PP} pp · STEAM ≥{STEAM_PP} pp com ≥60 % do movimento após T-3h. Descritivo: não se infere 'smart money'.",
    }


# ---------------------------------------------------------------------------
# §19/§20 — EdgeFut LEAD/LAG e tempo de antecedência do sinal
# ---------------------------------------------------------------------------
def lead_lag(norm: pd.DataFrame, tl: pd.DataFrame) -> dict:
    """O mercado move-se depois na direção do EdgeFut? Sinal = p_EdgeFut − fair(T); movimento = fair(closing) − fair(T),
    com T = última observação Superbet anterior à previsão. Só agregado (N efetivo ≥ 30); nunca por jogo."""
    if tl.empty or "edgefut_prob" not in tl.columns or tl["edgefut_prob"].notna().sum() == 0:
        return {"rows": [], "verdict": "INSUFFICIENT", "note": "sem previsões EdgeFut pré-jogo emparelhadas com seleções Superbet"}
    key = ["event_id", "canonical_market_id", "selection_id"]
    e = tl[tl["edgefut_prob"].notna() & tl["closing_fair"].notna()][key + ["market_category", "edgefut_prob", "edgefut_at", "closing_fair", "closing_odd", "closing_at"]]
    obs = norm[norm["fair_prob"].notna()][key + ["fair_prob", "odd", "fetched_at", "minutes_to_kickoff", "snapshot_target"]]
    m = e.merge(obs, on=key)
    m = m[m["fetched_at"] <= m["edgefut_at"]].sort_values("fetched_at").groupby(key, as_index=False).last()
    m = m[m["fetched_at"] < m["closing_at"]]
    if m.empty:
        return {"rows": [], "verdict": "INSUFFICIENT", "note": "sem observação Superbet anterior à previsão"}
    m["signal_pp"] = (m["edgefut_prob"] - m["fair_prob"]) * 100
    m["move_pp"] = (m["closing_fair"] - m["fair_prob"]) * 100
    m["clv_raw_pct"] = (m["odd"] / m["closing_odd"] - 1) * 100
    m = m[m["signal_pp"].abs() >= 1.0]  # sinais < 1 pp não têm direção
    m["agree"] = (np.sign(m["signal_pp"]) == np.sign(m["move_pp"])) & (m["move_pp"].abs() >= 0.5)
    m["disagree"] = (np.sign(m["signal_pp"]) == -np.sign(m["move_pp"])) & (m["move_pp"].abs() >= 0.5)
    m["ttk_bucket"] = m["minutes_to_kickoff"].map(_target_like_bucket)

    def block(g: pd.DataFrame) -> dict:
        cl = g["event_id"].to_numpy()
        ess = effective_sample_size(cl, rho=1.0)
        row = {"n": int(len(g)), "events": ess["clusters"], "effective_n": ess["effective_n"]}
        if ess["effective_n"] < MIN_N_METRIC:
            row["verdict"] = "INSUFFICIENT"
            return row
        moved = g[g["agree"] | g["disagree"]]
        row["direction_agreement"] = cluster_bootstrap_ci(moved["agree"].astype(float).to_numpy(), moved["event_id"].to_numpy()).to_dict() if len(moved) >= 10 else None
        row["unchanged_share"] = round(float(1 - len(moved) / len(g)), 3)
        # magnitude: movimento na direção do sinal (positivo = mercado foi para o lado do EdgeFut)
        toward = np.sign(g["signal_pp"].to_numpy()) * g["move_pp"].to_numpy()
        row["move_toward_signal_pp"] = cluster_bootstrap_ci(toward, cl, zero_test=True).to_dict()
        row["clv_raw_pct_if_taken"] = cluster_bootstrap_ci(np.where(g["signal_pp"] > 0, g["clv_raw_pct"], np.nan), cl, zero_test=True).to_dict()
        ci = row["move_toward_signal_pp"]
        row["verdict"] = "MARKET_MOVES_TOWARD_EDGEFUT" if ci["conclusive"] and ci["point"] > 0 else ("MARKET_MOVES_AGAINST_EDGEFUT" if ci["conclusive"] and ci["point"] < 0 else "NO_LEAD_DETECTED")
        return row

    rows = [{"market_category": cat, **block(g)} for cat, g in m.groupby("market_category")]
    by_bucket = [{"bucket": b, **block(g)} for b, g in m.groupby("ttk_bucket") if b]
    by_bucket.sort(key=lambda r: TARGET_ORDER.index(r["bucket"]) if r["bucket"] in TARGET_ORDER else 99)
    allb = block(m)
    return {"all": allb, "rows": rows, "by_bucket": by_bucket, "verdict": allb.get("verdict", "INSUFFICIENT"),
            "note": "YES/NO/UNCHANGED só agregado. 'Move toward' > 0 com IC excluindo zero = o mercado moveu-se depois para o lado do EdgeFut. Não é evidência de edge nem de causalidade."}


def _target_like_bucket(minutes: float | None) -> str | None:
    if minutes is None or (isinstance(minutes, float) and math.isnan(minutes)):
        return None
    for name, _, (lo, hi) in SNAPSHOT_TARGETS:
        if lo <= minutes < hi:
            return name
    return ">48h" if minutes >= 3240 else None


# ---------------------------------------------------------------------------
# §21–§24 / §28–§29 — discovery por mercado, baselines, maturidade
# ---------------------------------------------------------------------------
def market_discovery(tl: pd.DataFrame, margin: dict, clv: dict) -> list[dict]:
    """Uma linha por categoria: Raw N, eventos únicos, N efetivo, maturidade, Brier fair vs baseline simples vs EdgeFut."""
    out = []
    ov_by = {r["market_category"]: r for r in margin.get("by_market", [])}
    clv_close = {}
    for r in clv.get("rows", []):
        if r["target"] in ("T-1h", "T-30m", "T-15m", "T-5m") and r.get("clv_raw_pct"):
            clv_close.setdefault(r["market_category"], r)
    for cat in CATEGORY_ORDER:
        g = tl[tl["market_category"] == cat] if not tl.empty else pd.DataFrame()
        settled = g[g["won"].notna()] if not g.empty and "won" in g.columns else pd.DataFrame()
        row: dict = {"market_category": cat, "label": CATEGORY_LABELS[cat], "family": RESEARCH_FAMILY[cat], "selections_observed": int(len(g)), "events_observed": int(g["event_id"].nunique()) if not g.empty else 0,
                     "raw_n": int(len(settled)), "unique_events": int(settled["event_id"].nunique()) if not settled.empty else 0}
        ess = effective_sample_size(settled["event_id"].to_numpy(), rho=1.0) if not settled.empty else {"effective_n": 0}
        row["effective_n"] = ess["effective_n"]
        row["maturity"] = maturity(row["effective_n"]) if row["raw_n"] else ("COLLECTING" if row["selections_observed"] else "NO_DATA")
        row["overround_median_pct"] = ov_by.get(cat, {}).get("overround_median_pct")
        row["clv_near_close"] = clv_close.get(cat, {}).get("clv_raw_pct")
        if row["effective_n"] >= MIN_N_METRIC and settled["closing_fair"].notna().sum() >= MIN_N_METRIC:
            s = settled[settled["closing_fair"].notna()]
            y, cl = s["won"].to_numpy(float), s["event_id"].to_numpy()
            fair = s["closing_fair"].to_numpy(float)
            row["superbet_fair"] = _score_block(fair, y, cl)
            # baseline simples: taxa-base leave-one-event-out da própria categoria/mercado (sem informação de preço)
            base = _loo_base_rate(s)
            row["simple_baseline"] = _score_block(base, y, cl)
            d = cluster_bootstrap_ci((fair - y) ** 2 - (base - y) ** 2, cl, zero_test=True)
            row["fair_minus_baseline_brier"] = d.to_dict()
            e = s[s["edgefut_prob"].notna()] if "edgefut_prob" in s.columns else pd.DataFrame()
            if len(e) >= MIN_N_METRIC:
                ye, cle = e["won"].to_numpy(float), e["event_id"].to_numpy()
                row["edgefut"] = _score_block(e["edgefut_prob"].to_numpy(float), ye, cle)
                dd = cluster_bootstrap_ci((e["edgefut_prob"].to_numpy(float) - ye) ** 2 - (e["closing_fair"].to_numpy(float) - ye) ** 2, cle, zero_test=True)
                row["edgefut_minus_fair_brier"] = dd.to_dict()
                row["edgefut_vs_fair"] = "EDGEFUT" if dd.conclusive and (dd.high or 0) < 0 else ("SUPERBET" if dd.conclusive and (dd.low or 0) > 0 else "INCONCLUSIVE")
            else:
                row["edgefut_vs_fair"] = "INSUFFICIENT" if len(e) else "NO_EDGEFUT_PREDICTIONS"
            row["verdict"] = "MEASURED"
        else:
            row["verdict"] = "INSUFFICIENT" if row["raw_n"] else "COLLECTING"
        out.append(row)
    return out


def _loo_base_rate(s: pd.DataFrame) -> np.ndarray:
    """Taxa-base por mercado canónico (linha) com leave-one-event-out; fallback categoria."""
    y = s["won"].to_numpy(float)
    out = np.empty(len(s))
    grp = s["canonical_market_id"].to_numpy()
    ev = s["event_id"].to_numpy()
    for i in range(len(s)):
        mask = (grp == grp[i]) & (ev != ev[i])
        if mask.sum() >= 10:
            out[i] = y[mask].mean()
        else:
            m2 = ev != ev[i]
            out[i] = y[m2].mean() if m2.sum() else 0.5
    return np.clip(out, 0.02, 0.98)


# ---------------------------------------------------------------------------
# §25–§27 — experiment registry + BH-FDR
# ---------------------------------------------------------------------------
SEED_HYPOTHESES: list[dict] = [
    {"hypothesis_id": "H1_MATCH_RESULT_LONGSHOT_OVERPRICED", "title": "1X2: seleções a 3.00+ pagam menos do que a probabilidade justa implica (favourite-longshot)", "market_category": "MATCH_RESULT", "odds_band": "3.00-5.00", "feature": "superbet_fair_bias", "expected_direction": "-"},
    {"hypothesis_id": "H2_TOTAL_GOALS_OVER_OVERPRICED", "title": "Total de gols: OVER liquida abaixo da fair (público prefere over)", "market_category": "TOTAL_GOALS", "selection": "OVER", "feature": "superbet_fair_bias", "expected_direction": "-"},
    {"hypothesis_id": "H3_BTTS_YES_OVERPRICED", "title": "Ambas marcam: SIM liquida abaixo da fair", "market_category": "BTTS", "selection": "YES", "feature": "superbet_fair_bias", "expected_direction": "-"},
    {"hypothesis_id": "H4_CORNERS_OVER_OVERPRICED", "title": "Escanteios: OVER liquida abaixo da fair", "market_category": "CORNERS_TOTAL", "selection": "OVER", "feature": "superbet_fair_bias", "expected_direction": "-"},
    {"hypothesis_id": "H5_EDGEFUT_CORNERS_BEATS_FAIR", "title": "corners-v1 tem Brier menor que a fair da Superbet em escanteios", "market_category": "CORNERS_TOTAL", "feature": "edgefut_vs_fair", "expected_direction": "-"},
    {"hypothesis_id": "H6_EDGEFUT_CARDS_BEATS_FAIR", "title": "cards-v1 tem Brier menor que a fair da Superbet em cartões", "market_category": "CARDS_TOTAL", "feature": "edgefut_vs_fair", "expected_direction": "-"},
    {"hypothesis_id": "H7_TEAM_TOTAL_OVER_OVERPRICED", "title": "Gols por equipe: OVER liquida abaixo da fair", "market_category": "TEAM_TOTAL", "selection": "OVER", "feature": "superbet_fair_bias", "expected_direction": "-"},
    {"hypothesis_id": "H8_T24_PRICES_DRIFT_TO_CLOSE_1X2", "title": "1X2: preços em T-24h têm CLV bruto ≠ 0 vs fechamento (mercado ainda não eficiente 24 h antes)", "market_category": "MATCH_RESULT", "time_window": "T-24h", "feature": "clv_at_bucket", "expected_direction": "+"},
]


def seed_experiments(session: Session, *, now: datetime | None = None) -> int:
    """Pré-regista as hipóteses da iteração 5 (uma vez). O período de confirmação começa no registo: nada anterior conta."""
    from ..db.models import ExperimentRegistry

    now = now or datetime.utcnow()
    existing = {r.hypothesis_id for r in session.execute(select(ExperimentRegistry.hypothesis_id)).scalars()}
    created = 0
    for h in SEED_HYPOTHESES:
        if h["hypothesis_id"] in existing:
            continue
        session.add(ExperimentRegistry(
            hypothesis_id=h["hypothesis_id"], title=h["title"], market_category=h["market_category"], canonical_market_id=None, competition=None, odds_band=h.get("odds_band"), time_window=h.get("time_window"),
            feature=h["feature"], expected_direction=h["expected_direction"], discovery_start=None, discovery_end=now, confirmation_start=now, confirmation_end=None,
            min_effective_n=200, status="CONFIRMING", notes=f"selection={h.get('selection')}" if h.get("selection") else None,
        ))
        created += 1
    session.flush()
    return created


def _hyp_subset(h, tl: pd.DataFrame, clv_obs: pd.DataFrame | None, *, confirmation: bool) -> pd.DataFrame:
    if tl.empty:
        return tl
    x = tl[tl["market_category"] == h.market_category]
    if h.canonical_market_id:
        x = x[x["canonical_market_id"] == h.canonical_market_id]
    if h.competition:
        x = x[x["competition_name"] == h.competition]
    if h.odds_band:
        x = x[x["band"] == h.odds_band]
    sel = None
    if h.notes and h.notes.startswith("selection="):
        sel = h.notes.split("=", 1)[1]
    if sel:
        x = x[x["selection_id"] == sel]
    if confirmation:
        x = x[x["kickoff_utc"] >= pd.Timestamp(h.confirmation_start)]
        if h.confirmation_end is not None:
            x = x[x["kickoff_utc"] < pd.Timestamp(h.confirmation_end)]
    else:
        x = x[x["kickoff_utc"] < pd.Timestamp(h.confirmation_start)]
    return x


def _hyp_stat(h, x: pd.DataFrame, norm: pd.DataFrame) -> dict:
    """→ {n, events, effective_n, estimate, ci, p} para a feature da hipótese."""
    if h.feature == "superbet_fair_bias":
        s = x[x["won"].notna() & x["closing_fair"].notna()]
        vals = (s["won"].to_numpy(float) - s["closing_fair"].to_numpy(float)) * 100
        unit = "pp (observado − fair)"
    elif h.feature == "edgefut_vs_fair":
        s = x[x["won"].notna() & x["closing_fair"].notna() & x["edgefut_prob"].notna()] if "edgefut_prob" in x.columns else x.iloc[0:0]
        y = s["won"].to_numpy(float) if len(s) else np.array([])
        vals = (s["edgefut_prob"].to_numpy(float) - y) ** 2 - (s["closing_fair"].to_numpy(float) - y) ** 2 if len(s) else np.array([])
        unit = "ΔBrier (EdgeFut − fair)"
    elif h.feature == "clv_at_bucket":
        key = ["event_id", "canonical_market_id", "selection_id"]
        o = norm[(norm["snapshot_target"] == h.time_window)][key + ["odd"]] if not norm.empty else pd.DataFrame(columns=key + ["odd"])
        s = x.merge(o, on=key)
        vals = (s["odd"].to_numpy(float) / s["closing_odd"].to_numpy(float) - 1) * 100 if len(s) else np.array([])
        unit = "% (odd_T / closing − 1)"
    else:
        return {"n": 0, "effective_n": 0, "error": f"feature desconhecida {h.feature}"}
    cl = s["event_id"].to_numpy() if len(s) else np.array([])
    ess = effective_sample_size(cl, rho=1.0) if len(cl) else {"effective_n": 0, "clusters": 0}
    out = {"n": int(len(vals)), "events": ess["clusters"], "effective_n": ess["effective_n"], "unit": unit}
    if len(vals) >= MIN_N_METRIC and ess["clusters"] >= 10:
        ci = cluster_bootstrap_ci(vals, cl, zero_test=True)
        out["estimate"] = ci.point
        out["ci"] = ci.to_dict()
        out["p_value"] = bootstrap_p_value(vals, cl)
    return out


def evaluate_experiments(session: Session, frames: dict[str, pd.DataFrame], *, now: datetime | None = None, q: float = 0.10) -> dict:
    from ..db.models import ExperimentRegistry

    now = now or datetime.utcnow()
    tl, norm = frames["timelines"], frames["normalized"]
    hyps = session.execute(select(ExperimentRegistry)).scalars().all()
    results = []
    pvals: dict[str, float] = {}
    for h in hyps:
        conf = _hyp_stat(h, _hyp_subset(h, tl, None, confirmation=True), norm)
        disc = _hyp_stat(h, _hyp_subset(h, tl, None, confirmation=False), norm)
        r = {"hypothesis_id": h.hypothesis_id, "title": h.title, "market_category": h.market_category, "feature": h.feature, "expected_direction": h.expected_direction, "odds_band": h.odds_band, "time_window": h.time_window,
             "confirmation_start": h.confirmation_start.isoformat() if h.confirmation_start else None, "min_effective_n": h.min_effective_n, "status_before": h.status, "confirmation": conf, "discovery": {**disc, "label": "DISCOVERY (não confirmatório)"}}
        if conf.get("p_value") is not None and conf["effective_n"] >= h.min_effective_n:
            pvals[h.hypothesis_id] = conf["p_value"]
        results.append((h, r))
    fdr = benjamini_hochberg(pvals, q=q)
    out_rows = []
    for h, r in results:
        conf = r["confirmation"]
        new_status = h.status
        if h.status in ("DISCOVERY", "CANDIDATE") and h.confirmation_start <= now:
            new_status = "CONFIRMING"
        if h.hypothesis_id in fdr["adjusted"]:
            adj = fdr["adjusted"][h.hypothesis_id]
            est = conf.get("estimate") or 0.0
            direction_ok = (h.expected_direction == "+" and est > 0) or (h.expected_direction == "-" and est < 0) or h.expected_direction == "0"
            if adj <= q and direction_ok:
                new_status = "SUPPORTED"
            elif adj <= q and not direction_ok:
                new_status = "REJECTED"  # significativo na direção contrária
            else:
                new_status = "REJECTED" if conf["effective_n"] >= 2 * h.min_effective_n else "CONFIRMING"
            r["fdr_adjusted_p"] = adj
            r["survives_fdr"] = h.hypothesis_id in fdr["survivors"]
        r["status"] = new_status
        r["sample_label"] = "SMALL SAMPLE" if conf["effective_n"] < h.min_effective_n else "ADEQUATE"
        if new_status != h.status:
            hist = list((h.last_evaluation or {}).get("history", []))
            hist.append({"at": now.isoformat(), "from": h.status, "to": new_status})
            r["history"] = hist
        h.status = new_status
        h.last_evaluation = {k: v for k, v in r.items() if k not in ("history",)} | {"history": r.get("history", (h.last_evaluation or {}).get("history", []))}
        h.evaluated_at = now
        out_rows.append(r)
    session.flush()
    return {"generated_at": now, "q": q, "tested": fdr["tested"], "survivors": fdr["survivors"], "hypotheses": out_rows,
            "note": "Hipóteses pré-registadas; só o período de confirmação conta para o FDR. 'SMALL SAMPLE' nunca vira 'tendência'."}


# ---------------------------------------------------------------------------
# §35–§36 — Required Edge V2 e confiança por mercado
# ---------------------------------------------------------------------------
def required_edge_v2(row: dict) -> dict:
    """Edge mínimo (pp) para uma seleção deste mercado valer sequer observação de valor:
    margem por seleção + erro de preço do mercado (SE do viés × 1,96) + penalidade de amostra + penalidade de maturidade."""
    ov = row.get("overround_median_pct")
    n_sel = 3 if row["market_category"] in ("MATCH_RESULT", "DOUBLE_CHANCE") else 2
    margin_pp = (ov / n_sel) if ov is not None else 3.0
    bias = (row.get("superbet_fair") or {}).get("calibration_bias_pp") or {}
    se_pp = ((bias.get("high") or 0) - (bias.get("low") or 0)) / (2 * 1.96) if bias.get("high") is not None else None
    market_error_pp = round(1.96 * se_pp, 2) if se_pp is not None else 4.0
    eff = int(row.get("effective_n") or 0)
    sample_pp = round(min(6.0, 100.0 / math.sqrt(eff)), 2) if eff > 0 else 6.0
    maturity_pp = {"MATURE": 0.0, "TESTABLE": 1.0, "EARLY": 2.0, "COLLECTING": 3.0, "NO_DATA": 3.0}.get(row.get("maturity", "NO_DATA"), 3.0)
    total = round(max(settings.min_edge_pp, margin_pp + market_error_pp + sample_pp + maturity_pp), 2)
    return {"required_edge_pp": total, "components": {"margin_pp": round(margin_pp, 2), "market_error_pp": market_error_pp, "sample_pp": sample_pp, "maturity_pp": maturity_pp}, "market_error_measured": se_pp is not None}


def market_confidence(row: dict, settled_share: float | None) -> str:
    eff = int(row.get("effective_n") or 0)
    if eff < 30:
        return "INSUFFICIENT"
    if eff >= 1000 and (settled_share or 0) >= 0.9:
        return "A"
    if eff >= 300 and (settled_share or 0) >= 0.7:
        return "B"
    if eff >= 100:
        return "C"
    return "D"


__all__ = ["derived_frame", "load_normalized", "timelines", "margin_lab", "price_efficiency_by_time", "clv_v2", "line_movement", "classify_move", "lead_lag", "market_discovery",
           "seed_experiments", "evaluate_experiments", "required_edge_v2", "market_confidence", "maturity", "odds_band", "SEED_HYPOTHESES", "LEGACY_TO_CANONICAL"]
