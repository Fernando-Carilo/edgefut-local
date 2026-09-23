import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { BetSimMetrics, Interval, PairedComparison, ReplayReport, SampleQuality, ShadowPerf, Significance } from "@edgefut/contracts";
import { fmtDateTime, int, num, pct, signedPct } from "@edgefut/shared";
import clsx from "clsx";
import { Info, RefreshCw } from "lucide-react";
import { useState } from "react";

import { Card, Empty, ErrorBox, KV, Loading, PageHeader, SectionTitle, Segmented, Stat } from "@/components/ui";
import { api } from "@/lib/api";

type Tab = "replay" | "modelos" | "cobertura" | "shadow" | "drift";

const SIG_CLS: Record<Significance, string> = {
  "INSUFFICIENT DATA": "bg-gray-100 text-ink-2",
  "NO CLEAR ADVANTAGE": "bg-warning-50 text-warning",
  PROMISING: "bg-info-50 text-info",
  CONSISTENT: "bg-success-50 text-success",
};
const SQ_CLS: Record<SampleQuality, string> = {
  INSUFFICIENT: "bg-danger-50 text-danger",
  EARLY: "bg-warning-50 text-warning",
  MODERATE: "bg-info-50 text-info",
  STRONG: "bg-success-50 text-success",
};

export function SigChip({ s }: { s: Significance | string | null | undefined }) {
  if (!s) return <span className="chip bg-gray-100 text-ink-2">—</span>;
  return <span className={clsx("chip", SIG_CLS[s as Significance] ?? "bg-gray-100 text-ink-2")}>{s}</span>;
}

export function SampleChip({ q, n }: { q: SampleQuality | null | undefined; n?: number }) {
  if (!q) return null;
  return (
    <span className={clsx("chip", SQ_CLS[q])} title="INSUFFICIENT < 100 · EARLY < 300 · MODERATE < 1000 · STRONG ≥ 1000 (configurável)">
      {q}
      {n !== undefined ? ` · N ${int(n)}` : ""}
    </span>
  );
}

export function IntervalText({ v, digits = 4, signed = false, suffix = "" }: { v: Interval | null | undefined; digits?: number; signed?: boolean; suffix?: string }) {
  if (!v || v.point === null || v.point === undefined) return <span className="text-ink-3">—</span>;
  const f = (x: number | null) => (x === null ? "—" : signed ? `${x >= 0 ? "+" : ""}${x.toFixed(digits)}` : x.toFixed(digits));
  return (
    <span className="tabular-nums">
      {f(v.point)}
      {suffix}
      <span className="text-ink-3"> [{f(v.low)}, {f(v.high)}]</span>
    </span>
  );
}

export function ValidationPage() {
  const [tab, setTab] = useState<Tab>("replay");
  return (
    <div className="space-y-5">
      <PageHeader
        title="Validação preditiva"
        subtitle="Replay histórico walk-forward (nunca vê o futuro), baselines, IC 95% por bootstrap, cobertura por competição, shadow mode e drift. Só medição — nada aqui muda decisões."
        right={
          <Segmented
            value={tab}
            options={[
              { value: "replay", label: "Model Validation" },
              { value: "modelos", label: "Model Comparison" },
              { value: "cobertura", label: "Coverage Map" },
              { value: "shadow", label: "Shadow" },
              { value: "drift", label: "Drift" },
            ]}
            onChange={setTab}
          />
        }
      />
      {tab === "replay" && <ReplayTab />}
      {tab === "modelos" && <ModelsTab />}
      {tab === "cobertura" && <CoverageTab />}
      {tab === "shadow" && <ShadowTab />}
      {tab === "drift" && <DriftTab />}
    </div>
  );
}

// ---------------------------------------------------------------- Replay / Model Validation

