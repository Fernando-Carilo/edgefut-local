import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { RadarCard as RadarCardT, RadarItem, RadarResponse } from "@edgefut/contracts";
import { NO_BET_LABELS } from "@edgefut/contracts";
import { fmtDateTime, relativeTime } from "@edgefut/shared";
import { Info, Loader2, RefreshCw } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { RecommendationCard } from "@/components/EventCard";
import { Counter, Empty, ErrorBox, EvidenceChip, FreshnessChip, GradeBadge, Loading, NoBetChip, PageHeader, SectionTitle, Tooltip } from "@/components/ui";
import { api } from "@/lib/api";

const ORDER = ["top", "valor", "value_candidate", "high_probability", "model_only", "high_confidence", "gols", "escanteios", "cartoes", "finalizacoes", "observacao", "no_bet"];

const CARD_HELP: Record<string, string> = {
  top: "Somente seleções que passaram em TODOS os checks do Quality Gate. Uma PRIMÁRIA por tese: DNB, dupla chance e handicap do mesmo time não aparecem como oportunidades extra. Ordenadas por Opportunity Score V3.",
  high_probability: "MODEL FAVORITE: probabilidade do modelo ≥ limiar. Não implica valor: a odd pode estar justa ou ruim.",
  valor: "VALUE: passou no quality gate E o mercado tem prova out-of-sample suficiente (N mínimo configurável). Não implica alta probabilidade.",
  value_candidate: "VALUE CANDIDATE: passou no quality gate, mas o mercado ainda não tem prova out-of-sample suficiente. Opportunity Score recebe penalidade de incerteza.",
  model_only: "MODEL ONLY: probabilidade calculada, mas sem preço de mercado válido (ou competição nunca validada contra odds). Nunca vira VALUE, nunca entra em ROI.",
  high_confidence: "Confiança A: dados completos, modelos concordando, amostra grande e odds frescas.",
  observacao: "Seleções com edge, mas que falharam em algum check do Quality Gate ou nos limiares. Não são recomendações.",
  no_bet: "Jogos em que o sistema decidiu explicitamente NÃO entrar. O motivo é sempre mostrado.",
};

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
        subtitle={`Próximas 48 h · gerado ${fmtDateTime(d.generated_at)}${d.summary?.last_update ? ` · última análise ${relativeTime(d.summary.last_update)}` : ""}`}
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
      {d.summary && <RadarHeader d={d} />}
      {d.analyzed_events === 0 && !d.refreshing && (
        <Empty title="Nenhum jogo analisado ainda" detail="O radar analisa automaticamente jogos de competições com histórico público disponível. Clique em Reanalisar ou aguarde o agendador." />
      )}
      {cards.map((c) => (
        <RadarSection key={c.key} card={c} />
      ))}
    </div>
  );
}

