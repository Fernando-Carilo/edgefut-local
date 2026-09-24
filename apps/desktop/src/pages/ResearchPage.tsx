import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fmtDateTime, int, num, relativeTime } from "@edgefut/shared";
import clsx from "clsx";
import type { DiscoveryRow, EdgeStateRow, ExperimentStatus, HypothesisRow, MarketCategory } from "@edgefut/contracts";
import { MARKET_CATEGORY_LABELS } from "@edgefut/contracts";
import { Lock, Play, ShieldCheck, ShieldOff } from "lucide-react";
import { useState } from "react";

import { Card, Empty, ErrorBox, Loading, Modal, PageHeader, SectionTitle } from "@/components/ui";
import { api } from "@/lib/api";

import { EdgeStateChip, MaturityChip } from "./FlywheelPage";

const HYP_CLS: Record<ExperimentStatus, string> = { DISCOVERY: "bg-gray-100 text-ink-2", CANDIDATE: "bg-info-50 text-info", CONFIRMING: "bg-warning-50 text-warning", SUPPORTED: "bg-success-50 text-success", REJECTED: "bg-danger-50 text-danger" };

function Interval({ v, unit = "", digits = 3 }: { v: unknown; unit?: string; digits?: number }) {
  const iv = v as { point?: number | null; low?: number | null; high?: number | null; n?: number; conclusive?: boolean | null } | null | undefined;
  if (!iv || iv.point == null) return <span className="text-ink-3">INSUFFICIENT</span>;
  return (
    <span className={clsx(iv.conclusive === true && "font-semibold")} title={`n=${iv.n}`}>
      {num(iv.point, digits)}{unit} <span className="text-ink-3">[{num(iv.low ?? null, digits)}, {num(iv.high ?? null, digits)}]</span>
    </span>
  );
}

