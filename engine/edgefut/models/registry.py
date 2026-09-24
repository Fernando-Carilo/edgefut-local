"""model_registry — toda versão de modelo usada em previsões fica registrada.

`sync_registry` roda no boot: garante uma linha por (model_id, version) com features,
parâmetros e janela de treino declarados; versões que deixaram de existir em
`versions.ALL_MODELS` são marcadas `deprecated`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import versions
from ..core.config import settings
from ..db.models import ModelRegistry

SPECS: dict[str, dict] = {
    "strength": {"features": ["janelas 5/10/20", "splits casa/fora", "ataque/defesa relativos"], "parameters": {"recency_weights": "linear"}},
    "strength_v2": {"features": ["ataque/defesa ajustados ao adversário (MLE Poisson ponderado)", "ratings casa/fora encolhidos", "strength of schedule", "HA por competição/temporada"], "parameters": {"half_life_days": settings.strength_half_life_days, "prior_k": 6, "cond_k": 10, "ha_min_matches": 100, "decay_selection": "walk-forward validation/decay.py"}},
    "international_strength": {"features": ["strength-v2 sobre INTL", "amistoso com peso reduzido", "HA por tipo de torneio", "campo neutro → HA=1", "ELO desde 2000"], "parameters": {"friendly_weight": 0.6, "min_matches": 60}},
    "elo": {"features": ["resultado", "margem de gols", "mando ponderado"], "parameters": {"k": "adaptativo", "margin_multiplier": True}, "training_window": {"since": "2000-01-01 (seleções)", "note": "3 anos para ligas"}},
    "poisson": {"features": ["média da liga", "ataque × defesa", "vantagem de mando"], "parameters": {"lambda_bounds": [0.15, 5.0]}},
    "poisson_v2": {"features": ["λ = μ·att·def·HA com ratings strength-v2"], "parameters": {"lambda_bounds": [0.15, 5.0]}},
    "dixon_coles": {"features": ["ataque", "defesa", "γ mando", "ρ placares baixos"], "parameters": {"xi_per_day": 0.0018, "min_matches": 150, "optimizer": "L-BFGS-B"}, "training_window": {"note": "todas as partidas < as_of no dataset (peso exp(-ξ·dias))"}},
    "bivariate_poisson": {"features": ["ataque", "defesa", "γ mando", "λ3 covariância"], "parameters": {"xi_per_day": 0.0018, "min_matches": 150, "lambda3": "MLE 1-D com marginais fixas"}},
    "ensemble": {"features": ["matrizes de placar dos modelos disponíveis"], "parameters": {"weights": "walk-forward log loss (exp(-25·Δ))", "min_weight_sample": 200}},
    "calibration": {"features": ["probabilidade crua", "resultado liquidado"], "parameters": {"method": "isotonic (PAV)", "min_n": 300}},
    "monte_carlo": {"features": ["matriz de placares"], "parameters": {"simulations": [10000, 25000, 50000, 100000], "seed": "eventId"}},
    "corners": {"features": ["escanteios por janela", "ELO diff"], "parameters": {"distribution": "negative binomial", "lines": [6.5, 7.5, 8.5, 9.5, 10.5]}},
    "cards": {"features": ["cartões por janela"], "parameters": {"distribution": "negative binomial", "lines": [2.5, 3.5, 4.5, 5.5]}},
    "shots": {"features": ["finalizações", "no alvo", "conversão"], "parameters": {}},
    "confidence": {"features": ["qualidade", "amostra", "recência", "consistência", "calibração", "divergência", "frescor", "mando", "escalações"], "parameters": {"grades": {"A": 80, "B": 65, "C": 50}, "groups": ["DATA_QUALITY", "MODEL_AGREEMENT", "CALIBRATION", "HISTORICAL_SAMPLE", "FRESHNESS", "CONTEXT"]}},
    "opportunity": {"features": ["model confidence", "data quality", "calibration quality", "edge", "EV", "odds freshness", "model agreement", "historical performance", "sample size"], "parameters": {"scale": "0-100", "configurable": True}},
    "pipeline": {"features": ["coleta → … → explicação"], "parameters": {"min_edge_pp": settings.min_edge_pp, "min_ev_pct": settings.min_ev_pct, "odd_range": [settings.min_odd, settings.max_odd]}},
}


# Champion/Challenger: a produção decide com o campeão; challengers aparecem na comparação
# mas não entram no consenso até a regra de promoção (validation/governance.py) ser cumprida.
DEFAULT_ROLES: dict[str, str] = {
    "ensemble": "champion", "poisson": "champion", "dixon_coles": "champion", "bivariate_poisson": "champion",
    "poisson_v2": "challenger", "strength_v2": "challenger", "international_strength": "challenger",
    "elo": "baseline",
}


def sync_registry(session: Session) -> dict:
    existing = {(r.model_id, r.version): r for r in session.execute(select(ModelRegistry)).scalars()}
    created = 0
    for model_id, version in versions.ALL_MODELS.items():
        row = existing.get((model_id, version))
        spec = SPECS.get(model_id, {})
        if row is None:
            row = ModelRegistry(
                model_id=model_id, version=version, features=spec.get("features"), parameters=spec.get("parameters"),
                training_window=spec.get("training_window"), active=True, deprecated=False, role=DEFAULT_ROLES.get(model_id, "none"),
            )
            session.add(row)
            created += 1
        else:
            row.active = True
            row.deprecated = False
        if not row.role:
            row.role = DEFAULT_ROLES.get(model_id, "none")
    for (model_id, version), row in existing.items():
        if versions.ALL_MODELS.get(model_id) != version:
            row.active = False
            row.deprecated = True
            row.notes = (row.notes or "") + f" substituída em {datetime.utcnow():%Y-%m-%d}"
    session.commit()
    return {"ok": True, "created": created, "total": len(versions.ALL_MODELS)}


def registry_rows(session: Session) -> list[dict]:
    rows = session.execute(select(ModelRegistry).order_by(ModelRegistry.model_id, ModelRegistry.created_at)).scalars().all()
    return [
        {
            "id": r.id, "model_id": r.model_id, "version": r.version, "created_at": r.created_at, "training_window": r.training_window,
            "features": r.features, "parameters": r.parameters, "metrics": r.metrics, "active": r.active, "deprecated": r.deprecated, "notes": r.notes, "role": r.role,
        }
        for r in rows
    ]
