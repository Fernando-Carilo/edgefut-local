import { useQuery } from "@tanstack/react-query";
import { fmtDateTime, int, num, relativeTime } from "@edgefut/shared";
import clsx from "clsx";
import type { CollectorHealth, FlywheelMarketRow, MarketEdgeStateKind, MarketMaturity, StripItem } from "@edgefut/contracts";
import { Database, FlaskConical, HardDrive, RefreshCw, ShieldAlert } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Card, Counter, Empty, ErrorBox, Loading, PageHeader, SectionTitle } from "@/components/ui";
import { api } from "@/lib/api";

export const bytes = (b: number | null | undefined) => {
  if (b == null) return "—";
  if (b < 1024) return `${b} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`;
  return `${(b / 1024 ** 3).toFixed(2)} GB`;
};

const HEALTH_CLS: Record<CollectorHealth, string> = { HEALTHY: "bg-success-50 text-success", DEGRADED: "bg-warning-50 text-warning", BROKEN: "bg-danger-50 text-danger" };
const MATURITY_CLS: Record<MarketMaturity, string> = { COLLECTING: "bg-gray-100 text-ink-2", EARLY: "bg-info-50 text-info", TESTABLE: "bg-primary-50 text-primary", MATURE: "bg-success-50 text-success" };
const EDGE_CLS: Record<MarketEdgeStateKind, string> = { UNPROVEN: "bg-gray-100 text-ink-3", COLLECTING: "bg-gray-100 text-ink-2", PROMISING: "bg-info-50 text-info", VALIDATED: "bg-success-50 text-success", REJECTED: "bg-danger-50 text-danger" };

export function CollectorHealthChip({ health }: { health: CollectorHealth | string | null | undefined }) {
  if (!health) return null;
  return <span className={clsx("chip", HEALTH_CLS[health as CollectorHealth] ?? "bg-gray-100 text-ink-2")}>{health}</span>;
}
export function MaturityChip({ maturity }: { maturity: MarketMaturity | string }) {
  return <span className={clsx("chip", MATURITY_CLS[maturity as MarketMaturity] ?? "bg-gray-100")}>{maturity}</span>;
}
export function EdgeStateChip({ state }: { state: MarketEdgeStateKind | string }) {
  return <span className={clsx("chip", EDGE_CLS[state as MarketEdgeStateKind] ?? "bg-gray-100")}>{state}</span>;
}

/** §3 — faixa de status do topo (também usada no Início). Nunca mostra VALUE sem validação real. */
export function StatusStrip({ items, compact }: { items: StripItem[]; compact?: boolean }) {
  const tone = { ok: "border-success/40 bg-success-50/50 text-success", warn: "border-warning/40 bg-warning-50/50 text-warning", bad: "border-danger/40 bg-danger-50/50 text-danger" };
  return (
    <div className={clsx("flex flex-wrap gap-2", compact ? "text-[11px]" : "text-xs")}>
      {items.map((it) => (
        <span key={it.key} className={clsx("rounded-md border px-2.5 py-1 font-bold uppercase tracking-wide", tone[it.tone])}>
          {it.text}
        </span>
      ))}
    </div>
  );
}

