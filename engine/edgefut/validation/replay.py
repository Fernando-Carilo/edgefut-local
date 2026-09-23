"""Historical Replay Engine — reexecuta os modelos no passado como se fosse "hoje".

Para cada dataset, anda em janelas cronológicas (`window_days`). Em cada janela:

1. treino = partidas com `date < início_da_janela` (expanding) ou nos últimos
   `rolling_years` anos (rolling) — via `TemporalFeatureStore(frame=…)`, que levanta
   `LeakageError` se uma linha futura escapar;
2. ajusta todos os modelos com `reference_date = início_da_janela` (decaimento temporal
   calculado nesse instante, não em "agora");
3. para cada partida da janela produz probabilidades 1X2 e Over/Under 2,5 de cada modelo
   **e** de cada baseline (A mercado, B frequência ingênua, C Poisson simples da liga,
   D ELO puro, E favorito da casa);
4. compara com odds **daquele momento** (`odds_h/d/a`, `odds_o25/u25` do football-data =
   preço médio pré-jogo) e com o fechamento (`odds_close_*`) para CLV;
5. liquida com o placar real e acumula Brier / LogLoss / ECE por modelo, por dataset, por
   janela e por mercado; bootstrap pareado da diferença para os baselines.

Os pesos do ensemble em cada janela vêm SOMENTE das janelas anteriores (mesma regra do
job ensemble_weights). O replay nunca escreve em snapshots de produção: persiste um
`validation_run(kind="replay")` com agregados.

O que o replay NÃO reproduz (e o relatório diz isso): quality gate completo (confiança,
frescor de odds, qualidade de dados), clusters/correlação e mercados fora de 1X2/OU2.5 —
o football-data só traz esses preços. A simulação de apostas usa um gate simplificado:
edge ≥ min_edge_pp, EV ≥ min_ev_pct, odd em [min_odd, max_odd], 1 seleção por mercado.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db.models import ValidationRun
from ..features.strength import attack_defense, build_windows, league_averages, to_sides
from ..features.temporal import TemporalFeatureStore, assert_before
from ..models.bivariate_poisson import bivariate_output, fit_bivariate_poisson
from ..models.dixon_coles import dixon_coles_output, fit_dixon_coles
from ..models.elo import HOME_ADV_CLUB, HOME_ADV_NATIONAL, EloTable, elo_probabilities, update_elo
from ..models.ensemble import weights_from_scores
from ..models.poisson import poisson_model
from ..models.international_strength import fit_international, international_output, tournament_type
from ..models.strength_v2 import fit_strength_v2, strength_v2_output
from ..odds.implied import remove_margin
from .bootstrap import bootstrap_ci, lift_pct, model_significance, paired_bootstrap_diff, roi_stat, sample_quality

log = logging.getLogger(__name__)

MODELS = ("poisson", "dixon_coles", "bivariate_poisson", "poisson_v2", "ensemble", "ensemble_v2")
BASELINES = ("market", "naive", "simple_poisson", "elo", "favorite")
MODEL_LABELS = {
    "poisson": "Poisson (strength-v1)", "dixon_coles": "Dixon-Coles", "bivariate_poisson": "Bivariate Poisson",
    "poisson_v2": "Poisson (strength-v2)", "ensemble": "Ensemble v1 (P+DC+BP)", "ensemble_v2": "Ensemble v2 (+strength-v2)",
    "market": "A · Mercado (implícita)", "naive": "B · Frequência casa/fora", "simple_poisson": "C · Poisson simples (média da liga)",
    "elo": "D · ELO puro", "favorite": "E · Favorito da casa",
}
ENSEMBLE_V1 = ("poisson", "dixon_coles", "bivariate_poisson")
ENSEMBLE_V2 = ("poisson", "dixon_coles", "bivariate_poisson", "poisson_v2")
MARKETS = ("1X2", "OU25")
EPS = 1e-9


@dataclass
class ReplayRequest:
    datasets: list[str]
    start: datetime | None = None
    end: datetime | None = None
    window_days: int = 30
    scheme: str = "expanding"           # expanding | rolling
    rolling_years: float = 3.0
    models: tuple[str, ...] = MODELS
    half_life_days: float | None | str = "settings"  # para poisson_v2
    min_train: int = 150
    is_national: bool = False
    friendly_weight: float | None = None  # INTL: peso de amistosos no strength-v2
    bet_simulation: bool = True
    max_matches_per_dataset: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat() if self.start else None
        d["end"] = self.end.isoformat() if self.end else None
        d["models"] = list(self.models)
        return d


@dataclass
class _Pred:
    """Uma partida × um modelo."""

    dataset: str
    window: int
    date: pd.Timestamp
    model: str
    p1x2: tuple[float, float, float]
    p_over25: float | None
    outcome: int          # 0 casa 1 empate 2 fora
    over25: bool
    hg: int
    ag: int
    odds: tuple[float, float, float] | None
    odds_ou: tuple[float, float] | None
    close: tuple[float, float, float] | None
    close_ou: tuple[float, float] | None
    mid: int = -1             # índice da partida no frame (chave de pareamento)
    group: str | None = None  # temporada / tipo de torneio


@dataclass
class _Bet:
    dataset: str
    model: str
    market: str
    selection: str
    prob: float
    odd: float
    fair: float
    edge_pp: float
    ev_pct: float
    won: bool
    closing_odd: float | None
    date: pd.Timestamp


# ---------------------------------------------------------------------------
# helpers de probabilidade
# ---------------------------------------------------------------------------
def _outcome(hg: int, ag: int) -> int:
    return 0 if hg > ag else 1 if hg == ag else 2


def _p3(out) -> tuple[float, float, float] | None:
    if out is None or not out.available or out.p_home is None:
        return None
    return float(out.p_home), float(out.p_draw), float(out.p_away)  # type: ignore[arg-type]


def _ou(out) -> float | None:
    if out is None or not out.available or not out.over:
        return None
    v = out.over.get("2.5")
    return None if v is None else float(v)


def _odds3(r) -> tuple[float, float, float] | None:
    try:
        h, d, a = float(r.odds_h), float(r.odds_d), float(r.odds_a)
    except (TypeError, ValueError, AttributeError):
        return None
    if not all(np.isfinite([h, d, a])) or min(h, d, a) <= 1.0:
        return None
    return h, d, a


def _odds_ou(r, prefix: str = "odds") -> tuple[float, float] | None:
    try:
        o, u = float(getattr(r, f"{prefix}_o25")), float(getattr(r, f"{prefix}_u25"))
    except (TypeError, ValueError, AttributeError):
        return None
    if not all(np.isfinite([o, u])) or min(o, u) <= 1.0:
        return None
    return o, u


def _close3(r) -> tuple[float, float, float] | None:
    try:
        h, d, a = float(r.odds_close_h), float(r.odds_close_d), float(r.odds_close_a)
    except (TypeError, ValueError, AttributeError):
        return None
    if not all(np.isfinite([h, d, a])) or min(h, d, a) <= 1.0:
        return None
    return h, d, a


def _fair3(odds: tuple[float, float, float]) -> tuple[float, float, float]:
    fair, _ = remove_margin({"HOME": odds[0], "DRAW": odds[1], "AWAY": odds[2]})
    return fair["HOME"], fair["DRAW"], fair["AWAY"]


def _fair_ou(odds: tuple[float, float]) -> tuple[float, float]:
    fair, _ = remove_margin({"OVER": odds[0], "UNDER": odds[1]})
    return fair["OVER"], fair["UNDER"]


def _norm3(p: tuple[float, float, float]) -> tuple[float, float, float]:
    s = sum(p)
    return (p[0] / s, p[1] / s, p[2] / s) if s > 0 else (1 / 3, 1 / 3, 1 / 3)


def _multiclass_brier(p: tuple[float, float, float], o: int) -> float:
    return float(sum((p[k] - (1.0 if k == o else 0.0)) ** 2 for k in range(3)))


def _ece(probs: np.ndarray, outcomes: np.ndarray, bins: int = 10) -> float | None:
    if probs.size < 50:
        return None
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(probs, edges) - 1, 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(probs[m].mean() - outcomes[m].mean())
    return round(float(ece), 4)


# ---------------------------------------------------------------------------
# motor
# ---------------------------------------------------------------------------
class _WindowModels:
    """Ajustes de uma janela: tudo a partir do treino (date < start)."""

    def __init__(self, train: pd.DataFrame, start: pd.Timestamp, req: ReplayRequest, elo: EloTable) -> None:
        self.train = train
        self.start = start
        self.req = req
        self.la = league_averages(train)
        want = set(req.models)
        ref = start.to_pydatetime()
        self.dc = fit_dixon_coles(train, reference_date=ref) if want & {"dixon_coles", "ensemble", "ensemble_v2"} else None
        self.bp = fit_bivariate_poisson(train, reference_date=ref) if want & {"bivariate_poisson", "ensemble", "ensemble_v2"} else None
        hl = settings.strength_half_life_days if req.half_life_days == "settings" else req.half_life_days
        if want & {"poisson_v2", "ensemble_v2"}:
            if req.is_national:
                self.sv2 = fit_international(train, reference_date=ref, half_life_days=hl, friendly_weight=req.friendly_weight or 0.6)
            else:
                self.sv2 = fit_strength_v2(train, reference_date=ref, half_life_days=hl)  # type: ignore[arg-type]
        else:
            self.sv2 = None
        self.elo = elo
        self._sides_cache: dict[str, list] = {}
        # baselines B e C
        hg = pd.to_numeric(train["hg"], errors="coerce")
        ag = pd.to_numeric(train["ag"], errors="coerce")
        m = hg.notna() & ag.notna()
        n = int(m.sum())
        self.naive = ((hg[m] > ag[m]).mean(), (hg[m] == ag[m]).mean(), (hg[m] < ag[m]).mean()) if n else (0.45, 0.27, 0.28)
        self.naive_over = float(((hg[m] + ag[m]) > 2.5).mean()) if n else 0.5
        if self.la is not None:
            simple = poisson_model(la=self.la, attack_home=1.0, defense_home=1.0, attack_away=1.0, defense_away=1.0, home_adv_weight=1.0, fit_matches=n)
            self.simple3, self.simple_ou = _p3(simple), _ou(simple)
        else:
            self.simple3, self.simple_ou = None, None

    def _sides(self, team: str):
        if team not in self._sides_cache:
            t = self.train
            df = t[(t["home"] == team) | (t["away"] == team)].tail(40).iloc[::-1]
            self._sides_cache[team] = build_windows(to_sides(df, team)) if not df.empty else {}
        return self._sides_cache[team]

    def poisson_v1(self, home: str, away: str, ha_w: float):
        if self.la is None:
            return None
        ah, dh = attack_defense(self._sides(home), self.la, True)
        aa, da = attack_defense(self._sides(away), self.la, False)
        if None in (ah, dh, aa, da):
            return None
        return poisson_model(la=self.la, attack_home=ah, defense_home=dh, attack_away=aa, defense_away=da, home_adv_weight=ha_w, fit_matches=len(self.train))

    def predict(self, home: str, away: str, neutral: bool, ensemble_weights: dict[str, dict[str, float]], competition: str | None = None) -> dict[str, tuple[tuple[float, float, float], float | None]]:
        ha_w = 0.0 if neutral else 1.0
        outs: dict[str, tuple[tuple[float, float, float], float | None]] = {}
        want = set(self.req.models)
        if want & {"poisson", "ensemble", "ensemble_v2"}:
            o = self.poisson_v1(home, away, ha_w)
            p = _p3(o)
            if p:
                outs["poisson"] = (p, _ou(o))
        if self.dc is not None and home in self.dc.attack and away in self.dc.attack:
            o = dixon_coles_output(self.dc, home, away, ha_w)
            p = _p3(o)
            if p:
                outs["dixon_coles"] = (p, _ou(o))
        if self.bp is not None and home in self.bp.attack and away in self.bp.attack:
            o = bivariate_output(self.bp, home, away, ha_w)
            p = _p3(o)
            if p:
                outs["bivariate_poisson"] = (p, _ou(o))
        if self.sv2 is not None and home in self.sv2.ratings and away in self.sv2.ratings:
            o = international_output(self.sv2, home, away, ha_w, competition) if self.req.is_national else strength_v2_output(self.sv2, home, away, ha_w)
            p = _p3(o)
            if p:
                outs["poisson_v2"] = (p, _ou(o))
        for name, members in (("ensemble", ENSEMBLE_V1), ("ensemble_v2", ENSEMBLE_V2)):
            if name not in want:
                continue
            avail = [k for k in members if k in outs]
            if len(avail) < 2:
                continue
            w = ensemble_weights.get(name) or {}
            raw = {k: max(0.0, w.get(k, 1.0)) for k in avail}
            tot = sum(raw.values()) or float(len(avail))
            nw = {k: (v / tot if sum(raw.values()) > 0 else 1.0 / len(avail)) for k, v in raw.items()}
            p3 = tuple(sum(nw[k] * outs[k][0][i] for k in avail) for i in range(3))
            ous = [outs[k][1] for k in avail if outs[k][1] is not None]
            pou = sum(nw[k] * outs[k][1] for k in avail if outs[k][1] is not None) / sum(nw[k] for k in avail if outs[k][1] is not None) if ous else None  # type: ignore[operator]
            outs[name] = (_norm3(p3), pou)  # type: ignore[arg-type]
        return {k: v for k, v in outs.items() if k in want}

    def baselines(self, home: str, away: str, neutral: bool, odds: tuple[float, float, float] | None, odds_ou: tuple[float, float] | None) -> dict[str, tuple[tuple[float, float, float], float | None]]:
        out: dict[str, tuple[tuple[float, float, float], float | None]] = {}
        if odds:
            fair = _fair3(odds)
            out["market"] = (fair, _fair_ou(odds_ou)[0] if odds_ou else None)
            fav = int(np.argmin(odds))
            out["favorite"] = (tuple(1.0 - 2 * EPS if k == fav else EPS for k in range(3)), None)  # type: ignore[arg-type]
        out["naive"] = (_norm3(tuple(float(v) for v in self.naive)), self.naive_over)  # type: ignore[arg-type]
        if self.simple3:
            out["simple_poisson"] = (self.simple3, self.simple_ou)
        rh, ra = self.elo.get(home), self.elo.get(away)
        if rh is not None and ra is not None:
            ha = 0.0 if neutral else (HOME_ADV_NATIONAL if self.req.is_national else HOME_ADV_CLUB)
            out["elo"] = (elo_probabilities(rh, ra, ha, self.la.draw_rate if self.la and self.la.draw_rate else 0.26), None)
        return out


def _bets_for(pred: _Pred, model: str) -> list[_Bet]:
    """Gate simplificado do replay (ver docstring do módulo)."""
    bets: list[_Bet] = []
    if pred.odds:
        fair = _fair3(pred.odds)
        best = None
        for k, sel in enumerate(("HOME", "DRAW", "AWAY")):
            p, odd, f = pred.p1x2[k], pred.odds[k], fair[k]
            edge = (p - f) * 100
            ev = (p * odd - 1) * 100
            if edge >= settings.min_edge_pp and ev >= settings.min_ev_pct and settings.min_odd <= odd <= settings.max_odd:
                cand = _Bet(pred.dataset, model, "1X2", sel, p, odd, f, edge, ev, pred.outcome == k, pred.close[k] if pred.close else None, pred.date)
                if best is None or cand.ev_pct > best.ev_pct:
                    best = cand
        if best:
            bets.append(best)
    if pred.odds_ou and pred.p_over25 is not None:
        fo, fu = _fair_ou(pred.odds_ou)
        best = None
        for sel, p, odd, f, won in (("OVER", pred.p_over25, pred.odds_ou[0], fo, pred.over25), ("UNDER", 1 - pred.p_over25, pred.odds_ou[1], fu, not pred.over25)):
            edge = (p - f) * 100
            ev = (p * odd - 1) * 100
            if edge >= settings.min_edge_pp and ev >= settings.min_ev_pct and settings.min_odd <= odd <= settings.max_odd:
                co = (pred.close_ou[0] if sel == "OVER" else pred.close_ou[1]) if pred.close_ou else None
                cand = _Bet(pred.dataset, model, "OU25", sel, p, odd, f, edge, ev, bool(won), co, pred.date)
                if best is None or cand.ev_pct > best.ev_pct:
                    best = cand
        if best:
            bets.append(best)
    return bets


def replay_dataset(frame: pd.DataFrame, code: str, req: ReplayRequest) -> tuple[list[_Pred], list[_Bet], list[dict]]:
    frame = frame.dropna(subset=["hg", "ag"]).sort_values("date").reset_index(drop=True)
    frame["date"] = pd.to_datetime(frame["date"])
    tfs = TemporalFeatureStore(frame=frame)
    first = frame["date"].min()
    start = pd.Timestamp(req.start) if req.start else first + timedelta(days=365)
    end = pd.Timestamp(req.end) if req.end else frame["date"].max() + timedelta(days=1)
    preds: list[_Pred] = []
    bets: list[_Bet] = []
    windows: list[dict] = []
    elo = EloTable()
    elo_cursor = first - timedelta(days=1)
    cum_losses: dict[str, list[float]] = {}
    w_idx = 0
    n_done = 0
    while start < end:
        w_end = start + timedelta(days=req.window_days)
        since = None if req.scheme == "expanding" else (start - timedelta(days=int(365.25 * req.rolling_years)))
        train = tfs.competition_matches([code], as_of=start.to_pydatetime(), since=since or datetime(1900, 1, 1))
        assert_before(train, start, f"replay {code} treino da janela {start.date()}")  # guarda dupla: nada >= início da janela
        block = frame[(frame["date"] >= start) & (frame["date"] < w_end)]
        # ELO acumula TODO o passado (mesmo em rolling), como no pipeline
        new_hist = frame[(frame["date"] > elo_cursor) & (frame["date"] < start)]
        update_elo(elo, new_hist, req.is_national)
        elo_cursor = start - timedelta(microseconds=1)
        if len(train) < req.min_train or block.empty:
            start = w_end
            continue
        # pesos do ensemble: só janelas anteriores
        ens_w: dict[str, dict[str, float]] = {}
        for name, members in (("ensemble", ENSEMBLE_V1), ("ensemble_v2", ENSEMBLE_V2)):
            scores = {k: {"logloss": float(np.mean(cum_losses[k])), "n": len(cum_losses[k])} for k in members if cum_losses.get(k) and len(cum_losses[k]) >= 50}
            if len(scores) >= 2:
                ens_w[name] = weights_from_scores(scores)
        wm = _WindowModels(train, start, req, elo)
        w_idx += 1
        w_preds = 0
        for mid, r in zip(block.index, block.itertuples(index=False), strict=True):
            hg, ag = int(r.hg), int(r.ag)
            neutral = bool(getattr(r, "neutral", False)) if getattr(r, "neutral", None) is not None and not pd.isna(getattr(r, "neutral", None)) else False
            odds, odds_ou, close, close_ou = _odds3(r), _odds_ou(r), _close3(r), _odds_ou(r, "odds_close")
            if req.is_national:
                group = tournament_type(str(getattr(r, "competition", "") or getattr(r, "tournament", "") or ""))
            else:
                group = str(getattr(r, "season", None) or "") or None
            outs = wm.predict(r.home, r.away, neutral, ens_w, str(getattr(r, "competition", "") or ""))
            outs.update(wm.baselines(r.home, r.away, neutral, odds, odds_ou))
            for model, (p3, pou) in outs.items():
                pr = _Pred(code, w_idx, r.date, model, p3, pou, _outcome(hg, ag), hg + ag > 2.5, hg, ag, odds, odds_ou, close, close_ou, int(mid), group)
                preds.append(pr)
                if model in MODELS:
                    cum_losses.setdefault(model, []).append(-float(np.log(max(EPS, p3[pr.outcome]))))
                if req.bet_simulation and model in MODELS:
                    bets.extend(_bets_for(pr, model))
            w_preds += 1
            n_done += 1
        windows.append({"dataset": code, "window": w_idx, "start": start.date().isoformat(), "end": w_end.date().isoformat(), "train": len(train), "matches": w_preds, "ensemble_weights": ens_w})
        start = w_end
        if req.max_matches_per_dataset and n_done >= req.max_matches_per_dataset:
            break
    return preds, bets, windows


# ---------------------------------------------------------------------------
# agregação
# ---------------------------------------------------------------------------
def _metrics(rows: list[_Pred]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "sample_quality": sample_quality(0)}
    brier = np.array([_multiclass_brier(r.p1x2, r.outcome) for r in rows])
    ll = np.array([-np.log(max(EPS, r.p1x2[r.outcome])) for r in rows])
    probs = np.concatenate([[r.p1x2[k] for k in range(3)] for r in rows])
    outs = np.concatenate([[1.0 if r.outcome == k else 0.0 for k in range(3)] for r in rows])
    out = {
        "n": n, "sample_quality": sample_quality(n),
        "brier": bootstrap_ci(brier).to_dict(), "log_loss": bootstrap_ci(ll).to_dict(),
        "ece": _ece(probs, outs),
        "hit_rate": round(float(np.mean([int(np.argmax(r.p1x2)) == r.outcome for r in rows])), 4),
        "avg_p_home": round(float(np.mean([r.p1x2[0] for r in rows])), 4),
        "avg_p_draw": round(float(np.mean([r.p1x2[1] for r in rows])), 4),
        "extreme_share": round(float(np.mean([max(r.p1x2) > 0.9 for r in rows])), 4),
    }
    ou = [r for r in rows if r.p_over25 is not None]
    if ou:
        po = np.array([r.p_over25 for r in ou])
        oo = np.array([1.0 if r.over25 else 0.0 for r in ou])
        out["ou25"] = {"n": len(ou), "brier": bootstrap_ci((po - oo) ** 2).to_dict(), "ece": _ece(po, oo), "avg_p_over": round(float(po.mean()), 4), "observed_over_rate": round(float(oo.mean()), 4)}
    return out


def _paired_vs(rows_by_model: dict[str, list[_Pred]], model: str, baseline: str) -> dict | None:
    a, b = rows_by_model.get(model), rows_by_model.get(baseline)
    if not a or not b:
        return None
    key = lambda r: (r.dataset, r.mid)  # noqa: E731
    bm = {key(r): r for r in b}
    pairs = [(r, bm[key(r)]) for r in a if key(r) in bm]
    if len(pairs) < 30:
        return {"n": len(pairs), "significance": "INSUFFICIENT DATA"}
    ba = np.array([_multiclass_brier(x.p1x2, x.outcome) for x, _ in pairs])
    bb = np.array([_multiclass_brier(y.p1x2, y.outcome) for _, y in pairs])
    la = np.array([-np.log(max(EPS, x.p1x2[x.outcome])) for x, _ in pairs])
    lb = np.array([-np.log(max(EPS, y.p1x2[y.outcome])) for _, y in pairs])
    d_brier = paired_bootstrap_diff(ba, bb)
    d_ll = paired_bootstrap_diff(la, lb)
    # estabilidade: janelas em que o modelo venceu o baseline
    by_w: dict[tuple, list[float]] = {}
    for (x, y) in pairs:
        by_w.setdefault((x.dataset, x.window), []).append(_multiclass_brier(x.p1x2, x.outcome) - _multiclass_brier(y.p1x2, y.outcome))
    wins = sum(1 for v in by_w.values() if np.mean(v) < 0)
    return {
        "n": len(pairs),
        "brier_model": round(float(ba.mean()), 5), "brier_baseline": round(float(bb.mean()), 5),
        "lift_pct": lift_pct(float(ba.mean()), float(bb.mean())),
        "delta_brier_ci": d_brier.to_dict(), "delta_logloss_ci": d_ll.to_dict(),
        "windows_better": wins, "windows_total": len(by_w),
        "significance": model_significance(len(pairs), d_brier, windows_positive=wins, windows_total=len(by_w)),
    }


def _bet_metrics(bets: list[_Bet]) -> dict:
    n = len(bets)
    out: dict = {"n": n, "sample_quality": sample_quality(n)}
    if n == 0:
        return out
    profit = np.array([b.odd - 1 if b.won else -1.0 for b in bets])
    won = np.array([1.0 if b.won else 0.0 for b in bets])
    prob = np.array([b.prob for b in bets])
    out["roi"] = bootstrap_ci(profit, stat=roi_stat, zero_test=True).to_dict()
    out["hit_rate"] = bootstrap_ci(won).to_dict()
    out["brier"] = bootstrap_ci((prob - won) ** 2).to_dict()
    out["avg_odd"] = round(float(np.mean([b.odd for b in bets])), 3)
    out["avg_edge_pp"] = round(float(np.mean([b.edge_pp for b in bets])), 2)
    out["avg_model_prob"] = round(float(prob.mean()), 4)
    clv = [(b.odd / b.closing_odd - 1) * 100 for b in bets if b.closing_odd]
    out["clv"] = bootstrap_ci(np.array(clv), zero_test=True).to_dict() if len(clv) >= 10 else None
    out["profit_units"] = round(float(profit.sum()), 2)
    roi = out["roi"]
    out["verdict"] = "INCONCLUSIVE" if not roi.get("conclusive") else ("POSITIVE" if roi["low"] > 0 else "NEGATIVE")
    return out


def aggregate(preds: list[_Pred], bets: list[_Bet], windows: list[dict], req: ReplayRequest) -> dict:
    by_model: dict[str, list[_Pred]] = {}
    for p in preds:
        by_model.setdefault(p.model, []).append(p)
    models_present = [m for m in MODELS if m in by_model]
    baselines_present = [b for b in BASELINES if b in by_model]

    overall = {m: _metrics(rows) for m, rows in by_model.items()}
    vs_baseline = {m: {b: _paired_vs(by_model, m, b) for b in baselines_present} for m in models_present}

    # por dataset × modelo (mapa competição × modelo × mercado)
    by_dataset: dict[str, dict] = {}
    for code in sorted({p.dataset for p in preds}):
        rows_ds = [p for p in preds if p.dataset == code]
        bm = {}
        for p in rows_ds:
            bm.setdefault(p.model, []).append(p)
        by_dataset[code] = {
            "matches": len({p.mid for p in rows_ds}),
            "models": {m: _metrics(r) for m, r in bm.items()},
            "vs_market": {m: _paired_vs(bm, m, "market") for m in models_present if m in bm and "market" in bm},
            "vs_naive": {m: _paired_vs(bm, m, "naive") for m in models_present if m in bm and "naive" in bm},
        }

    # por grupo (INTL: tipo de torneio — amistoso / eliminatória / torneio; clubes: temporada)
    by_group: dict[str, dict] = {}
    for g in sorted({p.group for p in preds if p.group}):
        rows_g = [p for p in preds if p.group == g]
        bm = {}
        for p in rows_g:
            bm.setdefault(p.model, []).append(p)
        by_group[g] = {
            "matches": len({(p.dataset, p.mid) for p in rows_g}),
            "sample_quality": sample_quality(len({(p.dataset, p.mid) for p in rows_g})),
            "brier": {m: round(float(np.mean([_multiclass_brier(p.p1x2, p.outcome) for p in r])), 4) for m, r in bm.items()},
            "vs_naive": {m: _paired_vs(bm, m, "naive") for m in models_present if m in bm and "naive" in bm},
            "vs_market": {m: _paired_vs(bm, m, "market") for m in models_present if m in bm and "market" in bm},
        }

    # por janela (estabilidade)
    per_window: list[dict] = []
    for w in windows:
        rows_w = [p for p in preds if p.dataset == w["dataset"] and p.window == w["window"]]
        entry = dict(w)
        entry["brier"] = {m: round(float(np.mean([_multiclass_brier(p.p1x2, p.outcome) for p in rows_w if p.model == m])), 5) for m in by_model if any(p.model == m for p in rows_w)}
        per_window.append(entry)

    # apostas (gate simplificado)
    bet_report: dict = {}
    if bets:
        for m in models_present:
            mb = [b for b in bets if b.model == m]
            bet_report[m] = {
                "all": _bet_metrics(mb),
                "by_market": {mk: _bet_metrics([b for b in mb if b.market == mk]) for mk in MARKETS if any(b.market == mk for b in mb)},
                "by_selection": {f"{b.market}:{b.selection}": None for b in mb},
                "by_dataset": {code: _bet_metrics([b for b in mb if b.dataset == code]) for code in sorted({b.dataset for b in mb})},
            }
            bet_report[m]["by_selection"] = {k: _bet_metrics([b for b in mb if f"{b.market}:{b.selection}" == k]) for k in sorted(bet_report[m]["by_selection"])}

    # pares challenger × campeão para a regra de promoção (governance.py)
    from .governance import pairwise_from_preds

    pairwise: dict[str, dict] = {}
    for a, b in (("ensemble_v2", "ensemble"), ("poisson_v2", "ensemble"), ("poisson_v2", "poisson"), ("ensemble_v2", "dixon_coles")):
        pw = pairwise_from_preds(by_model, a, b)
        if pw:
            pairwise[f"{a}|{b}"] = pw

    matches = len({(p.dataset, p.mid) for p in preds})
    with_odds = len({(p.dataset, p.mid) for p in preds if p.odds})
    ranking = sorted(((m, overall[m]["brier"]["point"]) for m in by_model if overall[m].get("brier")), key=lambda kv: kv[1])
    return {
        "request": req.to_dict(),
        "matches": matches, "matches_with_odds": with_odds, "windows": len(windows),
        "datasets": sorted({p.dataset for p in preds}),
        "models": models_present, "baselines": baselines_present, "labels": MODEL_LABELS,
        "overall": overall, "vs_baseline": vs_baseline, "ranking_brier": ranking, "pairwise": pairwise,
        "by_dataset": by_dataset, "by_group": by_group, "per_window": per_window, "bets": bet_report,
        "limitations": [
            "Odds do football-data são médias pré-jogo (não Superbet) — proxy de mercado.",
            "Gate simplificado (edge/EV/faixa de odd); sem confiança, frescor, clusters ou correlação.",
            "Mercados limitados a 1X2 e Over/Under 2,5 — únicos com preço histórico.",
            "Ensemble usa pesos das janelas anteriores; primeiras janelas com pesos iguais.",
        ],
    }


def run_replay(req: ReplayRequest, *, session: Session | None = None, frames: dict[str, pd.DataFrame] | None = None, persist: bool = True, correlation_id: str | None = None) -> dict:
    """Executa o replay em todos os datasets pedidos. `frames` permite injetar DataFrames (testes)."""
    from ..providers.historical import get_store

    t0 = time.perf_counter()
    store = get_store() if frames is None else None
    all_preds: list[_Pred] = []
    all_bets: list[_Bet] = []
    all_windows: list[dict] = []
    for code in req.datasets:
        if frames is not None:
            frame = frames.get(code)
        else:
            frame = store.competition_matches([code], since=datetime(1900, 1, 1), before=req.end or datetime(2100, 1, 1))  # type: ignore[union-attr]
        if frame is None or frame.empty:
            log.info("replay: dataset %s vazio", code)
            continue
        p, b, w = replay_dataset(frame, code, req)
        all_preds.extend(p)
        all_bets.extend(b)
        all_windows.extend(w)
        log.info("replay %s: %d partidas·modelos, %d apostas, %d janelas", code, len(p), len(b), len(w))
    report = aggregate(all_preds, all_bets, all_windows, req)
    report["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    report["generated_at"] = datetime.utcnow().isoformat()
    if persist and session is not None:
        summary = {
            "matches": report["matches"], "matches_with_odds": report["matches_with_odds"], "datasets": report["datasets"],
            "windows": report["windows"], "scheme": req.scheme, "window_days": req.window_days,
            "ranking_brier": report["ranking_brier"],
            "vs_market": {m: (v.get("market") or {}).get("significance") for m, v in report["vs_baseline"].items()},
        }
        session.add(ValidationRun(kind="replay", correlation_id=correlation_id, request=req.to_dict(), summary=summary, detail=report, duration_ms=report["duration_ms"]))
        session.commit()
    return report


__all__ = ["ReplayRequest", "run_replay", "replay_dataset", "aggregate", "MODELS", "BASELINES", "MODEL_LABELS"]