function RadarHeader({ d }: { d: RadarResponse }) {
  const s = d.summary!;
  const navigate = useNavigate();
  const t = d.thresholds;
  const evidence = Object.entries(s.gate_passed_by_evidence) as [keyof typeof s.gate_passed_by_evidence, number][];
  const topReason = Object.entries(s.no_bet_by_reason).sort((a, b) => b[1] - a[1])[0];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5 xl:grid-cols-10">
        <Counter label="Jogos encontrados" value={s.events_found} hint="oferta Superbet 48 h" />
        <Counter label="Com dados suficientes" value={s.with_sufficient_data} hint={`${s.analyzed} analisados`} />
        <Counter label="Passaram no gate" value={s.quality_gate_passed} accent="text-success" hint={evidence.map(([k, v]) => `${v} ${k === "MODEL_ONLY" ? "só modelo" : k === "BACKTEST_ODDS" ? "backtest" : "settled"}`).join(" · ") || "—"} />
        <Counter label="Confiança A / B" value={`${s.confidence_a} / ${s.confidence_b}`} hint="entre as que passaram" />
        <Counter label="Value / Candidate" value={`${s.value} / ${s.value_candidates}`} accent="text-primary" hint={`edge ≥ ${t.min_edge_pp} pp · EV ≥ ${t.min_ev_pct}% · VALUE exige prova OOS`} />
        <Counter label="Teses acionáveis" value={s.actionable_clusters} hint={`${s.selections_actionable} seleções · ${s.exposure_high} jogos com exposição alta`} />
        <Counter label="Model favorite" value={s.high_probability} accent="text-info" hint={`prob. ≥ ${Math.round((t.high_probability_min ?? 0.65) * 100)}% · não é valor`} />
        <Counter label="Model only" value={s.model_only} accent="text-warning" hint="sem preço válido — nunca VALUE" />
        <Counter label="Em observação" value={s.watch} accent="text-warning" hint="edge sem passar no gate" />
        <Counter label="NO BET" value={s.no_bet} hint={topReason ? `principal: ${NO_BET_LABELS[topReason[0] as keyof typeof NO_BET_LABELS] ?? topReason[0]} (${topReason[1]})` : "—"} />
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
        <span className="font-semibold text-ink">Limiares ativos:</span>
        <span className="rounded bg-bg px-1.5 py-0.5">edge ≥ {t.min_edge_pp} pp</span>
        <span className="rounded bg-bg px-1.5 py-0.5">EV ≥ {t.min_ev_pct}%</span>
        <span className="rounded bg-bg px-1.5 py-0.5">
          odd {t.min_odd}–{t.max_odd}
        </span>
        <span className="rounded bg-bg px-1.5 py-0.5">DQ ≥ {t.gate_min_data_quality}%</span>
        <span className="rounded bg-bg px-1.5 py-0.5">confiança ≥ {t.gate_min_confidence}</span>
        <span className="rounded bg-bg px-1.5 py-0.5">amostra ≥ {t.gate_min_sample}</span>
        <span className="rounded bg-bg px-1.5 py-0.5">divergência ≤ {t.gate_max_disagreement_pp} pp</span>
        <span className="rounded bg-bg px-1.5 py-0.5">edge não calibrado ≤ {t.gate_max_edge_pp_uncalibrated} pp</span>
        {s.stale > 0 && <FreshnessChip status="STALE" label={`${s.stale} jogos com odds`} />}
        {s.alerts_unread > 0 && (
          <button className="ml-auto rounded bg-warning-50 px-1.5 py-0.5 font-semibold text-warning hover:underline" onClick={() => navigate("/alertas")}>
            {s.alerts_unread} alertas não lidos
          </button>
        )}
        <Tooltip text="O EdgeFut nunca reduz limiares automaticamente para 'achar' entradas. Se nada passa no gate, a resposta é NO BET. Ajuste consciente somente em Configurações, dentro dos pisos de segurança.">
          <Info size={13} className="text-ink-3" />
        </Tooltip>
      </div>
    </div>
  );
}

function RadarSection({ card }: { card: RadarCardT }) {
  const navigate = useNavigate();
  const help = CARD_HELP[card.key];
  if (card.key === "no_bet") {
    return (
      <section>
        <SectionTitle title={card.title} subtitle={card.subtitle} right={help ? <Tooltip text={help}><Info size={13} className="text-ink-3" /></Tooltip> : undefined} />
        {card.items.length === 0 ? (
          <div className="text-sm text-ink-3">Nenhum jogo bloqueado.</div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {card.items.map((it) => (
              <NoBetRow key={it.event.id} it={it} onClick={() => navigate(`/jogos/${it.event.id}`)} />
            ))}
          </div>
        )}
      </section>
    );
  }
  return (
    <section>
      <SectionTitle title={card.title} subtitle={card.subtitle} right={help ? <Tooltip text={help}><Info size={13} className="text-ink-3" /></Tooltip> : undefined} />
      {card.items.length === 0 ? (
        <div className="card px-4 py-5 text-sm text-ink-2">
          {card.key === "top" ? "Nenhuma seleção passou em todos os checks do Quality Gate agora. Isso é um resultado válido — não uma falha." : "Nenhuma seleção atende aos critérios deste card no momento."}
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {card.items.map((it, i) => (it.recommendation ? <RecommendationCard key={`${it.event.id}-${i}`} event={it.event} rec={it.recommendation} compact /> : null))}
        </div>
      )}
    </section>
  );
}

function NoBetRow({ it, onClick }: { it: RadarItem; onClick: () => void }) {
  return (
    <div className="card card-hover flex items-center justify-between gap-3 px-4 py-3" onClick={onClick}>
      <div className="min-w-0">
        <div className="truncate text-[11px] font-semibold uppercase text-ink-3">{it.event.competition_name}</div>
        <div className="truncate text-sm font-semibold">
          {it.event.home_name} x {it.event.away_name}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1">
          <NoBetChip reason={it.no_bet_reason} />
          <EvidenceChip level={it.evidence} compact />
          {it.freshness_status && it.freshness_status !== "FRESH" && <FreshnessChip status={it.freshness_status} label="odds" />}
        </div>
        {it.why.length > 0 && <div className="mt-1 line-clamp-2 text-[11px] text-ink-2">{it.why[0]}</div>}
      </div>
      <div className="flex flex-col items-end gap-1 text-xs text-ink-2">
        <GradeBadge grade={it.confidence_grade} />
        <span>DQ {Math.round(it.data_quality)}%</span>
      </div>
    </div>
  );
}
