/**
 * Iteração 4 — painéis market-aware e Superbet, partilhados entre Validação (abas Market-Aware / Superbet / Shadow)
 * e Performance (Market Efficiency Lab / Superbet Lab). Tudo aqui é medição: nada muda decisões.
 *
 * Duas fontes, sempre rotuladas:
 *  - RESEARCH MARKET BENCHMARK: replay com odds football-data (timestamp econômico ≈ fechamento).
 *  - SUPERBET SHADOW VALIDATION: odds Superbet coletadas pelo próprio app, com carimbo de tempo real.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Interval, MarketAwareMarketReport, MarketAwareReport, MarketAwareRun, MarketEdgeVerdict, ShadowBucket, ShadowMarketAwarePanel, ShadowReport, SuperbetEvidenceReport } from "@edgefut/contracts";
import { fmtDateTime, int, num, pct } from "@edgefut/shared";
import clsx from "clsx";
import { Lock, RefreshCw } from "lucide-react";
import { useState } from "react";

import { Card, Empty, ErrorBox, KV, Loading, SectionTitle, Segmented, Stat } from "@/components/ui";
import { api } from "@/lib/api";

import { IntervalText, SampleChip, SigChip } from "./ValidationPage";

const CH_LABEL: Record<string, string> = {
  market: "Mercado (justa)",
  edgefut: "EdgeFut (consenso)",
  blend: "A · market-model-blend-v1",
  logistic: "B · market-logistic-stack-v1",
  residual: "C · market-residual-v1",
  abl_market_only: "ablação: só mercado",
  abl_market_edgefut: "ablação: mercado + EdgeFut",
  abl_market_strength: "ablação: mercado + força",
  abl_market_elo: "ablação: mercado + ELO",
  abl_market_form: "ablação: mercado + forma",
  abl_market_home_adv: "ablação: mercado + mando",
  abl_market_all: "ablação: mercado + tudo",
};
const label = (k: string) => CH_LABEL[k] ?? k;
const signed = (v: number | null | undefined, d = 1, suffix = "") => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}${suffix}`);

export function VerdictChip({ status }: { status: MarketEdgeVerdict | string | null | undefined }) {
  if (!status) return <span className="chip bg-gray-100 text-ink-2">—</span>;
  const cls = status.startsWith("CHALLENGER BEATS") ? "bg-success-50 text-success" : status.startsWith("PROMISING") ? "bg-info-50 text-info" : status.startsWith("NO EVIDENCE") ? "bg-warning-50 text-warning" : "bg-gray-100 text-ink-2";
  return <span className={clsx("chip", cls)}>{status}</span>;
}

export function SourceBadge({ kind }: { kind: "research" | "superbet" }) {
  return kind === "research" ? (
    <span className="chip bg-gray-100 text-ink-2" title="Odds históricas football-data (timestamp econômico ≈ fechamento). Não é a Superbet.">RESEARCH MARKET BENCHMARK</span>
  ) : (
    <span className="chip bg-primary/10 text-primary" title="Odds Superbet coletadas por este app, com carimbo de tempo real.">SUPERBET SHADOW VALIDATION</span>
  );
}

// ---------------------------------------------------------------- Market-Aware (Validação)

export function MarketAwareTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["market-aware"], queryFn: api.marketAwareLatest, refetchInterval: (d) => (d.state.data?.running || d.state.data?.holdout_running ? 10000 : false) });
  const start = useMutation({ mutationFn: () => api.startMarketAware({}), onSuccess: () => qc.invalidateQueries({ queryKey: ["market-aware"] }) });
  const holdout = useMutation({ mutationFn: () => api.startHoldout(), onSuccess: () => qc.invalidateQueries({ queryKey: ["market-aware"] }) });
  const [phase, setPhase] = useState<"holdout" | "discovery">("holdout");
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const run: MarketAwareRun | null = phase === "holdout" ? d.holdout : d.discovery;
  const rep = run?.detail ?? null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <SourceBadge kind="research" />
          <Segmented value={phase} options={[{ value: "holdout", label: "Confirmação (holdout congelado)" }, { value: "discovery", label: "Discovery (exploratório)" }]} onChange={setPhase} />
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
          {d.running && <span className="chip bg-info-50 text-info">discovery em execução…</span>}
          {d.holdout_running && <span className="chip bg-info-50 text-info">holdout em execução…</span>}
          {!d.frame_available && <span className="chip bg-warning-50 text-warning">sem replay frame — rode o replay de clubes primeiro</span>}
          <button className="btn-outline" onClick={() => start.mutate()} disabled={start.isPending || d.running || !d.frame_available} title="Nested walk-forward temporal na discovery + congela o artefato. Não toca o holdout.">
            <RefreshCw size={14} className={d.running ? "animate-spin" : ""} /> Rodar discovery
          </button>
          <button className="btn-outline" onClick={() => holdout.mutate()} disabled={holdout.isPending || d.holdout_running || !d.discovery || d.holdout_consumed} title={d.holdout_consumed ? "Holdout já consumido para este config_hash + dataset_version. Não se ajusta o modelo depois do holdout." : "Uma única execução por artefato congelado."}>
            <Lock size={14} /> {d.holdout_consumed ? "Holdout consumido" : "Rodar holdout (1×)"}
          </button>
        </div>
      </div>
      {!rep ? (
        <Empty title={phase === "holdout" ? "Holdout ainda não executado" : "Nenhuma discovery concluída"} detail={phase === "holdout" ? "O holdout congelado só roda uma vez por artefato (config_hash + dataset_version). Rode a discovery primeiro." : "A discovery treina blend / logistic / residual em nested walk-forward temporal e congela o artefato. Demora ~30 s por 10 mil partidas."} />
      ) : (
        <MarketAwareReportView rep={rep} run={run!} repeats={phase === "holdout" ? d.holdout_repeats : []} />
      )}
      {d.active_artifact && (
        <Card className="text-xs text-ink-2">
          <SectionTitle title="Artefato congelado ativo" subtitle="Usado pelo bloco MARKET vs EDGEFUT na página do jogo. Nunca é reajustado depois do holdout." />
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>model_hash <b className="font-mono">{d.active_artifact.model_hash}</b></span>
            <span>config_hash <b className="font-mono">{d.active_artifact.config_hash}</b></span>
            <span>dataset <b className="font-mono">{d.active_artifact.dataset_version}</b></span>
            <span>congelado {fmtDateTime(d.active_artifact.frozen_at)}</span>
            <span>holdout desde {d.active_artifact.holdout_start.slice(0, 10)}</span>
            {Object.entries(d.active_artifact.markets).map(([m, v]) => (
              <span key={m}>{m}: {v.selected} (α={v.alpha ?? "—"}, treino {int(v.train_rows)})</span>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

export function MarketAwareReportView({ rep, run, repeats, compact }: { rep: MarketAwareReport; run: MarketAwareRun; repeats?: MarketAwareRun[]; compact?: boolean }) {
  const markets = Object.entries(rep.markets);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Stat label="Fase" value={rep.phase.startsWith("CONFIRMATION") ? "CONFIRMAÇÃO" : "DISCOVERY"} hint={rep.phase.startsWith("CONFIRMATION") ? "holdout congelado, 1 execução" : "exploratória — hipóteses"} />
        <Stat label="Linhas" value={int(rep.phase.startsWith("CONFIRMATION") ? rep.holdout_rows : (rep.discovery_rows ?? 0))} hint={rep.phase.startsWith("CONFIRMATION") ? `holdout desde ${rep.holdout_start.slice(0, 10)}` : rep.period ? `${rep.period.start.slice(0, 10)} → ${rep.period.end.slice(0, 10)}` : ""} />
        <Stat label="config_hash" value={<span className="font-mono text-sm">{rep.config_hash}</span>} />
        <Stat label="model_hash" value={<span className="font-mono text-sm">{rep.model_hash ?? (run.summary.model_hash as string | undefined) ?? "—"}</span>} />
        <Stat label="dataset_version" value={<span className="font-mono text-[11px]">{rep.dataset_version}</span>} />
        <Stat label="run_timestamp" value={<span className="text-sm">{fmtDateTime(rep.run_timestamp ?? run.created_at)}</span>} hint={repeats && repeats.length > 0 ? `${repeats.length} repetição(ões) registradas (não contam)` : undefined} />
      </div>

      {/* Veredito por mercado */}
      <div className="grid gap-3 md:grid-cols-2">
        {Object.entries(rep.verdict).map(([m, v]) => (
          <Card key={m} className={clsx("border-l-4", v.status.startsWith("CHALLENGER BEATS") ? "border-l-success" : v.status.startsWith("NO EVIDENCE") ? "border-l-warning" : "border-l-ink-3")}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-base font-bold">{m === "OU25" ? "Over/Under 2,5" : m}</span>
              <VerdictChip status={v.status} />
            </div>
            <div className="mt-2 space-y-1 text-xs">
              {Object.values(v.checks).map((c) => (
                <div key={c.challenger} className="flex flex-wrap items-center gap-x-2">
                  <span className={clsx("w-44 shrink-0 font-semibold", c.pass ? "text-success" : "text-ink-2")}>{label(c.challenger)}</span>
                  {Object.entries(c.criteria).map(([k, cr]) => (
                    <span key={k} className={clsx("chip", cr.pass ? "bg-success-50 text-success" : "bg-danger-50 text-danger")} title={`${k}: ${cr.value ?? "—"}`}>
                      {k.replace(/_/g, " ")} {cr.value !== null && cr.value !== undefined ? `(${typeof cr.value === "number" ? cr.value.toFixed(4) : cr.value})` : ""}
                    </span>
                  ))}
                </div>
              ))}
              {Object.keys(v.checks).length === 0 && <div className="text-ink-3">Sem linhas suficientes neste mercado.</div>}
            </div>
          </Card>
        ))}
      </div>

      {markets.map(([m, mr]) => (
        <MarketBlock key={m} market={m} mr={mr} compact={compact} />
      ))}

      {rep.features && !compact && (
        <Card className="text-xs text-ink-2">
          <SectionTitle title="Features e regra absoluta da closing line" />
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>grupos aprovados: <b>{rep.features.approved_groups.join(", ")}</b></span>
            <span>residual: <b>{rep.features.residual_groups.join(", ")}</b></span>
            <span className="text-danger">proibidas (só CLV, nunca feature/treino/recomendação): <b>{rep.features.forbidden.join(", ")}</b></span>
            <span>data_quality: {rep.features.data_quality}</span>
          </div>
        </Card>
      )}
    </div>
  );
}