function ReplayTab() {
  const qc = useQueryClient();
  const [intl, setIntl] = useState(false);
  const q = useQuery({ queryKey: ["replay", intl], queryFn: () => api.replayLatest(intl), refetchInterval: (d) => (d.state.data?.running ? 10000 : false) });
  const start = useMutation({
    mutationFn: () => api.startReplay(intl ? { international: true, start: "2010-01-01", window_days: 90 } : { start: "2022-08-01", window_days: 30 }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["replay"] }),
  });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const rep = d.detail;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Segmented value={intl ? "intl" : "clubs"} options={[{ value: "clubs", label: "Clubes (com odds)" }, { value: "intl", label: "Seleções (MODEL ONLY)" }]} onChange={(v) => setIntl(v === "intl")} />
        <div className="flex items-center gap-2 text-xs text-ink-2">
          {d.running && <span className="chip bg-info-50 text-info">replay em execução…</span>}
          {rep && <span>gerado {fmtDateTime(rep.generated_at)} · {Math.round(rep.duration_ms / 1000)} s</span>}
          <button className="btn-outline" onClick={() => start.mutate()} disabled={start.isPending || d.running}>
            <RefreshCw size={14} className={d.running ? "animate-spin" : ""} /> Rodar replay
          </button>
        </div>
      </div>
      {!rep ? (
        <Empty title="Nenhum replay concluído" detail="Rode o replay: ele percorre o histórico em janelas cronológicas, treina só com o passado e mede Brier/LogLoss contra baselines. Demora alguns minutos." />
      ) : (
        <ReplayReportView rep={rep} intl={intl} />
      )}
    </div>
  );
}

