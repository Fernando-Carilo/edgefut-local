import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { MarketOdds, MatchAnalysis, Recommendation, SelectionOdds, TeamProfile } from "@edgefut/contracts";
import { NO_BET_LABELS } from "@edgefut/contracts";
import { fmtDate, fmtDateTime, fmtTime, num, odd, pct, venueLabel } from "@edgefut/shared";
import clsx from "clsx";
import { ArrowDownRight, ArrowUpRight, Check, ExternalLink, Heart, Info, Loader2, MapPin, Plus, RefreshCw, Send, ShieldAlert, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { useParams } from "react-router-dom";

import { RecommendationCard } from "@/components/EventCard";
import { Card, EdgeValue, ErrorBox, GradeBadge, KV, Loading, MeterBar, ProbBar, Score, SectionTitle, StatusChip, Tooltip, VenueChip } from "@/components/ui";
import { api } from "@/lib/api";
import { useUi } from "@/store/ui";

export function MatchPage() {
  const { id } = useParams();
  const eventId = Number(id);
  const qc = useQueryClient();
  const simulations = useUi((s) => s.simulations);
  const setSimulations = useUi((s) => s.setSimulations);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [venueOpen, setVenueOpen] = useState(false);

  const q = useQuery({
    queryKey: ["analysis", eventId, simulations],
    queryFn: () => api.analysis(eventId, { simulations }),
    enabled: Number.isFinite(eventId),
    staleTime: 60000,
  });
  const refresh = useMutation({
    mutationFn: () => api.analysis(eventId, { simulations, refresh: true }),
    onSuccess: (data) => qc.setQueryData(["analysis", eventId, simulations], data),
  });
  const fav = useMutation({
    mutationFn: (on: boolean) => api.favorite(eventId, on),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["analysis", eventId] });
      qc.invalidateQueries({ queryKey: ["events"] });
    },
  });

  if (q.isLoading) return <Loading label="Rodando pipeline: coleta → normalização → modelos → simulação → edge…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const a = q.data!;
  const e = a.event;
  const recommended = a.recommendations.filter((r) => r.status === "RECOMMENDED");
  const watch = a.recommendations.filter((r) => r.status === "WATCH");
  const sim = a.simulation;

  return (
    <div className="space-y-5">
      {/* Cabeçalho */}
      <Card className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-ink-3">
              <span>{e.competition_name}</span>
              <span>·</span>
              <span>
                {fmtDate(e.kickoff_utc)} {fmtTime(e.kickoff_utc)}
              </span>
              {e.event_url && (
                <a href={e.event_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline" onClick={(ev) => ev.stopPropagation()}>
                  Ver na Superbet <ExternalLink size={11} />
                </a>
              )}
            </div>
            <h1 className="mt-1 flex flex-wrap items-center gap-3 text-2xl font-bold tracking-tight">
              <span>{e.home_name}</span>
              <span className="text-ink-3">x</span>
              <span>{e.away_name}</span>
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <VenueChip status={a.venue.status} />
              <button className="btn-ghost text-xs" onClick={() => setVenueOpen(true)}>
                <MapPin size={13} /> {a.venue.name ? `${a.venue.name}${a.venue.city ? `, ${a.venue.city}` : ""}` : "Definir local"}
              </button>
              <Tooltip text={a.venue.note ?? "Sem detalhes."}>
                <Info size={13} className="text-ink-3" />
              </Tooltip>
              {a.warnings.map((w, i) => (
                <span key={i} className="chip bg-warning-50 text-warning">
                  {w}
                </span>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <DataQualityBadge a={a} />
            <div className="text-center">
              <GradeBadge grade={a.confidence.grade} score={a.confidence.score} size="lg" />
              <div className="label mt-1">Confiança</div>
            </div>
            <Score value={a.opportunity_score} label="Opportunity" />
            <div className="flex flex-col gap-1">
              <button className={clsx("btn-outline", e.is_favorite && "text-primary")} onClick={() => fav.mutate(!e.is_favorite)}>
                <Heart size={14} fill={e.is_favorite ? "currentColor" : "none"} /> {e.is_favorite ? "Favorito" : "Favoritar"}
              </button>
              <button className="btn-outline" onClick={() => setSourcesOpen(true)}>
                <Info size={14} /> Ver fontes
              </button>
            </div>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-line pt-3 text-xs text-ink-2">
          <span>
            Simulações:
            <select className="input ml-2 inline-block w-auto py-0.5" value={simulations} onChange={(ev) => setSimulations(Number(ev.target.value))}>
              {[10000, 25000, 50000, 100000].map((n) => (
                <option key={n} value={n}>
                  {n.toLocaleString("pt-BR")}
                </option>
              ))}
            </select>
          </span>
          <span>
            Pipeline {a.pipeline_version} · gerado {fmtDateTime(a.generated_at)}
            {a.snapshot_id ? ` · snapshot #${a.snapshot_id}` : ""}
          </span>
          <button className="btn-ghost ml-auto text-xs" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            <RefreshCw size={13} className={refresh.isPending ? "animate-spin" : ""} /> Atualizar odds e reanalisar
          </button>
        </div>
      </Card>

      {/* Veredito */}
      {a.no_bet.no_bet ? (
        <Card className="flex items-start gap-3 border-l-4 border-l-ink-2 bg-gray-50">
          <ShieldAlert className="mt-0.5 shrink-0 text-ink-2" />
          <div>
            <div className="text-base font-bold">NENHUMA ENTRADA RECOMENDADA</div>
            <div className="text-sm text-ink-2">
              <b>{a.no_bet.reason ? NO_BET_LABELS[a.no_bet.reason] : ""}</b> {a.no_bet.detail ? `— ${a.no_bet.detail}` : ""}
            </div>
          </div>
        </Card>
      ) : recommended.length === 0 ? (
        <Card className="flex items-start gap-3 border-l-4 border-l-warning bg-warning-50/40">
          <ShieldAlert className="mt-0.5 shrink-0 text-warning" />
          <div>
            <div className="text-base font-bold">NENHUMA ENTRADA RECOMENDADA</div>
            <div className="text-sm text-ink-2">Nenhum mercado atinge simultaneamente edge, EV, faixa de odd e confiança mínimos. {watch.length > 0 ? `${watch.length} seleção(ões) em observação abaixo.` : ""}</div>
          </div>
        </Card>
      ) : (
        <section>
          <SectionTitle title="Melhores mercados" subtitle="Somente seleções com edge, EV e confiança acima dos limiares" />
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {recommended.slice(0, 6).map((r, i) => (
              <RecommendationCard key={i} event={e} rec={r} />
            ))}
          </div>
        </section>
      )}

      {/* Força & forma */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <SectionTitle title="Força das equipes" subtitle={a.elo.available ? `ELO ${a.elo.model_version} · ${a.elo.matches_used.toLocaleString("pt-BR")} jogos no ajuste` : "ELO indisponível"} />
          <div className="grid gap-6 md:grid-cols-2">
            <TeamBlock team={a.home} side="home" />
            <TeamBlock team={a.away} side="away" />
          </div>
        </Card>
        <Card>
          <SectionTitle title="Confronto direto" subtitle="Peso menor que a forma atual" />
          {a.h2h ? (
            <>
              <div className="grid grid-cols-3 text-center">
                <div>
                  <div className="text-xl font-bold text-primary">{a.h2h.home_wins}</div>
                  <div className="label">{a.home.name}</div>
                </div>
                <div>
                  <div className="text-xl font-bold text-ink-2">{a.h2h.draws}</div>
                  <div className="label">Empates</div>
                </div>
                <div>
                  <div className="text-xl font-bold text-info">{a.h2h.away_wins}</div>
                  <div className="label">{a.away.name}</div>
                </div>
              </div>
              <div className="mt-3">
                <KV k="Jogos" v={a.h2h.matches} />
                <KV k="Média de gols" v={num(a.h2h.avg_goals, 2)} />
                <KV k="BTTS" v={pct(a.h2h.btts_pct)} />
                <KV k="Over 2.5" v={pct(a.h2h.over25_pct)} />
              </div>
              <div className="mt-2 space-y-1">
                {a.h2h.recent.slice(0, 5).map((m, i) => (
                  <div key={i} className="flex justify-between text-xs text-ink-2">
                    <span>
                      {fmtDate(m.date)} · {m.home} {m.hg}-{m.ag} {m.away}
                    </span>
                    {m.neutral && <span className="chip bg-info-50 text-info">neutro</span>}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="text-sm text-ink-2">Sem confrontos diretos no histórico disponível.</div>
          )}
        </Card>
      </div>

      {/* Modelos e simulação */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <SectionTitle
            title="Probabilidades do modelo"
            subtitle={sim ? `${sim.simulations.toLocaleString("pt-BR")} simulações Monte Carlo sobre ${sim.base_model} · seed ${sim.seed}` : "Sem simulação disponível"}
            right={a.model_disagreement_pp !== null && <span className={clsx("chip", a.model_disagreement_pp > 10 ? "bg-warning-50 text-warning" : "bg-gray-100 text-ink-2")}>Divergência {a.model_disagreement_pp.toFixed(1)} pp</span>}
          />
          {sim ? (
            <div className="space-y-4">
              <ProbBar home={sim.p_home} draw={sim.p_draw} away={sim.p_away} labels={[a.home.name, "Empate", a.away.name]} />
              <div className="grid gap-3 md:grid-cols-3">
                <ModelMini title={`Poisson · ${a.poisson.model_version}`} m={a.poisson} />
                <ModelMini title={`Dixon-Coles · ${a.dixon_coles.model_version}`} m={a.dixon_coles} />
                <div className="rounded-lg bg-bg p-3">
                  <div className="label">ELO · {a.elo.model_version}</div>
                  {a.elo.available ? (
                    <>
                      <div className="mt-1 text-sm tabular-nums">
                        {pct(a.elo.p_home)} · {pct(a.elo.p_draw)} · {pct(a.elo.p_away)}
                      </div>
                      <div className="text-xs text-ink-2">
                        {num(a.elo.home_elo, 0)} vs {num(a.elo.away_elo, 0)} · HA {a.elo.home_advantage_points} pts
                      </div>
                    </>
                  ) : (
                    <div className="text-xs text-ink-2">{a.elo.note}</div>
                  )}
                </div>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <div>
                  <div className="label mb-1">Gols esperados</div>
                  <div className="flex items-center gap-3">
                    <span className="text-2xl font-bold tabular-nums text-primary">{num(sim.expected_goals_home, 2)}</span>
                    <MeterBar value={sim.expected_goals_home} max={sim.expected_goals_home + sim.expected_goals_away} color="bg-primary" />
                    <span className="text-2xl font-bold tabular-nums text-info">{num(sim.expected_goals_away, 2)}</span>
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
                    {Object.entries(sim.over).map(([l, p]) => (
                      <div key={l} className="rounded-md bg-bg px-2 py-1">
                        Over {l} <b className="tabular-nums">{pct(p)}</b>
                      </div>
                    ))}
                    <div className="rounded-md bg-bg px-2 py-1">
                      BTTS <b className="tabular-nums">{pct(sim.btts)}</b>
                    </div>
                  </div>
                </div>
                <div>
                  <div className="label mb-1">Distribuição de gols totais</div>
                  <ResponsiveContainer width="100%" height={110}>
                    <BarChart data={sim.total_goals_dist.map((p, i) => ({ g: i === 6 ? "6+" : String(i), p: +(p * 100).toFixed(1) }))}>
                      <XAxis dataKey="g" tick={{ fontSize: 11 }} />
                      <RTooltip formatter={(v) => `${v}%`} />
                      <Bar dataKey="p" fill="#FF2638" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-sm text-ink-2">
              {a.dixon_coles.note ?? a.poisson.note ?? "Histórico insuficiente para estimar o modelo de gols."}
            </div>
          )}
        </Card>
        <Card>
          <SectionTitle title="Placares prováveis" subtitle="Frequência nas simulações — probabilidade, nunca certeza" />
          {sim ? (
            <>
              <div className="mb-3 rounded-lg bg-primary-50 p-3 text-center">
                <div className="label text-primary">Placar mais provável</div>
                <div className="text-3xl font-extrabold tabular-nums text-primary">{sim.most_likely_score.replace("-", " – ")}</div>
                <div className="text-xs text-ink-2">{pct(sim.top_scores[0]?.[1], 1)} das simulações</div>
              </div>
              <div className="space-y-1.5">
                {sim.top_scores.slice(0, 5).map(([s, p]) => (
                  <div key={s} className="flex items-center gap-2 text-sm">
                    <span className="w-12 font-semibold tabular-nums">{s.replace("-", "–")}</span>
                    <MeterBar value={p} max={sim.top_scores[0][1]} color="bg-ink-2" />
                    <span className="w-12 text-right tabular-nums text-ink-2">{pct(p, 1)}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="text-sm text-ink-2">Indisponível.</div>
          )}
        </Card>
      </div>

      {/* Finalizações, escanteios, cartões */}
      <div className="grid gap-4 md:grid-cols-3">
        <CountCard title="Finalizações" d={a.shots} home={a.home.name} away={a.away.name} extraKeys={[["sot_home", "No alvo (casa)"], ["sot_away", "No alvo (fora)"]]} />
        <CountCard title="Escanteios" d={a.corners} home={a.home.name} away={a.away.name} />
        <CountCard title="Cartões" d={a.cards} home={a.home.name} away={a.away.name} />
      </div>

      {/* Odds e todos os mercados */}
      <MarketsTable a={a} />

      {/* Movimento de odds */}
      <OddsMovement eventId={eventId} markets={a.markets} />

      {/* Explicação + Edge AI */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Explicação" subtitle="Gerada a partir dos números acima — nunca inventa estatísticas" />
          <p className="text-sm leading-relaxed">{a.explanation}</p>
          {watch.length > 0 && (
            <div className="mt-3">
              <div className="label mb-1">Em observação</div>
              <div className="space-y-1">
                {watch.slice(0, 5).map((r, i) => (
                  <WatchRow key={i} r={r} />
                ))}
              </div>
            </div>
          )}
        </Card>
        <EdgeAiChat eventId={eventId} />
      </div>

      {sourcesOpen && <SourcesDrawer a={a} onClose={() => setSourcesOpen(false)} />}
      {venueOpen && <VenueDialog a={a} onClose={() => setVenueOpen(false)} onSaved={() => qc.invalidateQueries({ queryKey: ["analysis", eventId] })} />}
    </div>
  );
}

function DataQualityBadge({ a }: { a: MatchAnalysis }) {
  const dq = a.data_quality;
  const color = dq.score >= 80 ? "text-success" : dq.score >= 60 ? "text-warning" : "text-danger";
  return (
    <Tooltip text={dq.checks.map((c) => `${c.ok ? "✓" : "✗"} ${c.label}`).join("\n")}>
      <div className="text-center">
        <div className={clsx("text-2xl font-bold tabular-nums", color)}>{Math.round(dq.score)}%</div>
        <div className="label">Qualidade</div>
      </div>
    </Tooltip>
  );
}

function TeamBlock({ team, side }: { team: TeamProfile; side: "home" | "away" }) {
  const w10 = team.windows["all_10"];
  const color = side === "home" ? "bg-primary" : "bg-info";
  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <div className="text-base font-bold">{team.name}</div>
          <div className="text-xs text-ink-2">
            {team.canonical && team.canonical !== team.name ? `${team.canonical} · ` : ""}
            {team.dataset_code ?? "sem dataset"} · match {team.match_method} {team.match_confidence ? `(${pct(team.match_confidence)})` : ""}
          </div>
        </div>
        <div className="text-right">
          <div className="text-xl font-bold tabular-nums">{team.strength_score !== null ? Math.round(team.strength_score) : "—"}</div>
          <div className="label">Força</div>
        </div>
      </div>
      <MeterBar value={team.strength_score ?? 0} color={color} className="mt-2" />
      <div className="mt-3 flex items-center gap-1">
        {team.form.length === 0 && <span className="text-xs text-ink-3">Sem forma disponível</span>}
        {team.form.slice(0, 10).map((r, i) => (
          <span key={i} className={clsx("grid h-6 w-6 place-items-center rounded-md text-[11px] font-bold text-white", r === "V" ? "bg-success" : r === "E" ? "bg-ink-3" : "bg-danger")} title={i === 0 ? "mais recente" : ""}>
            {r}
          </span>
        ))}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-4 text-sm">
        <KV k="ELO" v={num(team.elo, 0)} />
        <KV k="Amostra" v={`${team.sample_size} jogos`} />
        <KV k="Ataque (rel.)" v={num(team.attack, 2)} />
        <KV k="Defesa (rel.)" v={num(team.defense, 2)} />
        {w10 && (
          <>
            <KV k="Gols pró (10)" v={num(w10.goals_for, 2)} />
            <KV k="Gols contra (10)" v={num(w10.goals_against, 2)} />
            <KV k="Pts/jogo (10)" v={num(w10.points_per_game, 2)} />
            <KV k="Clean sheets" v={pct(w10.clean_sheet_pct)} />
            {w10.shots_for !== null && <KV k="Finalizações" v={num(w10.shots_for, 1)} />}
            {w10.sot_for !== null && <KV k="No alvo" v={num(w10.sot_for, 1)} />}
            {w10.corners_for !== null && <KV k="Escanteios" v={num(w10.corners_for, 1)} />}
            {w10.cards !== null && <KV k="Cartões" v={num(w10.cards, 1)} />}
            <KV k="Over 2.5" v={pct(w10.over25_pct)} />
            <KV k="BTTS" v={pct(w10.btts_pct)} />
          </>
        )}
      </div>
      {team.recent.length > 0 && (
        <div className="mt-2 space-y-0.5">
          {team.recent.slice(0, 5).map((m, i) => (
            <div key={i} className="flex justify-between text-xs text-ink-2">
              <span className="truncate">
                {fmtDate(m.date)} · {m.home} <b className="text-ink">{m.hg}-{m.ag}</b> {m.away}
              </span>
              <span className={clsx("font-bold", m.result_for_team === "V" ? "text-success" : m.result_for_team === "D" ? "text-danger" : "text-ink-3")}>{m.result_for_team}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ModelMini({ title, m }: { title: string; m: MatchAnalysis["poisson"] }) {
  return (
    <div className="rounded-lg bg-bg p-3">
      <div className="label">{title}</div>
      {m.available ? (
        <>
          <div className="mt-1 text-sm tabular-nums">
            {pct(m.p_home)} · {pct(m.p_draw)} · {pct(m.p_away)}
          </div>
          <div className="text-xs text-ink-2">
            λ {num(m.lambda_home, 2)} / {num(m.lambda_away, 2)}
            {m.rho !== null ? ` · ρ ${num(m.rho, 3)}` : ""} · {m.fit_matches} jogos
          </div>
        </>
      ) : (
        <div className="text-xs text-ink-2">{m.note ?? "indisponível"}</div>
      )}
    </div>
  );
}

function CountCard({ title, d, home, away, extraKeys }: { title: string; d: MatchAnalysis["corners"]; home: string; away: string; extraKeys?: [string, string][] }) {
  return (
    <Card>
      <SectionTitle title={title} subtitle={d.available ? `${d.model_version} · amostra ${d.sample_size}` : "Sem dados suficientes"} />
      {d.available ? (
        <>
          <div className="flex items-end justify-between">
            <div>
              <div className="text-2xl font-bold tabular-nums">{num(d.expected_total, 1)}</div>
              <div className="label">esperado no jogo</div>
            </div>
            <div className="text-right text-sm tabular-nums">
              <div>
                <span className="text-primary">{home}</span> {num(d.expected_home, 1)}
              </div>
              <div>
                <span className="text-info">{away}</span> {num(d.expected_away, 1)}
              </div>
            </div>
          </div>
          {d.likely_range && <div className="mt-1 text-xs text-ink-2">Intervalo provável: {d.likely_range[0]} – {d.likely_range[1]}</div>}
          {Object.keys(d.over).length > 0 && (
            <div className="mt-2 grid grid-cols-3 gap-1.5 text-xs">
              {Object.entries(d.over).map(([l, p]) => (
                <div key={l} className="rounded-md bg-bg px-2 py-1">
                  O{l} <b className="tabular-nums">{pct(p)}</b>
                </div>
              ))}
            </div>
          )}
          {extraKeys && (
            <div className="mt-2 text-xs text-ink-2">
              {extraKeys.map(([k, l]) => (d.extra[k] !== undefined ? <div key={k}>{l}: <b>{num(d.extra[k], 1)}</b></div> : null))}
            </div>
          )}
          {d.note && <div className="mt-2 text-xs text-ink-3">{d.note}</div>}
        </>
      ) : (
        <div className="text-sm text-ink-2">{d.note ?? "O dataset desta competição não traz esta estatística."}</div>
      )}
    </Card>
  );
}

function WatchRow({ r }: { r: Recommendation }) {
  return (
    <div className="flex items-center justify-between rounded-md bg-bg px-3 py-1.5 text-xs">
      <span>
        <b>{r.market_label}</b> · {r.selection_name} @ {odd(r.odd)}
      </span>
      <span className="flex items-center gap-2">
        <EdgeValue value={r.edge_pp} /> <GradeBadge grade={r.confidence_grade} size="sm" /> <span className="text-ink-3">{r.reasons.join(", ")}</span>
      </span>
    </div>
  );
}

function MarketsTable({ a }: { a: MatchAnalysis }) {
  const [filter, setFilter] = useState("");
  const addLeg = useUi((s) => s.addLeg);
  const groups = useMemo(() => {
    const g = new Map<string, MarketOdds[]>();
    a.markets.forEach((m) => g.set(m.label, [...(g.get(m.label) ?? []), m]));
    return [...g.entries()].filter(([l]) => !filter || l.toLowerCase().includes(filter.toLowerCase()));
  }, [a.markets, filter]);
  const recByKey = useMemo(() => {
    const m = new Map<string, Recommendation>();
    a.recommendations.forEach((r) => m.set(`${r.market_key}|${r.selection_key}|${r.line}`, r));
    return m;
  }, [a.recommendations]);

  const collected = a.markets[0]?.collected_at;
  return (
    <Card>
      <SectionTitle
        title={`Todos os mercados (${a.markets.length})`}
        subtitle={`Odds Superbet coletadas ${collected ? fmtDateTime(collected) : "—"} · probabilidade justa só quando o conjunto de seleções está completo`}
        right={<input className="input w-56" placeholder="Filtrar mercado…" value={filter} onChange={(e) => setFilter(e.target.value)} />}
      />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {groups.map(([label, ms]) => (
          <div key={label} className="rounded-lg border border-line">
            <div className="flex items-center justify-between border-b border-line bg-bg px-3 py-1.5">
              <span className="text-xs font-bold uppercase tracking-wide">{label}</span>
              <span className="text-[10px] text-ink-3">{ms[0].margin_removed && ms[0].overround !== null ? `margem ${(ms[0].overround * 100).toFixed(1)}%` : "margem n/d"}</span>
            </div>
            <div className="max-h-64 overflow-y-auto">
              {ms.map((m) =>
                m.selections.map((s) => {
                  const r = recByKey.get(`${m.market_key}|${s.key}|${m.line}`);
                  return <SelRow key={`${m.line}-${s.key}`} m={m} s={s} r={r} onAdd={() => addLeg({ event_id: a.event.id, market_key: m.market_key, selection_key: s.key, line: m.line, odd: s.price, label: `${m.label}: ${s.name}${m.line !== null ? ` ${m.line}` : ""}`, event_label: `${a.home.name} x ${a.away.name}`, model_prob: s.model_prob })} />;
                }),
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function SelRow({ m, s, r, onAdd }: { m: MarketOdds; s: SelectionOdds; r?: Recommendation; onAdd: () => void }) {
  const status = r?.status;
  return (
    <div className={clsx("flex items-center gap-2 border-b border-line/60 px-3 py-1.5 text-xs last:border-0", status === "RECOMMENDED" && "bg-success-50/60")}>
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">
          {s.name}
          {m.line !== null && !s.name.includes(String(m.line)) ? ` ${m.line}` : ""}
        </div>
        <div className="text-[10px] text-ink-3">
          impl. {pct(s.implied, 1)}
          {s.fair !== null ? ` · justa ${pct(s.fair, 1)}` : ""}
          {s.model_prob !== null ? ` · modelo ${pct(s.model_prob, 1)}` : ""}
        </div>
      </div>
      <div className="w-14 text-right">{s.edge_pp !== null ? <EdgeValue value={s.edge_pp} /> : <span className="text-ink-3">—</span>}</div>
      <div className="flex items-center gap-1">
        {s.direction && s.direction !== "flat" && (
          <span className={clsx("inline-flex items-center", s.direction === "down" ? "text-primary" : "text-info")} title={`Abertura ${odd(s.opening_price)} → ${odd(s.price)} (${s.movement_pct}%)`}>
            {s.direction === "down" ? <ArrowDownRight size={12} /> : <ArrowUpRight size={12} />}
          </span>
        )}
        <span className="odd-pill">{odd(s.price)}</span>
        {status && <StatusChip status={status} />}
        <button className="btn-ghost p-1" title="Adicionar à múltipla" onClick={onAdd}>
          <Plus size={12} />
        </button>
      </div>
    </div>
  );
}

function OddsMovement({ eventId, markets }: { eventId: number; markets: MarketOdds[] }) {
  const q = useQuery({ queryKey: ["odds-history", eventId], queryFn: () => api.oddsHistory(eventId) });
  const keys = useMemo(() => {
    const wanted = ["1X2|HOME|None", "1X2|DRAW|None", "1X2|AWAY|None", "TOTAL_GOALS|OVER|2.5", "TOTAL_GOALS|UNDER|2.5", "BTTS|YES|None"];
    return wanted.filter((k) => q.data?.series[k]);
  }, [q.data]);
  if (!q.data || keys.length === 0) return null;
  const points = new Map<string, Record<string, number | string>>();
  keys.forEach((k) => q.data!.series[k].forEach((p) => points.set(p.collected_at, { ...(points.get(p.collected_at) ?? { t: fmtTime(p.collected_at) }), [k]: p.price })));
  const data = [...points.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([, v]) => v);
  const colors = ["#FF2638", "#98A2B3", "#2E90FA", "#12B76A", "#F79009", "#7A5AF8"];
  const opening = markets.find((m) => m.market_key === "1X2")?.selections;
  return (
    <Card>
      <SectionTitle title="Movimento de odds" subtitle={`${data.length} coletas locais · abertura = primeira coleta feita por este app`} />
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={data}>
              <CartesianGrid stroke="#EEF0F3" vertical={false} />
              <XAxis dataKey="t" tick={{ fontSize: 11 }} />
              <YAxis domain={["auto", "auto"]} tick={{ fontSize: 11 }} width={40} />
              <RTooltip />
              {keys.map((k, i) => (
                <Line key={k} type="monotone" dataKey={k} name={k.replace("|None", "").replace("|", " ")} stroke={colors[i]} dot={false} strokeWidth={2} connectNulls />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="space-y-1.5 text-sm">
          {opening?.map((s) => (
            <div key={s.key} className="flex items-center justify-between rounded-md bg-bg px-3 py-1.5">
              <span className="font-medium">{s.name}</span>
              <span className="tabular-nums">
                {odd(s.opening_price)} → <b>{odd(s.price)}</b>{" "}
                <span className={clsx("text-xs", (s.movement_pct ?? 0) < -0.5 ? "text-primary" : (s.movement_pct ?? 0) > 0.5 ? "text-info" : "text-ink-3")}>
                  {s.movement_pct !== null ? `${s.movement_pct > 0 ? "+" : ""}${s.movement_pct.toFixed(1)}%` : "—"}
                </span>
              </span>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function EdgeAiChat({ eventId }: { eventId: number }) {
  const [q, setQ] = useState("");
  const [log, setLog] = useState<{ role: "user" | "ai"; text: string; engine?: string }[]>([]);
  const ask = useMutation({
    mutationFn: (question: string) => api.chat(eventId, question),
    onSuccess: (r) => setLog((l) => [...l, { role: "ai", text: r.answer, engine: r.engine }]),
    onError: (e) => setLog((l) => [...l, { role: "ai", text: `Erro: ${(e as Error).message}` }]),
  });
  const send = (text: string) => {
    if (!text.trim()) return;
    setLog((l) => [...l, { role: "user", text }]);
    ask.mutate(text);
    setQ("");
  };
  const suggestions = ["Quem é mais forte?", "Qual o placar mais provável?", "Vale entrar em Over 2.5?", "Como está a fase dos times?", "Quantos escanteios esperar?", "Onde é o jogo?"];
  return (
    <Card className="flex flex-col">
      <SectionTitle title="Edge AI" subtitle="Responde apenas com os dados desta análise e sempre cita os números" />
      <div className="flex-1 space-y-2 overflow-y-auto rounded-lg bg-bg p-3" style={{ minHeight: 180, maxHeight: 320 }}>
        {log.length === 0 && <div className="text-xs text-ink-3">Pergunte algo sobre este jogo. Sem dados suficientes, o Edge AI dirá isso explicitamente.</div>}
        {log.map((m, i) => (
          <div key={i} className={clsx("max-w-[90%] rounded-lg px-3 py-2 text-sm", m.role === "user" ? "ml-auto bg-ink text-white" : "bg-card shadow-card")}>
            {m.text}
            {m.engine && <div className="mt-1 text-[10px] uppercase tracking-wide text-ink-3">{m.engine === "ollama" ? "ollama (reescrita)" : "templates"}</div>}
          </div>
        ))}
        {ask.isPending && (
          <div className="flex items-center gap-1 text-xs text-ink-3">
            <Loader2 size={12} className="animate-spin" /> pensando…
          </div>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {suggestions.map((s) => (
          <button key={s} className="rounded-full border border-line px-2.5 py-0.5 text-[11px] text-ink-2 hover:border-ink-3 hover:text-ink" onClick={() => send(s)}>
            {s}
          </button>
        ))}
      </div>
      <form
        className="mt-2 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          send(q);
        }}
      >
        <input className="input" placeholder="Ex.: quantos gols esperar?" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn-primary" type="submit" disabled={ask.isPending}>
          <Send size={14} />
        </button>
      </form>
    </Card>
  );
}

function SourcesDrawer({ a, onClose }: { a: MatchAnalysis; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-ink/30 backdrop-blur-sm" onClick={onClose}>
      <div className="h-full w-full max-w-xl overflow-y-auto bg-card p-5 shadow-hover" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold">Fontes desta análise</h2>
          <button className="btn-ghost" onClick={onClose}>
            <X size={16} />
          </button>
        </div>
        <p className="mb-4 text-xs text-ink-2">Toda estatística exibida carrega origem, URL, data de coleta, confiança e tamanho da amostra. Nenhum dado é inventado; quando falta, a tela diz que falta.</p>
        <div className="space-y-2">
          {a.sources.map((s, i) => (
            <div key={i} className="rounded-lg border border-line p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="font-semibold">{s.source}</span>
                <span className="text-xs text-ink-2">
                  conf. {pct(s.confidence)} · n={s.sample_size}
                </span>
              </div>
              {s.field && <div className="text-xs text-ink-2">Campo: {s.field}</div>}
              {s.source_url && (
                <a className="mt-1 inline-flex items-center gap-1 break-all text-xs text-primary hover:underline" href={s.source_url} target="_blank" rel="noreferrer">
                  {s.source_url} <ExternalLink size={11} />
                </a>
              )}
              <div className="text-xs text-ink-3">Coletado: {fmtDateTime(s.collected_at)}</div>
              {s.note && <div className="mt-1 text-xs text-ink-2">{s.note}</div>}
            </div>
          ))}
        </div>
        <h3 className="mb-2 mt-5 text-sm font-bold">Tentativas de coleta</h3>
        <div className="space-y-1">
          {a.source_attempts.map((s, i) => (
            <div key={i} className="flex items-start gap-2 text-xs">
              <span className={clsx("chip", s.status === "ok" || s.status === "cached" ? "bg-success-50 text-success" : "bg-warning-50 text-warning")}>{s.status}</span>
              <span className="font-medium">{s.provider}</span>
              <span className="text-ink-2">{s.detail}</span>
            </div>
          ))}
        </div>
        <h3 className="mb-2 mt-5 text-sm font-bold">Qualidade dos dados · {Math.round(a.data_quality.score)}%</h3>
        <div className="space-y-1">
          {a.data_quality.checks.map((c, i) => (
            <div key={i} className="flex items-center gap-2 text-xs">
              {c.ok ? <Check size={13} className="text-success" /> : <X size={13} className="text-danger" />}
              <span className={c.ok ? "" : "text-ink-2"}>{c.label}</span>
              <span className="ml-auto text-ink-3">peso {c.weight}</span>
            </div>
          ))}
        </div>
        <h3 className="mb-2 mt-5 text-sm font-bold">Confiança · {Math.round(a.confidence.score)}/100 ({a.confidence.grade})</h3>
        <div className="space-y-1">
          {a.confidence.components.map((c, i) => (
            <div key={i} className="text-xs">
              <div className="flex justify-between">
                <span>{c.name}</span>
                <span className="tabular-nums text-ink-2">
                  {pct(c.value)} × peso {c.weight}
                </span>
              </div>
              <MeterBar value={c.value * 100} color="bg-ink-2" className="mt-0.5 h-1.5" />
              {c.note && <div className="text-ink-3">{c.note}</div>}
            </div>
          ))}
        </div>
        <h3 className="mb-2 mt-5 text-sm font-bold">Versões dos modelos</h3>
        <div className="flex flex-wrap gap-1">
          {Object.entries(a.model_versions).map(([k, v]) => (
            <span key={k} className="chip bg-gray-100 text-ink-2 normal-case">
              {k}: {v}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function VenueDialog({ a, onClose, onSaved }: { a: MatchAnalysis; onClose: () => void; onSaved: () => void }) {
  const [neutral, setNeutral] = useState(a.venue.neutral ?? false);
  const [name, setName] = useState(a.venue.name ?? "");
  const [city, setCity] = useState(a.venue.city ?? "");
  const [country, setCountry] = useState(a.venue.country ?? "");
  const save = useMutation({
    mutationFn: () => api.setVenue(a.event.id, { neutral, name: name || null, city: city || null, country: country || null }),
    onSuccess: () => {
      onSaved();
      onClose();
    },
  });
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/30 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="card w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-bold">Local da partida</h2>
        <p className="mt-1 text-xs text-ink-2">
          Detecção atual: <b>{venueLabel(a.venue.status)}</b> (fonte {a.venue.source ?? "—"}, confiança {pct(a.venue.confidence)}). {a.venue.note}
        </p>
        <div className="mt-4 space-y-3">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={neutral} onChange={(e) => setNeutral(e.target.checked)} /> Campo neutro (remove a vantagem de mando)
          </label>
          <input className="input" placeholder="Estádio" value={name} onChange={(e) => setName(e.target.value)} />
          <div className="grid grid-cols-2 gap-2">
            <input className="input" placeholder="Cidade" value={city} onChange={(e) => setCity(e.target.value)} />
            <input className="input" placeholder="País" value={country} onChange={(e) => setCountry(e.target.value)} />
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-outline" onClick={onClose}>
            Cancelar
          </button>
          <button className="btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>
            Salvar e reanalisar
          </button>
        </div>
      </div>
    </div>
  );
}
