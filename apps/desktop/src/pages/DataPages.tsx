import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fmtDateTime, int, pct, relativeTime } from "@edgefut/shared";
import clsx from "clsx";
import type { BackupRow, QuarantineItem, UnknownMarketRow } from "@edgefut/contracts";
import { MARKET_CATEGORY_LABELS } from "@edgefut/contracts";
import { AlertTriangle, Archive, Database, Download, FileText, Fingerprint, HardDrive, RefreshCw, RotateCcw, ShieldAlert, Tags } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Card, Counter, Empty, ErrorBox, Loading, Modal, PageHeader, SectionTitle, Segmented } from "@/components/ui";
import { api } from "@/lib/api";

import { EdgeStateChip, MaturityChip, bytes } from "./FlywheelPage";

type Tab = "settlement" | "unknown" | "quarantine" | "storage" | "identity" | "daily";

/**
 * Sistema → Dados (it. 5): quarentena (§53), unknown markets (§56), storage/backup/export (§49–52),
 * settlement audit + MARKET DATA COVERAGE (§12–13), identidade + correções manuais (§58–59), relatório diário (§60).
 */
export function DataPages() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "settlement";
  const setTab = (t: Tab) =>
    setParams((p) => {
      p.set("tab", t);
      return p;
    });
  return (
    <div className="space-y-5">
      <PageHeader title="Dados" subtitle="Auditoria do dataset Superbet: liquidação, mercados desconhecidos, quarentena, armazenamento, identidade e relatório diário. Tudo aqui é trilha — nada é apagado em silêncio." />
      <Segmented
        value={tab}
        onChange={setTab}
        options={[
          { value: "settlement", label: "Liquidação" },
          { value: "unknown", label: "Mercados desconhecidos" },
          { value: "quarantine", label: "Quarentena" },
          { value: "storage", label: "Armazenamento" },
          { value: "identity", label: "Identidade" },
          { value: "daily", label: "Relatório diário" },
        ]}
      />
      {tab === "settlement" && <SettlementTab />}
      {tab === "unknown" && <UnknownMarketsTab />}
      {tab === "quarantine" && <QuarantineTab />}
      {tab === "storage" && <StorageTab />}
      {tab === "identity" && <IdentityTab />}
      {tab === "daily" && <DailyReportTab />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Liquidação (§11–13)
// ---------------------------------------------------------------------------
const SETTLE_CLS: Record<string, string> = {
  WON: "bg-success-50 text-success",
  LOST: "bg-gray-100 text-ink-2",
  VOID: "bg-info-50 text-info",
  UNSETTLED_DATA_MISSING: "bg-warning-50 text-warning",
  UNSUPPORTED: "bg-gray-100 text-ink-3",
  ERROR: "bg-danger-50 text-danger",
  PENDING: "bg-gray-100 text-ink-3",
};
export function SettleChip({ s }: { s: string | null | undefined }) {
  if (!s) return <span className="chip bg-gray-100 text-ink-3">—</span>;
  return <span className={clsx("chip", SETTLE_CLS[s] ?? "bg-gray-100 text-ink-2")}>{s}</span>;
}

function SettlementTab() {
  const q = useQuery({ queryKey: ["flywheel", "settlement-audit"], queryFn: api.settlementAudit, refetchInterval: 120000 });
  const [eventId, setEventId] = useState("");
  const ev = useQuery({ queryKey: ["flywheel", "settlement-event", eventId], queryFn: () => api.settlementEvent(Number(eventId)), enabled: /^\d+$/.test(eventId) });
  if (q.isLoading) return <Loading label="Auditando liquidação…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const a = q.data!;
  const e = a.events;
  const mr = a.match_result_vs_secondary;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
        <Counter label="Jogos terminados" value={int(e.finished)} hint="eventos com status FINISHED no período" />
        <Counter label="Liquidados" value={int(e.settled)} accent="text-success" hint="todas as seleções com WON/LOST/VOID" />
        <Counter label="Pendentes" value={int(e.pending)} hint="aguardando resultado" />
        <Counter label="Sem resultado" value={int(e.missing_result)} accent="text-warning" hint="UNSETTLED_DATA_MISSING — nunca assumimos loss" />
        <Counter label="Sem stats de mercado" value={int(e.missing_market_stats)} accent="text-warning" hint="escanteios/cartões ausentes no payload" />
        <Counter label="Erros" value={int(e.errors)} accent={e.errors > 0 ? "text-danger" : undefined} />
      </div>

      <Card>
        <SectionTitle title="Resultado do jogo vs mercados secundários" subtitle="A liquidação de 1X2/Over-Under/BTTS depende só do placar; escanteios e cartões dependem de metadata que a Superbet nem sempre publica. Cobertura menor nos secundários é informação, não falha." />
        <div className="grid gap-3 md:grid-cols-2">
          {(
            [
              ["Resultado do jogo (1X2 · O/U · BTTS · handicap)", mr.match_result],
              ["Secundários (escanteios · cartões)", mr.secondary],
            ] as const
          ).map(([label, v]) => {
            const tot = v.settled + v.missing;
            const p = tot ? (v.settled / tot) * 100 : null;
            return (
              <div key={label} className="rounded-lg border border-line p-3 text-xs">
                <div className="font-semibold text-ink">{label}</div>
                <div className="mt-1 flex items-center gap-3 text-ink-2">
                  <span>liquidadas <b className="text-success">{int(v.settled)}</b></span>
                  <span>sem dados <b className="text-warning">{int(v.missing)}</b></span>
                  <span className="ml-auto font-mono">{p === null ? "—" : pct(p, 0)}</span>
                </div>
                <div className="mt-2 h-1.5 w-full overflow-hidden rounded bg-gray-100">
                  <div className="h-full bg-success" style={{ width: `${p ?? 0}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card>
        <SectionTitle title="MARKET DATA COVERAGE" subtitle="Por mercado: quantas seleções foram liquidadas e quantas ficaram sem dados. Versão de liquidação: " />
        <div className="-mt-2 mb-2 text-[11px] text-ink-3">
          settlement <span className="font-mono">{a.settlement_version}</span> · gerado {fmtDateTime(a.generated_at)}
        </div>
        {a.market_data_coverage.length === 0 ? (
          <Empty title="Nenhuma liquidação ainda" detail="A liquidação corre a cada 30 min para jogos terminados. Sem jogos terminados no dataset, esta tabela fica vazia — isso é esperado num dataset jovem." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-[10px] uppercase text-ink-3">
                <tr>
                  <th className="py-1 pr-3">Mercado</th>
                  <th className="py-1 pr-3 text-right">Snapshots</th>
                  <th className="py-1 pr-3 text-right">Jogos liquidados</th>
                  <th className="py-1 pr-3 text-right">Liquidadas</th>
                  <th className="py-1 pr-3 text-right">Sem dados</th>
                  <th className="py-1 pr-3 text-right">Void</th>
                  <th className="py-1 pr-3 text-right">Sem suporte</th>
                  <th className="py-1 pr-3 text-right">Erro</th>
                  <th className="py-1 pr-3 text-right">Cobertura</th>
                </tr>
              </thead>
              <tbody>
                {a.market_data_coverage.map((r) => {
                  const tot = r.settled + r.missing;
                  const p = tot ? (r.settled / tot) * 100 : null;
                  return (
                    <tr key={r.market_category} className="border-t border-line">
                      <td className="py-1.5 pr-3 font-medium">{MARKET_CATEGORY_LABELS[r.market_category as keyof typeof MARKET_CATEGORY_LABELS] ?? r.market_category}</td>
                      <td className="py-1.5 pr-3 text-right font-mono">{int(r.snapshots)}</td>
                      <td className="py-1.5 pr-3 text-right font-mono">{int(r.events_settled)}</td>
                      <td className="py-1.5 pr-3 text-right font-mono text-success">{int(r.settled)}</td>
                      <td className="py-1.5 pr-3 text-right font-mono text-warning">{int(r.missing)}</td>
                      <td className="py-1.5 pr-3 text-right font-mono">{int(r.void)}</td>
                      <td className="py-1.5 pr-3 text-right font-mono text-ink-3">{int(r.unsupported)}</td>
                      <td className={clsx("py-1.5 pr-3 text-right font-mono", r.error > 0 && "text-danger")}>{int(r.error)}</td>
                      <td className="py-1.5 pr-3 text-right font-mono">{p === null ? "—" : pct(p, 0)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {a.missing_by_market.length > 0 && (
          <div className="mt-3 text-xs text-ink-2">
            <div className="label mb-1">Campos ausentes por mercado</div>
            <div className="flex flex-wrap gap-1.5">
              {a.missing_by_market.map((m, i) => (
                <span key={i} className="chip bg-warning-50 text-warning" title={m.missing_fields}>
                  {MARKET_CATEGORY_LABELS[m.market_category as keyof typeof MARKET_CATEGORY_LABELS] ?? m.market_category}: {m.missing_fields} × {int(m.n)}
                </span>
              ))}
            </div>
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle title="Liquidação por jogo" subtitle="Consulte a trilha de liquidação de um evento pelo id (mesmo id de Jogos)." />
        <div className="flex items-center gap-2">
          <input className="input max-w-xs" placeholder="event id" value={eventId} onChange={(e) => setEventId(e.target.value.trim())} />
          {ev.isFetching && <RefreshCw size={14} className="animate-spin text-ink-3" />}
        </div>
        {ev.data && (
          <div className="mt-3 overflow-x-auto">
            {ev.data.items.length === 0 ? (
              <div className="text-xs text-ink-3">Nenhuma seleção liquidada para o evento {ev.data.event_id}.</div>
            ) : (
              <table className="w-full text-xs">
                <thead className="text-left text-[10px] uppercase text-ink-3">
                  <tr>
                    <th className="py-1 pr-3">Mercado</th>
                    <th className="py-1 pr-3">Seleção</th>
                    <th className="py-1 pr-3 text-right">Linha</th>
                    <th className="py-1 pr-3">Status</th>
                    <th className="py-1 pr-3">Fonte</th>
                    <th className="py-1 pr-3">Campos ausentes</th>
                    <th className="py-1 pr-3">Liquidado em</th>
                  </tr>
                </thead>
                <tbody>
                  {ev.data.items.map((it, i) => (
                    <tr key={i} className="border-t border-line">
                      <td className="py-1 pr-3 font-mono">{String(it.canonical_market_id ?? it.market_category ?? "")}</td>
                      <td className="py-1 pr-3 font-mono">{String(it.selection_id ?? "")}</td>
                      <td className="py-1 pr-3 text-right font-mono">{it.line == null ? "—" : String(it.line)}</td>
                      <td className="py-1 pr-3">
                        <SettleChip s={String(it.status ?? "")} />
                      </td>
                      <td className="py-1 pr-3 text-ink-2">{String(it.result_source ?? "—")}</td>
                      <td className="py-1 pr-3 text-warning">{it.missing_fields ? String(it.missing_fields) : "—"}</td>
                      <td className="py-1 pr-3 text-ink-3">{it.settled_at ? fmtDateTime(String(it.settled_at)) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mercados desconhecidos (§54–56)
// ---------------------------------------------------------------------------
function UnknownMarketsTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["flywheel", "mapping"], queryFn: api.flywheelMapping, refetchInterval: 120000 });
  const [target, setTarget] = useState<UnknownMarketRow | null>(null);
  const decide = useMutation({
    mutationFn: (p: { id: number; status: "OUT_OF_SCOPE" | "UNKNOWN" | "AMBIGUOUS"; reason: string }) => api.decideMapping(p.id, { status: p.status, reason: p.reason }),
    onSuccess: () => {
      setTarget(null);
      qc.invalidateQueries({ queryKey: ["flywheel"] });
    },
  });
  if (q.isLoading) return <Loading label="Lendo registry de mercados…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const m = q.data!;
  const st = m.by_status;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <Counter label="Market ids vistos" value={int(m.market_ids)} hint="ids distintos da Superbet observados no raw" />
        <Counter label="Mapeados" value={int(st.MAPPED ?? 0)} accent="text-success" hint={m.mapped_pct === null ? "—" : `${pct(m.mapped_pct, 1)} das ocorrências`} />
        <Counter label="Desconhecidos" value={int(st.UNKNOWN ?? 0)} accent={(st.UNKNOWN ?? 0) > 0 ? "text-warning" : undefined} hint={m.unknown_pct === null ? "—" : `${pct(m.unknown_pct, 1)} das ocorrências`} />
        <Counter label="Ambíguos" value={int(st.AMBIGUOUS ?? 0)} hint="não mapear automaticamente" />
        <Counter label="Fora de escopo" value={int(st.OUT_OF_SCOPE ?? 0)} hint="ex.: mercados de jogador (§14)" />
      </div>

      <Card>
        <SectionTitle
          title="Mercados desconhecidos"
          subtitle="Nunca ignorados em silêncio: cada market_id que a canonicalização não reconhece fica aqui até uma decisão manual auditada. Mapear exige código (MARKET_MAPPING.md) — pela UI só se marca fora de escopo/ambíguo."
          right={<Tags size={14} className="text-ink-3" />}
        />
        {m.unknown.length === 0 ? (
          <Empty title="Nenhum mercado desconhecido" detail="Todos os market_ids observados estão mapeados, marcados como ambíguos ou fora de escopo." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-[10px] uppercase text-ink-3">
                <tr>
                  <th className="py-1 pr-3">Market id</th>
                  <th className="py-1 pr-3">Nome Superbet</th>
                  <th className="py-1 pr-3">Status</th>
                  <th className="py-1 pr-3 text-right">Ocorrências</th>
                  <th className="py-1 pr-3 text-right">Jogos</th>
                  <th className="py-1 pr-3">Seleções (amostra)</th>
                  <th className="py-1 pr-3">Specifiers</th>
                  <th className="py-1 pr-3">Visto</th>
                  <th className="py-1 pr-3" />
                </tr>
              </thead>
              <tbody>
                {m.unknown.map((r) => (
                  <tr key={r.superbet_market_id} className="border-t border-line">
                    <td className="py-1.5 pr-3 font-mono">{r.superbet_market_id}</td>
                    <td className="py-1.5 pr-3 font-medium">{r.market_name ?? "—"}</td>
                    <td className="py-1.5 pr-3">
                      <span className={clsx("chip", r.status === "AMBIGUOUS" ? "bg-info-50 text-info" : "bg-warning-50 text-warning")}>{r.status}</span>
                    </td>
                    <td className="py-1.5 pr-3 text-right font-mono">{int(r.occurrences)}</td>
                    <td className="py-1.5 pr-3 text-right font-mono">{int(r.events_seen)}</td>
                    <td className="max-w-[260px] truncate py-1.5 pr-3 text-ink-2" title={(r.sample_selections ?? []).join(" · ")}>
                      {(r.sample_selections ?? []).slice(0, 4).join(" · ") || "—"}
                    </td>
                    <td className="py-1.5 pr-3 font-mono text-ink-3">{(r.specifier_keys ?? []).join(",") || "—"}</td>
                    <td className="py-1.5 pr-3 text-ink-3" title={`primeiro ${fmtDateTime(r.first_seen_at)} · último ${fmtDateTime(r.last_seen_at)}`}>
                      {relativeTime(r.last_seen_at)}
                    </td>
                    <td className="py-1.5 pr-3 text-right">
                      <button className="btn-outline text-xs" onClick={() => setTarget(r)}>
                        Decidir
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Motivos fora de escopo" subtitle="Padrões declarados em código (nunca inferidos)" />
          {Object.keys(m.out_of_scope_reasons).length === 0 ? (
            <div className="text-xs text-ink-3">—</div>
          ) : (
            <ul className="space-y-1 text-xs">
              {Object.entries(m.out_of_scope_reasons).map(([k, v]) => (
                <li key={k} className="flex justify-between">
                  <span>{k}</span>
                  <span className="font-mono">{int(v)}</span>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-3 flex flex-wrap gap-1">
            {m.out_of_scope_patterns.map((p, i) => (
              <span key={i} className="chip bg-gray-100 font-mono text-[10px] text-ink-2" title={p.reason}>
                {p.pattern}
              </span>
            ))}
          </div>
        </Card>
        <Card>
          <SectionTitle title="Spec de mapeamento (código)" subtitle={`${m.mapped_spec.length} market_ids mapeados para ${new Set(m.mapped_spec.map((s) => s.market_category)).size} categorias canônicas`} />
          <div className="max-h-72 overflow-y-auto">
            <table className="w-full text-xs">
              <tbody>
                {m.mapped_spec.map((s) => (
                  <tr key={`${s.superbet_market_id}-${s.side ?? ""}`} className="border-t border-line">
                    <td className="py-1 pr-2 font-mono">{s.superbet_market_id}</td>
                    <td className="py-1 pr-2">{s.label}</td>
                    <td className="py-1 pr-2 text-ink-2">{MARKET_CATEGORY_LABELS[s.market_category] ?? s.market_category}</td>
                    <td className="py-1 pr-2 font-mono text-ink-3">
                      {s.kind}
                      {s.side ? `·${s.side}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {target && <DecisionModal row={target} pending={decide.isPending} error={decide.error} onClose={() => setTarget(null)} onDecide={(status, reason) => decide.mutate({ id: target.superbet_market_id, status, reason })} />}
    </div>
  );
}

function DecisionModal({ row, pending, error, onClose, onDecide }: { row: UnknownMarketRow; pending: boolean; error: unknown; onClose: () => void; onDecide: (s: "OUT_OF_SCOPE" | "UNKNOWN" | "AMBIGUOUS", reason: string) => void }) {
  const [status, setStatus] = useState<"OUT_OF_SCOPE" | "UNKNOWN" | "AMBIGUOUS">("OUT_OF_SCOPE");
  const [reason, setReason] = useState("");
  return (
    <Modal title={`Decisão manual · market ${row.superbet_market_id} — ${row.market_name ?? "?"}`} onClose={onClose}>
      <p className="mb-3 text-xs text-ink-2">Esta decisão fica registrada em manual_correction (quem/quando/por quê). Mapear para uma categoria canônica exige alteração em código e revisão — não é oferecido aqui, para evitar mapeamento ambíguo.</p>
      <label className="mb-2 block text-xs">
        <span className="label">Status</span>
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
          <option value="OUT_OF_SCOPE">OUT_OF_SCOPE — fora do escopo (ex.: jogador, especial)</option>
          <option value="AMBIGUOUS">AMBIGUOUS — semântica incerta, não mapear</option>
          <option value="UNKNOWN">UNKNOWN — reabrir para análise</option>
        </select>
      </label>
      <label className="mb-3 block text-xs">
        <span className="label">Motivo (obrigatório, ≥ 5 caracteres)</span>
        <textarea className="input min-h-[72px]" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="ex.: mercado de jogador — fora do escopo (§14)" />
      </label>
      {error ? <div className="mb-2 text-xs text-danger">{String((error as Error).message ?? error)}</div> : null}
      <div className="flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>
          Cancelar
        </button>
        <button className="btn-primary" disabled={pending || reason.trim().length < 5} onClick={() => onDecide(status, reason.trim())}>
          Registrar decisão
        </button>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Quarentena (§53)
// ---------------------------------------------------------------------------
function QuarantineTab() {
  const qc = useQueryClient();
  const [status, setStatus] = useState<"OPEN" | "RESOLVED" | "IGNORED" | "ALL">("OPEN");
  const q = useQuery({ queryKey: ["flywheel", "quarantine", status], queryFn: () => api.quarantine(status), refetchInterval: 60000 });
  const [sel, setSel] = useState<QuarantineItem | null>(null);
  const raw = useQuery({ queryKey: ["flywheel", "raw", sel?.raw_snapshot_id], queryFn: () => api.rawSnapshot(sel!.raw_snapshot_id!), enabled: !!sel?.raw_snapshot_id });
  const resolve = useMutation({
    mutationFn: (p: { id: number; status: "RESOLVED" | "IGNORED" | "OPEN"; note: string }) => api.resolveQuarantine(p.id, { status: p.status, note: p.note }),
    onSuccess: () => {
      setSel(null);
      qc.invalidateQueries({ queryKey: ["flywheel"] });
    },
  });
  if (q.isLoading) return <Loading label="Abrindo quarentena…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  return (
    <div className="space-y-4">
      <Card className="border-l-4 border-l-warning">
        <div className="flex items-start gap-3 text-sm">
          <ShieldAlert className="mt-0.5 shrink-0 text-warning" size={18} />
          <div>
            <div className="font-bold">Dados em quarentena</div>
            <p className="mt-0.5 text-xs text-ink-2">{d.note}</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.entries(d.open_by_reason).length === 0 ? (
                <span className="chip bg-success-50 text-success">0 abertos</span>
              ) : (
                Object.entries(d.open_by_reason).map(([k, v]) => (
                  <span key={k} className="chip bg-warning-50 text-warning">
                    {k} × {int(v)}
                  </span>
                ))
              )}
            </div>
          </div>
        </div>
      </Card>

      <div className="flex items-center justify-between gap-3">
        <Segmented
          value={status}
          onChange={setStatus}
          options={[
            { value: "OPEN", label: "Abertos" },
            { value: "RESOLVED", label: "Resolvidos" },
            { value: "IGNORED", label: "Ignorados" },
            { value: "ALL", label: "Todos" },
          ]}
        />
        <span className="text-xs text-ink-3">{int(d.items.length)} itens</span>
      </div>

      {d.items.length === 0 ? (
        <Empty title="Nada em quarentena neste filtro" detail="Payloads que falham integridade (hash, kickoff inconsistente, schema inesperado, evento desconhecido) entram aqui em vez de serem descartados." />
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] uppercase text-ink-3">
              <tr>
                <th className="px-3 py-2">#</th>
                <th className="px-3 py-2">Motivo</th>
                <th className="px-3 py-2">Evento</th>
                <th className="px-3 py-2">Raw</th>
                <th className="px-3 py-2">Detalhe</th>
                <th className="px-3 py-2">Quando</th>
                <th className="px-3 py-2">Estado</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {d.items.map((it) => (
                <tr key={it.id} className="border-t border-line align-top">
                  <td className="px-3 py-2 font-mono text-ink-3">{it.id}</td>
                  <td className="px-3 py-2">
                    <span className="chip bg-warning-50 text-warning">{it.reason}</span>
                  </td>
                  <td className="px-3 py-2 font-mono">{it.event_id ?? "—"}</td>
                  <td className="px-3 py-2 font-mono">{it.raw_snapshot_id ?? "—"}</td>
                  <td className="max-w-[360px] px-3 py-2 text-ink-2">{it.detail}</td>
                  <td className="px-3 py-2 text-ink-3" title={fmtDateTime(it.created_at)}>
                    {relativeTime(it.created_at)}
                  </td>
                  <td className="px-3 py-2">
                    <span className={clsx("chip", it.resolver_status === "OPEN" ? "bg-warning-50 text-warning" : it.resolver_status === "RESOLVED" ? "bg-success-50 text-success" : "bg-gray-100 text-ink-2")}>{it.resolver_status}</span>
                    {it.resolution_note && <div className="mt-0.5 max-w-[200px] text-[10px] text-ink-3">{it.resolution_note}</div>}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button className="btn-outline text-xs" onClick={() => setSel(it)}>
                      Inspecionar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {sel && <QuarantineModal item={sel} raw={raw.data} pending={resolve.isPending} onClose={() => setSel(null)} onResolve={(s, note) => resolve.mutate({ id: sel.id, status: s, note })} />}
    </div>
  );
}

function QuarantineModal({ item, raw, pending, onClose, onResolve }: { item: QuarantineItem; raw: Record<string, unknown> | undefined; pending: boolean; onClose: () => void; onResolve: (s: "RESOLVED" | "IGNORED" | "OPEN", note: string) => void }) {
  const [note, setNote] = useState("");
  return (
    <Modal title={`Quarentena #${item.id} · ${item.reason}`} onClose={onClose} wide>
      <div className="space-y-3 text-xs">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <KV k="Evento" v={item.event_id ?? "—"} />
          <KV k="Raw snapshot" v={item.raw_snapshot_id ?? "—"} />
          <KV k="Hash" v={<span className="font-mono">{item.payload_hash?.slice(0, 16) ?? "—"}</span>} />
          <KV k="Criado" v={fmtDateTime(item.created_at)} />
        </div>
        <div>
          <div className="label">Detalhe</div>
          <div className="text-ink-2">{item.detail}</div>
        </div>
        {item.payload_excerpt && (
          <div>
            <div className="label">Trecho do payload (imutável)</div>
            <pre className="max-h-48 overflow-auto rounded-lg bg-bg p-2 font-mono text-[10px] text-ink-2">{JSON.stringify(item.payload_excerpt, null, 1)}</pre>
          </div>
        )}
        {raw && (
          <div>
            <div className="label">Raw snapshot (cabeçalho)</div>
            <pre className="max-h-48 overflow-auto rounded-lg bg-bg p-2 font-mono text-[10px] text-ink-2">{JSON.stringify(raw, null, 1)}</pre>
          </div>
        )}
        <label className="block">
          <span className="label">Nota de resolução (obrigatória)</span>
          <textarea className="input min-h-[64px]" value={note} onChange={(e) => setNote(e.target.value)} placeholder="ex.: kickoff corrigido pela Superbet no snapshot seguinte; item mantido para auditoria" />
        </label>
        <div className="flex flex-wrap justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>
            Fechar
          </button>
          {item.resolver_status !== "OPEN" && (
            <button className="btn-outline" disabled={pending || note.trim().length < 3} onClick={() => onResolve("OPEN", note.trim())}>
              Reabrir
            </button>
          )}
          <button className="btn-outline" disabled={pending || note.trim().length < 3} onClick={() => onResolve("IGNORED", note.trim())}>
            Ignorar (mantém registro)
          </button>
          <button className="btn-primary" disabled={pending || note.trim().length < 3} onClick={() => onResolve("RESOLVED", note.trim())}>
            Marcar resolvido
          </button>
        </div>
        <p className="text-[10px] text-ink-3">Resolver ou ignorar nunca apaga o item nem o raw associado — só muda o estado do resolvedor e grava a nota.</p>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Armazenamento · backup · export (§49–52)
// ---------------------------------------------------------------------------
function StorageTab() {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["flywheel", "storage"], queryFn: api.storage, refetchInterval: 120000 });
  const bk = useQuery({ queryKey: ["flywheel", "backups"], queryFn: api.backups, refetchInterval: 120000 });
  const ex = useQuery({ queryKey: ["flywheel", "exports"], queryFn: api.exports, refetchInterval: 120000 });
  const [restoreTarget, setRestoreTarget] = useState<BackupRow | null>(null);
  const [exportFmt, setExportFmt] = useState<"parquet" | "csv">("parquet");
  const [layers, setLayers] = useState<string[]>(["raw", "normalized", "derived"]);
  const [msg, setMsg] = useState<string | null>(null);
  const inval = () => qc.invalidateQueries({ queryKey: ["flywheel"] });
  const runBackup = useMutation({
    mutationFn: api.runBackup,
    onSuccess: (r) => {
      setMsg(`Backup criado: ${String(r.file ?? "")} (${String(r.integrity ?? "")})`);
      inval();
    },
    onError: (e) => setMsg(`Falha no backup: ${(e as Error).message}`),
  });
  const restore = useMutation({
    mutationFn: (file: string) => api.restoreBackup(file),
    onSuccess: (r) => {
      setRestoreTarget(null);
      setMsg(`Restaurado ${String(r.restored_from ?? "")}. Cópia de segurança pré-restore: ${String(r.safety_copy ?? "—")}. ${String(r.note ?? "")}`);
      qc.invalidateQueries();
    },
    onError: (e) => setMsg(`Falha ao restaurar: ${(e as Error).message}`),
  });
  const runExport = useMutation({
    mutationFn: () => api.runExport({ layers, format: exportFmt }),
    onSuccess: (r) => {
      setMsg(`Export gerado em ${String(r.dir ?? r.path ?? "")}`);
      inval();
    },
    onError: (e) => setMsg(`Falha no export: ${(e as Error).message}`),
  });
  if (st.isLoading) return <Loading label="Medindo armazenamento…" />;
  if (st.isError) return <ErrorBox error={st.error} retry={() => st.refetch()} />;
  const s = st.data!;
  const toggleLayer = (l: string) => setLayers((ls) => (ls.includes(l) ? ls.filter((x) => x !== l) : [...ls, l]));
  return (
    <div className="space-y-4">
      {msg && (
        <div className="rounded-lg border border-line bg-card px-3 py-2 text-xs text-ink-2">
          {msg}{" "}
          <button className="btn-ghost text-xs" onClick={() => setMsg(null)}>
            ok
          </button>
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
        <Counter label="SQLite" value={bytes(s.sqlite.bytes)} hint={`${s.sqlite.path} · WAL ${bytes(s.sqlite.wal_bytes)}`} />
        <Counter label="Raw comprimido" value={bytes(s.raw_payloads.compressed_bytes)} hint={`${int(s.raw_payloads.snapshots)} snapshots · ${bytes(s.raw_payloads.uncompressed_bytes)} bruto · razão ${s.raw_payloads.compression_ratio?.toFixed(1) ?? "—"}×`} />
        <Counter label="Crescimento / dia" value={bytes(s.growth.raw_bytes_per_day)} hint={`${int(Math.round(s.growth.raw_snapshots_per_day))} snapshots/dia · ${s.raw_payloads.days_active ?? "—"} dias ativos`} />
        <Counter label="Projeção 30 d / 1 ano" value={`${bytes(s.growth.projection_30d_bytes)} / ${bytes(s.growth.projection_365d_bytes)}`} hint="só o raw, no ritmo atual" />
        <Counter label="Parquet" value={bytes(s.parquet.bytes)} hint={s.parquet.path} />
        <Counter label="Cache HTTP" value={bytes(s.cache.bytes)} hint={`${int(s.cache.files)} arquivos`} />
        <Counter label="Backups" value={`${int(s.backups.count)} · ${bytes(s.backups.bytes)}`} hint={s.backups.latest ? `último ${relativeTime(s.backups.latest.created_at)}` : "nenhum ainda"} />
      </div>

      <Card className="border-l-4 border-l-info">
        <div className="flex items-start gap-3 text-xs">
          <HardDrive className="mt-0.5 shrink-0 text-info" size={16} />
          <div>
            <div className="font-bold text-sm">Política de retenção do raw</div>
            <p className="mt-0.5 text-ink-2">{s.raw_retention_policy}</p>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-ink-3">
              {Object.entries(s.counts).map(([k, v]) => (
                <span key={k}>
                  {k}: <b className="font-mono text-ink-2">{int(v)}</b>
                </span>
              ))}
            </div>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle
            title="Backups"
            subtitle={`Retenção ${Object.entries(s.backups.retention).map(([k, v]) => `${v} ${k}`).join(" · ")} · diário automático · sqlite3 backup API + integrity_check`}
            right={
              <button className="btn-primary text-xs" onClick={() => runBackup.mutate()} disabled={runBackup.isPending}>
                <Archive size={13} /> Backup agora
              </button>
            }
          />
          {bk.isLoading ? (
            <Loading />
          ) : (bk.data?.backups.length ?? 0) === 0 ? (
            <Empty title="Nenhum backup" detail="O primeiro backup automático corre ~20 min após o arranque, depois a cada 24 h." />
          ) : (
            <table className="w-full text-xs">
              <thead className="text-left text-[10px] uppercase text-ink-3">
                <tr>
                  <th className="py-1 pr-2">Arquivo</th>
                  <th className="py-1 pr-2">Quando</th>
                  <th className="py-1 pr-2 text-right">Tamanho</th>
                  <th className="py-1 pr-2">Integridade</th>
                  <th className="py-1 pr-2">Tier</th>
                  <th className="py-1 pr-2" />
                </tr>
              </thead>
              <tbody>
                {bk.data!.backups.map((b) => (
                  <tr key={b.file} className="border-t border-line">
                    <td className="max-w-[220px] truncate py-1 pr-2 font-mono" title={`${bk.data!.dir}/${b.file} · motivo ${b.reason ?? "—"} · schema v${b.schema_version ?? "?"}`}>
                      {b.file}
                    </td>
                    <td className="py-1 pr-2 text-ink-2" title={fmtDateTime(b.created_at)}>
                      {relativeTime(b.created_at)}
                    </td>
                    <td className="py-1 pr-2 text-right font-mono">{bytes(b.bytes)}</td>
                    <td className="py-1 pr-2">
                      <span className={clsx("chip", b.integrity === "ok" ? "bg-success-50 text-success" : "bg-danger-50 text-danger")}>{b.integrity ?? "?"}</span>
                    </td>
                    <td className="py-1 pr-2 text-ink-3">{b.tier ?? "—"}</td>
                    <td className="py-1 pr-2 text-right">
                      <button className="btn-ghost text-xs" onClick={() => setRestoreTarget(b)}>
                        <RotateCcw size={12} /> Restaurar
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card>
          <SectionTitle
            title="Exportar dataset"
            subtitle="CSV ou Parquet por camada (raw · normalized · derived). Exportar nunca altera o banco."
            right={
              <button className="btn-primary text-xs" onClick={() => runExport.mutate()} disabled={runExport.isPending || layers.length === 0}>
                <Download size={13} /> Exportar
              </button>
            }
          />
          <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
            <Segmented
              value={exportFmt}
              onChange={setExportFmt}
              options={[
                { value: "parquet", label: "Parquet" },
                { value: "csv", label: "CSV" },
              ]}
            />
            {["raw", "normalized", "derived"].map((l) => (
              <label key={l} className="inline-flex items-center gap-1.5">
                <input type="checkbox" checked={layers.includes(l)} onChange={() => toggleLayer(l)} /> {l}
              </label>
            ))}
          </div>
          {ex.isLoading ? (
            <Loading />
          ) : (ex.data?.exports.length ?? 0) === 0 ? (
            <div className="text-xs text-ink-3">Nenhum export ainda. Destino: {ex.data?.dir}</div>
          ) : (
            <table className="w-full text-xs">
              <thead className="text-left text-[10px] uppercase text-ink-3">
                <tr>
                  <th className="py-1 pr-2">Pasta</th>
                  <th className="py-1 pr-2">Formato</th>
                  <th className="py-1 pr-2">Arquivos</th>
                  <th className="py-1 pr-2 text-right">Tamanho</th>
                </tr>
              </thead>
              <tbody>
                {ex.data!.exports.map((e) => (
                  <tr key={e.dir} className="border-t border-line align-top">
                    <td className="py-1 pr-2 font-mono" title={e.path}>
                      {e.dir}
                    </td>
                    <td className="py-1 pr-2">{e.format ?? "—"}</td>
                    <td className="py-1 pr-2 text-ink-2">
                      {e.files
                        ? Object.entries(e.files).map(([k, f]) => (
                            <div key={k}>
                              {k}: {int(f.rows)} linhas
                            </div>
                          ))
                        : "—"}
                    </td>
                    <td className="py-1 pr-2 text-right font-mono">{bytes(e.bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      {restoreTarget && (
        <Modal title="Restaurar backup" onClose={() => setRestoreTarget(null)}>
          <div className="space-y-3 text-sm">
            <div className="flex items-start gap-2 rounded-lg bg-warning-50 p-3 text-xs text-warning">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              <div>
                O banco atual será substituído por <b className="font-mono">{restoreTarget.file}</b> ({fmtDateTime(restoreTarget.created_at)}, {bytes(restoreTarget.bytes)}). Antes disso o engine grava uma cópia de segurança <i>pre-restore-*</i> do banco atual, para os jobs, verifica a integridade do backup, restaura e reaplica migrações. Snapshots coletados depois deste backup deixam de existir no banco ativo (ficam apenas na cópia pre-restore).
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => setRestoreTarget(null)}>
                Cancelar
              </button>
              <button className="btn-primary bg-danger hover:bg-danger" disabled={restore.isPending} onClick={() => restore.mutate(restoreTarget.file)}>
                {restore.isPending ? "Restaurando…" : "Confirmar restauração"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Identidade e correções manuais (§10, §58–59)
// ---------------------------------------------------------------------------
function IdentityTab() {
  const q = useQuery({ queryKey: ["flywheel", "identity"], queryFn: api.identityAudit, refetchInterval: 300000 });
  if (q.isLoading) return <Loading label="Auditando identidades…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const a = q.data!;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Counter label="Duplicados fundidos" value={int(a.events.duplicates_merged)} hint="eventos com mais de um id externo resolvidos para o mesmo canonical id" />
        <Counter label="Sem canonical id" value={int(a.events.without_canonical_id)} accent={a.events.without_canonical_id > 0 ? "text-warning" : undefined} />
        <Counter label="Kickoff inconsistente" value={int(a.events.kickoff_inconsistent_quarantined)} accent={a.events.kickoff_inconsistent_quarantined > 0 ? "text-warning" : undefined} hint="em quarentena, não sobrescrito" />
        <Counter label="Payloads de evento desconhecido" value={int(a.events.unknown_event_payloads)} hint="snapshot recebido sem evento correspondente" />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Competições (30 d)" subtitle="Eventos por competição normalizada" right={<Fingerprint size={14} className="text-ink-3" />} />
          {a.competitions_30d.length === 0 ? (
            <div className="text-xs text-ink-3">—</div>
          ) : (
            <div className="max-h-80 overflow-y-auto">
              <table className="w-full text-xs">
                <tbody>
                  {a.competitions_30d.map((c, i) => (
                    <tr key={i} className="border-t border-line">
                      <td className="py-1 pr-2">{c.competition_name ?? "(sem nome)"}</td>
                      <td className="py-1 text-right font-mono">{int(c.events)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card>
          <SectionTitle title="Identidade de jogador" subtitle={a.player_identity.note} />
          <div className="flex flex-wrap gap-1.5 text-xs">
            {Object.entries(a.player_identity.by_confidence).length === 0 ? (
              <span className="chip bg-gray-100 text-ink-3">sem seleções de jogador (mercados de jogador fora de escopo · §14)</span>
            ) : (
              Object.entries(a.player_identity.by_confidence).map(([k, v]) => (
                <span key={k} className="chip bg-gray-100 text-ink-2">
                  {k}: {int(v)}
                </span>
              ))
            )}
          </div>
        </Card>
      </div>
      <Card>
        <SectionTitle title="Correções manuais" subtitle="Trilha completa: entidade, campo, antes/depois, motivo. Nunca se altera sem registro (§59)." />
        {a.manual_corrections.length === 0 ? (
          <div className="text-xs text-ink-3">Nenhuma correção manual registrada.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-[10px] uppercase text-ink-3">
                <tr>
                  <th className="py-1 pr-3">#</th>
                  <th className="py-1 pr-3">Entidade</th>
                  <th className="py-1 pr-3">Campo</th>
                  <th className="py-1 pr-3">Antes</th>
                  <th className="py-1 pr-3">Depois</th>
                  <th className="py-1 pr-3">Motivo</th>
                  <th className="py-1 pr-3">Quando</th>
                </tr>
              </thead>
              <tbody>
                {a.manual_corrections.map((c) => (
                  <tr key={c.id} className="border-t border-line">
                    <td className="py-1 pr-3 font-mono text-ink-3">{c.id}</td>
                    <td className="py-1 pr-3 font-mono">
                      {c.entity}:{c.entity_id}
                    </td>
                    <td className="py-1 pr-3">{c.field}</td>
                    <td className="py-1 pr-3 font-mono text-ink-2">{JSON.stringify(c.before)}</td>
                    <td className="py-1 pr-3 font-mono">{JSON.stringify(c.after)}</td>
                    <td className="max-w-[280px] py-1 pr-3 text-ink-2">{c.reason}</td>
                    <td className="py-1 pr-3 text-ink-3">{fmtDateTime(c.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Relatório diário (§60) — RESEARCH ONLY
// ---------------------------------------------------------------------------
type DailyReport = {
  generated_at: string;
  window: string;
  research_only: string;
  collector: { health: string; reasons: string[]; raw_snapshots: number; events: number; raw_bytes: number; normalized_rows: number; targets_hit: Record<string, number>; gaps: { started_at: string; minutes: number; events_affected: number }[] };
  coverage_48h: { expected: number; observed: number; coverage_pct: number | null; by_target: { target: string; expected: number; observed: number; coverage_pct: number | null }[] };
  mapping: { mapped_pct: number | null; unknown_pct: number | null; unknown_markets_total: number; new_unknown_24h: { market_id: number; name: string | null; occurrences: number }[] };
  settlement: { by_status: Record<string, number>; by_market: { market_category: string; label: string; n: number }[] };
  quarantine_24h: Record<string, number>;
  model: { shadow_predictions_24h: number; research_signals_24h: number; freeze: string | null };
  markets: { market_category: string; label: string; maturity: string; effective_n: number; unique_events: number; edge_state: string }[];
  storage: { sqlite_bytes?: number; raw_compressed_bytes?: number; raw_bytes_per_day?: number; backups?: number; latest_backup?: string | null; error?: string };
  value_enabled_markets: string[];
  duration_ms: number;
};

function DailyReportTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["flywheel", "report-daily"], queryFn: api.reportDaily, refetchInterval: 300000 });
  const run = useMutation({ mutationFn: () => api.runReport("daily"), onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["flywheel", "report-daily"] }), 2500) });
  if (q.isLoading) return <Loading label="Lendo relatório…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const env = q.data!;
  const r = env.report as DailyReport | null;
  const header = (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="text-xs text-ink-2">
        {r ? (
          <>
            Gerado {fmtDateTime(r.generated_at)} · janela {r.window} · {int(r.duration_ms)} ms
          </>
        ) : (
          "Nenhum relatório diário ainda."
        )}
      </div>
      <button className="btn-outline text-xs" onClick={() => run.mutate()} disabled={run.isPending}>
        <RefreshCw size={13} className={run.isPending ? "animate-spin" : ""} /> Gerar agora
      </button>
    </div>
  );
  if (!r) {
    return (
      <div className="space-y-4">
        {header}
        <Empty title="Relatório diário ainda não gerado" detail="O job corre a cada 24 h (primeira vez ~12 min após o arranque). Você pode gerar agora." icon={<FileText size={28} />} />
      </div>
    );
  }
  const settled = Object.entries(r.settlement.by_status);
  return (
    <div className="space-y-4">
      {header}
      <Card className="border-l-4 border-l-info px-4 py-2 text-xs text-ink-2">{r.research_only}</Card>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <Counter label="Coletor" value={r.collector.health} accent={r.collector.health === "HEALTHY" ? "text-success" : r.collector.health === "DEGRADED" ? "text-warning" : "text-danger"} hint={r.collector.reasons.join(" · ") || "sem alertas"} />
        <Counter label="Snapshots 24 h" value={int(r.collector.raw_snapshots)} hint={`${int(r.collector.events)} jogos · ${bytes(r.collector.raw_bytes)}`} />
        <Counter label="Linhas normalizadas" value={int(r.collector.normalized_rows)} />
        <Counter label="Cobertura 48 h" value={r.coverage_48h.coverage_pct === null ? "—" : pct(r.coverage_48h.coverage_pct, 0)} hint={`${int(r.coverage_48h.observed)} / ${int(r.coverage_48h.expected)} alvos`} />
        <Counter label="Mapeados" value={r.mapping.mapped_pct === null ? "—" : pct(r.mapping.mapped_pct, 1)} hint={`${int(r.mapping.unknown_markets_total)} desconhecidos · ${r.mapping.new_unknown_24h.length} novos`} />
        <Counter label="Liquidadas 24 h" value={int(settled.filter(([k]) => ["WON", "LOST", "VOID"].includes(k)).reduce((a, [, v]) => a + v, 0))} hint={settled.map(([k, v]) => `${k} ${v}`).join(" · ") || "—"} />
        <Counter label="Quarentena 24 h" value={int(Object.values(r.quarantine_24h).reduce((a, b) => a + b, 0))} accent={Object.keys(r.quarantine_24h).length ? "text-warning" : undefined} hint={Object.entries(r.quarantine_24h).map(([k, v]) => `${k} ${v}`).join(" · ") || "nada"} />
        <Counter label="Research signals" value={int(r.model.research_signals_24h)} hint={`${int(r.model.shadow_predictions_24h)} previsões shadow · freeze ${r.model.freeze ?? "—"}`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Alvos temporais atingidos (24 h)" subtitle="T-48h … T-5m + último antes do kickoff" />
          <div className="flex flex-wrap gap-1.5 text-xs">
            {Object.entries(r.collector.targets_hit).map(([k, v]) => (
              <span key={k} className="chip bg-gray-100 text-ink-2">
                {k}: <b className="ml-1 font-mono">{int(v)}</b>
              </span>
            ))}
          </div>
          {r.collector.gaps.length > 0 && (
            <div className="mt-3 text-xs">
              <div className="label mb-1">Gaps de coleta</div>
              {r.collector.gaps.map((g, i) => (
                <div key={i} className="text-warning">
                  {fmtDateTime(g.started_at)} · {int(g.minutes)} min · {int(g.events_affected)} jogos afetados
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card>
          <SectionTitle title="Mercados" subtitle="Maturidade e estado de edge por mercado (nunca VALUE automático)" right={<Database size={14} className="text-ink-3" />} />
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] uppercase text-ink-3">
              <tr>
                <th className="py-1 pr-2">Mercado</th>
                <th className="py-1 pr-2">Maturidade</th>
                <th className="py-1 pr-2 text-right">N efetivo</th>
                <th className="py-1 pr-2 text-right">Jogos</th>
                <th className="py-1 pr-2">Edge</th>
              </tr>
            </thead>
            <tbody>
              {r.markets.map((m) => (
                <tr key={m.market_category} className="border-t border-line">
                  <td className="py-1 pr-2 font-medium">{m.label}</td>
                  <td className="py-1 pr-2">
                    <MaturityChip maturity={m.maturity} />
                  </td>
                  <td className="py-1 pr-2 text-right font-mono">{int(Math.round(m.effective_n))}</td>
                  <td className="py-1 pr-2 text-right font-mono">{int(m.unique_events)}</td>
                  <td className="py-1 pr-2">
                    <EdgeStateChip state={m.edge_state} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 text-[11px] text-ink-3">VALUE habilitado em: {r.value_enabled_markets.length ? r.value_enabled_markets.join(", ") : "nenhum mercado (VALUE_ENABLED=false)"}</div>
        </Card>
      </div>

      {env.history.length > 1 && (
        <Card>
          <SectionTitle title="Histórico" subtitle="Relatórios anteriores (resumo persistido)" />
          <table className="w-full text-xs">
            <tbody>
              {env.history.map((h) => (
                <tr key={h.id} className="border-t border-line">
                  <td className="py-1 pr-3 text-ink-3">{fmtDateTime(h.created_at)}</td>
                  <td className="py-1 pr-3 font-mono text-ink-2">{h.summary ? Object.entries(h.summary).map(([k, v]) => `${k}=${String(v)}`).join(" · ") : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase text-ink-3">{k}</div>
      <div className="text-ink">{v}</div>
    </div>
  );
}