export function ResearchPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["flywheel", "research"], queryFn: api.research, refetchInterval: 120000 });
  const es = useQuery({ queryKey: ["flywheel", "edge-states"], queryFn: api.edgeStates, refetchInterval: 120000 });
  const weekly = useQuery({ queryKey: ["flywheel", "weekly"], queryFn: api.reportWeekly });
  const run = useMutation({ mutationFn: api.runResearch, onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["flywheel"] }), 4000) });
  if (q.isLoading || es.isLoading) return <Loading label="Lendo evidência por mercado…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const r = q.data!;
  const states = es.data?.states ?? [];
  const weeklyRep = weekly.data?.report as Record<string, unknown> | null | undefined;
  const a67 = weeklyRep?.answer_to_67 as { promising: string[]; rejected: string[]; growing: string[]; answer: string } | undefined;
  return (
    <div className="space-y-6">
      <PageHeader
        title="Pesquisa por mercado"
        subtitle="Em que mercado vale a pena investir pesquisa? A resposta vem da evidência Superbet por mercado — nunca da previsão. VALUE só liga com validação e decisão humana."
        right={
          <button className="btn-outline" onClick={() => run.mutate()} disabled={run.isPending}>
            <Play size={14} /> Recalcular pesquisa
          </button>
        }
      />
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
        <span className="chip bg-gray-100 text-ink-2">
          <Lock size={11} className="mr-1 inline" /> MODEL FREEZE {r.freeze?.status ?? "—"}
        </span>
        <span className="chip bg-gray-100 text-ink-2">VALUE_ENABLED = {es.data?.value_enabled_default ? "true" : "false"} (padrão)</span>
        <span className={clsx("chip", es.data?.staking.enabled ? "bg-success-50 text-success" : "bg-danger-50 text-danger")}>STAKING {es.data?.staking.enabled ? "ENABLED" : "DISABLED"}</span>
        <span className="ml-auto">{r.generated_at ? `Calculado ${relativeTime(r.generated_at)} em ${r.duration_ms} ms` : r.note}</span>
      </div>

      {!r.available ? (
        <Empty title="Pesquisa ainda não executada" detail={r.note} />
      ) : (
        <>
          <Card className="border-l-4 border-l-primary">
            <SectionTitle title="§67 — Em que mercado vale a pena investir pesquisa?" subtitle={weeklyRep ? `Relatório semanal ${relativeTime(String(weeklyRep.generated_at))}` : "Relatório semanal ainda não gerado."} />
            {a67 ? (
              <div className="grid gap-3 text-sm md:grid-cols-4">
                <div className="md:col-span-4 font-semibold">{a67.answer}</div>
                <KV k="Promissores" v={a67.promising.length ? a67.promising.join(", ") : "—"} />
                <KV k="Rejeitados" v={a67.rejected.length ? a67.rejected.join(", ") : "—"} />
                <KV k="Já testáveis" v={a67.growing.length ? a67.growing.join(", ") : "nenhum (todos em coleta)"} />
                <KV k="Headline" v={String(weeklyRep?.headline ?? "")} />
              </div>
            ) : (
              <div className="text-sm text-ink-2">INSUFFICIENT — sem relatório semanal.</div>
            )}
          </Card>

          <Card>
            <SectionTitle title="Discovery por mercado" subtitle="Brier da fair da Superbet vs baseline simples (taxa-base leave-one-event-out) vs EdgeFut. Só mercados TESTABLE/MATURE recebem veredito; SMALL SAMPLE nunca vira tendência." />
            <DiscoveryTable rows={r.discovery ?? []} />
          </Card>

          <Card>
            <SectionTitle title="Estados de edge por mercado" subtitle={`Regras para VALUE_ENABLEMENT_CANDIDATE: N efetivo ≥ ${es.data?.rules.min_effective_n}, EdgeFut bate a fair (IC), CLV ≥ 0, calibração sem viés, hipótese SUPPORTED após FDR, confirmação ≥ ${es.data?.rules.confirmation_min_days} dias. Ativar é decisão humana com motivo.`} />
            <EdgeStatesTable rows={states} onChanged={() => qc.invalidateQueries({ queryKey: ["flywheel"] })} />
          </Card>

          <Card>
            <SectionTitle title="Experimentos pré-registados" subtitle={r.experiments?.note} right={<span className="text-xs text-ink-2">BH-FDR q={r.experiments?.q} · testadas {r.experiments?.tested} · sobreviventes {r.experiments?.survivors.length ?? 0}</span>} />
            <HypothesesTable rows={r.experiments?.hypotheses ?? []} />
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <SectionTitle title="Eficiência de preço por T-x" subtitle={r.price_efficiency?.note} />
              <EfficiencyTable rows={r.price_efficiency?.rows ?? []} />
            </Card>
            <Card>
              <SectionTitle title="MODEL FREEZE" subtitle={r.freeze?.freeze.note} />
              {r.freeze && (
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <KV k="Freeze" v={fmtDateTime(r.freeze.freeze.freeze_date)} />
                  <KV k="Estado" v={<span className={clsx("chip", r.freeze.status === "INTACT" ? "bg-success-50 text-success" : "bg-danger-50 text-danger")}>{r.freeze.status}</span>} />
                  <KV k="model_hash" v={<code>{r.freeze.freeze.model_hash}</code>} />
                  <KV k="config_hash" v={<code>{r.freeze.freeze.config_hash}</code>} />
                  <KV k="dataset_version" v={<code>{r.freeze.freeze.dataset_version}</code>} />
                  <KV k="Famílias proibidas" v={r.freeze.freeze.forbidden_families.join(", ")} />
                  <div className="col-span-2">
                    <div className="text-[10px] uppercase tracking-wide text-ink-3">Pausado</div>
                    {Object.entries(r.freeze.freeze.paused).map(([k, v]) => (
                      <div key={k} className="text-ink-2">
                        <b>{k}</b>: {v}
                      </div>
                    ))}
                  </div>
                  <div className="col-span-2 text-ink-3">Modelos congelados: {r.freeze.freeze.frozen_models.join(", ")}</div>
                </div>
              )}
            </Card>
          </div>
          <div className="text-xs text-ink-3">{r.research_only}</div>
        </>
      )}
    </div>
  );
}

