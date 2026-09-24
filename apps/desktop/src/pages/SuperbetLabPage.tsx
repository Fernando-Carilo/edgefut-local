import { useQuery } from "@tanstack/react-query";
import { fmtDateTime, int, num, odd } from "@edgefut/shared";
import clsx from "clsx";
import type { MarketCategory, MoveClass, MovementRow, SelectionSeries } from "@edgefut/contracts";
import { MARKET_CATEGORY_LABELS } from "@edgefut/contracts";
import { Activity, GitCompareArrows, Search, SlidersHorizontal } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { Card, Empty, ErrorBox, Loading, PageHeader, SectionTitle, Segmented } from "@/components/ui";
import { api } from "@/lib/api";

type Tab = "lab" | "movement" | "signal";
const MOVE_CLS: Record<MoveClass, string> = { STEAM: "bg-danger-50 text-danger", DRIFT: "bg-warning-50 text-warning", STABLE: "bg-gray-100 text-ink-2" };

export function MoveChip({ c }: { c: MoveClass | string | null | undefined }) {
  if (!c) return <span className="chip bg-gray-100 text-ink-3">—</span>;
  return <span className={clsx("chip", MOVE_CLS[c as MoveClass] ?? "bg-gray-100")}>{c}</span>;
}

const asNum = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const asStr = (v: unknown): string => (v == null ? "" : String(v));

export function SuperbetLabPage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "lab";
  const setTab = (t: Tab) => setParams((p) => { p.set("tab", t); return p; });
  return (
    <div className="space-y-5">
      <PageHeader title="Superbet Lab" subtitle="Descritivo por construção: margem, movimento e CLV do preço da casa. Nada aqui é edge — o mercado é a referência, não o adversário." />
      <Segmented value={tab} onChange={setTab} options={[{ value: "lab", label: "Filtros e margem" }, { value: "movement", label: "Line movement" }, { value: "signal", label: "Signal vs movement" }]} />
      {tab === "lab" && <LabTab />}
      {tab === "movement" && <MovementTab />}
      {tab === "signal" && <SignalTab />}
    </div>
  );
}

