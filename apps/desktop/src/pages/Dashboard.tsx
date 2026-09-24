import { useQuery } from "@tanstack/react-query";
import { fmtDateTime, int, num, relativeTime, signedPct } from "@edgefut/shared";
import clsx from "clsx";
import type { ModelHealth } from "@edgefut/contracts";
import { Bell, Radar, RefreshCw, ShieldCheck, Stethoscope } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { EventRow, RecommendationCard } from "@/components/EventCard";
import { Card, Counter, Empty, ErrorBox, HealthChip, Loading, SectionTitle } from "@/components/ui";
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
            {d.greeting}, {d.user_name}
          </h1>
          <p className="mt-1 text-sm text-ink-2">Resumo do que foi analisado, do que passou no Quality Gate e do que foi descartado — nunca só as odds mais altas.</p>
        </div>
        <button className="btn-outline" onClick={() => q.refetch()} disabled={q.isFetching}>
          <RefreshCw size={14} className={q.isFetching ? "animate-spin" : ""} /> Atualizar
        </button>
      </div>

      {/* Resumo da manhã */}
      <Card className="border-l-4 border-l-primary p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="label mb-1">Resumo</div>
            <p className="text-[15px] leading-relaxed">{d.morning_summary}</p>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-ink-2">
              <span className="inline-flex items-center gap-1.5">
                Saúde do sistema: <HealthChip status={d.health_overall} />
              </span>
              <button className="btn-ghost text-xs" onClick={() => navigate("/sistema/diagnostico")}>
                <Stethoscope size={13} /> Diagnóstico
              </button>
              {d.alerts_unread > 0 && (
                <button className="btn-ghost text-xs text-warning" onClick={() => navigate("/alertas")}>
                  <Bell size={13} /> {d.alerts_unread} alertas não lidos
                </button>
              )}
              <span className="ml-auto">{d.last_update ? `Última análise ${relativeTime(d.last_update)} (${fmtDateTime(d.last_update)})` : d.scheduler.radar_running ? "analisando…" : "aguardando coleta"}</span>
            </div>
          </div>
          <button className="btn-primary shrink-0 px-5 py-2.5 text-sm" onClick={() => navigate("/radar")}>
            <Radar size={16} /> {d.cta}
          </button>
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
        <Counter label="Jogos encontrados" value={int(d.events_found)} hint="oferta Superbet 48 h" onClick={() => navigate("/jogos")} />
        <Counter label="Analisados hoje" value={int(d.analyzed_today)} hint="snapshots antes do jogo" onClick={() => navigate("/historico")} />
        <Counter label="Passaram no gate" value={int(d.quality_gate_passed)} accent="text-success" hint={`A ${d.confidence_a} · B ${d.confidence_b} · ${d.actionable_clusters} teses`} onClick={() => navigate("/radar")} />
        <Counter label="Value / Candidate" value={`${int(d.value)} / ${int(d.value_candidates)}`} accent="text-primary" hint={`${int(d.model_only)} model only (sem preço)`} onClick={() => navigate("/radar")} />
        <Counter label="Em observação" value={int(d.watch)} accent="text-warning" hint="edge sem passar no gate" onClick={() => navigate("/radar")} />
        <Counter label="NO BET" value={int(d.no_bet)} hint="decisão explícita de não entrar" onClick={() => navigate("/radar")} />
        <Counter label="Mercados descartados" value={int(d.discarded_markets)} hint="sem edge, sem dados ou fora da faixa" />
      </div>

      {d.model_health && <ModelHealthStrip h={d.model_health} onOpen={() => navigate("/validacao")} />}

      <section>
        <SectionTitle
          title="Top oportunidades"
          subtitle="Somente seleções que passaram no Quality Gate · uma primária por tese · ordenadas por Opportunity Score V3"
          right={
            <button className="btn-ghost text-xs" onClick={() => navigate("/entradas")}>
              Ver todas
            </button>
          }
        />
        {d.top_opportunities.length === 0 ? (
          <Empty
            title={d.scheduler.radar_running ? "Analisando os próximos jogos…" : "Nenhuma entrada passou no Quality Gate nas próximas 48h"}
            detail="O EdgeFut só recomenda quando há dados suficientes, concordância entre modelos, odds frescas e edge plausível. Isso é um resultado válido. Veja o Radar para os jogos em observação e os motivos de NO BET."
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
        <SectionTitle
          title="Eventos populares"
          subtitle="Maior número de mercados ofertados pela Superbet nas próximas 48h"
          right={
            <button className="btn-ghost text-xs" onClick={() => navigate("/jogos")}>
              Todos os jogos
            </button>
          }
        />
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

/** MODEL HEALTH (§47): discreto, sem gráficos — o modelo bate o mercado? há drift? o shadow já mede algo? */
function ModelHealthStrip({ h, onOpen }: { h: ModelHealth; onOpen: () => void }) {
  const cls =
    h.status === "OK" ? "bg-success-50 text-success" : h.status === "WATCH" ? "bg-warning-50 text-warning" : h.status === "DRIFT" ? "bg-danger-50 text-danger" : "bg-gray-100 text-ink-2";
  const r = h.replay;
  return (
    <Card className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 text-xs text-ink-2">
      <span className="inline-flex items-center gap-1 font-semibold text-ink">
        <ShieldCheck size={13} /> MODEL HEALTH
      </span>
      <span className={clsx("chip", cls)}>{h.status}</span>
      {h.champion && <span>campeão <b className="font-mono">{h.champion}</b></span>}
      {r ? (
        <span title={`Replay N=${int(r.matches)} · ${r.windows} janelas · amostra ${r.sample_quality}`}>
          Brier {num(r.champion_brier, 4)} vs mercado {num(r.market_brier, 4)} · <b className={r.vs_market.significance === "NO CLEAR ADVANTAGE" ? "text-warning" : "text-ink"}>{r.vs_market.significance ?? "—"}</b>
          {r.vs_market.lift_pct !== null && r.vs_market.lift_pct !== undefined ? ` (lift ${signedPct(r.vs_market.lift_pct)})` : ""}
        </span>
      ) : (
        <span>sem replay concluído</span>
      )}
      {h.drift && <span>drift: <b>{h.drift.status}</b></span>}
      {h.shadow && <span>shadow: {int(h.shadow.total)} previsões · {int(h.shadow.settled)} liquidadas</span>}
      {h.settlement && h.settlement.unsettled_finished > 0 && <span className="text-warning">{h.settlement.unsettled_finished} jogos terminados sem liquidação</span>}
      <button className="btn-ghost ml-auto text-xs" onClick={onOpen}>
        Ver validação
      </button>
      {h.v2 && <ModelHealthV2Row v2={h.v2} />}
    </Card>
  );
}

/** MODEL HEALTH V2 (§49): FOOTBALL MODEL · MARKET MODEL · SUPERBET EVIDENCE · SHADOW SETTLED N · MARKET EDGE. */
function ModelHealthV2Row({ v2 }: { v2: NonNullable<ModelHealth["v2"]> }) {
  const chip = (ok: boolean | null, warn = false) => clsx("chip", ok ? "bg-success-50 text-success" : warn ? "bg-warning-50 text-warning" : "bg-gray-100 text-ink-2");
  const fm = v2.football_model;
  const mm = v2.market_model;
  const se = v2.superbet_evidence;
  const ss = v2.shadow_settled;
  const me = v2.market_edge;
  return (
    <div className="grid w-full grid-cols-2 gap-x-4 gap-y-1 border-t border-line pt-2 md:grid-cols-5">
      <span title={fm.text}>
        <span className="block text-[10px] font-semibold uppercase text-ink-3">Football model</span>
        <span className={chip(fm.status.startsWith("PREDICTIVE"), fm.status === "WEAK")}>{fm.status}</span>
      </span>
      <span title={`Fonte: ${mm.source ?? "—"} · model_hash ${mm.model_hash ?? "—"} · ${Object.entries(mm.markets).map(([k, v]) => `${k}: ${v}`).join(" · ")}`}>
        <span className="block text-[10px] font-semibold uppercase text-ink-3">Market model</span>
        <span className={chip(mm.status.startsWith("PROVEN"))}>{mm.status}</span>
      </span>
      <span title={`buckets: ${(se.buckets_present ?? []).join(", ") || "—"} · closing em ${int(se.closing_events ?? 0)} eventos · CLV N=${int(se.clv_n ?? 0)}`}>
        <span className="block text-[10px] font-semibold uppercase text-ink-3">Superbet evidence</span>
        <span className={chip(se.status === "COLLECTING")}>{se.status}</span> {se.events !== null && <span>{int(se.events)} eventos · {int(se.snapshots_pre_kickoff ?? 0)} snapshots pré-kickoff</span>}
      </span>
      <span title="Raw N = seleções liquidadas; Effective N corrige a correlação entre seleções do mesmo jogo (§19).">
        <span className="block text-[10px] font-semibold uppercase text-ink-3">Shadow settled N</span>
        <span className={chip(ss.status === "MEASURABLE", ss.status === "INSUFFICIENT")}>{ss.status}</span> <span>raw {int(ss.raw_n)} · efetivo {int(ss.effective_n)} · {int(ss.events)} jogos</span>
      </span>
      <span title={me.text}>
        <span className="block text-[10px] font-semibold uppercase text-ink-3">Market edge</span>
        <span className={chip(me.status === "PROVEN", true)}>{me.status === "UNPROVEN" ? "MARKET EDGE UNPROVEN" : me.detail}</span>
      </span>
    </div>
  );
}