function ReplayReportView({ rep, intl }: { rep: ReplayReport; intl: boolean }) {
  const label = (m: string) => rep.labels[m] ?? m;
  const models = rep.ranking_brier.map(([m]) => m);
  const champion = rep.promotion_evaluation ? Object.values(rep.promotion_evaluation)[0]?.champion : "ensemble";
  const champOverall = champion ? rep.overall[champion] : undefined;
  const vsMarket = champion ? rep.vs_baseline[champion]?.market : undefined;
  const vsNaive = champion ? rep.vs_baseline[champion]?.naive : undefined;
  const markets = rep.bets && rep.bets[champion ?? ""] ? rep.bets[champion ?? ""] : undefined;
  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <Stat label="Partidas (replay N)" value={int(rep.matches)} hint={`${int(rep.matches_with_odds)} com odds · ${rep.windows} janelas`} />
        <Stat label="Datasets" value={rep.datasets.length} hint={rep.datasets.join(", ")} />
        <Stat label={`Brier ${label(champion ?? "")}`} value={num(champOverall?.brier.point, 4)} hint="campeão em produção" />
        <Stat label="Brier mercado" value={intl ? "—" : num(rep.overall.market?.brier.point, 4)} hint={intl ? "sem odds em seleções" : "baseline: odds justas de fechamento"} />
        <Card className="flex flex-col gap-1">
          <span className="label">Campeão vs mercado</span>
          <SigChip s={vsMarket?.significance ?? (intl ? "INSUFFICIENT DATA" : null)} />
          <span className="text-xs text-ink-2">lift {vsMarket?.lift_pct !== undefined && vsMarket?.lift_pct !== null ? signedPct(vsMarket.lift_pct) : "—"}</span>
        </Card>
        <Card className="flex flex-col gap-1">
          <span className="label">Campeão vs naive</span>
          <SigChip s={vsNaive?.significance} />
          <span className="text-xs text-ink-2">lift {vsNaive?.lift_pct !== undefined && vsNaive?.lift_pct !== null ? signedPct(vsNaive.lift_pct) : "—"}</span>
        </Card>
      </div>

      <Card>
        <SectionTitle title="Ranking por Brier (1X2, multiclasse) — quanto menor, melhor" subtitle="IC 95% por bootstrap sobre as partidas; ECE = erro de calibração esperado; lift vs mercado/naive com IC da diferença pareada." />
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
              <tr>
                <th className="py-1.5">Modelo</th>
                <th className="text-right">N</th>
                <th className="text-right">Brier [IC 95%]</th>
                <th className="text-right">LogLoss</th>
                <th className="text-right">ECE</th>
                <th className="text-right">Hit</th>
                <th className="text-right">OU2.5 Brier</th>
                {!intl && <th>vs mercado</th>}
                <th>vs naive</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => {
                const o = rep.overall[m];
                const vm = rep.vs_baseline[m]?.market;
                const vn = rep.vs_baseline[m]?.naive;
                const isBaseline = rep.baselines.includes(m);
                return (
                  <tr key={m} className={clsx("border-t border-line/70", m === champion && "font-semibold", isBaseline && "text-ink-2")}>
                    <td className="py-1.5">
                      {label(m)} {isBaseline && <span className="chip bg-gray-100 text-ink-2">baseline</span>}
                      {m === champion && <span className="chip bg-primary-50 text-primary">campeão</span>}
                    </td>
                    <td className="text-right tabular-nums">{int(o.n)}</td>
                    <td className="text-right"><IntervalText v={o.brier} /></td>
                    <td className="text-right tabular-nums">{num(o.log_loss.point, 4)}</td>
                    <td className="text-right tabular-nums">{o.ece !== null ? num(o.ece, 4) : "—"}</td>
                    <td className="text-right tabular-nums">{pct(o.hit_rate, 1)}</td>
                    <td className="text-right tabular-nums">{o.ou25 ? num(o.ou25.brier.point, 4) : "—"}</td>
                    {!intl && <td>{vm ? <Paired p={vm} /> : <span className="text-ink-3">—</span>}</td>}
                    <td>{vn ? <Paired p={vn} /> : <span className="text-ink-3">—</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {rep.by_group && Object.keys(rep.by_group).length > 0 && (
        <Card>
          <SectionTitle title={intl ? "Por tipo de torneio (§43)" : "Por temporada"} subtitle="Brier do campeão e dos baselines por grupo, com qualidade de amostra. Amistosos e eliminatórias são mundos diferentes." />
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
              <tr>
                <th className="py-1.5">Grupo</th>
                <th className="text-right">Partidas</th>
                <th>Amostra</th>
                <th className="text-right">Brier campeão</th>
                <th className="text-right">Brier naive</th>
                {!intl && <th className="text-right">Brier mercado</th>}
                <th>vs naive</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(rep.by_group).map(([g, v]) => (
                <tr key={g} className="border-t border-line/70">
                  <td className="py-1.5 font-mono text-xs">{g}</td>
                  <td className="text-right tabular-nums">{int(v.matches)}</td>
                  <td><SampleChip q={v.sample_quality} /></td>
                  <td className="text-right tabular-nums">{num(v.brier[champion ?? ""], 4)}</td>
                  <td className="text-right tabular-nums">{num(v.brier.naive, 4)}</td>
                  {!intl && <td className="text-right tabular-nums">{num(v.brier.market, 4)}</td>}
                  <td>{v.vs_naive[champion ?? ""] ? <Paired p={v.vs_naive[champion ?? ""]} /> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {!intl && (
        <Card>
          <SectionTitle title="Mapa competição × modelo × mercado" subtitle="Brier 1X2 e OU2.5 do campeão por dataset, com significância vs mercado. Onde o modelo NÃO supera o mercado, VALUE não deveria existir no 1X2." />
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
              <tr>
                <th className="py-1.5">Dataset</th>
                <th className="text-right">Partidas</th>
                <th className="text-right">1X2 Brier campeão</th>
                <th className="text-right">1X2 Brier mercado</th>
                <th className="text-right">OU2.5 Brier campeão</th>
                <th className="text-right">OU2.5 Brier mercado</th>
                <th>vs mercado</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(rep.by_dataset).map(([code, v]) => {
                const c = v.models[champion ?? ""];
                const mk = v.models.market;
                return (
                  <tr key={code} className="border-t border-line/70">
                    <td className="py-1.5 font-mono text-xs">{code}</td>
                    <td className="text-right tabular-nums">{int(v.matches)}</td>
                    <td className="text-right tabular-nums">{num(c?.brier.point, 4)}</td>
                    <td className="text-right tabular-nums">{num(mk?.brier.point, 4)}</td>
                    <td className="text-right tabular-nums">{c?.ou25 ? num(c.ou25.brier.point, 4) : "—"}</td>
                    <td className="text-right tabular-nums">{mk?.ou25 ? num(mk.ou25.brier.point, 4) : "—"}</td>
                    <td>{v.vs_market[champion ?? ""] ? <Paired p={v.vs_market[champion ?? ""]} /> : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}

      {markets && (
        <Card>
          <SectionTitle title={`Apostas simuladas — ${label(champion ?? "")} (gate simplificado, stake 1u)`} subtitle="Uma seleção por mercado por partida, edge/EV mínimos das configurações, odds de abertura do dataset. ROI com IC 95%: INCONCLUSIVE quando o IC cruza zero. Isto NÃO é performance real — é a prova de que o gate teria feito dinheiro no passado (ou não)." />
          <div className="grid gap-3 md:grid-cols-2">
            <BetBlock title="Todas as apostas" b={markets.all} />
            {Object.entries(markets.by_market).map(([mk, b]) => (
              <BetBlock key={mk} title={mk} b={b} />
            ))}
          </div>
        </Card>
      )}

      <Card className="text-xs text-ink-2">
        <SectionTitle title="Limitações declaradas" />
        <ul className="list-disc space-y-1 pl-5">
          {rep.limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </Card>
    </>
  );
}

function Paired({ p }: { p: PairedComparison }) {
  return (
    <div className="flex flex-col gap-0.5">
      <SigChip s={p.significance} />
      {p.lift_pct !== undefined && p.lift_pct !== null && (
        <span className="text-[11px] text-ink-2 tabular-nums">
          lift {signedPct(p.lift_pct)} · ΔBrier <IntervalText v={p.delta_brier_ci} signed />
          {p.windows_total ? ` · ${p.windows_better}/${p.windows_total} janelas` : ""}
        </span>
      )}
    </div>
  );
}

function BetBlock({ title, b }: { title: string; b: BetSimMetrics }) {
  const v = b.verdict;
  return (
    <div className="rounded-lg border border-line p-3 text-sm">
      <div className="flex items-center justify-between">
        <span className="font-semibold">{title}</span>
        <div className="flex items-center gap-1">
          <SampleChip q={b.sample_quality} n={b.n} />
          {v && <span className={clsx("chip", v === "POSITIVE" ? "bg-success-50 text-success" : v === "NEGATIVE" ? "bg-danger-50 text-danger" : "bg-gray-100 text-ink-2")}>{v}</span>}
        </div>
      </div>
      {b.n > 0 && (
        <div className="mt-2 grid grid-cols-2 gap-1 text-xs">
          <KV k="ROI" v={<IntervalText v={b.roi} digits={1} signed suffix="%" />} />
          <KV k="Hit rate" v={<IntervalText v={b.hit_rate} digits={3} />} />
          <KV k="Odd média" v={num(b.avg_odd, 2)} />
          <KV k="Edge médio" v={`${num(b.avg_edge_pp, 1)} pp`} />
          <KV k="CLV" v={b.clv ? <IntervalText v={b.clv} digits={1} signed suffix="%" /> : "—"} />
          <KV k="Lucro (u)" v={num(b.profit_units, 1)} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Model Comparison + governança

function ModelsTab() {
  const gov = useQuery({ queryKey: ["governance"], queryFn: api.governance });
  const decay = useQuery({ queryKey: ["decay"], queryFn: api.decayLatest });
  const rep = useQuery({ queryKey: ["replay", false], queryFn: () => api.replayLatest(false) });
  if (gov.isLoading || decay.isLoading || rep.isLoading) return <Loading />;
  if (gov.isError) return <ErrorBox error={gov.error} retry={() => gov.refetch()} />;
  const g = gov.data!;
  const dd = decay.data?.detail;
  const r = rep.data?.detail;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        <Card>
          <span className="label">Campeão (decide em produção)</span>
          <div className="mt-1 text-lg font-bold">{g.champion}</div>
          <div className="text-xs text-ink-2">Challengers rodam em paralelo e aparecem na comparação, mas não entram no consenso.</div>
        </Card>
        <Card>
          <span className="label">Papéis</span>
          <ul className="mt-1 space-y-0.5 text-xs">
            {g.roles.map((x) => (
              <li key={x.model_id} className="flex justify-between">
                <span className="font-mono">{x.model_id} <span className="text-ink-3">{x.version}</span></span>
                <span className={clsx("chip", x.role === "champion" ? "bg-primary-50 text-primary" : x.role === "challenger" ? "bg-info-50 text-info" : "bg-gray-100 text-ink-2")}>{x.role ?? "—"}</span>
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <span className="label">Decay temporal (strength-v2)</span>
          <div className="mt-1 text-lg font-bold">meia-vida {decay.data?.current_half_life_days ?? "—"} d</div>
          <div className="text-xs text-ink-2">
            {dd ? `Escolhida por walk-forward: ${dd.recommended_half_life_days ?? "sem decay"} d (N ${int(dd.matches)}, ${dd.windows} janelas)${dd.tie_with_runner_up ? " — empate técnico com o 2º colocado" : ""}.` : "Nenhuma seleção de decay registrada."}
          </div>
        </Card>
      </div>

      <Card>
        <SectionTitle title="Regra de promoção (avaliada no último replay — nunca automática)" subtitle={g.rule.join(" · ")} />
        {!g.promotion_evaluation ? (
          <Empty title="Sem avaliação" detail="Rode um replay de clubes para avaliar os challengers contra o campeão." />
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {Object.entries(g.promotion_evaluation).map(([k, ev]) => (
              <div key={k} className="rounded-lg border border-line p-3 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-semibold">{k} → {ev.champion}</span>
                  <span className={clsx("chip", ev.eligible ? "bg-success-50 text-success" : ev.verdict.startsWith("PROMISING") ? "bg-info-50 text-info" : "bg-gray-100 text-ink-2")}>{ev.verdict}</span>
                </div>
                <ul className="mt-2 space-y-1 text-xs">
                  {Object.entries(ev.checks).map(([c, chk]) => (
                    <li key={c} className="flex items-start gap-2">
                      <span className={clsx("mt-0.5 inline-block h-2 w-2 rounded-full", chk.ok ? "bg-success" : "bg-danger")} />
                      <span>
                        <span className="font-medium">{c}</span>{" "}
                        <span className="text-ink-2">{Object.entries(chk).filter(([kk]) => kk !== "ok").map(([kk, vv]) => `${kk}=${typeof vv === "number" ? vv.toFixed(4).replace(/\.?0+$/, "") : String(vv)}`).join(" · ")}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </Card>

      {r && (
        <Card>
          <SectionTitle title="Estabilidade por janela" subtitle="Em quantas janelas cronológicas cada modelo bateu o mercado / o naive. Um modelo bom é bom quase sempre, não só na média." />
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
              <tr>
                <th className="py-1.5">Modelo</th>
                <th className="text-right">Janelas melhores que o mercado</th>
                <th className="text-right">Janelas melhores que o naive</th>
                <th className="text-right">Brier</th>
              </tr>
            </thead>
            <tbody>
              {r.models.map((m) => {
                const vm = r.vs_baseline[m]?.market;
                const vn = r.vs_baseline[m]?.naive;
                return (
                  <tr key={m} className="border-t border-line/70">
                    <td className="py-1.5">{r.labels[m] ?? m}</td>
                    <td className="text-right tabular-nums">{vm?.windows_total ? `${vm.windows_better}/${vm.windows_total} (${pct((vm.windows_better ?? 0) / vm.windows_total, 0)})` : "—"}</td>
                    <td className="text-right tabular-nums">{vn?.windows_total ? `${vn.windows_better}/${vn.windows_total} (${pct((vn.windows_better ?? 0) / vn.windows_total, 0)})` : "—"}</td>
                    <td className="text-right tabular-nums">{num(r.overall[m]?.brier.point, 4)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}

      {dd && (
        <Card>
          <SectionTitle title="Seleção de decay por walk-forward" subtitle="Brier 1X2 do strength-v2 por meia-vida candidata. Não escolhemos decay por gosto." />
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
              <tr>
                <th className="py-1.5">Meia-vida</th>
                <th className="text-right">Brier</th>
                <th className="text-right">LogLoss</th>
                <th>vs melhor</th>
              </tr>
            </thead>
            <tbody>
              {dd.ranking.map(([k]) => {
                const c = dd.candidates[k];
                const vb = dd.best_vs_others_delta_brier?.[k];
                return (
                  <tr key={k} className={clsx("border-t border-line/70", k === dd.best && "font-semibold")}>
                    <td className="py-1.5">{c.half_life_days === null ? "sem decay" : `${c.half_life_days} d`}</td>
                    <td className="text-right tabular-nums">{num(c.brier, 5)}</td>
                    <td className="text-right tabular-nums">{num(c.log_loss, 5)}</td>
                    <td>{vb ? <IntervalText v={vb} signed /> : k === dd.best ? <span className="chip bg-success-50 text-success">melhor</span> : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}

      {g.promotions.length > 0 && (
        <Card>
          <SectionTitle title="Promoções registradas" />
          <ul className="space-y-1 text-xs">
            {g.promotions.map((p) => (
              <li key={p.id}>
                {fmtDateTime(p.created_at)} · {String(p.summary.from)} → {String(p.summary.to)} · {String(p.request.reason ?? "")}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Coverage Map

function CoverageTab() {
  const q = useQuery({ queryKey: ["coverage"], queryFn: api.coverage });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const cell = (v: number) => (
    <td className={clsx("text-right tabular-nums", v === 0 ? "text-ink-3" : v < 50 ? "text-warning" : "text-ink")}>{v === 0 ? "0%" : `${v.toFixed(0)}%`}</td>
  );
  return (
    <Card>
      <SectionTitle title="Competition Data Coverage" subtitle={`Percentual de partidas com cada dado. VALUE só é possível onde há odds históricas (≥ 200 partidas). ${d.note}`} />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
            <tr>
              <th className="py-1.5">Dataset</th>
              <th>Competição</th>
              <th className="text-right">Partidas</th>
              <th>Período</th>
              <th className="text-right">Resultado</th>
              <th className="text-right">Odds</th>
              <th className="text-right">Finalizações</th>
              <th className="text-right">Escanteios</th>
              <th className="text-right">Cartões</th>
              <th className="text-right">Jogadores</th>
              <th>Amostra c/ odds</th>
              <th>Capaz de VALUE</th>
            </tr>
          </thead>
          <tbody>
            {d.datasets.map((r) => (
              <tr key={r.dataset_code} className="border-t border-line/70">
                <td className="py-1.5 font-mono text-xs">{r.dataset_code}</td>
                <td className="text-xs">{r.competition ?? "—"}{r.competitions > 1 ? ` (+${r.competitions - 1})` : ""}</td>
                <td className="text-right tabular-nums">{int(r.matches)}</td>
                <td className="text-xs text-ink-2">{r.first_date} → {r.last_date}</td>
                {cell(r.results_pct)}
                {cell(r.odds_pct)}
                {cell(r.shots_pct)}
                {cell(r.corners_pct)}
                {cell(r.cards_pct)}
                {cell(r.players_pct)}
                <td><SampleChip q={r.sample_quality} /></td>
                <td>{r.value_capable ? <span className="chip bg-success-50 text-success">SIM</span> : <span className="chip bg-warning-50 text-warning">MODEL ONLY</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- Shadow

function ShadowTab() {
  const q = useQuery({ queryKey: ["shadow"], queryFn: api.shadow });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const windows = Object.entries(d.performance ?? {});
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Previsões shadow" value={int(d.shadow_rows_total)} hint="append-only, sem interação do usuário" />
        <Stat label="Liquidadas" value={int(d.shadow_rows_settled)} hint={`${int(d.settled_today)} hoje`} />
        <Stat label="Eventos hoje" value={int(d.events_observed)} hint={`${int(d.events_analyzed)} analisados`} />
        <Stat label="VALUE / CANDIDATE hoje" value={`${int(d.selections_by_state.VALUE ?? 0)} / ${int(d.selections_by_state.VALUE_CANDIDATE ?? 0)}`} />
        <Stat label="MODEL ONLY hoje" value={int(d.selections_by_state.MODEL_ONLY ?? 0)} hint="nunca em ROI" />
      </div>
      <Card>
        <SectionTitle title="Performance real por janela" subtitle="Só seleções liquidadas com preço entram em ROI/CLV. MODEL_ONLY mede apenas Brier/LogLoss. IC 95% por bootstrap." />
        {windows.length === 0 ? (
          <Empty title="Sem shadow liquidado" detail="As previsões são gravadas antes do kickoff e liquidadas pela reconciliação. Volte depois dos jogos." />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
              <tr>
                <th className="py-1.5">Janela</th>
                <th>Com preço</th>
                <th className="text-right">ROI [IC]</th>
                <th className="text-right">Hit</th>
                <th className="text-right">Brier</th>
                <th>Só VALUE</th>
                <th className="text-right">ROI VALUE</th>
                <th>MODEL ONLY</th>
                <th className="text-right">Brier MODEL ONLY</th>
              </tr>
            </thead>
            <tbody>
              {windows.map(([w, p]) => (
                <tr key={w} className="border-t border-line/70">
                  <td className="py-1.5 font-mono text-xs">{w}</td>
                  <td><SampleChip q={p.priced.sample_quality} n={p.priced.n} /></td>
                  <td className="text-right"><IntervalText v={p.priced.roi} digits={1} signed suffix="%" /></td>
                  <td className="text-right"><IntervalText v={p.priced.hit_rate} digits={3} /></td>
                  <td className="text-right"><IntervalText v={p.priced.brier} /></td>
                  <td><SampleChip q={p.value_only.sample_quality} n={p.value_only.n} /></td>
                  <td className="text-right"><IntervalText v={p.value_only.roi} digits={1} signed suffix="%" /></td>
                  <td><SampleChip q={p.model_only.sample_quality} n={p.model_only.n} /></td>
                  <td className="text-right"><IntervalText v={p.model_only.brier} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {Object.keys(d.by_market ?? {}).length > 0 && (
        <Card>
          <SectionTitle title="Por mercado (com preço)" />
          <div className="grid gap-2 md:grid-cols-3">
            {Object.entries(d.by_market).map(([mk, p]) => (
              <ShadowPerfBlock key={mk} title={mk} p={p} />
            ))}
          </div>
        </Card>
      )}
      <Card className="text-xs text-ink-2">
        <ul className="list-disc space-y-1 pl-5">
          {d.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function ShadowPerfBlock({ title, p }: { title: string; p: ShadowPerf }) {
  return (
    <div className="rounded-lg border border-line p-3 text-sm">
      <div className="flex items-center justify-between">
        <span className="font-semibold">{title}</span>
        <SampleChip q={p.sample_quality} n={p.n} />
      </div>
      {p.n > 0 && (
        <div className="mt-2 grid grid-cols-2 gap-1 text-xs">
          <KV k="ROI" v={<IntervalText v={p.roi} digits={1} signed suffix="%" />} />
          <KV k="Hit" v={<IntervalText v={p.hit_rate} digits={3} />} />
          <KV k="Brier" v={<IntervalText v={p.brier} />} />
          <KV k="CLV" v={p.clv ? <IntervalText v={p.clv} digits={1} signed suffix="%" /> : "—"} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Drift

function DriftTab() {
  const q = useQuery({ queryKey: ["drift"], queryFn: api.drift });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const cls = d.status === "DRIFT" ? "bg-danger-50 text-danger" : d.status === "WATCH" ? "bg-warning-50 text-warning" : d.status === "STABLE" ? "bg-success-50 text-success" : "bg-gray-100 text-ink-2";
  const keys = Object.keys(d.recent).filter((k) => k !== "n");
  return (
    <div className="space-y-4">
      <Card className="flex items-start gap-3">
        <span className={clsx("chip", cls)}>{d.status}</span>
        <div className="text-sm">
          <div>
            Janela recente {d.windows.recent_days} d (<SampleChip q={d.sample_quality.recent} n={d.recent.n} />) vs referência {d.windows.reference_days} d (<SampleChip q={d.sample_quality.reference} n={d.reference.n} />)
          </div>
          <div className="mt-1 text-xs text-ink-2">{d.action}</div>
        </div>
      </Card>
      <Card>
        <SectionTitle title="Métricas recente × referência" subtitle="Drift é sinal, não ação: nenhum modelo é retreinado ou trocado automaticamente." />
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
            <tr>
              <th className="py-1.5">Métrica</th>
              <th className="text-right">Recente</th>
              <th className="text-right">Referência</th>
              <th>Alerta</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => {
              const a = d.alerts.find((x) => x.metric === k);
              return (
                <tr key={k} className="border-t border-line/70">
                  <td className="py-1.5 font-mono text-xs">{k}</td>
                  <td className="text-right tabular-nums">{d.recent[k] === null || d.recent[k] === undefined ? "—" : num(d.recent[k] as number, 4)}</td>
                  <td className="text-right tabular-nums">{d.reference[k] === null || d.reference[k] === undefined ? "—" : num(d.reference[k] as number, 4)}</td>
                  <td className="text-xs">{a ? <span className={clsx("chip", a.severity === "ALERT" ? "bg-danger-50 text-danger" : "bg-warning-50 text-warning")}>{a.message}</span> : <span className="text-ink-3">—</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
      {d.probability_audit && (
        <Card>
          <SectionTitle title="Auditoria de probabilidades (snapshots recentes)" subtitle="Distribuição das probabilidades emitidas: média, extremos (> 90%) e histograma. Probabilidades extremas sem amostra forte penalizam a confiança." />
          <pre className="max-h-64 overflow-auto rounded bg-gray-50 p-3 text-[11px] leading-relaxed">{JSON.stringify(d.probability_audit, null, 1)}</pre>
        </Card>
      )}
      <div className="flex items-center gap-1 text-xs text-ink-3">
        <Info size={12} /> Gerado {fmtDateTime(d.generated_at)}
      </div>
    </div>
  );
}
