import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CalibrationBucket, GroupMetrics, LiveEvent, SourceCard as SourceCardT } from "@edgefut/contracts";
import { fmtDateTime, fmtTime, int, num, odd, parseUtc, pct, relativeTime, signedPct } from "@edgefut/shared";
import clsx from "clsx";
import { Database, Eye, Heart, Info, RefreshCw, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip as RTooltip, XAxis, YAxis, ZAxis } from "recharts";

import { EventRow } from "@/components/EventCard";
import { ageLabel, Card, Empty, ErrorBox, EvidenceChip, FreshnessChip, GradeBadge, HealthChip, HealthDot, KV, Loading, NoBetChip, PageHeader, SectionTitle, Segmented, Stat, Tooltip } from "@/components/ui";
import { api } from "@/lib/api";

import { MarketEfficiencyLab, SuperbetTab } from "./MarketAwarePanels";

// ---------------------------------------------------------------- Ao vivo (observação)
export function LivePage() {
  const q = useQuery({ queryKey: ["live"], queryFn: () => api.live(), refetchInterval: 15000 });
  const [tick, setTick] = useState(0);
  // relógio local de 1 s para "atualizadas há N s" sem depender do refetch
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);
  if (q.isLoading) return <Loading label="Consultando jogos em andamento…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const updated = parseUtc(d.updated_at);
  const age = updated ? Math.max(0, Math.round((Date.now() - updated.getTime()) / 1000)) : d.age_seconds;
  void tick;
  return (
    <div className="space-y-5">
      <PageHeader
        title="Ao Vivo"
        subtitle="Modo observação: placar, minuto, estatísticas expostas pela fonte e odds. Nenhuma recomendação, edge ou EV é calculado durante o jogo."
        right={
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <FreshnessChip status={d.freshness.status} />
            <span className="text-ink-2">Odds atualizadas {ageLabel(age)}</span>
            <span className="text-ink-3">· poll a cada {d.poll_interval_s} s{d.backoff_s > 0 ? ` · backoff ${d.backoff_s} s` : ""}</span>
            <button className="btn-outline" onClick={() => q.refetch()} disabled={q.isFetching}>
              <RefreshCw size={14} className={q.isFetching ? "animate-spin" : ""} />
            </button>
          </div>
        }
      />
      <Card className="flex items-start gap-3 border-l-4 border-l-info bg-info-50/40">
        <Eye className="mt-0.5 shrink-0 text-info" size={18} />
        <div className="text-sm">
          <div className="font-bold">OBSERVATION ONLY</div>
          <div className="text-ink-2">{d.notice}</div>
          {d.last_error && (
            <div className="mt-1 text-xs text-danger">
              Última falha na coleta: {d.last_error} · {d.consecutive_errors} erro(s) consecutivo(s)
            </div>
          )}
        </div>
      </Card>
      {!d.available || d.events.length === 0 ? (
        <Empty icon={<Zap size={28} />} title={d.available ? "Nenhum jogo em andamento agora" : "Ao vivo indisponível"} detail={d.available ? `A fonte não lista jogos em andamento neste momento (${d.polls} consultas feitas).` : d.last_error ?? "A fonte pública não respondeu. O app registra a indisponibilidade e tenta novamente com backoff."} />
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {d.events.map((e) => (
            <LiveCard key={e.event_id} e={e} />
          ))}
        </div>
      )}
    </div>
  );
}

const STAT_LABELS: Record<string, string> = {
  corners_home: "Escanteios",
  corners_away: "Escanteios",
  yellow_home: "Amarelos",
  yellow_away: "Amarelos",
  red_home: "Vermelhos",
  red_away: "Vermelhos",
  shots_home: "Finalizações",
  shots_away: "Finalizações",
  sot_home: "No alvo",
  sot_away: "No alvo",
  possession_home: "Posse %",
  possession_away: "Posse %",
};

