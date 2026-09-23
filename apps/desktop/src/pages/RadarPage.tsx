import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { RadarCard as RadarCardT } from "@edgefut/contracts";
import { fmtDateTime } from "@edgefut/shared";
import { Loader2, RefreshCw } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { RecommendationCard } from "@/components/EventCard";
import { Empty, ErrorBox, GradeBadge, Loading, NoBetChip, PageHeader, SectionTitle } from "@/components/ui";
import { api } from "@/lib/api";

const ORDER = ["top", "high_confidence", "gols", "escanteios", "cartoes", "finalizacoes", "valor", "observacao", "no_bet"];

export function RadarPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["radar"], queryFn: () => api.radar(48), refetchInterval: (query) => (query.state.data?.refreshing ? 8000 : 60000) });
  const refresh = useMutation({ mutationFn: () => api.refreshSource("radar"), onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["radar"] }), 1500) });
  if (q.isLoading) return <Loading label="Carregando radar…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const cards = [...d.cards].sort((a, b) => ORDER.indexOf(a.key) - ORDER.indexOf(b.key));
  return (
    <div className="space-y-6">
      <PageHeader
        title="Radar"
        subtitle={`${d.analyzed_events} jogos analisados nas próximas 48h · gerado ${fmtDateTime(d.generated_at)}`}
        right={
          <>
            {d.refreshing && (
              <span className="flex items-center gap-1.5 text-xs text-warning">
                <Loader2 size={13} className="animate-spin" /> analisando…
              </span>
            )}
            <button className="btn-outline" onClick={() => refresh.mutate()} disabled={d.refreshing || refresh.isPending}>
              <RefreshCw size={14} /> Reanalisar
            </button>
          </>
        }
      />
      {d.analyzed_events === 0 && !d.refreshing && (
        <Empty title="Nenhum jogo analisado ainda" detail="O radar analisa automaticamente jogos de competições com histórico público disponível. Clique em Reanalisar ou aguarde o agendador." />
      )}
      {cards.map((c) => (
        <RadarSection key={c.key} card={c} />
      ))}
    </div>
  );
}

function RadarSection({ card }: { card: RadarCardT }) {
  const navigate = useNavigate();
  if (card.key === "no_bet") {
    return (
      <section>
        <SectionTitle title={card.title} subtitle={card.subtitle} />
        {card.items.length === 0 ? (
          <div className="text-sm text-ink-3">Nenhum jogo bloqueado.</div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {card.items.map((it) => (
              <div key={it.event.id} className="card card-hover flex items-center justify-between gap-3 px-4 py-3" onClick={() => navigate(`/jogos/${it.event.id}`)}>
                <div className="min-w-0">
                  <div className="truncate text-[11px] font-semibold uppercase text-ink-3">{it.event.competition_name}</div>
                  <div className="truncate text-sm font-semibold">
                    {it.event.home_name} x {it.event.away_name}
                  </div>
                  <div className="mt-1">
                    <NoBetChip reason={it.no_bet_reason} />
                  </div>
                </div>
                <div className="flex flex-col items-end gap-1 text-xs text-ink-2">
                  <GradeBadge grade={it.confidence_grade} />
                  <span>DQ {Math.round(it.data_quality)}%</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    );
  }
  return (
    <section>
      <SectionTitle title={card.title} subtitle={card.subtitle} />
      {card.items.length === 0 ? (
        <div className="card px-4 py-5 text-sm text-ink-2">Nenhuma seleção atende aos critérios deste card no momento.</div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {card.items.map((it, i) => (it.recommendation ? <RecommendationCard key={`${it.event.id}-${i}`} event={it.event} rec={it.recommendation} compact /> : null))}
        </div>
      )}
    </section>
  );
}
