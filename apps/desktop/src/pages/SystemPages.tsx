import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AlertKind, ComponentHealth, JobRun } from "@edgefut/contracts";
import { fmtDateTime, int, relativeTime } from "@edgefut/shared";
import clsx from "clsx";
import { Bell, CheckCheck, Play, RefreshCw } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ageLabel, Card, Empty, ErrorBox, FreshnessChip, HealthChip, HealthDot, KV, Loading, PageHeader, Segmented } from "@/components/ui";
import { api } from "@/lib/api";

// ---------------------------------------------------------------- Alertas
export function AlertsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [onlyUnread, setOnlyUnread] = useState<"all" | "unread">("all");
  const [kind, setKind] = useState<AlertKind | "ALL">("ALL");
  const q = useQuery({ queryKey: ["alerts", onlyUnread], queryFn: () => api.alerts({ limit: 300, unread_only: onlyUnread === "unread" }), refetchInterval: 60000 });
  const mark = useMutation({
    mutationFn: (ids?: number[]) => api.markAlertsRead(ids),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["radar"] });
    },
  });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const rows = d.alerts.filter((a) => kind === "ALL" || a.kind === kind);
  return (
    <div className="space-y-5">
      <PageHeader
        title="Alertas"
        subtitle="Gerados localmente pelo agendador: movimento de odd, mudança de qualidade/confiança, oportunidades que surgiram ou se perderam. Nenhuma notificação externa."
        right={
          <>
            <Segmented value={onlyUnread} options={[{ value: "all", label: "Todos" }, { value: "unread", label: `Não lidos (${d.unread})` }]} onChange={setOnlyUnread} />
            <button className="btn-outline" onClick={() => mark.mutate(undefined)} disabled={mark.isPending || d.unread === 0}>
              <CheckCheck size={14} /> Marcar todos como lidos
            </button>
          </>
        }
      />
      <div className="flex flex-wrap gap-1">
        {(["ALL", ...Object.keys(d.kinds)] as (AlertKind | "ALL")[]).map((k) => (
          <button key={k} className={clsx("rounded-full border px-2.5 py-0.5 text-[11px]", kind === k ? "border-ink bg-ink text-white" : "border-line text-ink-2 hover:text-ink")} onClick={() => setKind(k)}>
            {k === "ALL" ? "Todos" : d.kinds[k]}
          </button>
        ))}
      </div>
      {rows.length === 0 ? (
        <Empty icon={<Bell size={28} />} title="Nenhum alerta" detail="Alertas são gerados após cada ciclo do radar e da coleta de odds." />
      ) : (
        <div className="card overflow-hidden p-0">
          {rows.map((a) => (
            <div
              key={a.id}
              className={clsx("flex cursor-pointer items-start gap-3 border-b border-line/70 px-4 py-2.5 text-sm last:border-0 hover:bg-gray-50", !a.read_at && "bg-primary-50/30")}
              onClick={() => {
                if (!a.read_at) mark.mutate([a.id]);
                if (a.event_id) navigate(`/jogos/${a.event_id}`);
              }}
            >
              <span className={clsx("mt-1.5 h-2 w-2 shrink-0 rounded-full", a.read_at ? "bg-gray-200" : a.severity === "warning" ? "bg-warning" : "bg-primary")} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={clsx("chip", a.severity === "warning" ? "bg-warning-50 text-warning" : "bg-info-50 text-info")}>{a.kind_label}</span>
                  <span className="font-semibold">{a.title}</span>
                </div>
                {a.detail && Object.keys(a.detail).length > 0 && (
                  <div className="mt-0.5 flex flex-wrap gap-1 text-[11px] text-ink-2">
                    {Object.entries(a.detail).map(([k, v]) => (
                      <span key={k} className="rounded bg-bg px-1.5 py-0.5">
                        {k}: <b>{typeof v === "number" ? v.toFixed(2).replace(/\.00$/, "") : String(v)}</b>
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <span className="shrink-0 text-xs text-ink-3" title={fmtDateTime(a.created_at)}>
                {relativeTime(a.created_at)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Jobs
const STATUS_CLS: Record<string, string> = {
  ok: "bg-success-50 text-success",
  running: "bg-info-50 text-info",
  error: "bg-danger-50 text-danger",
  skipped: "bg-gray-100 text-ink-3",
};

export function JobsPage() {
  const qc = useQueryClient();
  const [job, setJob] = useState<string>("");
  const q = useQuery({ queryKey: ["jobs", job], queryFn: () => api.jobs({ job: job || undefined, limit: 150 }), refetchInterval: 10000 });
  const run = useMutation({ mutationFn: (j: string) => api.runJob(j), onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["jobs"] }), 1200) });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const jobs = Object.keys(d.labels);
  return (
    <div className="space-y-5">
      <PageHeader
        title="Sistema · Jobs"
        subtitle={`Agendador ${d.scheduler_running ? "ativo" : "parado"} · cada execução grava início, fim, duração, registros e erros com correlation-id`}
        right={
          <button className="btn-outline" onClick={() => q.refetch()} disabled={q.isFetching}>
            <RefreshCw size={14} className={q.isFetching ? "animate-spin" : ""} /> Atualizar
          </button>
        }
      />
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
        {jobs.map((j) => {
          const last = d.last_by_job[j];
          const sched = d.scheduled.find((s) => s.id === j || s.id === j.replace("sync_", ""));
          return (
            <Card key={j} className="flex flex-col gap-1.5">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-sm font-bold">{d.labels[j]}</div>
                  <div className="font-mono text-[10px] text-ink-3">{j}</div>
                </div>
                <button className="btn-ghost text-xs" title="Executar agora" onClick={() => run.mutate(j)} disabled={run.isPending || last?.status === "running"}>
                  <Play size={13} /> Rodar
                </button>
              </div>
              {last ? (
                <div className="text-xs text-ink-2">
                  <span className={clsx("chip mr-1", STATUS_CLS[last.status] ?? "bg-gray-100 text-ink-2")}>{last.status}</span>
                  {relativeTime(last.started_at)} · {last.duration_ms !== null ? `${(last.duration_ms / 1000).toFixed(1)} s` : "…"}
                  {last.records_processed !== null ? ` · ${int(last.records_processed)} registros` : ""}
                  {last.errors && last.errors.length > 0 && <div className="mt-0.5 truncate text-danger" title={last.errors.join("\n")}>{last.errors[0]}</div>}
                </div>
              ) : (
                <div className="text-xs text-ink-3">nunca executado</div>
              )}
              {sched?.next_run_at && <div className="text-[11px] text-ink-3">próxima {relativeTime(sched.next_run_at)} · {sched.trigger}</div>}
            </Card>
          );
        })}
      </div>

      <Card className="p-0">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <span className="text-[13px] font-bold uppercase tracking-wider">Execuções recentes</span>
          <select className="input w-auto py-1 text-xs" value={job} onChange={(e) => setJob(e.target.value)}>
            <option value="">todos os jobs</option>
            {jobs.map((j) => (
              <option key={j} value={j}>
                {d.labels[j]}
              </option>
            ))}
          </select>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-bg text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
            <tr>
              <th className="px-4 py-2">Início</th>
              <th className="px-3 py-2">Job</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2 text-right">Duração</th>
              <th className="px-3 py-2 text-right">Registros</th>
              <th className="px-3 py-2">Detalhe</th>
              <th className="px-3 py-2">Correlation</th>
            </tr>
          </thead>
          <tbody>
            {d.runs.map((r) => (
              <JobRow key={r.id} r={r} />
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function JobRow({ r }: { r: JobRun }) {
  const detail = r.detail ? Object.entries(r.detail).filter(([k]) => !["ok"].includes(k)).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`) : [];
  return (
    <tr className="border-t border-line/70">
      <td className="px-4 py-1.5 text-xs text-ink-2">{fmtDateTime(r.started_at)}</td>
      <td className="px-3 py-1.5">{r.label}</td>
      <td className="px-3 py-1.5">
        <span className={clsx("chip", STATUS_CLS[r.status] ?? "bg-gray-100 text-ink-2")}>{r.status}</span>
      </td>
      <td className="px-3 py-1.5 text-right tabular-nums">{r.duration_ms !== null ? `${(r.duration_ms / 1000).toFixed(1)} s` : "—"}</td>
      <td className="px-3 py-1.5 text-right tabular-nums">{r.records_processed !== null ? int(r.records_processed) : "—"}</td>
      <td className="max-w-md truncate px-3 py-1.5 text-xs text-ink-2" title={[...detail, ...(r.errors ?? [])].join("\n")}>
        {r.errors && r.errors.length > 0 ? <span className="text-danger">{r.errors[0]}</span> : detail.slice(0, 4).join(" · ")}
      </td>
      <td className="px-3 py-1.5 font-mono text-[10px] text-ink-3">{r.correlation_id}</td>
    </tr>
  );
}

// ---------------------------------------------------------------- Diagnóstico
export function DiagnosticsPage() {
  const q = useQuery({ queryKey: ["system-health"], queryFn: api.systemHealth, refetchInterval: 15000 });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  return (
    <div className="space-y-5">
      <PageHeader
        title="Sistema · Diagnóstico"
        subtitle={`Saúde de cada componente · gerado ${fmtDateTime(d.generated_at)}`}
        right={
          <span className="inline-flex items-center gap-2 text-sm">
            Geral: <HealthChip status={d.overall} />
          </span>
        }
      />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {d.components.map((c) => (
          <ComponentCard key={c.key} c={c} />
        ))}
      </div>
      {health.data && (
        <Card>
          <div className="text-[13px] font-bold uppercase tracking-wider">Engine local</div>
          <div className="grid gap-x-8 md:grid-cols-2">
            <KV k="Endereço" v={`${health.data.host}:${health.data.port} (somente loopback)`} />
            <KV k="Versão" v={health.data.version} />
            <KV k="Banco" v={<span className="truncate font-mono text-xs">{health.data.db_path}</span>} />
            <KV k="Datasets" v={health.data.datasets} />
            <KV k="Superbet" v={health.data.superbet_enabled ? "habilitado (leitura pública)" : "desabilitado"} />
            <KV k="Ollama" v={health.data.ollama_available ? "disponível" : "não detectado (templates)"} />
          </div>
          {health.data.scheduler.errors.length > 0 && (
            <div className="mt-2 text-xs text-danger">
              Erros recentes do agendador:
              <ul className="list-disc pl-4">
                {health.data.scheduler.errors.slice(-5).map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

function ComponentCard({ c }: { c: ComponentHealth }) {
  const details = Object.entries(c.details ?? {}).filter(([, v]) => v !== null && v !== undefined && typeof v !== "object");
  return (
    <Card className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <HealthDot status={c.status} />
          <div>
            <div className="text-sm font-bold">{c.name}</div>
            <div className="font-mono text-[10px] text-ink-3">{c.key}</div>
          </div>
        </div>
        <HealthChip status={c.status} />
      </div>
      <p className="text-xs text-ink-2">{c.summary}</p>
      <div className="flex flex-wrap items-center gap-1 text-[11px] text-ink-3">
        {c.freshness && <FreshnessChip status={c.freshness.status} ageSeconds={c.freshness.age_seconds} label={c.freshness.label} />}
        {c.last_update && <span title={fmtDateTime(c.last_update)}>atualizado {ageLabel((Date.now() - new Date(c.last_update.endsWith("Z") ? c.last_update : `${c.last_update}Z`).getTime()) / 1000)}</span>}
      </div>
      {details.length > 0 && (
        <div className="flex flex-wrap gap-1 text-[11px]">
          {details.slice(0, 8).map(([k, v]) => (
            <span key={k} className="rounded bg-bg px-1.5 py-0.5 text-ink-2">
              {k}: <b>{typeof v === "number" ? (Number.isInteger(v) ? int(v) : v.toFixed(2)) : String(v)}</b>
            </span>
          ))}
        </div>
      )}
      {c.note && <div className="text-[11px] text-ink-3">{c.note}</div>}
    </Card>
  );
}
