"""Schema canônico de partida histórica (Parquet).

Todas as fontes históricas convergem para estas colunas. Colunas ausentes na
fonte ficam NULL — nunca são preenchidas com zero ou inventadas.
"""

from __future__ import annotations

import pandas as pd

COLUMNS: dict[str, str] = {
    "dataset_code": "string",  # E0, SP1, INTL, BRA...
    "competition": "string",
    "season": "string",
    "date": "datetime64[ns]",
    "home": "string",
    "away": "string",
    "hg": "Int64",
    "ag": "Int64",
    "hthg": "Int64",
    "htag": "Int64",
    "hs": "Int64",
    "as_": "Int64",
    "hst": "Int64",
    "ast": "Int64",
    "hc": "Int64",
    "ac": "Int64",
    "hy": "Int64",
    "ay": "Int64",
    "hr": "Int64",
    "ar": "Int64",
    "hf": "Int64",
    "af": "Int64",
    "referee": "string",
    "neutral": "boolean",
    "country": "string",
    "city": "string",
    "tournament": "string",
    "odds_h": "Float64",
    "odds_d": "Float64",
    "odds_a": "Float64",
    "odds_close_h": "Float64",
    "odds_close_d": "Float64",
    "odds_close_a": "Float64",
    "odds_o25": "Float64",
    "odds_u25": "Float64",
    "source": "string",
    "source_url": "string",
    "collected_at": "datetime64[ns]",
}


def conform(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for col, dtype in COLUMNS.items():
        if col in df.columns:
            s = df[col]
        else:
            s = pd.Series([pd.NA] * len(df), index=df.index)
        try:
            if dtype.startswith("datetime"):
                out[col] = pd.to_datetime(s, errors="coerce")
            elif dtype == "boolean":
                out[col] = s.map(
                    lambda v: None
                    if pd.isna(v)
                    else str(v).strip().lower() in {"true", "1", "yes", "t"}
                ).astype("boolean")
            elif dtype in ("Int64", "Float64"):
                out[col] = pd.to_numeric(s, errors="coerce").astype(dtype)
            else:
                out[col] = s.astype("string")
        except (TypeError, ValueError):
            out[col] = pd.Series([pd.NA] * len(df), index=df.index).astype(dtype if dtype != "datetime64[ns]" else "object")
    out = out.dropna(subset=["date", "home", "away"])
    return out.reset_index(drop=True)
