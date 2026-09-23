import { useQuery } from "@tanstack/react-query";
import { fmtDateTime, int, relativeTime } from "@edgefut/shared";
import { RefreshCw } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { EventRow, RecommendationCard } from "@/components/EventCard";
import { Empty, ErrorBox, Loading, SectionTitle, Stat } from "@/components/ui";
import { api } from "@/lib/api";

export function Dashboard() {
  const q = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 60000 });
  const navigate = useNavigate();
  if (q.isLoading) return <Loading label="Preparando o painel…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">
            {d.greeting}, {d.user_name} 👋
          </h1>
          <p className="mt-1 text-sm text-ink-2">Aqui estão as melhores oportunidades detectadas pelos modelos hoje.</p>
        </div>
        <button className="btn-outline" onClick={() => q.refetch()} disabled={q.isFetching}>
          <RefreshCw size={14} className={q.isFetching ? "animate-spin" : ""} /> Atualizar
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Jogos analisados hoje" value={int(d.analyzed_today)} hint="snapshots gravados antes do jogo" />
        <Stat label="Entradas Confidence A" value={int(d.confidence_a)} accent="text-success" hint="próximas 48h" />
        <Stat label="Entradas Confidence B" value={int(d.confidence_b)} accent="text-info" hint="próximas 48h" />
        <Stat label="Mercados descartados" value={int(d.discarded_markets)} hint="sem edge, sem dados ou NO BET" />
        <Stat label="Última atualização" value={d.last_update ? relativeTime(d.last_update) : "—"} hint={d.last_update ? fmtDateTime(d.last_update) : d.scheduler.radar_running ? "analisando…" : "aguardando coleta"} />
      </div>

      <section>
        <SectionTitle title="Top oportunidades" subtitle="Ordenadas por Opportunity Score — nunca pela odd mais alta" right={<button className="btn-ghost text-xs" onClick={() => navigate("/entradas")}>Ver todas</button>} />
        {d.top_opportunities.length === 0 ? (
          <Empty
            title={d.scheduler.radar_running ? "Analisando os próximos jogos…" : "Nenhuma entrada com confiança A/B nas próximas 48h"}
            detail="O EdgeFut só recomenda quando há dados suficientes, concordância entre modelos e edge acima do limiar. Veja o Radar para os jogos em observação e os motivos de NO BET."
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {d.top_opportunities.map((r, i) => (
              <RecommendationCard key={i} event={r.event} rec={r.recommendation} compact />
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionTitle title="Eventos populares" subtitle="Maior número de mercados ofertados pela Superbet nas próximas 48h" right={<button className="btn-ghost text-xs" onClick={() => navigate("/jogos")}>Todos os jogos</button>} />
        {d.popular_events.length === 0 ? (
          <Empty title="Nenhum evento carregado" detail="Aguarde a coleta de eventos ou acione a sincronização em Fontes." />
        ) : (
          <div className="space-y-2">
            {d.popular_events.map((e) => (
              <EventRow key={e.id} event={e} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