function LiveCard({ e }: { e: LiveEvent }) {
  const navigate = useNavigate();
  const pairs = Object.keys(e.stats)
    .filter((k) => k.endsWith("_home"))
    .map((k) => k.replace(/_home$/, ""))
    .filter((base) => e.stats[`${base}_home`] !== null || e.stats[`${base}_away`] !== null);
  const main = e.markets.find((m) => m.market_key === "1X2") ?? e.markets[0];
  const moves = Object.entries(e.movement);
  return (
    <Card className="flex flex-col gap-3" hover onClick={() => navigate(`/jogos/${e.event_id}`)}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[11px] font-semibold uppercase tracking-wide text-ink-3">
            {e.competition_name} {e.category_name ? `· ${e.category_name}` : ""}
          </div>
          <div className="mt-0.5 flex items-center gap-3 text-base font-semibold">
            <span className="truncate">{e.home_name}</span>
            <span className="rounded-md bg-ink px-2 py-0.5 text-sm font-bold tabular-nums text-white">
              {e.home_score ?? "–"} : {e.away_score ?? "–"}
            </span>
            <span className="truncate">{e.away_name}</span>
          </div>
        </div>
        <div className="text-right text-xs">
          <span className="chip bg-danger-50 text-danger">
            <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-danger" />
            {e.period ?? e.status}
            {e.minute !== null ? ` ${e.minute}'` : ""}
            {e.stoppage_time ? `+${e.stoppage_time}` : ""}
          </span>
          <div className="mt-1 text-ink-3">início {fmtTime(e.kickoff_utc)}</div>
        </div>
      </div>
      {pairs.length > 0 ? (
        <div className="grid grid-cols-3 gap-x-2 gap-y-0.5 text-xs">
          {pairs.map((base) => (
            <div key={base} className="contents">
              <span className="text-right tabular-nums">{e.stats[`${base}_home`] ?? "—"}</span>
              <span className="text-center text-ink-3">{STAT_LABELS[`${base}_home`] ?? base}</span>
              <span className="tabular-nums">{e.stats[`${base}_away`] ?? "—"}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-xs text-ink-3">A fonte não expõe estatísticas ao vivo para este jogo. Nada é estimado.</div>
      )}
      {main && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="label mr-1">{main.label}</span>
          {main.selections.map((s) => (
            <span key={s.key} className="odd-pill" title={`${s.name} · implícita ${pct(s.implied, 1)}`}>
              {s.name} {odd(s.price)}
            </span>
          ))}
          <span className="ml-auto text-[11px] text-ink-3">
            {e.market_count} mercados{e.full_markets ? "" : " (resumo)"} · odds {e.odds_collected_at ? relativeTime(e.odds_collected_at) : "—"}
          </span>
        </div>
      )}
      {moves.length > 0 && (
        <div className="flex flex-wrap gap-1 text-[11px]">
          {moves.slice(0, 4).map(([k, m]) => (
            <span key={k} className={clsx("rounded px-1.5 py-0.5", m.pct < 0 ? "bg-primary-50 text-primary" : "bg-info-50 text-info")}>
              {k.replace("|None", "").replace(/\|/g, " ")}: {odd(m.from)} → {odd(m.to)} ({m.pct > 0 ? "+" : ""}
              {m.pct.toFixed(1)}%)
            </span>
          ))}
        </div>
      )}
    </Card>
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

// ---------------------------------------------------------------- Fontes V2
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
        {d.cards.map((c) => (
          <SourceCardView key={c.key} c={c} />
        ))}
      </div>

      <Card>
        <SectionTitle title="Datasets históricos" subtitle={`Parquet em ${d.paths.processed} · datasets sem odds históricas geram evidência MODEL_ONLY`} />
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
            <tr>
              <th className="py-1.5">Código</th>
              <th>Competição</th>
              <th className="text-right">Jogos</th>
              <th>Período</th>
              <th>Odds</th>
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
                <td>{ds.evidence ? <EvidenceChip level={ds.evidence} compact /> : ds.has_odds === false ? <span className="chip bg-gray-100 text-ink-3">sem odds</span> : "—"}</td>
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

function SourceCardView({ c }: { c: SourceCardT }) {
  return (
    <Card className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <HealthDot status={c.status} />
          <div>
            <div className="text-sm font-bold">{c.name}</div>
            <div className="label">{c.kind}</div>
          </div>
        </div>
        <HealthChip status={c.status} />
      </div>
      <p className="text-xs text-ink-2">{c.summary}</p>
      <div className="flex flex-wrap items-center gap-1 text-[11px] text-ink-3">
        {c.freshness && <FreshnessChip status={c.freshness.status} ageSeconds={c.freshness.age_seconds} />}
        {c.last_ok && <span title={fmtDateTime(c.last_ok)}>último OK {relativeTime(c.last_ok)}</span>}
        {c.avg_latency_ms !== null && <span>· {Math.round(c.avg_latency_ms)} ms</span>}
        {c.error_rate !== null && <span className={c.error_rate > 0.1 ? "text-danger" : ""}>· erros {pct(c.error_rate, 0)}</span>}
        {c.requests_24h !== null && <span>· {int(c.requests_24h)} req/24h</span>}
      </div>
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div>
          <div className="mb-0.5 font-semibold text-success">Fornece</div>
          {c.provides.length === 0 ? <div className="text-ink-3">nada</div> : c.provides.map((p) => <div key={p} className="text-ink-2">• {p}</div>)}
        </div>
        <div>
          <div className="mb-0.5 font-semibold text-ink-3">Não fornece</div>
          {c.does_not_provide.map((p) => (
            <div key={p} className="text-ink-3">
              • {p}
            </div>
          ))}
        </div>
      </div>
      {c.url && (
        <a className="truncate text-xs text-primary hover:underline" href={c.url} target="_blank" rel="noreferrer">
          {c.url}
        </a>
      )}
      {c.note && <div className="text-[11px] text-ink-3">{c.note}</div>}
    </Card>
  );
}

// ---------------------------------------------------------------- Modelos
const MODEL_LABELS: Record<string, string> = { poisson: "Poisson (força)", dixon_coles: "Dixon-Coles", bivariate_poisson: "Poisson bivariado" };

export function ModelsPage() {
  const q = useQuery({ queryKey: ["models"], queryFn: api.models });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const groups = Object.entries(d.ensemble_weights).sort(([a], [b]) => (a === "GLOBAL" ? -1 : b === "GLOBAL" ? 1 : a.localeCompare(b)));
  return (
    <div className="space-y-5">
      <PageHeader title="Modelos" subtitle={`Versões congeladas em cada snapshot de previsão · app v${d.app_version} · margem removida por ${d.margin_method}`} />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {d.models.map((m) => (
          <Card key={m.key}>
            <div className="flex items-center justify-between">
              <div className="text-sm font-bold capitalize">{m.key.replace(/_/g, " ")}</div>
              <span className="chip bg-gray-100 text-ink-2 normal-case">{m.version}</span>
            </div>
            <p className="mt-2 text-xs leading-relaxed text-ink-2">{m.description}</p>
          </Card>
        ))}
      </div>

      <Card>
        <SectionTitle
          title="Pesos do ensemble (walk-forward)"
          subtitle="Pesos ∝ exp(−25·Δlogloss) por competição (N ≥ 200) ou GLOBAL. Modelos com peso < 10% continuam exibidos, mas ficam fora do veto de divergência."
        />
        {groups.length === 0 ? (
          <div className="text-sm text-ink-2">Pesos ainda não calculados — o job “Pesos do ensemble” roda no agendador (Sistema → Jobs).</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
              <tr>
                <th className="py-1.5">Grupo</th>
                <th className="text-right">Amostra</th>
                {["poisson", "dixon_coles", "bivariate_poisson"].map((k) => (
                  <th key={k} className="text-right">
                    {MODEL_LABELS[k]}
                  </th>
                ))}
                <th>Calculado</th>
              </tr>
            </thead>
            <tbody>
              {groups.map(([g, w]) => (
                <tr key={g} className={clsx("border-t border-line/70", g === "GLOBAL" && "font-semibold")}>
                  <td className="py-1.5 font-mono text-xs">{g}</td>
                  <td className="text-right tabular-nums">{int(w.sample)}</td>
                  {["poisson", "dixon_coles", "bivariate_poisson"].map((k) => {
                    const v = w.weights[k];
                    const ll = w.scores[k]?.logloss;
                    return (
                      <td key={k} className={clsx("text-right tabular-nums", v !== undefined && v < 0.1 && "text-warning")} title={ll !== undefined ? `log loss ${ll.toFixed(4)}` : ""}>
                        {v !== undefined ? pct(v, 1) : "—"}
                      </td>
                    );
                  })}
                  <td className="text-xs text-ink-2">{fmtDateTime(w.computed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Model registry" subtitle="Cada versão registrada com janela de treino, features, parâmetros e métricas. Versões antigas ficam marcadas como depreciadas, nunca apagadas." />
          <div className="max-h-96 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
                <tr>
                  <th className="py-1.5">Modelo</th>
                  <th>Versão</th>
                  <th>Estado</th>
                  <th>Registrado</th>
                </tr>
              </thead>
              <tbody>
                {d.registry.map((r) => (
                  <tr key={r.id} className="border-t border-line/70" title={[r.training_window, r.notes, r.features?.join(", ")].filter(Boolean).join("\n")}>
                    <td className="py-1.5 font-medium">{r.model_id}</td>
                    <td className="font-mono">{r.version}</td>
                    <td>
                      <span className={clsx("chip", r.active ? "bg-success-50 text-success" : r.deprecated ? "bg-gray-100 text-ink-3" : "bg-info-50 text-info")}>{r.active ? "ativo" : r.deprecated ? "depreciado" : "registrado"}</span>
                    </td>
                    <td className="text-ink-2">{fmtDateTime(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card>
          <SectionTitle title="Parâmetros ativos" />
          <div className="grid gap-x-8 md:grid-cols-2">
            {Object.entries(d.parameters).map(([k, v]) => (
              <KV key={k} k={k} v={String(v)} />
            ))}
          </div>
          <div className="mt-3 text-xs text-ink-2">
            Ajustes em cache: ELO {d.fitted.elo.length} · Dixon-Coles {d.fitted.dixon_coles.length} · Poisson bivariado {d.fitted.bivariate_poisson.length}
          </div>
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Performance
const METRIC_HELP = {
  brier: "Brier score: média de (probabilidade prevista − resultado)². 0 = perfeito, 0.25 = chute de moeda em eventos 50/50. Mede calibração + resolução; quanto menor, melhor.",
  log_loss: "Log loss: penaliza fortemente previsões confiantes e erradas. Quanto menor, melhor.",
  roi: "ROI: lucro / total apostado (stake 1u por entrada). Só com apostas liquidadas — nunca retroativo.",
  clv: "CLV (Closing Line Value): quanto a odd recomendada estava melhor que a odd de fechamento. Positivo e consistente é o melhor sinal de que o modelo bate o mercado. A closing line nunca é usada para recomendar.",
  hit: "Hit rate: percentual de entradas vencedoras. Sozinho não diz nada sobre lucro — compare com a odd média.",
};

export function PerformancePage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"resultados" | "calibracao" | "market" | "superbet">("resultados");
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
          <>
            <Segmented
              value={tab}
              options={[
                { value: "resultados", label: "Resultados" },
                { value: "calibracao", label: "Calibração" },
                { value: "market", label: "Market Efficiency Lab" },
                { value: "superbet", label: "Superbet Lab" },
              ]}
              onChange={setTab}
            />
            <button className="btn-outline" onClick={() => settle.mutate()} disabled={settle.isPending}>
              <RefreshCw size={14} className={settle.isPending ? "animate-spin" : ""} /> Liquidar pendentes
            </button>
          </>
        }
      />
      {tab === "market" ? (
        <MarketEfficiencyLab />
      ) : tab === "superbet" ? (
        <SuperbetTab lab />
      ) : tab === "calibracao" ? (
        <CalibrationTab />
      ) : d.settled_snapshots === 0 ? (
        <Empty
          title="Ainda não há previsões liquidadas"
          detail={`As métricas aparecem depois que jogos analisados terminam e o resultado real é anexado ao snapshot. Nada é retroativo. Mínimo para exibir uma métrica por grupo: ${d.min_sample} apostas.`}
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-8">
            <Stat label="Snapshots liquidados" value={d.settled_snapshots} />
            <Stat label="Entradas recomendadas" value={d.recommended_bets} hint={`${d.bets_with_closing_line} com closing line`} />
            <StatT label="Brier" value={num(o.brier, 3)} help={METRIC_HELP.brier} accent="text-ink" />
            <StatT label="Log loss" value={num(o.log_loss, 3)} help={METRIC_HELP.log_loss} />
            <StatT label="CLV médio" value={signedPct(o.clv_pct)} help={METRIC_HELP.clv} accent={(o.clv_pct ?? 0) >= 0 ? "text-success" : "text-danger"} />
            <StatT label="ROI" value={signedPct(o.roi)} help={METRIC_HELP.roi} accent={(o.roi ?? 0) >= 0 ? "text-success" : "text-danger"} />
            <StatT label="Hit rate" value={pct(o.hit_rate, 1)} help={METRIC_HELP.hit} />
            <Stat label="Max drawdown" value={num(o.max_drawdown, 1)} accent="text-danger" />
          </div>
          {o.sample_status === "INSUFFICIENT_SAMPLE" && (
            <div className="card flex items-center gap-2 border-warning/30 bg-warning-50/40 px-4 py-2 text-sm text-warning">
              <Info size={14} /> INSUFFICIENT SAMPLE: {o.bets} apostas liquidadas (mínimo {d.min_sample}). Os números acima ainda não sustentam conclusões.
            </div>
          )}
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
              <SectionTitle title="Modelo 1X2 — todas as seleções" subtitle="Brier e log loss sobre toda a distribuição prevista, não só as recomendadas" />
              <KV k="Amostras" v={d.model_1x2_all_selections.bets} />
              <KV k="Brier" v={num(d.model_1x2_all_selections.brier, 3)} />
              <KV k="Log loss" v={num(d.model_1x2_all_selections.log_loss, 3)} />
              {d.model_1x2_all_selections.sample_status === "INSUFFICIENT_SAMPLE" && <div className="mt-2 text-xs text-warning">INSUFFICIENT SAMPLE</div>}
            </Card>
          </div>
          <GroupTable title="Por mercado" rows={d.by_market} minSample={d.min_sample} />
          <GroupTable title="Por competição" rows={d.by_competition} minSample={d.min_sample} />
          {d.selection_vs_cluster && (
            <GroupTable
              title="Seleções × primárias (sem duplicidade)"
              subtitle={d.selection_vs_cluster.note}
              rows={{ "Todas as seleções recomendadas": d.selection_vs_cluster.all_selections, "Somente primárias (oficial)": d.selection_vs_cluster.primaries_only, "Somente alternativas": d.selection_vs_cluster.alternatives_only }}
              minSample={d.min_sample}
            />
          )}
          {d.by_cluster && Object.keys(d.by_cluster).length > 0 && <GroupTable title="Por tese (cluster) — só primárias" rows={d.by_cluster} minSample={d.min_sample} />}
          {d.by_state && Object.keys(d.by_state).length > 0 && <GroupTable title="Por estado no momento da recomendação" subtitle="VALUE exige prova OOS; VALUE_CANDIDATE ainda não tinha. LEGACY = snapshots anteriores à iteração 3." rows={d.by_state} minSample={d.min_sample} />}
          {d.secondary_markets && Object.keys(d.secondary_markets).length > 0 && (
            <GroupTable
              title="Mercados secundários v1 — escanteios, cartões, finalizações"
              subtitle="Performance do modelo em toda seleção com probabilidade (não só recomendadas). INSUFFICIENT abaixo do mínimo; nunca extrapolamos."
              rows={Object.fromEntries(Object.values(d.secondary_markets).map((v) => [`${v.label} · ${v.verdict}`, v]))}
              minSample={d.min_sample}
            />
          )}
        </>
      )}
    </div>
  );
}

function StatT({ label, value, help, accent }: { label: string; value: React.ReactNode; help: string; accent?: string }) {
  return (
    <Card className="flex flex-col gap-1">
      <span className="label inline-flex items-center gap-1">
        {label}
        <Tooltip text={help}>
          <Info size={11} className="text-ink-3" />
        </Tooltip>
      </span>
      <span className={clsx("text-2xl font-bold tabular-nums", accent)}>{value}</span>
    </Card>
  );
}

function GroupTable({ title, subtitle, rows, minSample }: { title: string; subtitle?: string; rows: Record<string, GroupMetrics>; minSample: number }) {
  const entries = Object.entries(rows).sort((a, b) => b[1].bets - a[1].bets);
  return (
    <Card>
      <SectionTitle title={title} subtitle={subtitle ?? `Grupos com menos de ${minSample} apostas mostram INSUFFICIENT SAMPLE — os números ficam visíveis, mas não sustentam conclusão`} />
      {entries.length === 0 ? (
        <div className="text-sm text-ink-2">Sem dados.</div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
            <tr>
              <th className="py-1.5">Grupo</th>
              <th className="text-right">Apostas</th>
              <th className="text-right">Hit</th>
              <th className="text-right">
                <span className="inline-flex items-center gap-1">
                  Brier <Tooltip text={METRIC_HELP.brier}><Info size={11} className="text-ink-3" /></Tooltip>
                </span>
              </th>
              <th className="text-right">ROI</th>
              <th className="text-right">
                <span className="inline-flex items-center gap-1">
                  CLV <Tooltip text={METRIC_HELP.clv}><Info size={11} className="text-ink-3" /></Tooltip>
                </span>
              </th>
              <th className="text-right">Odd média</th>
              <th>Amostra</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([k, m]) => (
              <tr key={k} className={clsx("border-t border-line/70", m.sample_status === "INSUFFICIENT_SAMPLE" && "text-ink-3")}>
                <td className="py-1.5 font-medium">{k}</td>
                <td className="text-right tabular-nums">{m.bets}</td>
                <td className="text-right tabular-nums">{pct(m.hit_rate, 1)}</td>
                <td className="text-right tabular-nums font-semibold">{num(m.brier, 3)}</td>
                <td className={clsx("text-right tabular-nums", m.sample_status !== "INSUFFICIENT_SAMPLE" && ((m.roi ?? 0) >= 0 ? "text-success" : "text-danger"))} title={m.roi_ci && m.roi_ci.low !== null && m.roi_ci.high !== null ? `IC 95% bootstrap [${m.roi_ci.low.toFixed(1)}%, ${m.roi_ci.high.toFixed(1)}%]${m.roi_ci.conclusive ? "" : " — cruza zero: INCONCLUSIVE"}` : "IC indisponível (< 10 apostas)"}>
                  {signedPct(m.roi)}
                  {m.roi_ci && m.roi_ci.low !== null && m.roi_ci.high !== null && <span className="block text-[10px] text-ink-3">[{m.roi_ci.low.toFixed(0)}, {m.roi_ci.high.toFixed(0)}]</span>}
                </td>
                <td className="text-right tabular-nums">{signedPct(m.clv_pct)}</td>
                <td className="text-right tabular-nums">{odd(m.avg_odd)}</td>
                <td>{m.sample_status === "INSUFFICIENT_SAMPLE" ? <span className="chip bg-warning-50 text-warning">INSUFFICIENT SAMPLE</span> : <span className="chip bg-success-50 text-success">OK</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function CalibrationTab() {
  const [market, setMarket] = useState<string>("");
  const q = useQuery({ queryKey: ["calibration", market], queryFn: () => api.calibration(market || undefined) });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const dg = d.diagram;
  const points = dg.buckets.filter((b) => b.sample > 0 && b.predicted !== null && b.actual !== null);
  const markets = [...new Set(d.groups.map((g) => g.market_key))];
  return (
    <div className="space-y-4">
      <div className="card flex items-start gap-3 border-l-4 border-l-info bg-info-50/30 px-4 py-3 text-sm">
        <Info size={16} className="mt-0.5 shrink-0 text-info" />
        <div>
          <b>RAW vs CALIBRATED.</b> {d.note} Enquanto um grupo não atinge {d.min_n} previsões liquidadas, a recomendação usa a probabilidade crua (RAW) e a página do jogo mostra isso explicitamente.
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <SectionTitle
            title="Reliability diagram"
            subtitle={`Previsto × observado por faixa de probabilidade · ${int(dg.sample)} previsões liquidadas · Brier ${num(dg.brier, 3)} · ${dg.model_version}`}
            right={
              <select className="input w-auto py-1 text-xs" value={market} onChange={(e) => setMarket(e.target.value)}>
                <option value="">todos os mercados</option>
                {markets.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            }
          />
          {points.length === 0 ? (
            <div className="grid h-56 place-items-center text-center text-sm text-ink-2">
              <div>
                <div className="font-semibold">INSUFFICIENT SAMPLE</div>
                <div className="text-xs">Nenhuma previsão liquidada ainda. O diagrama aparece conforme os jogos analisados terminam — nunca com dados retroativos.</div>
              </div>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <ScatterChart margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
                <CartesianGrid stroke="#EEF0F3" />
                <XAxis type="number" dataKey="x" domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} tick={{ fontSize: 11 }} name="previsto" />
                <YAxis type="number" dataKey="y" domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} tick={{ fontSize: 11 }} width={44} name="observado" />
                <ZAxis type="number" dataKey="n" range={[40, 400]} name="amostra" />
                <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#98A2B3" strokeDasharray="4 4" />
                <RTooltip formatter={(v: number, name: string) => (name === "amostra" ? v : `${(v * 100).toFixed(1)}%`)} />
                <Scatter data={points.map((b) => ({ x: b.predicted, y: b.actual, n: b.sample, bucket: b.bucket }))} fill="#FF2638" />
              </ScatterChart>
            </ResponsiveContainer>
          )}
          <BucketTable buckets={dg.buckets} />
        </Card>
        <Card>
          <SectionTitle title="Grupos de calibração" subtitle={`Isotônica por mercado/competição · mínimo ${d.min_n}`} />
          {d.groups.length === 0 ? (
            <div className="text-sm text-ink-2">Nenhum grupo ajustado ainda. Todas as probabilidades exibidas são RAW.</div>
          ) : (
            <div className="space-y-1.5 text-xs">
              {d.groups.map((g) => (
                <div key={g.group_key} className="rounded-md border border-line px-2.5 py-1.5">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">
                      {g.market_key} · {g.group}
                    </span>
                    <span className={clsx("chip", g.reliable ? "bg-success-50 text-success" : "bg-warning-50 text-warning")}>{g.reliable ? "CALIBRATED" : "RAW"}</span>
                  </div>
                  <div className="text-ink-2">
                    n={g.n} · Brier raw {num(g.brier_raw, 3)} → cal {num(g.brier_calibrated, 3)} · {g.method}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

function BucketTable({ buckets }: { buckets: CalibrationBucket[] }) {
  return (
    <table className="mt-3 w-full text-xs">
      <thead className="text-left text-[10px] font-semibold uppercase tracking-wide text-ink-2">
        <tr>
          <th className="py-1">Faixa</th>
          <th className="text-right">Previsto</th>
          <th className="text-right">Observado</th>
          <th className="text-right">Amostra</th>
        </tr>
      </thead>
      <tbody>
        {buckets.map((b) => (
          <tr key={b.bucket} className={clsx("border-t border-line/60", b.sample === 0 && "text-ink-3")}>
            <td className="py-0.5 font-mono">{b.bucket}%</td>
            <td className="text-right tabular-nums">{pct(b.predicted, 1)}</td>
            <td className="text-right tabular-nums">{pct(b.actual, 1)}</td>
            <td className="text-right tabular-nums">{b.sample}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