function DiscoveryTable({ rows }: { rows: DiscoveryRow[] }) {
  if (!rows.length) return <Empty title="Sem mercados" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-ink-3">
            <th className="py-1 font-semibold">Mercado</th>
            <th>Raw N</th>
            <th>Eventos</th>
            <th>N efetivo</th>
            <th>Maturidade</th>
            <th>Overround</th>
            <th>Brier fair</th>
            <th>Brier baseline</th>
            <th>Brier EdgeFut</th>
            <th>ΔBrier EdgeFut − fair</th>
            <th>CLV raw perto do fecho</th>
            <th>Veredito</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.market_category} className="border-t border-line">
              <td className="py-1.5 font-semibold">{d.label}</td>
              <td>{int(d.raw_n)}</td>
              <td>{int(d.unique_events)}</td>
              <td className="font-semibold">{int(d.effective_n)}</td>
              <td><MaturityChip maturity={d.maturity} /></td>
              <td>{d.overround_median_pct != null ? `${num(d.overround_median_pct, 1)}%` : "—"}</td>
              <td>{d.superbet_fair?.brier != null ? num(d.superbet_fair.brier, 4) : <span className="text-ink-3">INSUFFICIENT</span>}</td>
              <td>{d.simple_baseline?.brier != null ? num(d.simple_baseline.brier, 4) : <span className="text-ink-3">INSUFFICIENT</span>}</td>
              <td>{d.edgefut?.brier != null ? num(d.edgefut.brier, 4) : <span className="text-ink-3">INSUFFICIENT</span>}</td>
              <td><Interval v={d.delta_brier_edgefut_vs_fair} digits={4} /></td>
              <td><Interval v={d.clv_near_close?.clv_raw_pct} unit="%" digits={2} /></td>
              <td className="text-ink-2">{d.verdict ?? d.edgefut_vs_fair ?? "INSUFFICIENT"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EdgeStatesTable({ rows, onChanged }: { rows: EdgeStateRow[]; onChanged: () => void }) {
  const [target, setTarget] = useState<EdgeStateRow | null>(null);
  if (!rows.length) return <Empty title="Sem estados" detail="A pesquisa cria os estados na primeira execução." />;
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-ink-3">
              <th className="py-1 font-semibold">Mercado</th>
              <th>Estado</th>
              <th>N efetivo</th>
              <th>Confiança</th>
              <th>Required edge V2</th>
              <th>Regras OK / falhas</th>
              <th>VALUE</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              const ev = s.evidence ?? {};
              const req = ev.required_edge_v2;
              return (
                <tr key={s.market_category} className="border-t border-line align-top">
                  <td className="py-1.5 font-semibold">{s.label}</td>
                  <td><EdgeStateChip state={s.state} /></td>
                  <td>{int(ev.effective_n ?? 0)}</td>
                  <td>{ev.confidence ?? "INSUFFICIENT"}</td>
                  <td title={req ? Object.entries(req.components).map(([k, v]) => `${k}: ${v}`).join(" · ") : ""}>{req ? `${num(req.required_edge_pp, 1)} pp${req.market_error_measured ? "" : " (erro de mercado não medido)"}` : "—"}</td>
                  <td>
                    <span className="text-success">{(ev.rules_ok ?? []).length} OK</span> · <span className="text-danger">{(ev.rules_failed ?? []).length} falhas</span>
                    <details className="mt-0.5 text-[11px] text-ink-2">
                      <summary className="cursor-pointer">detalhes</summary>
                      <ul className="list-disc pl-4">
                        {(ev.rules_ok ?? []).map((x) => <li key={x} className="text-success">{x}</li>)}
                        {(ev.rules_failed ?? []).map((x) => <li key={x}>{x}</li>)}
                      </ul>
                    </details>
                  </td>
                  <td>{s.value_enabled ? <span className="chip bg-success-50 text-success">ON</span> : s.enablement_candidate ? <span className="chip bg-info-50 text-info">VALUE_ENABLEMENT_CANDIDATE</span> : <span className="chip bg-gray-100 text-ink-3">OFF</span>}</td>
                  <td>
                    {s.value_enabled ? (
                      <button className="btn-ghost text-xs text-danger" onClick={() => setTarget(s)}>
                        <ShieldOff size={12} /> Desativar
                      </button>
                    ) : (
                      <button className="btn-ghost text-xs" disabled={!s.enablement_candidate} title={s.enablement_candidate ? "Ativar VALUE (decisão humana, registada)" : "Só com VALUE_ENABLEMENT_CANDIDATE"} onClick={() => setTarget(s)}>
                        <ShieldCheck size={12} /> Ativar
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {target && <ValueToggleModal row={target} onClose={() => setTarget(null)} onDone={() => { setTarget(null); onChanged(); }} />}
    </>
  );
}

function ValueToggleModal({ row, onClose, onDone }: { row: EdgeStateRow; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const m = useMutation({ mutationFn: () => api.toggleValue(row.market_category, { enabled: !row.value_enabled, reason }), onSuccess: onDone });
  return (
    <Modal title={`${row.value_enabled ? "Desativar" : "Ativar"} VALUE — ${row.label}`} onClose={onClose}>
      <p className="text-sm text-ink-2">
        {row.value_enabled
          ? "Desativar volta o mercado a PROMISING/UNPROVEN e as seleções passam a RESEARCH_SIGNAL."
          : "Ativar marca o mercado como VALIDATED: as seleções com edge voltam a poder aparecer como VALUE e o staking é reativado para este mercado. A decisão fica registada com o motivo (trilha de auditoria)."}
      </p>
      <label className="mt-3 block text-xs">
        <span className="label">Motivo (obrigatório)</span>
        <textarea className="input h-20" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="ex.: relatório semanal de 2026-10-30: ΔBrier −0,004 [−0,007, −0,001], CLV +0,8% [0,1, 1,5], H5 SUPPORTED após FDR" />
      </label>
      {m.isError && <div className="mt-2 text-xs text-danger">{(m.error as Error).message}</div>}
      <div className="mt-3 flex justify-end gap-2">
        <button className="btn-outline" onClick={onClose}>Cancelar</button>
        <button className={row.value_enabled ? "btn-outline text-danger" : "btn-primary"} disabled={reason.trim().length < 5 || m.isPending} onClick={() => m.mutate()}>
          Confirmar
        </button>
      </div>
    </Modal>
  );
}

function HypothesesTable({ rows }: { rows: HypothesisRow[] }) {
  if (!rows.length) return <Empty title="Sem hipóteses registadas" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-ink-3">
            <th className="py-1 font-semibold">Hipótese</th>
            <th>Mercado</th>
            <th>Direção</th>
            <th>Confirmação desde</th>
            <th>N efetivo (mín.)</th>
            <th>Estimativa [IC 95%]</th>
            <th>p</th>
            <th>p ajustado (BH)</th>
            <th>Amostra</th>
            <th>Estado</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((h) => (
            <tr key={h.hypothesis_id} className="border-t border-line align-top">
              <td className="py-1.5">
                <div className="font-semibold">{h.hypothesis_id}</div>
                <div className="text-ink-2">{h.title}</div>
              </td>
              <td>{MARKET_CATEGORY_LABELS[h.market_category as MarketCategory] ?? h.market_category}{h.odds_band ? ` · ${h.odds_band}` : ""}{h.time_window ? ` · ${h.time_window}` : ""}</td>
              <td className="text-center">{h.expected_direction}</td>
              <td>{h.confirmation_start ? fmtDateTime(h.confirmation_start) : "—"}</td>
              <td>{int(h.confirmation.effective_n)} ({int(h.min_effective_n)})</td>
              <td>{h.confirmation.estimate != null ? <><Interval v={h.confirmation.ci} digits={2} /> <span className="text-ink-3">{h.confirmation.unit}</span></> : <span className="text-ink-3">INSUFFICIENT</span>}</td>
              <td>{h.confirmation.p_value != null ? num(h.confirmation.p_value, 3) : "—"}</td>
              <td>{h.fdr_adjusted_p != null ? num(h.fdr_adjusted_p, 3) : "—"}</td>
              <td className={h.sample_label === "SMALL SAMPLE" ? "text-warning" : "text-success"}>{h.sample_label}</td>
              <td><span className={clsx("chip", HYP_CLS[h.status])}>{h.status}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EfficiencyTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return <Empty title="INSUFFICIENT" detail="Precisa de seleções liquidadas com snapshot no alvo (N ≥ 30 e ≥ 10 eventos)." />;
  const s = (v: unknown) => (v == null ? "" : String(v));
  const n = (v: unknown) => (typeof v === "number" ? v : null);
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-ink-3">
          <th className="py-1 font-semibold">Mercado</th>
          <th>Alvo</th>
          <th>N</th>
          <th>Brier</th>
          <th>LogLoss</th>
          <th>Viés (pp)</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-line">
            <td className="py-1 font-semibold">{MARKET_CATEGORY_LABELS[s(r.market_category) as MarketCategory] ?? s(r.market_category)}</td>
            <td>{s(r.target)}</td>
            <td>{int(n(r.n))}</td>
            <td>{n(r.brier) != null ? num(n(r.brier), 4) : "—"}</td>
            <td>{n(r.logloss) != null ? num(n(r.logloss), 4) : "—"}</td>
            <td><Interval v={r.calibration_bias_pp} digits={2} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-ink-3">{k}</div>
      <div className="font-semibold">{v}</div>
    </div>
  );
}
