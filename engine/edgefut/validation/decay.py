"""Escolha do decaimento temporal (half-life) do strength-v2 por walk-forward.

Não se escolhe decay por gosto: para cada candidato roda-se o replay (só `poisson_v2`)
sobre os datasets pedidos e compara-se o Brier multiclasse 1X2 com bootstrap pareado
contra o melhor candidato. O vencedor só é "vencedor" se a diferença para o segundo for
conclusiva; caso contrário o relatório diz EMPATE e recomenda o valor mais simples
(mais longo — menos variância).
"""

from __future__ import annotations

import time
from datetime import datetime

import numpy as np
from sqlalchemy.orm import Session

from ..db.models import ValidationRun
from .bootstrap import paired_bootstrap_diff
from .replay import ReplayRequest, replay_dataset

CANDIDATES: tuple[float | None, ...] = (30.0, 60.0, 90.0, 180.0, 365.0, 730.0, None)


def select_half_life(
    datasets: list[str],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    window_days: int = 30,
    candidates: tuple[float | None, ...] = CANDIDATES,
    session: Session | None = None,
    frames: dict | None = None,
    persist: bool = True,
) -> dict:
    from ..providers.historical import get_store

    t0 = time.perf_counter()
    store = get_store() if frames is None else None
    loaded = {}
    for code in datasets:
        fr = frames.get(code) if frames is not None else store.competition_matches([code], since=datetime(1900, 1, 1), before=end or datetime(2100, 1, 1))  # type: ignore[union-attr]
        if fr is not None and not fr.empty:
            loaded[code] = fr

    per_hl: dict[str, dict] = {}
    briers: dict[str, dict[tuple, float]] = {}
    for hl in candidates:
        key = "none" if hl is None else f"{hl:g}"
        req = ReplayRequest(datasets=list(loaded), start=start, end=end, window_days=window_days, models=("poisson_v2",), half_life_days=hl, bet_simulation=False)
        rows: dict[tuple, float] = {}
        n_windows = 0
        for code, fr in loaded.items():
            preds, _, windows = replay_dataset(fr, code, req)
            n_windows += len(windows)
            for p in preds:
                if p.model == "poisson_v2":
                    rows[(code, p.mid)] = float(sum((p.p1x2[k] - (1.0 if k == p.outcome else 0.0)) ** 2 for k in range(3)))
        briers[key] = rows
        per_hl[key] = {"half_life_days": hl, "n": len(rows), "brier": round(float(np.mean(list(rows.values()))), 5) if rows else None, "windows": n_windows}

    valid = {k: v for k, v in per_hl.items() if v["brier"] is not None}
    if not valid:
        return {"ok": False, "error": "sem partidas avaliáveis"}
    ranking = sorted(valid, key=lambda k: valid[k]["brier"])
    best = ranking[0]
    comparisons = {}
    common = set(briers[best])
    for k in ranking[1:]:
        common_k = common & set(briers[k])
        keys = sorted(common_k)
        if len(keys) < 30:
            comparisons[k] = None
            continue
        a = np.array([briers[best][x] for x in keys])
        b = np.array([briers[k][x] for x in keys])
        comparisons[k] = paired_bootstrap_diff(a, b).to_dict()  # negativo = best melhor
    runner_up = ranking[1] if len(ranking) > 1 else None
    tie = bool(runner_up and comparisons.get(runner_up) and not comparisons[runner_up]["conclusive"])
    # em empate, preferir o candidato mais simples (half-life mais longo / sem decaimento) entre os estatisticamente indistinguíveis do melhor
    indistinguishable = [best] + [k for k in ranking[1:] if comparisons.get(k) and not comparisons[k]["conclusive"]]
    def _len(k: str) -> float:
        return float("inf") if k == "none" else float(k)
    recommended = max(indistinguishable, key=_len) if tie else best
    report = {
        "ok": True,
        "datasets": list(loaded), "start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
        "window_days": window_days, "candidates": per_hl, "ranking": ranking, "best": best,
        "best_vs_others_delta_brier": comparisons, "tie_with_runner_up": tie,
        "recommended": recommended,
        "recommended_half_life_days": per_hl[recommended]["half_life_days"],
        "note": (
            "Melhor candidato estatisticamente indistinguível do seguinte: escolhido o mais simples entre os equivalentes."
            if tie else "Melhor candidato com vantagem conclusiva (IC 95% da diferença de Brier não cruza zero)."
        ),
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }
    if persist and session is not None:
        session.add(ValidationRun(kind="decay", request={"datasets": datasets, "window_days": window_days}, summary={k: report[k] for k in ("best", "recommended", "recommended_half_life_days", "tie_with_runner_up")}, detail=report, duration_ms=report["duration_ms"]))
        session.commit()
    return report


__all__ = ["select_half_life", "CANDIDATES"]
