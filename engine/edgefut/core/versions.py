"""Versões de modelos. Todo snapshot de previsão referencia estas strings."""

APP_VERSION = "0.1.0"

STRENGTH = "strength-v1"
STRENGTH_V2 = "strength-v2"
INTL_STRENGTH = "international-strength-v1"
ELO = "elo-v1"
POISSON = "goals-poisson-v1"
POISSON_V2 = "goals-poisson-v2"  # Poisson sobre ratings strength-v2 (opponent-adjusted)
DIXON_COLES = "goals-dixon-coles-v1"
BIVARIATE_POISSON = "goals-bivariate-poisson-v1"
ENSEMBLE = "ensemble-v1"
CALIBRATION = "calibration-isotonic-v1"
MONTE_CARLO = "mc-v1"
CORNERS = "corners-v1"
CARDS = "cards-v1"
SHOTS = "shots-v1"
CONFIDENCE = "confidence-v2"
OPPORTUNITY = "opportunity-v2"
PIPELINE = "pipeline-v2"

ALL_MODELS = {
    "strength": STRENGTH,
    "strength_v2": STRENGTH_V2,
    "international_strength": INTL_STRENGTH,
    "elo": ELO,
    "poisson": POISSON,
    "poisson_v2": POISSON_V2,
    "dixon_coles": DIXON_COLES,
    "bivariate_poisson": BIVARIATE_POISSON,
    "ensemble": ENSEMBLE,
    "calibration": CALIBRATION,
    "monte_carlo": MONTE_CARLO,
    "corners": CORNERS,
    "cards": CARDS,
    "shots": SHOTS,
    "confidence": CONFIDENCE,
    "opportunity": OPPORTUNITY,
    "pipeline": PIPELINE,
}