function MarketBlock({ market, mr, compact }: { market: string; mr: MarketAwareMarketReport; compact?: boolean }) {
  const [showAbl, setShowAbl] = useState(false);
  const rows = mr.ranking_brier.filter(([k]) => showAbl || !k.startsWith("abl_"));
  const eff = mr.effective_sample;
  return (
    <Card>
      <SectionTitle
        title={`${market === "OU25" ? "Over/Under 2,5" : market} — Brier OOS por modelo (quanto menor, melhor)`}
        subtitle={`Raw N ${int(eff.raw_n)} · Effective N ${int(eff.effective_n)} (ρ=${eff.rho}, ${int(eff.clusters)} clusters) · ${mr.windows} janelas temporais · IC 95% por bootstrap de cluster · Δ vs mercado: negativo = melhor que o mercado.`}
        right={
          <button className="btn-ghost text-xs" onClick={() => setShowAbl((s) => !s)}>
            {showAbl ? "Ocultar ablação" : "Mostrar ablação (§14)"}
          </button>
        }
      />
      <table className="w-full text-sm">
        <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
          <tr>
            <th className="py-1.5">Modelo</th>
            <th className="text-right">Brier [IC]</th>
            <th className="text-right">LogLoss</th>
            <th className="text-right">ECE</th>
            <th className="text-right">Δ Brier vs mercado [IC]</th>
            <th className="text-right">Δ LogLoss</th>
            <th className="text-right">janelas melhores</th>
            <th className="text-right">p</th>
            <th>Significância</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([k]) => {
            const o = mr.overall[k];
            const vs = mr.vs_market[k];
            return (
              <tr key={k} className={clsx("border-t border-line/70", k === "market" && "bg-gray-50 font-semibold")}>
                <td className="py-1.5">{label(k)}</td>
                <td className="text-right"><IntervalText v={o?.brier} digits={4} /></td>
                <td className="text-right tabular-nums">{num(o?.log_loss?.point, 4)}</td>
                <td className="text-right tabular-nums">{num(o?.ece, 4)}</td>
                <td className="text-right">{vs ? <IntervalText v={vs.delta_brier_ci} digits={4} signed /> : <span className="text-ink-3">baseline</span>}</td>
                <td className="text-right">{vs ? <IntervalText v={vs.delta_logloss_ci} digits={4} signed /> : ""}</td>
                <td className="text-right tabular-nums">{vs ? `${vs.windows_better}/${vs.windows_total}` : ""}</td>
                <td className="text-right tabular-nums">{vs?.p_value !== null && vs?.p_value !== undefined ? vs.p_value.toFixed(3) : ""}</td>
                <td>{vs ? <SigChip s={vs.significance} /> : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {mr.alpha && (
        <div className="mt-3 rounded-lg bg-bg px-3 py-2 text-xs">
          <span className="font-semibold">α do blend por janela (validação temporal):</span>{" "}
          {Object.entries(mr.alpha.chosen_per_window).map(([w, a]) => (
            <span key={w} className={clsx("mr-1 inline-block rounded px-1 tabular-nums", a >= 1 ? "bg-warning-50 text-warning" : "bg-gray-100")}>
              j{w}: {a}
            </span>
          ))}
          <span className="ml-2">média {mr.alpha.mean} · α=1,0 em {pct(mr.alpha.share_alpha_1, 0)} das janelas</span>
          <div className="mt-1 text-ink-2">{mr.alpha.note}</div>
        </div>
      )}

      {!compact && (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div>
            <SectionTitle title="Divergência × realidade (§44)" subtitle="Quando o EdgeFut discorda do mercado, quem acerta? Brier por bucket de |EdgeFut − mercado|." />
            <table className="w-full text-xs">
              <thead className="text-left text-[10px] font-semibold uppercase text-ink-2">
                <tr>
                  <th className="py-1">Bucket</th>
                  <th className="text-right">N</th>
                  <th className="text-right">Brier mercado</th>
                  <th className="text-right">Brier EdgeFut</th>
                  <th className="text-right">Brier residual</th>
                  <th className="text-right">lado favorecido: obs / mkt / EF</th>
                </tr>
              </thead>
              <tbody>
                {mr.disagreement_buckets.map((b) => (
                  <tr key={b.bucket} className="border-t border-line/60">
                    <td className="py-1">{b.bucket}</td>
                    <td className="text-right tabular-nums">{int(b.n)}</td>
                    <td className="text-right tabular-nums">{b.brier_market.toFixed(4)}</td>
                    <td className={clsx("text-right tabular-nums", b.brier_edgefut > b.brier_market ? "text-danger" : "text-success")}>{b.brier_edgefut.toFixed(4)}</td>
                    <td className="text-right tabular-nums">{b.brier_residual?.toFixed(4) ?? "—"}</td>
                    <td className="text-right tabular-nums">{pct(b.favored_side.observed_rate, 1)} / {pct(b.favored_side.market_prob, 1)} / {pct(b.favored_side.model_prob, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <SectionTitle title="Teste contrário (§45)" subtitle="Quando o EdgeFut dá ≥ 5 pp a mais que o mercado para um lado, com que frequência esse lado acontece?" />
            {mr.contrarian ? (
              <div className="rounded-lg border border-line p-3 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs text-ink-2">N = {int(mr.contrarian.n)} seleções com divergência ≥ {mr.contrarian.threshold_pp} pp</span>
                  <span className={clsx("chip", mr.contrarian.verdict === "MARKET CORRECT" ? "bg-warning-50 text-warning" : mr.contrarian.verdict === "EDGEFUT CORRECT" ? "bg-success-50 text-success" : "bg-gray-100 text-ink-2")}>{mr.contrarian.verdict}</span>
                </div>
                <div className="mt-2 grid grid-cols-3 gap-2 text-center">
                  <div>
                    <div className="text-[10px] font-semibold uppercase text-ink-3">Observado</div>
                    <div className="text-lg font-bold tabular-nums">{pct(mr.contrarian.observed_rate, 1)}</div>
                    <div className="text-[10px] text-ink-3">[{pct(mr.contrarian.observed_ci.low ?? 0, 1)}, {pct(mr.contrarian.observed_ci.high ?? 0, 1)}]</div>
                  </div>
                  <div>
                    <div className="text-[10px] font-semibold uppercase text-ink-3">Mercado dizia</div>
                    <div className="text-lg font-bold tabular-nums">{pct(mr.contrarian.market_prob, 1)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] font-semibold uppercase text-ink-3">EdgeFut dizia</div>
                    <div className="text-lg font-bold tabular-nums">{pct(mr.contrarian.edgefut_prob, 1)}</div>
                  </div>
                </div>
                <div className="mt-2 text-xs text-ink-2">{mr.contrarian.note}</div>
              </div>
            ) : (
              <Empty title="Sem divergências suficientes" />
            )}
          </div>
        </div>
      )}

      {!compact && mr.segments && (
        <div className="mt-4">
          <SectionTitle title={`Segmentos (exploratório, challenger ${label(mr.segments.challenger)}) — FDR Benjamini-Hochberg q=${mr.segments.fdr.q}`} subtitle={`${mr.segments.fdr.tested} testes · sobreviventes ao FDR: ${mr.segments.fdr.survivors.length ? mr.segments.fdr.survivors.join(", ") : "nenhum"}. ${mr.segments.note}`} />
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] font-semibold uppercase text-ink-2">
              <tr>
                <th className="py-1">Segmento</th>
                <th className="text-right">N</th>
                <th>Amostra</th>
                <th className="text-right">Brier mercado</th>
                <th className="text-right">Brier EdgeFut</th>
                <th className="text-right">Δ challenger vs mercado [IC]</th>
                <th className="text-right">p</th>
                <th className="text-right">p ajustado</th>
                <th>Veredito</th>
              </tr>
            </thead>
            <tbody>
              {mr.segments.rows.map((r) => (
                <tr key={r.segment} className="border-t border-line/60">
                  <td className="py-1 font-mono">{r.segment}</td>
                  <td className="text-right tabular-nums">{int(r.n)}</td>
                  <td><SampleChip q={r.sample_quality} /></td>
                  <td className="text-right tabular-nums">{r.brier_market.toFixed(4)}</td>
                  <td className="text-right tabular-nums">{r.brier_edgefut.toFixed(4)}</td>
                  <td className="text-right"><IntervalText v={r.delta_ci} digits={4} signed /></td>
                  <td className="text-right tabular-nums">{r.p_value?.toFixed(3) ?? "—"}</td>
                  <td className="text-right tabular-nums">{mr.segments!.fdr.adjusted[r.segment]?.toFixed(3) ?? "—"}</td>
                  <td>{(r.verdict as string | undefined) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!compact && Object.keys(mr.by_dataset).length > 0 && (
        <div className="mt-4">
          <SectionTitle title="Por competição — Brier" subtitle="Mercado vs EdgeFut vs challengers por dataset. Sem seleção pós-hoc do melhor nicho." />
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] font-semibold uppercase text-ink-2">
              <tr>
                <th className="py-1">Dataset</th>
                <th className="text-right">N</th>
                <th className="text-right">Mercado</th>
                <th className="text-right">EdgeFut</th>
                <th className="text-right">Blend</th>
                <th className="text-right">Logistic</th>
                <th className="text-right">Residual</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(mr.by_dataset).map(([ds, r]) => (
                <tr key={ds} className="border-t border-line/60">
                  <td className="py-1 font-mono">{ds}</td>
                  <td className="text-right tabular-nums">{int(r.n)}</td>
                  <td className="text-right tabular-nums">{r.market?.toFixed(4)}</td>
                  <td className={clsx("text-right tabular-nums", r.edgefut > r.market ? "text-danger" : "text-success")}>{r.edgefut?.toFixed(4)}</td>
                  <td className="text-right tabular-nums">{r.blend?.toFixed(4)}</td>
                  <td className="text-right tabular-nums">{r.logistic?.toFixed(4)}</td>
                  <td className="text-right tabular-nums">{r.residual?.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- Market Efficiency Lab (Performance) — resumo compacto

export function MarketEfficiencyLab() {
  const q = useQuery({ queryKey: ["market-aware"], queryFn: api.marketAwareLatest });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const run = d.holdout ?? d.discovery;
  const rep = run?.detail;
  if (!rep) return <Empty title="Sem experimento market-aware" detail="Rode a discovery em Validação → Market-Aware." />;
  return (
    <div className="space-y-4">
      <Card className="border-l-4 border-l-warning">
        <div className="flex flex-wrap items-center gap-2">
          <SourceBadge kind="research" />
          <span className="text-sm font-bold">MARKET EFFICIENCY LAB</span>
          <span className="text-xs text-ink-2">— o modelo contém informação incremental além do preço? Fonte: {rep.phase.startsWith("CONFIRMATION") ? "holdout congelado" : "discovery (não confirmado)"}.</span>
        </div>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          {Object.entries(rep.verdict).map(([m, v]) => (
            <div key={m} className="flex items-center justify-between rounded-lg border border-line px-3 py-2 text-sm">
              <span className="font-semibold">{m === "OU25" ? "Over/Under 2,5" : m}</span>
              <VerdictChip status={v.status} />
            </div>
          ))}
        </div>
      </Card>
      <MarketAwareReportView rep={rep} run={run!} compact />
    </div>
  );
}

// ---------------------------------------------------------------- Superbet (Validação) e Superbet Lab (Performance)

export function SuperbetTab({ lab }: { lab?: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["superbet-evidence"], queryFn: () => api.superbetEvidence(false) });
  const refresh = useMutation({ mutationFn: () => api.superbetEvidence(true), onSuccess: (data) => qc.setQueryData(["superbet-evidence"], data) });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <SourceBadge kind="superbet" />
          {lab && <span className="text-sm font-bold">SUPERBET LAB</span>}
          <span className="text-xs text-ink-2">dataset <b className="font-mono">{d.dataset_version}</b> · gerado {fmtDateTime(d.generated_at)}</span>
        </div>
        <button className="btn-outline" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
          <RefreshCw size={14} className={refresh.isPending ? "animate-spin" : ""} /> Recalcular agora
        </button>
      </div>
      <SuperbetReportView d={d} lab={lab} />
    </div>
  );
}

export function SuperbetReportView({ d, lab }: { d: SuperbetEvidenceReport; lab?: boolean }) {
  const cov = d.coverage;
  const sp = d.shadow_panel;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Stat label="Snapshots pré-kickoff" value={int(cov.snapshots_pre_kickoff)} hint={`${int(cov.snapshots_live_excluded)} ao vivo excluídos`} />
        <Stat label="Eventos" value={int(cov.events)} hint={`${int(cov.selections)} seleções`} />
        <Stat label="Cadência mediana" value={cov.cadence_minutes_median !== null ? `${num(cov.cadence_minutes_median, 0)} min` : "—"} hint={`mediana ${num(cov.snapshots_per_selection_median, 0)} snapshots/seleção`} />
        <Stat label="Closing capturado" value={int(d.closing_lines.events)} hint={`${int(d.closing_lines.rows)} linhas · eventos`} />
        <Stat label="Shadow liquidado" value={int(sp.total.settled)} hint={`${int(sp.total.settled_events)} jogos · ${int(sp.total.predictions)} previsões`} />
        <Stat label="VALUE / OBSERVATION" value={`${int(sp.total.value)} / ${int(sp.total.observation)}`} hint="no shadow, com preço" />
      </div>

      <Card>
        <SectionTitle title="Buckets de tempo até o kickoff (§8)" subtitle="Só existem os buckets com coleta real. Nenhum snapshot ausente é inventado." />
        <div className="grid grid-cols-4 gap-2 md:grid-cols-8">
          {d.buckets.map((b) => (
            <div key={b.bucket} className={clsx("rounded-lg border p-2 text-center text-xs", b.exists ? "border-line" : "border-dashed border-line/60 text-ink-3")}>
              <div className="font-semibold">{b.bucket}</div>
              <div className="tabular-nums">{b.exists ? `${int(b.events)} ev · ${pct(b.events_share, 0)}` : "sem coleta"}</div>
              <div className="text-[10px] text-ink-3">{int(b.snapshots)} snaps</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Overround Superbet por mercado (§10)" subtitle={d.overround.note} />
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] font-semibold uppercase text-ink-2">
              <tr>
                <th className="py-1">Mercado</th>
                <th className="text-right">Eventos</th>
                <th className="text-right">Mediana</th>
                <th className="text-right">P10–P90</th>
              </tr>
            </thead>
            <tbody>
              {d.overround.by_market.map((r) => (
                <tr key={r.market_key} className="border-t border-line/60">
                  <td className="py-1">{r.market_key}</td>
                  <td className="text-right tabular-nums">{int(r.events)}</td>
                  <td className="text-right tabular-nums font-semibold">{r.overround_median_pct.toFixed(2)}%</td>
                  <td className="text-right tabular-nums text-ink-2">{r.p10_pct.toFixed(1)}–{r.p90_pct.toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          {d.overround.by_time_to_kickoff.filter((r) => r.market_key === "1X2").length > 0 && (
            <div className="mt-2 text-xs text-ink-2">
              <b>1X2 por tempo até o kickoff:</b>{" "}
              {d.overround.by_time_to_kickoff.filter((r) => r.market_key === "1X2").map((r) => (
                <span key={r.ttk_bucket} className="mr-2 tabular-nums">{r.ttk_bucket} {r.overround_median_pct.toFixed(2)}%</span>
              ))}
            </div>
          )}
        </Card>
        <Card>
          <SectionTitle title="Movimento de linha (§27)" subtitle={`${int(d.line_movement.n_with_2plus_snapshots)} de ${int(d.line_movement.n_selections)} seleções com ≥ 2 snapshots. ${d.line_movement.favourite_drift_note}`} />
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] font-semibold uppercase text-ink-2">
              <tr>
                <th className="py-1">Mercado</th>
                <th className="text-right">N</th>
                <th className="text-right">moveu &gt; 0,5 pp</th>
                <th className="text-right">|Δ| mediana</th>
                <th className="text-right">|Δ| P90</th>
                <th className="text-right">Δ líquido</th>
              </tr>
            </thead>
            <tbody>
              {d.line_movement.by_market.slice(0, lab ? 6 : 14).map((r) => (
                <tr key={r.market_key} className="border-t border-line/60">
                  <td className="py-1">{r.market_key}</td>
                  <td className="text-right tabular-nums">{int(r.n)}</td>
                  <td className="text-right tabular-nums">{r.share_moved_gt_0_5pp !== null ? pct(r.share_moved_gt_0_5pp, 0) : "—"}</td>
                  <td className="text-right tabular-nums">{signed(r.abs_move_pp_median, 2, " pp").replace("+", "")}</td>
                  <td className="text-right tabular-nums">{signed(r.abs_move_pp_p90, 2, " pp").replace("+", "")}</td>
                  <td className="text-right tabular-nums">{signed(r.net_move_pp_mean, 2, " pp")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {d.line_movement.favourite_drift_corr_1x2 !== null && <div className="mt-2 text-xs text-ink-2">corr(favorito, drift) 1X2 = {d.line_movement.favourite_drift_corr_1x2.toFixed(3)}</div>}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Superbet fair como baseline (§12) — shadow liquidado" subtitle="Brier / LogLoss da probabilidade justa Superbet vs EdgeFut no shadow liquidado com preço. N efetivo por evento." />
          <BaselineTable d={d} />
        </Card>
        <Card>
          <SectionTitle title="CLV vs closing Superbet (§9, §28)" subtitle={d.clv.note} />
          <div className="text-xs text-ink-2">
            {int(d.clv.n_with_closing)} de {int(d.clv.n_priced)} seleções com closing Superbet · {int(d.clv.events_with_closing)} eventos
          </div>
          {d.clv.all ? (
            <div className="mt-2 grid grid-cols-2 gap-2 text-sm">
              <KV k="CLV médio [IC]" v={<IntervalText v={d.clv.all.clv_pct} digits={2} signed suffix="%" />} />
              <KV k="% positivo" v={d.clv.all.share_positive !== null ? pct(d.clv.all.share_positive, 1) : "—"} />
            </div>
          ) : (
            <Empty title="Sem closing Superbet ainda" />
          )}
          {d.clv.by_family.length > 0 && (
            <table className="mt-2 w-full text-xs">
              <thead className="text-left text-[10px] font-semibold uppercase text-ink-2">
                <tr>
                  <th className="py-1">Família</th>
                  <th className="text-right">N</th>
                  <th className="text-right">Eventos</th>
                  <th className="text-right">CLV [IC]</th>
                  <th className="text-right">% positivo</th>
                </tr>
              </thead>
              <tbody>
                {d.clv.by_family.map((r) => (
                  <tr key={r.family} className="border-t border-line/60">
                    <td className="py-1">{r.family}</td>
                    <td className="text-right tabular-nums">{int(r.n)}</td>
                    <td className="text-right tabular-nums">{int(r.events)}</td>
                    <td className="text-right"><IntervalText v={r.clv_pct} digits={2} signed suffix="%" /></td>
                    <td className="text-right tabular-nums">{r.share_positive !== null ? pct(r.share_positive, 0) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      {!lab && (
        <Card>
          <SectionTitle title="Teste de viés da Superbet (§11)" subtitle={d.bias.note} />
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] font-semibold uppercase text-ink-2">
              <tr>
                <th className="py-1">Segmento</th>
                <th className="text-right">N</th>
                <th className="text-right">Eventos</th>
                <th>Amostra</th>
                <th className="text-right">Observado</th>
                <th className="text-right">Fair média</th>
                <th className="text-right">Brier fair</th>
                <th>Veredito</th>
              </tr>
            </thead>
            <tbody>
              {d.bias.rows.map((r) => (
                <tr key={r.segment} className="border-t border-line/60">
                  <td className="py-1 font-mono">{r.segment}</td>
                  <td className="text-right tabular-nums">{int(r.n)}</td>
                  <td className="text-right tabular-nums">{int(r.events)}</td>
                  <td><SampleChip q={r.sample_quality} /></td>
                  <td className="text-right tabular-nums">{r.observed_rate !== null ? pct(r.observed_rate, 1) : "—"}</td>
                  <td className="text-right tabular-nums">{r.fair_prob_mean !== null ? pct(r.fair_prob_mean, 1) : "—"}</td>
                  <td className="text-right tabular-nums">{r.brier_fair !== null ? r.brier_fair.toFixed(4) : "—"}</td>
                  <td className="text-ink-2">{r.verdict}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card>
        <SectionTitle title="Superbet shadow por família de mercado (§25)" subtitle="Eventos, previsões, liquidadas, pendentes, erros, futuras — com preço, VALUE e OBSERVATION." />
        <table className="w-full text-xs">
          <thead className="text-left text-[10px] font-semibold uppercase text-ink-2">
            <tr>
              <th className="py-1">Família</th>
              <th className="text-right">Eventos</th>
              <th className="text-right">Previsões</th>
              <th className="text-right">Liquidadas</th>
              <th className="text-right">Pendentes</th>
              <th className="text-right">Erros</th>
              <th className="text-right">Futuras</th>
              <th className="text-right">VALUE</th>
              <th className="text-right">OBSERVATION</th>
            </tr>
          </thead>
          <tbody>
            {[...sp.families, { ...sp.total, family: "TOTAL" }].map((r) => (
              <tr key={r.family} className={clsx("border-t border-line/60", r.family === "TOTAL" && "font-semibold")}>
                <td className="py-1">{r.family}</td>
                <td className="text-right tabular-nums">{int(r.events)}</td>
                <td className="text-right tabular-nums">{int(r.predictions)}</td>
                <td className="text-right tabular-nums">{int(r.settled)}</td>
                <td className="text-right tabular-nums">{int(r.pending)}</td>
                <td className="text-right tabular-nums">{int(r.errors)}</td>
                <td className="text-right tabular-nums">{int(r.upcoming)}</td>
                <td className="text-right tabular-nums">{int(r.value)}</td>
                <td className="text-right tabular-nums">{int(r.observation)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card className="text-xs text-ink-2">
        <ul className="list-disc space-y-1 pl-5">
          {d.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
          {d.export && <li>Dataset versionado exportado: {Object.entries(d.export.files).map(([k, f]) => `${k} (${int(f.rows)} linhas, sha ${f.sha16})`).join(" · ")}</li>}
        </ul>
      </Card>
    </div>
  );
}

function BaselineTable({ d }: { d: SuperbetEvidenceReport }) {
  const all = d.baseline.all;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <SampleChip q={all.sample_quality} n={all.n} />
        <span className="text-ink-2">Effective N {int(all.effective_n)} · {int(all.events)} jogos</span>
        <span className={clsx("chip", all.winner === "INCONCLUSIVE" ? "bg-gray-100 text-ink-2" : all.winner === "EDGEFUT" ? "bg-success-50 text-success" : "bg-warning-50 text-warning")}>{all.winner}</span>
      </div>
      <table className="mt-2 w-full text-xs">
        <thead className="text-left text-[10px] font-semibold uppercase text-ink-2">
          <tr>
            <th className="py-1">Família</th>
            <th className="text-right">N</th>
            <th className="text-right">Eff. N</th>
            <th className="text-right">Brier Superbet</th>
            <th className="text-right">Brier EdgeFut</th>
            <th className="text-right">Δ (EF − SB) [IC]</th>
            <th>Vencedor</th>
          </tr>
        </thead>
        <tbody>
          {[...d.baseline.by_market, { ...all, family: "TODAS" }].map((r) => (
            <tr key={r.family} className={clsx("border-t border-line/60", r.family === "TODAS" && "font-semibold")}>
              <td className="py-1">{r.family}</td>
              <td className="text-right tabular-nums">{int(r.n)}</td>
              <td className="text-right tabular-nums">{int(r.effective_n)}</td>
              <td className="text-right tabular-nums">{num(r.superbet_fair.brier, 4)}</td>
              <td className="text-right tabular-nums">{num(r.edgefut.brier, 4)}</td>
              <td className="text-right"><IntervalText v={r.edgefut_minus_superbet_brier} digits={4} signed /></td>
              <td>{r.winner}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- Shadow v2 (Superbet fair vs EdgeFut vs Híbrido, grau, opportunity)

export function ShadowMarketAwareSection({ d }: { d: ShadowReport }) {
  const p = d.market_aware;
  if (!p) return null;
  const cv = d.confidence_validation;
  const ov = d.opportunity_validation;
  return (
    <>
      <Card>
        <SectionTitle
          title="Superbet fair vs EdgeFut vs Híbrido — shadow liquidado (§48)"
          subtitle={`Raw N ${int(p.raw_n)} · Effective N ${int(p.effective.effective_n)} (${int(p.events)} jogos, ρ=${p.effective.rho}) · IC 95% por bootstrap de cluster (evento) · VALUE ${int(p.value_count)} · NO BET ${int(p.no_bet_count)}`}
          right={<span className={clsx("chip", p.verdict === "INSUFFICIENT DATA" ? "bg-gray-100 text-ink-2" : p.verdict.startsWith("EDGEFUT BETTER") ? "bg-success-50 text-success" : "bg-warning-50 text-warning")}>{p.verdict}</span>}
        />
        {p.superbet_fair ? (
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
              <tr>
                <th className="py-1.5">Fonte</th>
                <th className="text-right">N</th>
                <th className="text-right">Brier [IC]</th>
                <th className="text-right">LogLoss [IC]</th>
                <th className="text-right">Δ Brier vs Superbet</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-line/70 bg-gray-50 font-semibold">
                <td className="py-1.5">Superbet fair</td>
                <td className="text-right tabular-nums">{int(p.raw_n)}</td>
                <td className="text-right"><IntervalText v={p.superbet_fair.brier} /></td>
                <td className="text-right"><IntervalText v={p.superbet_fair.log_loss} /></td>
                <td className="text-right text-ink-3">baseline</td>
              </tr>
              <tr className="border-t border-line/70">
                <td className="py-1.5">EdgeFut</td>
                <td className="text-right tabular-nums">{int(p.raw_n)}</td>
                <td className="text-right"><IntervalText v={p.edgefut!.brier} /></td>
                <td className="text-right"><IntervalText v={p.edgefut!.log_loss} /></td>
                <td className="text-right"><IntervalText v={p.edgefut_vs_superbet!.delta_brier} signed /></td>
              </tr>
              <tr className="border-t border-line/70">
                <td className="py-1.5">Híbrido (market-aware congelado)</td>
                <td className="text-right tabular-nums">{int(p.hybrid?.n ?? 0)}</td>
                <td className="text-right">{p.hybrid?.brier ? <IntervalText v={p.hybrid.brier} /> : <span className="text-ink-3">—</span>}</td>
                <td className="text-right">{p.hybrid?.log_loss ? <IntervalText v={p.hybrid.log_loss} /> : <span className="text-ink-3">—</span>}</td>
                <td className="text-right">{p.hybrid?.delta_brier_vs_superbet ? <IntervalText v={p.hybrid.delta_brier_vs_superbet} signed /> : <span className="text-ink-3" title={p.hybrid?.note}>sem linhas com hybrid_prob</span>}</td>
              </tr>
            </tbody>
          </table>
        ) : (
          <Empty title="Sem shadow liquidado com preço e probabilidade de mercado" />
        )}
        {p.clv && <div className="mt-2 text-xs text-ink-2">CLV vs closing Superbet: N {int(p.clv.n)} ({int(p.clv.events)} jogos) · <IntervalText v={p.clv.pct} digits={2} signed suffix="%" /></div>}
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        {cv && <BucketCard title="Grau de confiança A/B/C/D fora da amostra (§46)" subtitle={cv.note} verdict={cv.verdict} eff={cv.effective} buckets={cv.grades} />}
        {ov && <BucketCard title="Opportunity Score por faixa fora da amostra (§47)" subtitle={ov.note} verdict={ov.verdict} eff={ov.effective} buckets={ov.bins} />}
      </div>
    </>
  );
}

function BucketCard({ title, subtitle, verdict, eff, buckets }: { title: string; subtitle: string; verdict: string; eff: { raw_n: number; effective_n: number }; buckets: Record<string, ShadowBucket> }) {
  const entries = Object.entries(buckets);
  return (
    <Card>
      <SectionTitle title={title} subtitle={`${subtitle} Raw N ${int(eff.raw_n)} · Effective N ${int(eff.effective_n)}.`} right={<span className={clsx("chip", verdict === "INSUFFICIENT DATA" ? "bg-gray-100 text-ink-2" : verdict.includes("NOT") || verdict.startsWith("NO ") ? "bg-warning-50 text-warning" : "bg-success-50 text-success")}>{verdict}</span>} />
      {entries.length === 0 ? (
        <Empty title="Sem linhas liquidadas" />
      ) : (
        <table className="w-full text-xs">
          <thead className="text-left text-[10px] font-semibold uppercase text-ink-2">
            <tr>
              <th className="py-1">Faixa</th>
              <th className="text-right">N</th>
              <th className="text-right">Jogos</th>
              <th className="text-right">p̄ modelo</th>
              <th className="text-right">Hit [IC]</th>
              <th className="text-right">Brier [IC]</th>
              <th className="text-right">ROI [IC]</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([k, b]) => (
              <tr key={k} className="border-t border-line/60">
                <td className="py-1 font-semibold">{k}</td>
                <td className="text-right tabular-nums">{int(b.n)}</td>
                <td className="text-right tabular-nums">{int(b.events)}</td>
                <td className="text-right tabular-nums">{pct(b.avg_model_prob, 1)}</td>
                <td className="text-right"><IntervalText v={b.hit_rate} digits={3} /></td>
                <td className="text-right"><IntervalText v={b.brier} digits={4} /></td>
                <td className="text-right"><IntervalText v={b.roi as Interval | undefined} digits={1} signed suffix="%" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

export type { ShadowMarketAwarePanel };
