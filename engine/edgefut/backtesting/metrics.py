"""Métricas de calibração e desempenho."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class BetRecord:
    prob: float
    odd: float
    won: bool
    stake: float = 1.0
    edge_pp: float | None = None
    closing_odd: float | None = None
    market_key: str | None = None
    label: str | None = None


@dataclass
class Metrics:
    bets: int = 0
    wins: int = 0
    losses: int = 0
    hit_rate: float | None = None
    brier: float | None = None
    log_loss: float | None = None
    roi: float | None = None
    yield_pct: float | None = None
    profit: float = 0.0
    staked: float = 0.0
    avg_edge_pp: float | None = None
    max_drawdown: float | None = None
    clv_pct: float | None = None
    avg_odd: float | None = None
    equity_curve: list[float] = field(default_factory=list)


def compute_metrics(records: list[BetRecord]) -> Metrics:
    m = Metrics()
    if not records:
        return m
    eq = 0.0
    peak = 0.0
    dd = 0.0
    brier = 0.0
    ll = 0.0
    edges = []
    clvs = []
    curve = []
    for r in records:
        m.bets += 1
        m.staked += r.stake
        pnl = r.stake * (r.odd - 1) if r.won else -r.stake
        m.profit += pnl
        eq += pnl
        curve.append(round(eq, 2))
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        y = 1.0 if r.won else 0.0
        p = min(max(r.prob, 1e-6), 1 - 1e-6)
        brier += (p - y) ** 2
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        if r.won:
            m.wins += 1
        else:
            m.losses += 1
        if r.edge_pp is not None:
            edges.append(r.edge_pp)
        if r.closing_odd and r.closing_odd > 0:
            clvs.append((r.odd / r.closing_odd - 1) * 100)
    n = m.bets
    m.hit_rate = round(m.wins / n, 4)
    m.brier = round(brier / n, 4)
    m.log_loss = round(ll / n, 4)
    m.roi = round(m.profit / m.staked * 100, 2) if m.staked else None
    m.yield_pct = m.roi
    m.avg_edge_pp = round(sum(edges) / len(edges), 2) if edges else None
    m.max_drawdown = round(dd, 2)
    m.clv_pct = round(sum(clvs) / len(clvs), 2) if clvs else None
    m.avg_odd = round(sum(r.odd for r in records) / n, 3)
    m.profit = round(m.profit, 2)
    m.staked = round(m.staked, 2)
    m.equity_curve = curve
    return m
