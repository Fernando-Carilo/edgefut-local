"""strength-v2 — força ofensiva/defensiva ajustada ao adversário (opponent-adjusted).

Diferença essencial para o strength-v1: em v1 "gols marcados ÷ média da liga" trata 2 gols
contra o líder e 2 gols contra o lanterna como iguais. Aqui cada gol é comparado ao que se
*esperava* naquele confronto (força defensiva do adversário, mando, época), resolvendo o
sistema por MLE Poisson ponderado em forma fechada alternada:

    λ_home = μ · att_h · def_a · HA          λ_away = μ · att_a · def_h
    att_i  = (Σ w·gf + k) / (Σ w·μ·def_opp·ha + k)       ← k = pseudo-gols de shrinkage para 1,0
    def_i  = (Σ w·ga + k) / (Σ w·μ·att_opp·ha + k)

* `w = 0.5^(idade/half_life)` — o half-life é escolhido por walk-forward (validation/decay.py),
  não por gosto; `None` desliga o decaimento.
* Ratings por condição (home_attack, away_defense, …) são ajustados só nos jogos da condição
  e encolhidos para o rating pooled com `COND_K` pseudo-gols — o ideal do spec sem estourar
  variância em equipes com 8 jogos em casa.
* Vantagem de mandante estimada por grupo (a competição do DataFrame; opcionalmente por
  temporada/`tournament`) com fallback quando N < `HA_MIN_MATCHES`. Jogo neutro → HA = 1.
* Strength of schedule (`sos`) = força média ponderada dos adversários enfrentados
  (att_opp / def_opp; 1,0 = calendário médio). É diagnóstico e explicação — não altera λ,
  porque o ajuste ao adversário já está embutido nas equações.

Saída para o pipeline: `GoalsModelOutput` (`goals-poisson-v2`) com a mesma matriz de placares
dos outros modelos, para entrar no ensemble e no replay em pé de igualdade.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..core import versions
from ..core.config import settings
from ..domain.analysis import GoalsModelOutput
from .goals_common import markets_from_matrix, score_matrix

log = logging.getLogger(__name__)

MIN_MATCHES = 60           # abaixo disso o modelo não se pronuncia
PRIOR_K = 6.0              # pseudo-gols de shrinkage do rating pooled para 1,0
COND_K = 10.0              # pseudo-gols de shrinkage do rating por condição para o pooled
HA_MIN_MATCHES = 100       # grupo (temporada/tipo) com menos jogos usa o HA do conjunto
HA_FALLBACK = 1.15         # usado só quando nem o conjunto tem HA_MIN_MATCHES (≈ média das ligas europeias)
ITERATIONS = 40
TOL = 1e-6
_DEFAULT = object()  # sentinela: "use settings.strength_half_life_days"


@dataclass
class TeamRating:
    attack: float
    defense: float
    home_attack: float
    home_defense: float
    away_attack: float
    away_defense: float
    sos: float | None
    matches: int
    weight: float  # soma dos pesos temporais = "jogos efetivos"

    def to_dict(self) -> dict:
        return {
            "attack": round(self.attack, 3), "defense": round(self.defense, 3),
            "home_attack": round(self.home_attack, 3), "home_defense": round(self.home_defense, 3),
            "away_attack": round(self.away_attack, 3), "away_defense": round(self.away_defense, 3),
            "strength_of_schedule": round(self.sos, 3) if self.sos is not None else None,
            "matches": self.matches, "effective_matches": round(self.weight, 1),
        }


@dataclass
class StrengthV2Params:
    ratings: dict[str, TeamRating]
    mu: float                      # média de gols por equipe por jogo (ponderada)
    home_advantage: float          # multiplicador dos gols do mandante (1,0 = nenhum)
    home_advantage_by_group: dict[str, dict] = field(default_factory=dict)
    matches: int = 0
    half_life_days: float | None = None
    converged: bool = False
    iterations: int = 0
    fitted_at: datetime | None = None
    reference_date: datetime | None = None

    @property
    def teams(self) -> list[str]:
        return list(self.ratings)

    def lambdas(self, home: str, away: str, home_adv_weight: float, home_advantage: float | None = None) -> tuple[float, float]:
        """`home_adv_weight` ∈ [0,1]: 1 = mando confirmado, 0 = neutro. Interpolação geométrica
        entre rating pooled e rating por condição — em campo neutro ninguém "joga em casa".
        `home_advantage` substitui o HA da competição (ex.: HA por tipo de torneio)."""
        h, a = self.ratings[home], self.ratings[away]
        w = float(min(1.0, max(0.0, home_adv_weight)))
        att_h = h.attack ** (1 - w) * h.home_attack ** w
        def_h = h.defense ** (1 - w) * h.home_defense ** w
        att_a = a.attack ** (1 - w) * a.away_attack ** w
        def_a = a.defense ** (1 - w) * a.away_defense ** w
        ha = (home_advantage if home_advantage is not None else self.home_advantage) ** w
        return self.mu * att_h * def_a * ha, self.mu * att_a * def_h

    def group_home_advantage(self, group: str | None) -> tuple[float, str]:
        if group and group in self.home_advantage_by_group:
            g = self.home_advantage_by_group[group]
            if g["matches"] >= HA_MIN_MATCHES:
                return float(g["home_advantage"]), f"grupo {group} (N={g['matches']})"
        return self.home_advantage, f"competição (N={self.matches})"


def time_weights(dates: pd.Series, reference: datetime | pd.Timestamp, half_life_days: float | None) -> np.ndarray:
    if half_life_days is None or half_life_days <= 0:
        return np.ones(len(dates))
    age = (pd.Timestamp(reference) - pd.to_datetime(dates)).dt.days.clip(lower=0).to_numpy(dtype=float)
    return np.power(0.5, age / float(half_life_days))


def fit_strength_v2(
    df: pd.DataFrame,
    *,
    reference_date: datetime | None = None,
    half_life_days: float | None | object = _DEFAULT,
    match_weights: np.ndarray | None = None,
    group_col: str | None = None,
    prior_k: float = PRIOR_K,
) -> StrengthV2Params | None:
    """Ajusta ratings a partir de partidas com `home, away, hg, ag, date` (+`neutral` opcional).

    `match_weights` multiplica o peso temporal (ex.: amistosos internacionais valem menos).
    `group_col` (ex.: `season` ou `tournament`) produz HA por grupo com fallback."""
    if df is None or len(df) < MIN_MATCHES:
        return None
    if half_life_days is _DEFAULT:
        half_life_days = settings.strength_half_life_days
    hl: float | None = half_life_days  # type: ignore[assignment]
    d = df.dropna(subset=["hg", "ag"]).copy()
    if len(d) < MIN_MATCHES:
        return None
    ref = pd.Timestamp(reference_date or datetime.utcnow())
    d = d[pd.to_datetime(d["date"]) < ref] if reference_date is not None else d
    if len(d) < MIN_MATCHES:
        return None
    d = d.reset_index(drop=True)

    teams = sorted(set(d["home"]) | set(d["away"]))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    hi = d["home"].map(idx).to_numpy()
    ai = d["away"].map(idx).to_numpy()
    x = d["hg"].astype(float).to_numpy()
    y = d["ag"].astype(float).to_numpy()
    neutral = d["neutral"].fillna(False).astype(bool).to_numpy() if "neutral" in d else np.zeros(len(d), dtype=bool)
    w = time_weights(d["date"], ref, hl)
    if match_weights is not None:
        w = w * np.asarray(match_weights, dtype=float)
    w = np.clip(w, 1e-6, None)

    mu = float((w * (x + y)).sum() / (2.0 * w.sum()))
    att = np.ones(n)
    de = np.ones(n)
    ha = HA_FALLBACK
    converged = False
    it = 0
    for it in range(1, ITERATIONS + 1):
        ha_vec = np.where(neutral, 1.0, ha)
        # ataque
        exp_home = mu * de[ai] * ha_vec          # gols esperados do mandante por unidade de att_h
        exp_away = mu * de[hi]                   # idem visitante
        num = np.bincount(hi, weights=w * x, minlength=n) + np.bincount(ai, weights=w * y, minlength=n) + prior_k
        den = np.bincount(hi, weights=w * exp_home, minlength=n) + np.bincount(ai, weights=w * exp_away, minlength=n) + prior_k
        new_att = num / den
        new_att /= np.exp(np.log(new_att).mean())  # média geométrica 1
        # defesa (gols sofridos)
        exp_h_conc = mu * new_att[ai]            # esperados sofridos pelo mandante por unidade de def_h
        exp_a_conc = mu * new_att[hi] * ha_vec   # esperados sofridos pelo visitante
        num_d = np.bincount(hi, weights=w * y, minlength=n) + np.bincount(ai, weights=w * x, minlength=n) + prior_k
        den_d = np.bincount(hi, weights=w * exp_h_conc, minlength=n) + np.bincount(ai, weights=w * exp_a_conc, minlength=n) + prior_k
        new_de = num_d / den_d
        new_de /= np.exp(np.log(new_de).mean())
        # vantagem de mandante (só jogos não neutros)
        nn = ~neutral
        base = mu * new_att[hi] * new_de[ai]
        if nn.sum() >= HA_MIN_MATCHES:
            new_ha = float((w[nn] * x[nn]).sum() / max(1e-9, (w[nn] * base[nn]).sum()))
        else:
            new_ha = HA_FALLBACK
        # μ livre: com att/def normalizados (média geométrica 1) e HA > 1, fixar μ na média por equipe
        # forçaria o HA para baixo para fechar o total de gols — μ é reestimado a cada iteração.
        ha_vec = np.where(neutral, 1.0, new_ha)
        new_mu = float((w * (x + y)).sum() / max(1e-9, (w * (new_att[hi] * new_de[ai] * ha_vec + new_att[ai] * new_de[hi])).sum()))
        delta = max(np.abs(new_att - att).max(), np.abs(new_de - de).max(), abs(new_ha - ha), abs(new_mu - mu))
        att, de, ha, mu = new_att, new_de, new_ha, new_mu
        if delta < TOL:
            converged = True
            break
    ha = float(min(1.8, max(0.8, ha)))

    # ratings por condição, encolhidos para o pooled
    ha_vec = np.where(neutral, 1.0, ha)
    exp_hg = mu * att[hi] * de[ai] * ha_vec   # esperado do mandante (com ratings finais)
    exp_ag = mu * att[ai] * de[hi]
    ratings: dict[str, TeamRating] = {}
    opp_strength = att / de  # força relativa do adversário
    for t, i in idx.items():
        hm = (hi == i) & ~neutral
        am = (ai == i) & ~neutral
        # ataque em casa: gols reais vs esperados (com att pooled) → razão encolhida
        h_att = _cond_ratio(w[hm], x[hm], exp_hg[hm] / att[i], att[i])
        h_def = _cond_ratio(w[hm], y[hm], exp_ag[hm] / de[i], de[i])
        a_att = _cond_ratio(w[am], y[am], exp_ag[am] / att[i], att[i])
        a_def = _cond_ratio(w[am], x[am], exp_hg[am] / de[i], de[i])
        played = (hi == i) | (ai == i)
        opp = np.where(hi[played] == i, ai[played], hi[played])
        sos = float((w[played] * opp_strength[opp]).sum() / w[played].sum()) if played.any() else None
        ratings[t] = TeamRating(
            attack=float(att[i]), defense=float(de[i]), home_attack=h_att, home_defense=h_def,
            away_attack=a_att, away_defense=a_def, sos=sos, matches=int(played.sum()), weight=float(w[played].sum()),
        )

    by_group: dict[str, dict] = {}
    if group_col and group_col in d:
        for g, sub in d.groupby(group_col):
            m = sub.index.to_numpy()
            nn = m[~neutral[m]]
            if len(nn) == 0:
                continue
            g_ha = float((w[nn] * x[nn]).sum() / max(1e-9, (w[nn] * (mu * att[hi[nn]] * de[ai[nn]])).sum()))
            by_group[str(g)] = {"home_advantage": round(g_ha, 4), "matches": int(len(nn)), "used": len(nn) >= HA_MIN_MATCHES}

    return StrengthV2Params(
        ratings=ratings, mu=mu, home_advantage=ha, home_advantage_by_group=by_group, matches=len(d),
        half_life_days=None if hl is None else float(hl), converged=converged, iterations=it,
        fitted_at=datetime.utcnow(), reference_date=ref.to_pydatetime(),
    )


def _cond_ratio(w: np.ndarray, actual: np.ndarray, expected_unit: np.ndarray, pooled: float, k: float = COND_K) -> float:
    """Rating por condição: (Σ w·gols + k·pooled) / (Σ w·esperado_por_unidade + k), encolhido para o pooled."""
    if w.size == 0:
        return float(pooled)
    num = float((w * actual).sum()) + k * pooled
    den = float((w * expected_unit).sum()) + k
    return float(max(0.2, min(5.0, num / den)))


def strength_v2_output(params: StrengthV2Params | None, home: str | None, away: str | None, home_adv_weight: float, *, home_advantage: float | None = None) -> GoalsModelOutput:
    if params is None:
        return GoalsModelOutput(model_version=versions.POISSON_V2, available=False, note=f"strength-v2 não ajustado: menos de {MIN_MATCHES} jogos.")
    if not home or not away or home not in params.ratings or away not in params.ratings:
        return GoalsModelOutput(model_version=versions.POISSON_V2, available=False, fit_matches=params.matches, note="strength-v2 indisponível: equipe ausente do ajuste da competição.")
    lam, mu = params.lambdas(home, away, home_adv_weight, home_advantage)
    lam = max(0.15, min(5.0, lam))
    mu = max(0.15, min(5.0, mu))
    m = score_matrix(lam, mu)
    mk = markets_from_matrix(m)
    return GoalsModelOutput(
        model_version=versions.POISSON_V2, available=True, lambda_home=round(lam, 3), lambda_away=round(mu, 3),
        p_home=round(mk["p_home"], 4), p_draw=round(mk["p_draw"], 4), p_away=round(mk["p_away"], 4),
        dist_home=[round(v, 4) for v in mk["dist_home"]], dist_away=[round(v, 4) for v in mk["dist_away"]],
        over={k: round(v, 4) for k, v in mk["over"].items()}, under={k: round(v, 4) for k, v in mk["under"].items()},
        btts=round(mk["btts"], 4), top_scores=[(s, round(p, 4)) for s, p in mk["top_scores"][:6]],
        fit_matches=params.matches,
        note=None if params.converged else "Ajuste alternado não convergiu totalmente; resultado aproximado.",
    )


class _Cache:
    def __init__(self) -> None:
        self._fits: dict[tuple, StrengthV2Params | None] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple, builder):
        with self._lock:
            if key not in self._fits:
                self._fits[key] = builder()
            return self._fits[key]

    def clear(self) -> None:
        with self._lock:
            self._fits.clear()


sv2_cache = _Cache()

__all__ = ["fit_strength_v2", "strength_v2_output", "StrengthV2Params", "TeamRating", "sv2_cache", "time_weights", "MIN_MATCHES"]