function LabTab() {
  const [f, setF] = useState<{ category: string; competition: string; odds_band: string; target: string; line: string; days: number }>({ category: "", competition: "", odds_band: "", target: "", line: "", days: 60 });
  const q = useQuery({
    queryKey: ["flywheel", "lab", f],
    queryFn: () => api.superbetLab({ category: f.category || undefined, competition: f.competition || undefined, odds_band: f.odds_band || undefined, target: f.target || undefined, line: f.line ? Number(f.line) : undefined, days: f.days, limit: 200 }),
  });
  const set = (k: keyof typeof f, v: string | number) => setF((s) => ({ ...s, [k]: v }));
  const d = q.data;
  const margin = (d?.margin ?? {}) as Record<string, Record<string, unknown>[]>;
  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle title="Filtros" subtitle="Mercado · competição · faixa de odd · alvo temporal · linha" right={<SlidersHorizontal size={14} className="text-ink-3" />} />
        <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
          <label className="text-xs">
            <span className="label">Mercado</span>
            <select className="input" value={f.category} onChange={(e) => set("category", e.target.value)}>
              <option value="">Todos</option>
              {(d?.options?.categories ?? (Object.keys(MARKET_CATEGORY_LABELS) as MarketCategory[])).map((c) => (
                <option key={c} value={c}>
                  {MARKET_CATEGORY_LABELS[c] ?? c}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs">
            <span className="label">Competição</span>
            <input className="input" list="lab-comps" value={f.competition} onChange={(e) => set("competition", e.target.value)} placeholder="contém…" />
            <datalist id="lab-comps">{(d?.options?.competitions ?? []).map((c) => <option key={c} value={c} />)}</datalist>
          </label>
          <label className="text-xs">
            <span className="label">Faixa de odd (fechamento)</span>
            <select className="input" value={f.odds_band} onChange={(e) => set("odds_band", e.target.value)}>
              <option value="">Todas</option>
              {(d?.options?.odds_bands ?? []).map((b) => <option key={b}>{b}</option>)}
            </select>
          </label>
          <label className="text-xs">
            <span className="label">Alvo T-x</span>
            <select className="input" value={f.target} onChange={(e) => set("target", e.target.value)}>
              <option value="">Todos</option>
              {(d?.options?.targets ?? []).map((t) => <option key={t}>{t}</option>)}
            </select>
          </label>
          <label className="text-xs">
            <span className="label">Linha</span>
            <input className="input" type="number" step="0.5" value={f.line} onChange={(e) => set("line", e.target.value)} placeholder="ex. 2.5" />
          </label>
          <label className="text-xs">
            <span className="label">Janela (dias)</span>
            <input className="input" type="number" min={1} max={365} value={f.days} onChange={(e) => set("days", Number(e.target.value) || 60)} />
          </label>
        </div>
      </Card>
      {q.isLoading && <Loading label="Filtrando snapshots…" />}
      {q.isError && <ErrorBox error={q.error} retry={() => q.refetch()} />}
      {d && (
        <>
          <div className="text-xs text-ink-2">
            <b>{int(d.n)}</b> observações · <b>{int(d.events ?? 0)}</b> eventos · <b>{int(d.selections ?? 0)}</b> seleções. {d.note}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <SectionTitle title="Margin Lab — overround por mercado" subtitle="Mediana e percentis do overround em mercados completos" />
              <MarginTable rows={margin.by_market ?? []} keyField="market_category" />
            </Card>
            <Card>
              <SectionTitle title="Overround por tempo até o jogo" subtitle="A margem muda quando o kickoff se aproxima?" />
              <MarginTable rows={margin.by_time_to_kickoff ?? []} keyField="snapshot_target" />
            </Card>
            <Card>
              <SectionTitle title="Por faixa de odd" />
              <MarginTable rows={margin.by_odds_band ?? []} keyField="odds_band" />
            </Card>
            <Card>
              <SectionTitle title="Por competição (1X2)" />
              <MarginTable rows={(margin.by_competition ?? []).slice(0, 15)} keyField="competition_name" />
            </Card>
          </div>
          <Card>
            <SectionTitle title="CLV V2 — preço da casa em T-x vs fechamento" subtitle="clv_raw = odd_T / closing − 1 · clv_fair = fair_close − fair_T (pp). É o preço da Superbet a mover-se, não apostas." />
            <ClvTable rows={d.clv?.rows ?? []} />
          </Card>
        </>
      )}
    </div>
  );
}

function MarginTable({ rows, keyField }: { rows: Record<string, unknown>[]; keyField: string }) {
  if (!rows.length) return <Empty title="Sem grupos suficientes" detail="Mínimo de 20 mercados completos por grupo." />;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-ink-3">
          <th className="py-1 font-semibold">Grupo</th>
          <th>Mercados</th>
          <th>Eventos</th>
          <th>Mediana</th>
          <th>Média</th>
          <th>p10–p90</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-line">
            <td className="py-1 font-semibold">{MARKET_CATEGORY_LABELS[asStr(r[keyField]) as MarketCategory] ?? asStr(r[keyField])}{r.line != null ? ` ${asStr(r.line)}` : ""}</td>
            <td>{int(asNum(r.groups))}</td>
            <td>{int(asNum(r.events))}</td>
            <td className="font-semibold">{asNum(r.overround_median_pct) != null ? `${num(asNum(r.overround_median_pct), 2)}%` : "—"}</td>
            <td>{asNum(r.overround_mean_pct) != null ? `${num(asNum(r.overround_mean_pct), 2)}%` : "—"}</td>
            <td className="text-ink-2">{asNum(r.p10_pct) != null ? `${num(asNum(r.p10_pct), 1)}–${num(asNum(r.p90_pct), 1)}%` : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Interval({ v, unit = "" }: { v: unknown; unit?: string }) {
  const iv = v as { point?: number | null; low?: number | null; high?: number | null; n?: number; conclusive?: boolean | null } | null | undefined;
  if (!iv || iv.point == null) return <span className="text-ink-3">INSUFFICIENT</span>;
  return (
    <span className={clsx(iv.conclusive === true && "font-semibold")} title={`n=${iv.n}`}>
      {num(iv.point, 2)}{unit} <span className="text-ink-3">[{num(iv.low ?? null, 2)}, {num(iv.high ?? null, 2)}]</span>
    </span>
  );
}

function ClvTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return <Empty title="Sem observações anteriores ao fechamento" detail="CLV precisa de snapshot em T-x e do último preço antes do kickoff do mesmo evento." />;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-ink-3">
          <th className="py-1 font-semibold">Mercado</th>
          <th>Alvo</th>
          <th>N</th>
          <th>Eventos</th>
          <th>CLV raw (%)</th>
          <th>CLV fair (pp)</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-line">
            <td className="py-1 font-semibold">{MARKET_CATEGORY_LABELS[asStr(r.market_category) as MarketCategory] ?? asStr(r.market_category)}</td>
            <td>{asStr(r.target)}</td>
            <td>{int(asNum(r.n))}</td>
            <td>{int(asNum(r.events))}</td>
            <td><Interval v={r.clv_raw_pct} unit="%" /></td>
            <td><Interval v={r.clv_fair_pp} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MovementTab() {
  const [category, setCategory] = useState("");
  const [eventId, setEventId] = useState("");
  const q = useQuery({ queryKey: ["flywheel", "movement", category], queryFn: () => api.movement({ category: category || undefined, days: 30, top: 80 }) });
  const d = q.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs">
          <span className="label">Mercado</span>
          <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Todos</option>
            {(Object.keys(MARKET_CATEGORY_LABELS) as MarketCategory[]).map((c) => (
              <option key={c} value={c}>
                {MARKET_CATEGORY_LABELS[c]}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          <span className="label">Evento (id) — timeline canónica</span>
          <div className="flex gap-1">
            <input className="input" value={eventId} onChange={(e) => setEventId(e.target.value)} placeholder="ex. 14888100" />
          </div>
        </label>
        <div className="text-xs text-ink-2">{d?.note}</div>
      </div>
      {q.isLoading && <Loading label="Classificando movimentos…" />}
      {q.isError && <ErrorBox error={q.error} retry={() => q.refetch()} />}
      {d && (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card>
            <SectionTitle title="STEAM / DRIFT / STABLE por mercado" subtitle="STEAM ≥ 3 pp de fair com ≥ 60% do movimento após T-3h na mesma direção; DRIFT ≥ 1,5 pp; STABLE abaixo." />
            {d.movement.by_market.length === 0 ? (
              <Empty title="Sem seleções com ≥ 2 observações" detail="Movimento exige opening e closing do mesmo evento." />
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-ink-3">
                    <th className="py-1 font-semibold">Mercado</th>
                    <th>N</th>
                    <th>Steam</th>
                    <th>Drift</th>
                    <th>Stable</th>
                    <th>|move| med.</th>
                  </tr>
                </thead>
                <tbody>
                  {d.movement.by_market.map((r) => (
                    <tr key={r.market_category} className="border-t border-line">
                      <td className="py-1 font-semibold">{MARKET_CATEGORY_LABELS[r.market_category] ?? r.market_category}</td>
                      <td>{int(r.n)}</td>
                      <td className="text-danger">{int(r.steam)}</td>
                      <td className="text-warning">{int(r.drift)}</td>
                      <td>{int(r.stable)}</td>
                      <td>{r.abs_move_pp_median != null ? `${num(r.abs_move_pp_median, 2)} pp` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
          <Card className="lg:col-span-2">
            <SectionTitle title="Maiores movimentos (30 d)" subtitle="Opening → closing observados. Não interpretamos como smart money." />
            <MovementRows rows={d.movement.top} onPick={(id) => setEventId(String(id))} />
          </Card>
        </div>
      )}
      {eventId && <SelectionTimeline eventId={Number(eventId)} category={category || undefined} />}
    </div>
  );
}

export function MovementRows({ rows, onPick }: { rows: MovementRow[]; onPick?: (eventId: number) => void }) {
  const navigate = useNavigate();
  if (!rows.length) return <Empty title="Nenhum movimento classificado" detail="Precisa de opening e closing reais para a mesma seleção." />;
  return (
    <div className="max-h-[420px] overflow-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="sticky top-0 bg-card text-left text-ink-3">
            <th className="py-1 font-semibold">Evento</th>
            <th>Mercado</th>
            <th>Seleção</th>
            <th>Abertura</th>
            <th>Fechamento</th>
            <th>Δ fair</th>
            <th>Classe</th>
            <th>Obs.</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-line hover:bg-gray-50">
              <td className="py-1">
                <button className="text-left font-semibold text-primary hover:underline" onClick={() => (onPick ? onPick(r.event_id) : navigate(`/jogos/${r.event_id}`))} title={asStr(r.kickoff_utc)}>
                  #{r.event_id}
                </button>
                <div className="text-[10px] text-ink-3">{asStr(r.competition)}</div>
              </td>
              <td>{r.market}</td>
              <td>{r.selection}</td>
              <td>{odd(r.opening_odd ?? null)}</td>
              <td>{odd(r.closing_odd ?? null)}</td>
              <td className={clsx("font-semibold", (r.move_pp ?? 0) > 0 ? "text-success" : (r.move_pp ?? 0) < 0 ? "text-danger" : "")}>{r.move_pp != null ? `${r.move_pp > 0 ? "+" : ""}${num(r.move_pp, 2)} pp` : "—"}</td>
              <td><MoveChip c={r.move_class} /></td>
              <td>{int(r.n_obs ?? 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** LINE MOVEMENT (§40) — timeline canónica de um evento: opening, alvos T-x, closing, EdgeFut. */
export function SelectionTimeline({ eventId, category }: { eventId: number; category?: string }) {
  const q = useQuery({ queryKey: ["flywheel", "selection", eventId, category], queryFn: () => api.selectionTimeline(eventId, category), enabled: Number.isFinite(eventId) && eventId > 0 });
  const [pick, setPick] = useState<string | null>(null);
  const series = q.data?.series ?? [];
  const cur = useMemo(() => series.find((s) => `${s.canonical_market_id}|${s.selection_id}` === pick) ?? series[0], [series, pick]);
  if (q.isLoading) return <Loading label="Carregando timeline…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  if (!q.data) return null;
  return (
    <Card>
      <SectionTitle title={`Line movement · ${q.data.label ?? eventId}`} subtitle={`${q.data.kickoff_utc ? fmtDateTime(q.data.kickoff_utc) : ""} · ${series.length} seleções canónicas · ${q.data.note}`} right={<Activity size={14} className="text-ink-3" />} />
      {series.length === 0 ? (
        <Empty title="Sem snapshots normalizados para este evento" detail="O evento pode ser anterior ao Collector V2 ou não ter mercados mapeados." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="max-h-[360px] overflow-auto text-xs">
            {series.map((s) => {
              const k = `${s.canonical_market_id}|${s.selection_id}`;
              return (
                <button key={k} className={clsx("flex w-full items-center justify-between gap-2 border-b border-line px-2 py-1.5 text-left hover:bg-gray-50", cur && k === `${cur.canonical_market_id}|${cur.selection_id}` && "bg-primary-50")} onClick={() => setPick(k)}>
                  <span className="truncate">
                    <span className="text-ink-3">{MARKET_CATEGORY_LABELS[s.market_category] ?? s.market_category}</span> · {s.selection_name}
                  </span>
                  <span className="flex shrink-0 items-center gap-1">
                    <MoveChip c={s.classification} />
                    <span className="text-ink-2">{s.n_obs}</span>
                  </span>
                </button>
              );
            })}
          </div>
          {cur && <SeriesDetail s={cur} />}
        </div>
      )}
    </Card>
  );
}

function SeriesDetail({ s }: { s: SelectionSeries }) {
  const pts = s.points;
  const odds = pts.map((p) => p.odd);
  const lo = Math.min(...odds), hi = Math.max(...odds);
  return (
    <div className="lg:col-span-2">
      <div className="mb-2 flex flex-wrap items-center gap-3 text-xs">
        <span className="font-semibold">{s.canonical_market_id} · {s.selection_name}</span>
        <span>Abertura {odd(s.opening?.odd ?? null)} → Fechamento {odd(s.closing?.odd ?? null)}</span>
        <span>Δ fair {s.move_pp != null ? `${s.move_pp > 0 ? "+" : ""}${num(s.move_pp, 2)} pp` : "—"}</span>
        <MoveChip c={s.classification} />
        {s.edgefut_prob != null && <span className="chip bg-info-50 text-info">EdgeFut {num(s.edgefut_prob * 100, 1)}%</span>}
      </div>
      <div className="relative h-36 w-full rounded border border-line bg-bg">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-full w-full">
          <polyline
            fill="none"
            stroke="#2E90FA"
            strokeWidth={1.2}
            vectorEffect="non-scaling-stroke"
            points={pts.map((p, i) => `${(i / Math.max(1, pts.length - 1)) * 100},${hi === lo ? 50 : 100 - ((p.odd - lo) / (hi - lo)) * 90 - 5}`).join(" ")}
          />
          {pts.map((p, i) => p.snapshot_target && <circle key={i} cx={(i / Math.max(1, pts.length - 1)) * 100} cy={hi === lo ? 50 : 100 - ((p.odd - lo) / (hi - lo)) * 90 - 5} r={1.4} fill="#F04438" />)}
        </svg>
        <div className="absolute left-1 top-1 text-[10px] text-ink-3">{odd(hi)}</div>
        <div className="absolute bottom-1 left-1 text-[10px] text-ink-3">{odd(lo)}</div>
      </div>
      <div className="mt-2 max-h-40 overflow-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-left text-ink-3">
              <th className="py-0.5 font-semibold">Coleta</th>
              <th>T-kickoff</th>
              <th>Alvo</th>
              <th>Odd</th>
              <th>Implícita</th>
              <th>Fair</th>
              <th>Overround</th>
            </tr>
          </thead>
          <tbody>
            {pts.map((p, i) => (
              <tr key={i} className="border-t border-line">
                <td className="py-0.5">{fmtDateTime(p.fetched_at)}</td>
                <td>{p.minutes_to_kickoff != null ? `${Math.round(p.minutes_to_kickoff)} min` : "—"}</td>
                <td className="font-semibold text-danger">{p.snapshot_target ?? ""}</td>
                <td className="font-semibold">{odd(p.odd)}</td>
                <td>{num(p.implied_prob * 100, 1)}%</td>
                <td>{p.fair_prob != null ? `${num(p.fair_prob * 100, 1)}%` : "—"}</td>
                <td>{p.overround != null ? `${num(p.overround * 100, 2)}%` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** SIGNAL VS MOVEMENT (§41) — o mercado move-se na direção do EdgeFut? LEAD / LAG / nenhum. */
function SignalTab() {
  const q = useQuery({ queryKey: ["flywheel", "movement", "all"], queryFn: () => api.movement({ days: 60, top: 10 }) });
  if (q.isLoading) return <Loading label="Comparando sinal e movimento…" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const ll = q.data!.lead_lag;
  const all = (ll.all ?? {}) as Record<string, unknown>;
  const verdictCls: Record<string, string> = { MARKET_MOVES_TOWARD_EDGEFUT: "bg-success-50 text-success", MARKET_MOVES_AGAINST_EDGEFUT: "bg-danger-50 text-danger", NO_LEAD_DETECTED: "bg-gray-100 text-ink-2", INSUFFICIENT: "bg-gray-100 text-ink-3" };
  return (
    <div className="space-y-4">
      <Card className="border-l-4 border-l-info">
        <div className="flex flex-wrap items-center gap-3">
          <GitCompareArrows size={18} className="text-info" />
          <div className="text-sm font-bold">Veredito</div>
          <span className={clsx("chip", verdictCls[ll.verdict] ?? "bg-gray-100")}>{ll.verdict}</span>
          <div className="text-xs text-ink-2">{ll.note}</div>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 text-xs md:grid-cols-5">
          <KV k="Pares sinal × movimento" v={int(asNum(all.n))} />
          <KV k="Eventos" v={int(asNum(all.events))} />
          <KV k="Concordância de direção" v={<Interval v={all.direction_agreement} />} />
          <KV k="Movimento na direção do sinal (pp)" v={<Interval v={all.move_toward_signal_pp} />} />
          <KV k="Sem movimento" v={asNum(all.unchanged_share) != null ? `${num(asNum(all.unchanged_share)! * 100, 0)}%` : "—"} />
        </div>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Por mercado" />
          <LeadTable rows={ll.rows} keyField="market_category" />
        </Card>
        <Card>
          <SectionTitle title="Por instante da previsão (T-x)" subtitle="Sinal emitido em T-x; movimento medido até o fechamento." />
          <LeadTable rows={ll.by_bucket ?? []} keyField="bucket" />
        </Card>
      </div>
      <Card className="p-4 text-xs text-ink-2">
        <Search size={13} className="mr-1 inline" /> Sinal = EdgeFut − fair(T). Movimento = fair(closing) − fair(T). Concordar com o mercado depois do fato não é edge; LEAD só conta se o sinal for anterior ao movimento e a diferença sobreviver ao IC (bootstrap por evento).
      </Card>
    </div>
  );
}

function LeadTable({ rows, keyField }: { rows: Record<string, unknown>[]; keyField: string }) {
  if (!rows.length) return <Empty title="INSUFFICIENT" detail="Sem previsões EdgeFut anteriores a um snapshot Superbet do mesmo evento." />;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-ink-3">
          <th className="py-1 font-semibold">Grupo</th>
          <th>N</th>
          <th>Eventos</th>
          <th>Concordância</th>
          <th>Move → sinal (pp)</th>
          <th>Veredito</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-line">
            <td className="py-1 font-semibold">{MARKET_CATEGORY_LABELS[asStr(r[keyField]) as MarketCategory] ?? asStr(r[keyField])}</td>
            <td>{int(asNum(r.n))}</td>
            <td>{int(asNum(r.events))}</td>
            <td><Interval v={r.direction_agreement} /></td>
            <td><Interval v={r.move_toward_signal_pp} /></td>
            <td className="text-ink-2">{asStr(r.verdict)}</td>
          </tr>
        ))}
      </tbody>
    </table>
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
