import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { EventWindow } from "@edgefut/contracts";
import { RefreshCw } from "lucide-react";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { EventRow } from "@/components/EventCard";
import { Empty, ErrorBox, Loading, PageHeader, Segmented } from "@/components/ui";
import { api } from "@/lib/api";

export function EventsPage() {
  const [params, setParams] = useSearchParams();
  const window = (params.get("window") as EventWindow) || "48h";
  const competition = params.get("competition") ?? "";
  const search = params.get("search") ?? "";
  const supportedOnly = params.get("supported") === "1";
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["events", window, competition, search, supportedOnly],
    queryFn: () => api.events({ window, competition: competition || undefined, search: search || undefined, supported_only: supportedOnly }),
    refetchInterval: 120000,
  });
  const sync = useMutation({ mutationFn: api.syncEvents, onSuccess: () => qc.invalidateQueries({ queryKey: ["events"] }) });
  const fav = useMutation({ mutationFn: ({ id, on }: { id: number; on: boolean }) => api.favorite(id, on), onSuccess: () => qc.invalidateQueries({ queryKey: ["events"] }) });

  const set = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v);
    else p.delete(k);
    setParams(p, { replace: true });
  };

  const competitions = useMemo(() => {
    const m = new Map<string, number>();
    q.data?.events.forEach((e) => e.competition_name && m.set(e.competition_name, (m.get(e.competition_name) ?? 0) + 1));
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [q.data]);

  const grouped = useMemo(() => {
    const m = new Map<string, typeof q.data extends undefined ? never : NonNullable<typeof q.data>["events"]>();
    q.data?.events.forEach((e) => {
      const k = e.competition_name ?? "Outros";
      m.set(k, [...(m.get(k) ?? []), e]);
    });
    return [...m.entries()];
  }, [q.data]);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Jogos"
        subtitle="Eventos reais da oferta pública da Superbet. Clique para ver a análise completa."
        right={
          <button className="btn-outline" onClick={() => sync.mutate()} disabled={sync.isPending}>
            <RefreshCw size={14} className={sync.isPending ? "animate-spin" : ""} /> Sincronizar eventos
          </button>
        }
      />
      <div className="card flex flex-wrap items-center gap-3 p-3">
        <Segmented
          value={window}
          onChange={(v) => set("window", v)}
          options={[
            { value: "today", label: "Hoje" },
            { value: "tomorrow", label: "Amanhã" },
            { value: "48h", label: "48h" },
            { value: "week", label: "7 dias" },
          ]}
        />
        <select className="input max-w-xs" value={competition} onChange={(e) => set("competition", e.target.value)}>
          <option value="">Todos os campeonatos</option>
          {competitions.map(([c, n]) => (
            <option key={c} value={c}>
              {c} ({n})
            </option>
          ))}
        </select>
        <input className="input max-w-xs" placeholder="Filtrar por time…" value={search} onChange={(e) => set("search", e.target.value)} />
        <label className="flex items-center gap-2 text-sm text-ink-2">
          <input type="checkbox" checked={supportedOnly} onChange={(e) => set("supported", e.target.checked ? "1" : "")} /> Só competições com histórico
        </label>
        <span className="ml-auto text-xs text-ink-3">{q.data?.total ?? 0} jogos</span>
      </div>

      {q.isLoading && <Loading />}
      {q.isError && <ErrorBox error={q.error} retry={() => q.refetch()} />}
      {q.data && q.data.total === 0 && <Empty title="Nenhum jogo neste filtro" detail="Ajuste a janela ou sincronize os eventos." />}
      {grouped.map(([comp, evs]) => (
        <section key={comp}>
          <div className="mb-2 flex items-center gap-2">
            <h2 className="text-sm font-bold">{comp}</h2>
            <span className="text-xs text-ink-3">{evs.length}</span>
          </div>
          <div className="space-y-2">
            {evs.map((e) => (
              <EventRow key={e.id} event={e} onFavorite={(id, on) => fav.mutate({ id, on })} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
