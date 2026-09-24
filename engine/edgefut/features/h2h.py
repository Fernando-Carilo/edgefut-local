from __future__ import annotations

import pandas as pd

from ..domain.analysis import H2HSummary, RecentMatch
from ..domain.provenance import Provenance


def summarize_h2h(df: pd.DataFrame, home: str, away: str, source: str, source_url: str | None) -> H2HSummary | None:
    if df is None or df.empty:
        return None
    hw = dw = aw = hg_total = ag_total = 0
    btts = over25 = 0
    corners: list[float] = []
    cards: list[float] = []
    recent: list[RecentMatch] = []
    for _, r in df.iterrows():
        hg, ag = int(r["hg"]), int(r["ag"])
        # orientar pelo "home" do confronto atual
        if r["home"] == home:
            gh, ga = hg, ag
        else:
            gh, ga = ag, hg
        hg_total += gh
        ag_total += ga
        if gh > ga:
            hw += 1
        elif gh == ga:
            dw += 1
        else:
            aw += 1
        if gh > 0 and ga > 0:
            btts += 1
        if gh + ga > 2.5:
            over25 += 1
        if pd.notna(r.get("hc")) and pd.notna(r.get("ac")):
            corners.append(float(r["hc"]) + float(r["ac"]))
        if pd.notna(r.get("hy")) and pd.notna(r.get("ay")):
            cards.append(float(r["hy"]) + float(r["ay"]) + float(r.get("hr") or 0) + float(r.get("ar") or 0))
        recent.append(
            RecentMatch(
                date=r["date"].to_pydatetime(),
                home=str(r["home"]),
                away=str(r["away"]),
                hg=hg,
                ag=ag,
                competition=str(r.get("competition")) if r.get("competition") is not None else None,
                neutral=bool(r["neutral"]) if pd.notna(r.get("neutral")) else None,
            )
        )
    n = len(df)
    return H2HSummary(
        matches=n,
        home_wins=hw,
        draws=dw,
        away_wins=aw,
        home_goals=hg_total,
        away_goals=ag_total,
        avg_goals=round((hg_total + ag_total) / n, 2),
        btts_pct=round(btts / n, 3),
        over25_pct=round(over25 / n, 3),
        avg_corners=round(sum(corners) / len(corners), 2) if corners else None,
        avg_cards=round(sum(cards) / len(cards), 2) if cards else None,
        recent=recent,
        provenance=Provenance(
            source=source,
            source_url=source_url,
            collected_at=df["collected_at"].max().to_pydatetime() if "collected_at" in df and df["collected_at"].notna().any() else None,
            confidence=0.9,
            sample_size=n,
            field="hg, ag, hc, ac, hy, ay",
        ),
    )
