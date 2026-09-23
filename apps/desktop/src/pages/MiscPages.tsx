import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fmtDateTime, num, odd, pct, signedPct } from "@edgefut/shared";
import clsx from "clsx";
import { Database, Heart, RefreshCw, Zap } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";

import { EventRow } from "@/components/EventCard";
import { Card, Empty, ErrorBox, GradeBadge, KV, Loading, NoBetChip, PageHeader, SectionTitle, Stat } from "@/components/ui";
import { api } from "@/lib/api";

import { MetricsBox } from "./BacktestPage";

// ---------------------------------------------------------------- Ao vivo
export function LivePage() {
  const q = useQuery({ queryKey: ["live"], queryFn: api.live, refetchInterval: 60000 });
  return (
    <div className="space-y-5">
      <PageHeader title="Ao Vivo" subtitle="Odds e eventos em andamento" />
      {q.data && !q.data.available ? (
        <Empty icon={<Zap size={28} />} title="Ao vivo indisponível nesta versão" detail={q.data.reason} />
      ) : q.data ? (
        <div className="space-y-2">
          {q.data.events.map((e) => (
            <EventRow key={e.id} event={e} />
          ))}
        </div>
      ) : (
        <Loading />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Favoritos
export function FavoritesPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["events", "week-all"], queryFn: () => api.events({ window: "week" }) });
  const fav = useMutation({ mutationFn: ({ id, on }: { id: number; on: boolean }) => api.favorite(id, on), onSuccess: () => qc.invalidateQueries({ queryKey: ["events"] }) });
  const favs = q.data?.events.filter((e) => e.is_favorite) ?? [];
  return (
    <div className="space-y-5">
      <PageHeader title="Favoritos" subtitle="Jogos marcados para acompanhar" />
      {q.isLoading && <Loading />}
      {q.data && favs.length === 0 && <Empty icon={<Heart size={28} />} title="Nenhum favorito" detail="Marque jogos com o coração na lista de jogos ou na página da partida." />}
      <div className="space-y-2">
        {favs.map((e) => (
          <EventRow key={e.id} event={e} onFavorite={(id, on) => fav.mutate({ id, on })} />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Histórico
export function HistoryPage() {
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["history"], queryFn: () => api.history(200) });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const rows = q.data!.snapshots;
  return (
    <div className="space-y-5">
      <PageHeader title="Histórico" subtitle="Snapshots de previsão gravados ANTES do jogo. Nunca são sobrescritos — o resultado é anexado depois." />
      {rows.length === 0 ? (
        <Empty title="Nenhum snapshot ainda" detail="Abra a análise de um jogo futuro para gravar o primeiro snapshot." />
      ) : (
        <div className="card overflow-hidden p-0">
          <table className="w-full text-sm">
            <thead className="bg-bg text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
              <tr>
                <th className="px-4 py-2.5">Gerado</th>
                <th className="px-3 py-2.5">Jogo</th>
                <th className="px-3 py-2.5">Melhor entrada</th>
                <th className="px-3 py-2.5 text-center">Conf.</th>
                <th className="px-3 py-2.5 text-right">DQ</th>
                <th className="px-3 py-2.5">Resultado</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const res = r.result as { hg?: number; ag?: number; outcomes?: Record<string, boolean> } | null;
                const bestKey = r.best ? `${r.best.market_key}|${r.best.selection_key}|${r.best.line}` : null;
                const won = bestKey && res?.outcomes ? res.outcomes[bestKey] : undefined;
                return (
                  <tr key={r.id} className="cursor-pointer border-t border-line/70 hover:bg-gray-50" onClick={() => navigate(`/jogos/${r.event_id}`)}>
                    <td className="px-4 py-2 text-xs text-ink-2">
                      {fmtDateTime(r.created_at)}
                      <div className="text-[10px] text-ink-3">{r.model_version}</div>
                    </td>
                    <td className="px-3 py-2">
                      <div className="text-[10px] font-semibold uppercase text-ink-3">
                        {r.competition_name} · {fmtDateTime(r.kickoff_utc)}
                      </div>
                      <div className="font-semibold">
                        {r.home_name} x {r.away_name}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      {r.best ? (
                        <span>
                          {r.best.market_label} · <b>{r.best.selection_name}</b> @ {odd(r.best.odd)} <span className="text-xs text-ink-2">({r.recommended} rec.)</span>
                        </span>
                      ) : (
                        <NoBetChip reason={r.no_bet_reason ?? "NO_EDGE"} />
                      )}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <GradeBadge grade={r.confidence_grade} />
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{r.data_quality !== null ? `${Math.round(r.data_quality)}%` : "—"}</td>
                    <td className="px-3 py-2">
                      {res && res.hg !== undefined ? (
                        <span className="flex items-center gap-2">
                          <b className="tabular-nums">
                            {res.hg}-{res.ag}
                          </b>
                          {won !== undefined && <span className={clsx("chip", won ? "bg-success-50 text-success" : "bg-danger-50 text-danger")}>{won ? "green" : "red"}</span>}
                        </span>
                      ) : (
                        <span className="text-xs text-ink-3">aguardando</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Fontes
export function SourcesPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["sources"], queryFn: api.sources, refetchInterval: 30000 });
  const refresh = useMutation({
    mutationFn: ({ what, force }: { what: "events" | "odds" | "history" | "settle" | "radar"; force?: boolean }) => api.refreshSource(what, force),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["sources"] }), 1000),
  });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  return (
    <div className="space-y-5">
      <PageHeader
        title="Fontes de dados"
        subtitle="Somente fontes públicas. Sem login, sem credenciais, sem contornar bloqueios. Se uma fonte bloquear, o app registra e segue com o que tem."
        right={
          <>
            <button className="btn-outline" onClick={() => refresh.mutate({ what: "events" })} disabled={refresh.isPending}>
              <RefreshCw size={14} /> Eventos
            </button>
            <button className="btn-outline" onClick={() => refresh.mutate({ what: "odds" })} disabled={refresh.isPending}>
              <RefreshCw size={14} /> Odds
            </button>
            <button className="btn-outline" onClick={() => refresh.mutate({ what: "history", force: true })} disabled={refresh.isPending}>
              <Database size={14} /> Histórico
            </button>
          </>
        }
      />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {d.providers.map((p) => (
          <Card key={p.key}>
            <div className="flex items-start justify-between">
              <div>
                <div className="text-sm font-bold">{p.name}</div>
                <div className="label">{p.kind}</div>
              </div>
              <span className={clsx("chip", p.enabled ? "bg-success-50 text-success" : "bg-gray-100 text-ink-3")}>{p.enabled ? "ativo" : "inativo"}</span>
            </div>
            <p className="mt-2 text-xs text-ink-2">{p.notes}</p>
            <a className="mt-1 block truncate text-xs text-primary hover:underline" href={p.url} target="_blank" rel="noreferrer">
              {p.url}
            </a>
            {p.stats && (
              <div className="mt-2 flex flex-wrap gap-1 text-[11px]">
                {Object.entries(p.stats).map(([k, v]) => (
                  <span key={k} className="rounded bg-bg px-1.5 py-0.5 text-ink-2">
                    {k}: <b>{k === "last" ? fmtDateTime(String(v)) : String(v)}</b>
                  </span>
                ))}
              </div>
            )}
          </Card>
        ))}
      </div>

      <Card>
        <SectionTitle title="Datasets históricos" subtitle={`Parquet em ${d.paths.processed}`} />
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
            <tr>
              <th className="py-1.5">Código</th>
              <th>Competição</th>
              <th className="text-right">Jogos</th>
              <th>Período</th>
              <th className="text-right">Escanteios</th>
              <th className="text-right">Finalizações</th>
              <th className="text-right">Cartões</th>
              <th>Coletado</th>
            </tr>
          </thead>
          <tbody>
            {d.datasets.map((ds) => (
              <tr key={ds.code} className="border-t border-line/70">
                <td className="py-1.5 font-mono text-xs">{ds.code}</td>
                <td>{ds.label}</td>
                <td className="text-right tabular-nums">{ds.rows.toLocaleString("pt-BR")}</td>
                <td className="text-xs text-ink-2">
                  {ds.first_date} → {ds.last_date}
                </td>
                <td className="text-right tabular-nums">{ds.coverage ? `${ds.coverage.corners_pct}%` : "—"}</td>
                <td className="text-right tabular-nums">{ds.coverage ? `${ds.coverage.shots_pct}%` : "—"}</td>
                <td className="text-right tabular-nums">{ds.coverage ? `${ds.coverage.cards_pct}%` : "—"}</td>
                <td className="text-xs text-ink-2">{fmtDateTime(ds.collected_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Competições na oferta" subtitle={`${d.competitions.filter((c) => c.supported).length} de ${d.competitions.length} com histórico mapeado`} />
          <div className="max-h-80 space-y-1 overflow-y-auto text-sm">
            {d.competitions.map((c) => (
              <div key={c.id} className="flex items-center justify-between gap-2 rounded px-2 py-1 hover:bg-gray-50">
                <span className="truncate">
                  {c.name} <span className="text-xs text-ink-3">{c.category}</span>
                </span>
                <span className={clsx("chip", c.supported ? "bg-success-50 text-success" : "bg-gray-100 text-ink-3")}>{c.supported ? (c.dataset_code ?? "INTL") : c.is_womens ? "feminino" : "sem histórico"}</span>
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <SectionTitle title="Log de coleta (24h)" subtitle="Cada requisição HTTP feita pelo engine" />
          <div className="max-h-80 space-y-1 overflow-y-auto text-xs">
            {d.log.map((l, i) => (
              <div key={i} className="flex items-center gap-2 rounded px-2 py-1 hover:bg-gray-50">
                <span className={clsx("chip", l.status === "ok" ? "bg-success-50 text-success" : l.status === "cached" ? "bg-info-50 text-info" : "bg-danger-50 text-danger")}>{l.status}</span>
                <span className="font-medium">{l.provider}</span>
                <span className="truncate text-ink-2">{l.url}</span>
                <span className="ml-auto shrink-0 tabular-nums text-ink-3">
                  {l.http_status ?? ""} {l.latency_ms ? `${l.latency_ms}ms` : ""} · {fmtDateTime(l.collected_at)}
                </span>
              </div>
            ))}
          </div>
          {Object.entries(d.hosts).map(([h, s]) => (
            <div key={h} className="mt-2 text-xs text-ink-2">
              {h}: {s.circuit_open ? <b className="text-danger">circuito aberto</b> : "ok"} · falhas consecutivas {s.consecutive_failures}
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Modelos
export function ModelsPage() {
  const q = useQuery({ queryKey: ["models"], queryFn: api.models });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  return (
    <div className="space-y-5">
      <PageHeader title="Modelos" subtitle={`Versões congeladas em cada snapshot de previsão · app v${d.app_version}`} />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {d.models.map((m) => (
          <Card key={m.key}>
            <div className="flex items-center justify-between">
              <div className="text-sm font-bold capitalize">{m.key.replace("_", " ")}</div>
              <span className="chip bg-gray-100 text-ink-2 normal-case">{m.version}</span>
            </div>
            <p className="mt-2 text-xs leading-relaxed text-ink-2">{m.description}</p>
          </Card>
        ))}
      </div>
      <Card>
        <SectionTitle title="Parâmetros ativos" />
        <div className="grid gap-x-8 md:grid-cols-2">
          {Object.entries(d.parameters).map(([k, v]) => (
            <KV key={k} k={k} v={String(v)} />
          ))}
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- Performance
export function PerformancePage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["performance"], queryFn: api.performance });
  const settle = useMutation({ mutationFn: api.settle, onSuccess: () => qc.invalidateQueries({ queryKey: ["performance"] }) });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const o = d.overall;
  return (
    <div className="space-y-5">
      <PageHeader
        title="Performance"
        subtitle={d.note}
        right={
          <button className="btn-outline" onClick={() => settle.mutate()} disabled={settle.isPending}>
            <RefreshCw size={14} className={settle.isPending ? "animate-spin" : ""} /> Liquidar pendentes
          </button>
        }
      />
      {d.settled_snapshots === 0 ? (
        <Empty title="Ainda não há previsões liquidadas" detail="As métricas aparecem depois que jogos analisados terminam e o resultado real é anexado ao snapshot. Nada é retroativo." />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-8">
            <Stat label="Snapshots liquidados" value={d.settled_snapshots} />
            <Stat label="Entradas recomendadas" value={d.recommended_bets} />
            <Stat label="ROI" value={signedPct(o.roi)} accent={(o.roi ?? 0) >= 0 ? "text-success" : "text-danger"} />
            <Stat label="Yield" value={signedPct(o.yield_pct)} />
            <Stat label="Hit rate" value={pct(o.hit_rate, 1)} />
            <Stat label="Brier" value={num(o.brier, 3)} />
            <Stat label="Log loss" value={num(o.log_loss, 3)} />
            <Stat label="Max drawdown" value={num(o.max_drawdown, 1)} accent="text-danger" />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <SectionTitle title="Curva de banca (stake 1u)" />
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={d.equity_curve.map((v, i) => ({ i, v: +v.toFixed(2) }))}>
                  <CartesianGrid stroke="#EEF0F3" vertical={false} />
                  <XAxis dataKey="i" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} width={40} />
                  <RTooltip />
                  <Line type="monotone" dataKey="v" stroke="#FF2638" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </Card>
            <Card>
              <SectionTitle title="Calibração do modelo 1X2" subtitle="Todas as seleções, não só as recomendadas" />
              <KV k="Amostras" v={d.model_1x2_all_selections.bets} />
              <KV k="Brier" v={num(d.model_1x2_all_selections.brier, 3)} />
              <KV k="Log loss" v={num(d.model_1x2_all_selections.log_loss, 3)} />
            </Card>
          </div>
          <Card>
            <SectionTitle title="Por mercado" />
            <div className="grid gap-2 md:grid-cols-3 lg:grid-cols-5">
              {Object.entries(d.by_market).map(([k, m]) => (
                <MetricsBox key={k} title={k} m={m} />
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
