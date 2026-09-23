"""Versões de modelos. Todo snapshot de previsão referencia estas strings."""

APP_VERSION = "0.1.0"

STRENGTH = "strength-v1"
ELO = "elo-v1"
POISSON = "goals-poisson-v1"
DIXON_COLES = "goals-dixon-coles-v1"
MONTE_CARLO = "mc-v1"
CORNERS = "corners-v1"
CARDS = "cards-v1"
SHOTS = "shots-v1"
CONFIDENCE = "confidence-v1"
OPPORTUNITY = "opportunity-v1"
PIPELINE = "pipeline-v1"

ALL_MODELS = {
    "strength": STRENGTH,
    "elo": ELO,
    "poisson": POISSON,
    "dixon_coles": DIXON_COLES,
    "monte_carlo": MONTE_CARLO,
    "corners": CORNERS,
    "cards": CARDS,
    "shots": SHOTS,
    "confidence": CONFIDENCE,
    "opportunity": OPPORTUNITY,
    "pipeline": PIPELINE,
}