export function FlywheelPage() {
  const q = useQuery({ queryKey: ["flywheel", "summary"], queryFn: api.flywheelSummary, refetchInterval: 60000 });
  const navigate = useNavigate();
  if (q.isLoading) return <Loading label="Lendo o dataset Superbet…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const maxDay = Math.max(1, ...d.dataset.daily.map((x) => x.snapshots));
  return (
    <div className="space-y-6">
      <PageHeader
        title="Data Flywheel"
        subtitle="O dataset proprietário da Superbet só cresce com o app rodando. Aqui está o que foi coletado de verdade — sem interpolação, sem histórico fabricado."
        right={
          <button className="btn-outline" onClick={() => q.refetch()} disabled={q.isFetching}>
            <RefreshCw size={14} className={q.isFetching ? "animate-spin" : ""} /> Atualizar
          </button>
        }
      />
      <StatusStrip items={d.strip} />

      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
        <Counter label="Eventos únicos" value={int(d.dataset.unique_events)} hint="com snapshot raw" />
        <Counter label="Snapshots raw" value={int(d.dataset.raw_snapshots)} hint={`append-only · desde ${d.dataset.first_snapshot_at ? fmtDateTime(d.dataset.first_snapshot_at) : "—"}`} />
        <Counter label="Odds normalizadas" value={int(d.dataset.normalized_rows)} hint={`normalizador ${d.versions.normalizer}`} />
        <Counter label="Seleções liquidadas" value={int(d.dataset.settled_selections)} hint="WON/LOST/VOID reais" accent={d.dataset.settled_selections ? "text-success" : undefined} onClick={() => navigate("/sistema/dados?tab=settlement")} />
        <Counter label="Mapeamento" value={d.mapping.mapped_pct != null ? `${num(d.mapping.mapped_pct, 1)}%` : "—"} hint={`${int(d.mapping.unknown_count)} mercados desconhecidos`} accent={d.mapping.unknown_count ? "text-warning" : undefined} onClick={() => navigate("/sistema/dados?tab=unknown")} />
        <Counter label="Cobertura 7 d" value={d.coverage.coverage_pct != null ? `${num(d.coverage.coverage_pct, 1)}%` : "—"} hint={`${int(d.coverage.observed)} / ${int(d.coverage.expected)} alvos`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <SectionTitle title="Coletor Superbet" subtitle="Saúde derivada de métricas reais: sucesso HTTP, 429, parse, mapeamento, atraso e mudanças de schema." right={<CollectorHealthChip health={d.collector.health} />} />
          {d.collector.reasons && d.collector.reasons.length > 0 && (
            <ul className="mb-3 list-disc space-y-0.5 pl-4 text-xs text-warning">
              {d.collector.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
          <div className="grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
            <KV k="Última coleta" v={d.collector.last_fetched_at ? relativeTime(d.collector.last_fetched_at) : "nunca"} />
            <KV k="Cadência" v={`${d.collector.cadence_minutes ?? "—"} min`} />
            <KV k="Eventos futuros" v={int(d.collector.upcoming_events ?? 0)} />
            <KV k="Lacunas 7 d" v={int(d.collector.gaps_7d ?? 0)} />
          </div>
          {d.collector.last_hour && d.collector.last_24h && (
            <table className="mt-3 w-full text-xs">
              <thead>
                <tr className="text-left text-ink-3">
                  <th className="py-1 font-semibold">Janela</th>
                  <th>Requisições</th>
                  <th>Sucesso</th>
                  <th>429</th>
                  <th>Bloqueios</th>
                  <th>p50</th>
                  <th>Snapshots</th>
                  <th>Parse</th>
                  <th>Mapeadas</th>
                  <th>Schema</th>
                </tr>
              </thead>
              <tbody>
                {(["last_hour", "last_24h"] as const).map((k) => {
                  const w = d.collector[k]!;
                  return (
                    <tr key={k} className="border-t border-line">
                      <td className="py-1 font-semibold">{k === "last_hour" ? "1 h" : "24 h"}</td>
                      <td>{int(w.requests.requests)}</td>
                      <td>{w.requests.success_rate != null ? `${num(w.requests.success_rate * 100, 1)}%` : "—"}</td>
                      <td className={w.requests.rate_limited ? "text-warning" : ""}>{int(w.requests.rate_limited)}</td>
                      <td className={w.requests.blocked ? "text-danger" : ""}>{int(w.requests.blocked)}</td>
                      <td>{w.requests.latency_ms_p50 != null ? `${w.requests.latency_ms_p50} ms` : "—"}</td>
                      <td>{int(w.raw.snapshots)}</td>
                      <td>{w.raw.parse_rate != null ? `${num(w.raw.parse_rate * 100, 1)}%` : "—"}</td>
                      <td>{w.raw.mapping_coverage != null ? `${num(w.raw.mapping_coverage * 100, 1)}%` : "—"}</td>
                      <td className={w.raw.schema_issue_snapshots ? "text-danger" : ""}>{int(w.raw.schema_issue_snapshots)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Card>
        <Card>
          <SectionTitle title="Crescimento (30 d)" subtitle="Snapshots raw por dia" />
          {d.dataset.daily.length === 0 ? (
            <Empty title="Sem snapshots ainda" detail="O coletor V2 grava a cada ciclo de odds." />
          ) : (
            <div className="flex h-32 items-end gap-[3px]">
              {d.dataset.daily.map((x) => (
                <div key={x.day} className="flex h-full flex-1 flex-col justify-end" title={`${x.day}: ${x.snapshots} snapshots · ${x.events} eventos`}>
                  <div className="w-full rounded-t bg-primary/80" style={{ height: `${Math.max(3, (x.snapshots / maxDay) * 100)}%` }} />
                  <div className="mt-0.5 truncate text-center text-[9px] text-ink-3">{x.day.slice(5)}</div>
                </div>
              ))}
            </div>
          )}
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <KV k="Raw comprimido" v={bytes(d.storage.raw_compressed_bytes)} />
            <KV k="Crescimento/dia" v={bytes(d.storage.raw_bytes_per_day)} />
            <KV k="SQLite" v={bytes(d.storage.sqlite_bytes)} />
            <KV k="Backups" v={`${d.storage.backups ?? 0}${d.storage.latest_backup ? ` · ${relativeTime(d.storage.latest_backup)}` : ""}`} />
          </div>
          <button className="btn-ghost mt-2 text-xs" onClick={() => navigate("/sistema/dados?tab=storage")}>
            <HardDrive size={13} /> Armazenamento e backup
          </button>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Cobertura por alvo (7 d)" subtitle={d.coverage.note} />
          <table className="w-full text-xs">
            <tbody>
              {d.coverage.by_target.map((t) => (
                <tr key={t.target} className="border-t border-line">
                  <td className="py-1 font-semibold">{t.target}</td>
                  <td className="text-ink-2">
                    {int(t.observed)} / {int(t.expected)}
                  </td>
                  <td className="w-1/2">
                    <div className="h-2 w-full rounded bg-gray-100">
                      <div className="h-2 rounded bg-primary" style={{ width: `${t.pct ?? 0}%` }} />
                    </div>
                  </td>
                  <td className="text-right">{t.pct != null ? `${num(t.pct, 0)}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card>
          <SectionTitle title="Lacunas de coleta" subtitle="Downtime registado (§48). Nada é preenchido depois." right={d.quarantine_open ? <button className="btn-ghost text-xs text-warning" onClick={() => navigate("/sistema/dados?tab=quarantine")}><ShieldAlert size={13} /> {d.quarantine_open} em quarentena</button> : undefined} />
          {d.gaps.length === 0 ? (
            <Empty title="Nenhuma lacuna registada" detail="Se o app ficar fechado, a próxima abertura registra o intervalo sem coleta." />
          ) : (
            <table className="w-full text-xs">
              <tbody>
                {d.gaps.map((g) => (
                  <tr key={g.id} className="border-t border-line">
                    <td className="py-1">{fmtDateTime(g.started_at)}</td>
                    <td>→ {fmtDateTime(g.ended_at)}</td>
                    <td className="font-semibold">{num(g.minutes, 0)} min</td>
                    <td className="text-ink-2">{g.events_affected} eventos na janela</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Card>
        <SectionTitle
          title="Mercados — maturidade e estado de edge"
          subtitle="Raw N = seleções liquidadas · N efetivo desconta correlação dentro do mesmo jogo. Nenhum mercado vira VALUE por previsão: só por validação (Pesquisa)."
          right={
            <button className="btn-ghost text-xs" onClick={() => navigate("/pesquisa")}>
              <FlaskConical size={13} /> Pesquisa por mercado
            </button>
          }
        />
        <MarketTable rows={d.markets} />
        <div className="mt-2 text-[11px] text-ink-3">
          {d.research_generated_at ? `Pesquisa calculada ${relativeTime(d.research_generated_at)}.` : "A pesquisa ainda não correu (job a cada 6 h)."} {d.research_only}
        </div>
      </Card>

      <Card className="border-l-4 border-l-info p-4 text-xs text-ink-2">
        <div className="flex items-start gap-2">
          <Database size={14} className="mt-0.5 shrink-0 text-info" />
          <div>
            <b>Regras do dataset.</b> Snapshots raw são imutáveis (trigger SQLite bloqueia UPDATE/DELETE); confirmações com o mesmo hash referenciam o payload anterior; linhas normalizadas só são gravadas quando o preço muda ou quando um alvo T-x é atingido. Fonte {d.versions.source} · settlement {d.versions.settlement} · dataset {d.versions.dataset}. Staking: <b>{d.staking.enabled ? "ativo" : "DISABLED"}</b>{d.staking.reason ? ` — ${d.staking.reason}` : ""}.
          </div>
        </div>
      </Card>
    </div>
  );
}

export function MarketTable({ rows, onSelect }: { rows: FlywheelMarketRow[]; onSelect?: (cat: string) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-ink-3">
            <th className="py-1 font-semibold">Mercado</th>
            <th>Seleções obs.</th>
            <th>Eventos obs.</th>
            <th>Raw N</th>
            <th>Eventos únicos</th>
            <th>N efetivo</th>
            <th>Overround</th>
            <th>Maturidade</th>
            <th>EdgeFut vs fair</th>
            <th>Estado</th>
            <th>VALUE</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.market_category} className={clsx("border-t border-line", onSelect && "cursor-pointer hover:bg-gray-50")} onClick={() => onSelect?.(m.market_category)}>
              <td className="py-1.5 font-semibold">{m.label}</td>
              <td>{int(m.selections_observed)}</td>
              <td>{int(m.events_observed)}</td>
              <td>{int(m.raw_n)}</td>
              <td>{int(m.unique_events)}</td>
              <td className="font-semibold">{int(m.effective_n)}</td>
              <td>{m.overround_median_pct != null ? `${num(m.overround_median_pct, 1)}%` : "—"}</td>
              <td>
                <MaturityChip maturity={m.maturity} />
              </td>
              <td className="text-ink-2">{m.edgefut_vs_fair ?? "INSUFFICIENT"}</td>
              <td>
                <EdgeStateChip state={m.edge_state} />
              </td>
              <td>{m.value_enabled ? <span className="chip bg-success-50 text-success">ON</span> : m.enablement_candidate ? <span className="chip bg-info-50 text-info">CANDIDATE</span> : <span className="chip bg-gray-100 text-ink-3">OFF</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
