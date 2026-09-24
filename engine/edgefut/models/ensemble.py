"""Model Comparison + ModelEnsemble (ensemble-v1).

* `compare(...)` coloca Poisson, Dixon-Coles e Bivariate Poisson lado a lado (1X2,
  Over 2.5, BTTS, λ) e mede a divergência máxima no 1X2 (pp).
* `consensus(...)` combina as MATRIZES de placar dos modelos disponíveis com pesos
  vindos do walk-forward (`ensemble_weights` em `setting`), por competição quando a
  amostra é suficiente, senão GLOBAL; sem pesos calculados → pesos iguais.
* Os pesos nunca são ajustados "à mão" por evento; o job `ensemble_weights` recalcula.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core import versions
from ..domain.analysis import GoalsModelOutput
from .bivariate_poisson import bivariate_matrix_for
from .goals_common import markets_from_matrix, score_matrix

log = logging.getLogger(__name__)

MODEL_KEYS = ("poisson", "dixon_coles", "bivariate_poisson", "poisson_v2")
MODEL_LABELS = {"poisson": "Poisson", "dixon_coles": "Dixon-Coles", "bivariate_poisson": "Bivariate Poisson", "poisson_v2": "Poisson · strength-v2", "consensus": "Consenso"}
# Membros do consenso por campeão (governança: validation/governance.py decide qual está ativo)
CHAMPION_MEMBERS = {"ensemble": ("poisson", "dixon_coles", "bivariate_poisson"), "ensemble_v2": ("poisson", "dixon_coles", "bivariate_poisson", "poisson_v2")}
WEIGHTS_KEY = "ensemble_weights"
MIN_WEIGHT_SAMPLE = 200  # partidas avaliadas no walk-forward para pesos por competição
LOW_WEIGHT_THRESHOLD = 0.10  # abaixo disso o modelo não participa do veto MODEL_DISAGREEMENT (segue visível)


class ModelRow(BaseModel):
    key: str
    label: str
    model_version: str
    available: bool
    lambda_home: float | None = None
    lambda_away: float | None = None
    p_home: float | None = None
    p_draw: float | None = None
    p_away: float | None = None
    over25: float | None = None
    btts: float | None = None
    weight: float | None = None  # peso no consenso (None se indisponível)
    note: str | None = None


class ModelComparison(BaseModel):
    model_version: str = versions.ENSEMBLE
    rows: list[ModelRow]
    consensus: ModelRow | None
    max_disagreement_pp: float | None  # maior diferença no 1X2 entre modelos relevantes (peso >= LOW_WEIGHT_THRESHOLD)
    disagreement_pairs: dict[str, float] = Field(default_factory=dict)  # todos os pares disponíveis, inclusive baixo peso
    disagreement_scope: list[str] = Field(default_factory=list)  # modelos considerados no veto
    excluded_low_weight: list[str] = Field(default_factory=list)  # modelos visíveis mas fora do veto
    weights_source: str  # "walk-forward:<grupo>" | "equal"
    weights_group: str | None = None
    weights_sample: int | None = None
    note: str | None = None
    champion: str = "ensemble"  # ensemble | ensemble_v2
    challengers: list[str] = Field(default_factory=list)  # modelos visíveis fora do consenso


@dataclass
class WeightSet:
    weights: dict[str, float]
    source: str
    group: str | None
    sample: int | None


def _row(key: str, out: GoalsModelOutput, weight: float | None) -> ModelRow:
    return ModelRow(
        key=key, label=MODEL_LABELS[key], model_version=out.model_version, available=out.available,
        lambda_home=out.lambda_home, lambda_away=out.lambda_away, p_home=out.p_home, p_draw=out.p_draw, p_away=out.p_away,
        over25=out.over.get("2.5") if out.over else None, btts=out.btts, weight=weight, note=out.note,
    )


def _matrix(key: str, out: GoalsModelOutput) -> np.ndarray:
    if key == "bivariate_poisson":
        return bivariate_matrix_for(out)
    return score_matrix(out.lambda_home or 1.0, out.lambda_away or 1.0, out.rho if key == "dixon_coles" else None)


def load_weights(session: Session | None, dataset_code: str | None) -> WeightSet:
    """Pesos do walk-forward por competição (se N suficiente) ou GLOBAL; senão iguais."""
    if session is not None:
        from ..db.models import Setting

        row = session.get(Setting, WEIGHTS_KEY)
        data = (row.value or {}) if row else {}
        for group in ([dataset_code] if dataset_code else []) + ["GLOBAL"]:
            g = data.get(group)
            if g and g.get("weights") and (g.get("sample") or 0) >= (MIN_WEIGHT_SAMPLE if group != "GLOBAL" else 1):
                return WeightSet(weights={k: float(v) for k, v in g["weights"].items()}, source=f"walk-forward:{group}", group=group, sample=int(g.get("sample") or 0))
    return WeightSet(weights={k: 1.0 for k in MODEL_KEYS}, source="equal", group=None, sample=None)


def compare(
    *,
    poisson: GoalsModelOutput,
    dixon_coles: GoalsModelOutput,
    bivariate: GoalsModelOutput,
    weights: WeightSet,
    poisson_v2: GoalsModelOutput | None = None,
    champion: str = "ensemble",
) -> tuple[ModelComparison, GoalsModelOutput | None, np.ndarray | None]:
    outs = {"poisson": poisson, "dixon_coles": dixon_coles, "bivariate_poisson": bivariate}
    if poisson_v2 is not None:
        outs["poisson_v2"] = poisson_v2
    members = CHAMPION_MEMBERS.get(champion, CHAMPION_MEMBERS["ensemble"])
    challengers = [k for k in outs if k not in members]
    all_avail = {k: o for k, o in outs.items() if o.available and o.lambda_home is not None}
    avail = {k: o for k, o in all_avail.items() if k in members}
    raw_w = {k: max(0.0, weights.weights.get(k, 0.0)) for k in avail}
    total = sum(raw_w.values())
    norm_w = {k: (v / total if total > 0 else 1.0 / len(avail)) for k, v in raw_w.items()} if avail else {}
    rows = [_row(k, o, round(norm_w[k], 3) if k in norm_w else None) for k, o in outs.items()]
    for r in rows:
        if r.key in challengers and r.available:
            r.note = (r.note + " · " if r.note else "") + "challenger: visível, fora do consenso até a regra de promoção."

    pairs: dict[str, float] = {}
    keys = list(avail)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = avail[keys[i]], avail[keys[j]]
            d = 100 * max(abs(a.p_home - b.p_home), abs(a.p_draw - b.p_draw), abs(a.p_away - b.p_away))  # type: ignore[operator]
            pairs[f"{keys[i]}|{keys[j]}"] = round(d, 2)

    # Veto por divergência: só entre modelos relevantes. Um modelo que o walk-forward já mostrou ser
    # claramente pior (peso < LOW_WEIGHT_THRESHOLD) continua visível na tabela, mas não veta sozinho.
    # Com pesos iguais (sem evidência), todos contam.
    if weights.source != "equal" and len(avail) >= 2:
        scope = [k for k in keys if norm_w.get(k, 0.0) >= LOW_WEIGHT_THRESHOLD] or keys
    else:
        scope = keys
    excluded = [k for k in keys if k not in scope]
    scoped = {p: d for p, d in pairs.items() if all(part in scope for part in p.split("|"))}
    max_dis = max(scoped.values()) if scoped else (0.0 if len(scope) == 1 and len(keys) > 1 else None)

    if not avail:
        return ModelComparison(rows=rows, consensus=None, max_disagreement_pp=None, disagreement_pairs={}, weights_source=weights.source, weights_group=weights.group, weights_sample=weights.sample, note="Nenhum modelo de gols disponível.", champion=champion, challengers=challengers), None, None

    matrix = sum(norm_w[k] * _matrix(k, o) for k, o in avail.items())
    matrix = matrix / matrix.sum()
    mk = markets_from_matrix(matrix)
    lam = sum(norm_w[k] * (o.lambda_home or 0) for k, o in avail.items())
    mu = sum(norm_w[k] * (o.lambda_away or 0) for k, o in avail.items())
    cons = GoalsModelOutput(
        model_version=versions.ENSEMBLE, available=True, lambda_home=round(lam, 3), lambda_away=round(mu, 3), rho=None,
        p_home=round(mk["p_home"], 4), p_draw=round(mk["p_draw"], 4), p_away=round(mk["p_away"], 4),
        dist_home=[round(v, 4) for v in mk["dist_home"]], dist_away=[round(v, 4) for v in mk["dist_away"]],
        over={k: round(v, 4) for k, v in mk["over"].items()}, under={k: round(v, 4) for k, v in mk["under"].items()},
        btts=round(mk["btts"], 4), top_scores=[(s, round(p, 4)) for s, p in mk["top_scores"][:6]],
        fit_matches=max(o.fit_matches for o in avail.values()),
        note=f"Consenso de {len(avail)} modelo(s); pesos {weights.source}.",
    )
    cons_row = _row("consensus", cons, 1.0)
    cons_row.label = MODEL_LABELS["consensus"]
    note = None
    if excluded:
        note = f"{', '.join(MODEL_LABELS[k] for k in excluded)} com peso < {LOW_WEIGHT_THRESHOLD:.0%} no walk-forward ({weights.group}): exibido, mas fora do veto de divergência."
    return ModelComparison(
        rows=rows, consensus=cons_row, max_disagreement_pp=max_dis, disagreement_pairs=pairs,
        disagreement_scope=scope, excluded_low_weight=excluded,
        weights_source=weights.source, weights_group=weights.group, weights_sample=weights.sample, note=note,
        champion=champion, challengers=challengers,
    ), cons, matrix


# ---------------------------------------------------------------------------
# pesos por walk-forward (job)
# ---------------------------------------------------------------------------


def _logloss_1x2(p: tuple[float, float, float], hg: int, ag: int) -> float:
    idx = 0 if hg > ag else 1 if hg == ag else 2
    return -float(np.log(max(1e-9, p[idx])))


def _strength_poisson_probs(train: pd.DataFrame, la, home: str, away: str) -> tuple[float, float, float] | None:
    """Mesmo caminho do pipeline: janelas 5/10/20 → ataque/defesa → Poisson (strength-v1 + goals-poisson-v1)."""
    from ..features.strength import attack_defense, build_windows, to_sides
    from .poisson import poisson_model

    hdf = train[(train["home"] == home) | (train["away"] == home)].tail(40).iloc[::-1]
    adf = train[(train["home"] == away) | (train["away"] == away)].tail(40).iloc[::-1]
    if hdf.empty or adf.empty:
        return None
    ah, dh = attack_defense(build_windows(to_sides(hdf, home)), la, True)
    aa, da = attack_defense(build_windows(to_sides(adf, away)), la, False)
    out = poisson_model(la=la, attack_home=ah, defense_home=dh, attack_away=aa, defense_away=da, home_adv_weight=1.0, fit_matches=len(train))
    if not out.available:
        return None
    return out.p_home, out.p_draw, out.p_away  # type: ignore[return-value]


def walk_forward_scores(df: pd.DataFrame, since: datetime, until: datetime, step_days: int = 60) -> dict[str, dict]:
    """Log loss médio no 1X2 de cada modelo, reajustando a cada `step_days` sem vazamento.

    Cada modelo é avaliado exatamente como roda no pipeline: Poisson por força
    (janelas de forma), Dixon-Coles e Bivariate Poisson por MLE na janela de treino.
    """
    from ..features.strength import league_averages
    from .bivariate_poisson import bivariate_output, fit_bivariate_poisson
    from .dixon_coles import MIN_MATCHES, dixon_coles_output, fit_dixon_coles
    from .strength_v2 import fit_strength_v2, strength_v2_output

    df = df.sort_values("date").reset_index(drop=True)
    losses: dict[str, list[float]] = {k: [] for k in MODEL_KEYS}
    start = pd.Timestamp(since)
    while start < pd.Timestamp(until):
        end = start + timedelta(days=step_days)
        train = df[df["date"] < start]
        block = df[(df["date"] >= start) & (df["date"] < end)]
        if len(train) < MIN_MATCHES or block.empty:
            start = end
            continue
        dc = fit_dixon_coles(train, reference_date=start.to_pydatetime())
        bp = fit_bivariate_poisson(train, reference_date=start.to_pydatetime())
        sv2 = fit_strength_v2(train, reference_date=start.to_pydatetime())
        la = league_averages(train)
        for r in block.itertuples():
            h, a, hg, ag = r.home, r.away, int(r.hg), int(r.ag)
            if dc is not None and h in dc.attack and a in dc.attack:
                o = dixon_coles_output(dc, h, a, 1.0)
                losses["dixon_coles"].append(_logloss_1x2((o.p_home, o.p_draw, o.p_away), hg, ag))  # type: ignore[arg-type]
            if bp is not None and h in bp.attack and a in bp.attack:
                o = bivariate_output(bp, h, a, 1.0)
                losses["bivariate_poisson"].append(_logloss_1x2((o.p_home, o.p_draw, o.p_away), hg, ag))  # type: ignore[arg-type]
            if sv2 is not None and h in sv2.ratings and a in sv2.ratings:
                o = strength_v2_output(sv2, h, a, 1.0)
                losses["poisson_v2"].append(_logloss_1x2((o.p_home, o.p_draw, o.p_away), hg, ag))  # type: ignore[arg-type]
            if la is not None:
                pp = _strength_poisson_probs(train, la, h, a)
                if pp is not None:
                    losses["poisson"].append(_logloss_1x2(pp, hg, ag))
        start = end
    out = {}
    for k, ls in losses.items():
        if ls:
            out[k] = {"logloss": round(float(np.mean(ls)), 5), "n": len(ls)}
    return out


def weights_from_scores(scores: dict[str, dict]) -> dict[str, float]:
    """Peso ∝ exp(−κ·(logloss − min)); modelos piores recebem menos, nunca zero."""
    if not scores:
        return {}
    best = min(s["logloss"] for s in scores.values())
    kappa = 25.0
    raw = {k: float(np.exp(-kappa * (s["logloss"] - best))) for k, s in scores.items()}
    tot = sum(raw.values())
    return {k: round(v / tot, 4) for k, v in raw.items()}


def refresh_ensemble_weights(session: Session, months: int = 12, step_days: int = 60) -> dict:
    from ..db.models import Setting
    from ..providers.historical import get_store

    store = get_store()
    codes = [str(c) for c in store.datasets()["dataset_code"].tolist()]
    until = datetime.utcnow()
    since = until - timedelta(days=30 * months)
    result: dict[str, dict] = {}
    all_scores: dict[str, list[tuple[float, int]]] = {k: [] for k in MODEL_KEYS}
    for code in codes:
        if code == "INTL":
            df = store.competition_matches([code], since=since - timedelta(days=3 * 365), before=until)
        else:
            df = store.competition_matches([code], since=since - timedelta(days=2 * 365), before=until)
        if df.empty:
            continue
        scores = walk_forward_scores(df, since, until, step_days)
        n = min((s["n"] for s in scores.values()), default=0)
        if scores:
            result[code] = {"scores": scores, "weights": weights_from_scores(scores), "sample": n, "computed_at": until.isoformat()}
            for k, s in scores.items():
                all_scores[k].append((s["logloss"], s["n"]))
    glob = {}
    for k, pairs in all_scores.items():
        n = sum(n for _, n in pairs)
        if n:
            glob[k] = {"logloss": round(sum(ll * n for ll, n in pairs) / n, 5), "n": n}
    if glob:
        result["GLOBAL"] = {"scores": glob, "weights": weights_from_scores(glob), "sample": min(s["n"] for s in glob.values()), "computed_at": until.isoformat()}
    row = session.get(Setting, WEIGHTS_KEY)
    if row is None:
        session.add(Setting(key=WEIGHTS_KEY, value=result))
    else:
        row.value = result
        row.updated_at = datetime.utcnow()
    session.commit()
    return {"ok": True, "groups": len(result), "global": result.get("GLOBAL", {}).get("weights")}
