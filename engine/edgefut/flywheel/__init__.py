"""SUPERBET DATA FLYWHEEL (iteração 5).

Camadas:
- raw      → `raw_superbet_snapshot` (imutável, append-only; payload zlib + hash)
- processed→ `superbet_normalized_v1` (seleções canónicas com odd, implícita, fair, overround, T-x)
- derived  → relatórios/Parquet com `source_version`, `normalizer_version`, `settlement_version`

Nada aqui recomenda apostas. O objetivo é construir evidência própria da Superbet por mercado.
"""

SOURCE_VERSION = "superbet-offer-v2"
NORMALIZER_VERSION = "superbet-normalizer-v1"
SETTLEMENT_VERSION = "superbet-settlement-v1"
DATASET_VERSION = "superbet-shadow-v2"
