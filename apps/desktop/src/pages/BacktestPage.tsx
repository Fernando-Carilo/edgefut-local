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
  scheme: "expanding",
  train_window_days: null,
  closing_for_clv: true,
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
          <Field label="Janela">
            <select className="input" value={req.scheme} onChange={(e) => set("scheme", e.target.value as BacktestRequest["scheme"])}>
              <option value="expanding">Expansiva (todo o passado)</option>
              <option value="rolling">Rolante (últimos N dias)</option>
            </select>
          </Field>
          {req.scheme === "rolling" && (
            <Field label="Treino (dias)">
              <input className="input" type="number" min={90} value={req.train_window_days ?? 365} onChange={(e) => set("train_window_days", Number(e.target.value))} />
            </Field>
          )}
          <label className="flex items-end gap-2 pb-2 text-sm text-ink-2" title="A odd de fechamento nunca decide a aposta: só mede o CLV depois.">
            <input type="checkbox" checked={req.closing_for_clv} onChange={(e) => set("closing_for_clv", e.target.checked)} /> CLV vs. fechamento
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
          <Card>
            <SectionTitle
              title="Janelas walk-forward"
              subtitle={`${res.windows.length} janelas · ${res.leakage_checks} verificações anti-leakage · decisão com ${res.decision_odds} · CLV ${signedPct(res.metrics.clv_pct)}`}
            />
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-ink-2">
                  <tr className="text-left">
                    <th className="py-1 pr-3">Treino</th>
                    <th className="py-1 pr-3">Teste</th>
                    <th className="py-1 pr-3 text-right">N treino</th>
                    <th className="py-1 pr-3 text-right">Avaliados</th>
                    <th className="py-1 pr-3 text-right">Apostas</th>
                    <th className="py-1 pr-3 text-right">Hit</th>
                    <th className="py-1 pr-3 text-right">ROI</th>
                    <th className="py-1 pr-3 text-right">Brier</th>
                    <th className="py-1 pr-3 text-right">CLV</th>
                  </tr>
                </thead>
                <tbody>
                  {res.windows.map((w) => (
                    <tr key={w.test_start} className="border-t border-line">
                      <td className="py-1 pr-3 font-mono">{w.train_start?.slice(0, 10) ?? "—"} → {w.train_end?.slice(0, 10) ?? "—"}</td>
                      <td className="py-1 pr-3 font-mono">{w.test_start.slice(0, 10)} → {w.test_end.slice(0, 10)}</td>
                      <td className="py-1 pr-3 text-right">{w.n_train}</td>
                      <td className="py-1 pr-3 text-right">{w.evaluated}{!w.fitted && " (sem ajuste)"}</td>
                      <td className="py-1 pr-3 text-right">{w.metrics.bets}</td>
                      <td className="py-1 pr-3 text-right">{pct(w.metrics.hit_rate, 0)}</td>
                      <td className={`py-1 pr-3 text-right ${(w.metrics.roi ?? 0) >= 0 ? "text-success" : "text-danger"}`}>{signedPct(w.metrics.roi)}</td>
                      <td className="py-1 pr-3 text-right">{num(w.metrics.brier, 3)}</td>
                      <td className="py-1 pr-3 text-right">{signedPct(w.metrics.clv_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-xs text-ink-2">
              Cada janela usa apenas partidas anteriores ao seu início (as_of). A odd de fechamento nunca participa da decisão — apenas do CLV.
              Nunca há split aleatório.
            </p>
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
