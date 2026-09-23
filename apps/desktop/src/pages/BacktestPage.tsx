import { useMutation, useQuery } from "@tanstack/react-query";
import type { BacktestMetrics, BacktestRequest } from "@edgefut/contracts";
import { num, pct, signedPct } from "@edgefut/shared";
import clsx from "clsx";
import { FlaskConical } from "lucide-react";
import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip as RTooltip, XAxis, YAxis } from "recharts";

import { Card, Empty, ErrorBox, KV, Loading, PageHeader, SectionTitle, Stat } from "@/components/ui";
import { api } from "@/lib/api";

const defaultReq: BacktestRequest = {
  dataset_code: "E0",
  market: "1X2",
  model: "dixon_coles",
  min_odd: 1.3,
  max_odd: 5.0,
  min_edge_pp: 3,
  min_team_games: 10,
  since: "2025-08-01",
  until: null,
  refit_every_days: 30,
  use_closing_odds: true,
  stake: 1,
};

export function BacktestPage() {
  const [req, setReq] = useState<BacktestRequest>(defaultReq);
  const sources = useQuery({ queryKey: ["sources"], queryFn: api.sources, staleTime: 60000 });
  const run = useMutation({ mutationFn: (r: BacktestRequest) => api.backtest(r) });
  const set = <K extends keyof BacktestRequest>(k: K, v: BacktestRequest[K]) => setReq((r) => ({ ...r, [k]: v }));
  const res = run.data;
  const datasets = (sources.data?.datasets ?? []).filter((d) => d.code !== "INTL" && d.code !== "ERRO");

  return (
    <div className="space-y-5">
      <PageHeader title="Backtest Lab" subtitle="Walk-forward honesto: o modelo é reajustado periodicamente só com jogos anteriores e comparado às odds de fechamento públicas." />
      <Card>
        <div className="grid gap-3 md:grid-cols-4 lg:grid-cols-8">
          <Field label="Competição">
            <select className="input" value={req.dataset_code} onChange={(e) => set("dataset_code", e.target.value)}>
              {datasets.length === 0 && <option value={req.dataset_code}>{req.dataset_code}</option>}
              {datasets.map((d) => (
                <option key={d.code} value={d.code}>
                  {d.label} ({d.code})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Mercado">
            <select className="input" value={req.market} onChange={(e) => set("market", e.target.value as BacktestRequest["market"])}>
              <option value="1X2">1X2</option>
              <option value="TOTAL_GOALS_2.5">Over/Under 2.5</option>
              <option value="ALL">Ambos</option>
            </select>
          </Field>
          <Field label="Modelo">
            <select className="input" value={req.model} onChange={(e) => set("model", e.target.value)}>
              <option value="dixon_coles">Dixon-Coles</option>
              <option value="poisson">Poisson</option>
            </select>
          </Field>
          <Field label="Odd mín">
            <input className="input" type="number" step="0.05" value={req.min_odd} onChange={(e) => set("min_odd", Number(e.target.value))} />
          </Field>
          <Field label="Odd máx">
            <input className="input" type="number" step="0.05" value={req.max_odd} onChange={(e) => set("max_odd", Number(e.target.value))} />
          </Field>
          <Field label="Edge mín (pp)">
            <input className="input" type="number" step="0.5" value={req.min_edge_pp} onChange={(e) => set("min_edge_pp", Number(e.target.value))} />
          </Field>
          <Field label="Confiança (jogos mín.)">
            <input className="input" type="number" value={req.min_team_games} onChange={(e) => set("min_team_games", Number(e.target.value))} />
          </Field>
          <Field label="Reajuste (dias)">
            <input className="input" type="number" value={req.refit_every_days} onChange={(e) => set("refit_every_days", Number(e.target.value))} />
          </Field>
          <Field label="Desde">
            <input className="input" type="date" value={req.since ?? ""} onChange={(e) => set("since", e.target.value || null)} />
          </Field>
          <Field label="Até">
            <input className="input" type="date" value={req.until ?? ""} onChange={(e) => set("until", e.target.value || null)} />
          </Field>
          <label className="flex items-end gap-2 pb-2 text-sm text-ink-2">
            <input type="checkbox" checked={req.use_closing_odds} onChange={(e) => set("use_closing_odds", e.target.checked)} /> Odds de fechamento
          </label>
          <div className="flex items-end">
            <button className="btn-primary w-full justify-center" onClick={() => run.mutate(req)} disabled={run.isPending}>
              <FlaskConical size={14} /> Rodar
            </button>
          </div>
        </div>
      </Card>

      {run.isPending && <Loading label="Reajustando modelos e liquidando apostas históricas…" />}
      {run.isError && <ErrorBox error={run.error} />}
      {!res && !run.isPending && <Empty title="Configure e rode um backtest" detail="Os resultados incluem ROI, yield, hit rate, Brier, log loss, drawdown e calibração. Resultados negativos são exibidos sem maquiagem." />}
      {res && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-8">
            <Stat label="Jogos avaliados" value={res.matches_evaluated} />
            <Stat label="Apostas" value={res.metrics.bets} hint={`${res.metrics.wins}V · ${res.metrics.losses}D`} />
            <Stat label="ROI" value={signedPct(res.metrics.roi)} accent={(res.metrics.roi ?? 0) >= 0 ? "text-success" : "text-danger"} />
            <Stat label="Hit rate" value={pct(res.metrics.hit_rate, 1)} />
            <Stat label="Brier" value={num(res.metrics.brier, 3)} hint="menor é melhor" />
            <Stat label="Log loss" value={num(res.metrics.log_loss, 3)} hint="menor é melhor" />
            <Stat label="Edge médio" value={`${num(res.metrics.avg_edge_pp, 1)} pp`} />
            <Stat label="Max drawdown" value={num(res.metrics.max_drawdown, 1)} hint="em unidades de stake" accent="text-danger" />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <SectionTitle title="Curva de banca" subtitle={`${res.model_version} · stake fixa ${res.request.stake as number}u`} />
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={res.equity_curve.map((v, i) => ({ i, v: +v.toFixed(2) }))}>
                  <CartesianGrid stroke="#EEF0F3" vertical={false} />
                  <XAxis dataKey="i" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} width={40} />
                  <RTooltip />
                  <Line type="monotone" dataKey="v" stroke={(res.metrics.profit ?? 0) >= 0 ? "#12B76A" : "#FF2638"} dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </Card>
            <Card>
              <SectionTitle title="Calibração" subtitle="Probabilidade prevista vs frequência observada (diagonal = perfeito)" />
              <ResponsiveContainer width="100%" height={220}>
                <ScatterChart>
                  <CartesianGrid stroke="#EEF0F3" />
                  <XAxis dataKey="predicted" domain={[0, 1]} tick={{ fontSize: 11 }} name="prevista" tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                  <YAxis dataKey="observed" domain={[0, 1]} tick={{ fontSize: 11 }} name="observada" width={40} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                  <RTooltip formatter={(v: number) => pct(v, 1)} />
                  <Scatter data={[{ predicted: 0, observed: 0 }, { predicted: 1, observed: 1 }]} line fill="transparent" shape={() => <g />} />
                  <Scatter data={res.calibration_bins} fill="#FF2638" />
                </ScatterChart>
              </ResponsiveContainer>
            </Card>
          </div>
          <Card>
            <SectionTitle title="Por seleção" />
            <div className="grid gap-2 md:grid-cols-3 lg:grid-cols-5">
              {Object.entries(res.by_selection).map(([k, m]) => (
                <MetricsBox key={k} title={k} m={m} />
              ))}
            </div>
            {res.note && <div className="mt-3 text-xs text-ink-2">{res.note}</div>}
          </Card>
        </>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-xs">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}

export function MetricsBox({ title, m }: { title: string; m: BacktestMetrics }) {
  return (
    <div className="rounded-lg border border-line p-3 text-sm">
      <div className="mb-1 text-xs font-bold uppercase tracking-wide">{title}</div>
      <KV k="Apostas" v={m.bets} />
      <KV k="ROI" v={<span className={clsx((m.roi ?? 0) >= 0 ? "text-success" : "text-danger")}>{signedPct(m.roi)}</span>} />
      <KV k="Hit" v={pct(m.hit_rate, 1)} />
      <KV k="Brier" v={num(m.brier, 3)} />
    </div>
  );
}
