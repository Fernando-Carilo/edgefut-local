import { useQuery } from "@tanstack/react-query";
import type { EventWindow } from "@edgefut/contracts";
import { dayLabel, fmtTime, odd, pct } from "@edgefut/shared";
import { Plus } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { EdgeValue, Empty, ErrorBox, GradeBadge, Loading, PageHeader, Segmented, StatusChip } from "@/components/ui";
import { api } from "@/lib/api";
import { useUi } from "@/store/ui";

const MARKETS = [
  ["", "Todos os mercados"],
  ["1X2", "Resultado Final"],
  ["DOUBLE_CHANCE", "Dupla Chance"],
  ["DRAW_NO_BET", "Empate Anula"],
  ["TOTAL_GOALS", "Total de Gols"],
  ["BTTS", "Ambas Marcam"],
  ["TEAM_TOTAL_HOME", "Gols Mandante"],
  ["TEAM_TOTAL_AWAY", "Gols Visitante"],
  ["HANDICAP", "Handicap"],
  ["TOTAL_CORNERS", "Escanteios"],
  ["TOTAL_CARDS", "Cartões"],
];

export function EntriesPage() {
  const [window, setWindow] = useState<EventWindow>("48h");
  const [market, setMarket] = useState("");
  const [competition, setCompetition] = useState("");
  const [minOdd, setMinOdd] = useState("");
  const [maxOdd, setMaxOdd] = useState("");
  const [grades, setGrades] = useState("A,B");
  const [includeWatch, setIncludeWatch] = useState(false);
  const navigate = useNavigate();
  const addLeg = useUi((s) => s.addLeg);

  const q = useQuery({
    queryKey: ["entries", window, market, competition, minOdd, maxOdd, grades, includeWatch],
    queryFn: () =>
      api.entries({
        window,
        market: market || undefined,
        competition: competition || undefined,
        min_odd: minOdd ? Number(minOdd) : undefined,
        max_odd: maxOdd ? Number(maxOdd) : undefined,
        grades,
        include_watch: includeWatch,
      }),
    refetchInterval: 60000,
  });

  return (
    <div className="space-y-5">
      <PageHeader title="Melhores Entradas" subtitle="Seleções com edge e confiança acima dos limiares. Ordenadas por Opportunity Score." />
      <div className="card flex flex-wrap items-center gap-3 p-3">
        <Segmented
          value={window}
          onChange={setWindow}
          options={[
            { value: "today", label: "Hoje" },
            { value: "tomorrow", label: "Amanhã" },
            { value: "48h", label: "48h" },
            { value: "week", label: "7 dias" },
          ]}
        />
        <input className="input max-w-[200px]" placeholder="Campeonato" value={competition} onChange={(e) => setCompetition(e.target.value)} />
        <select className="input max-w-[200px]" value={market} onChange={(e) => setMarket(e.target.value)}>
          {MARKETS.map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
        <input className="input w-24" placeholder="Odd mín" value={minOdd} onChange={(e) => setMinOdd(e.target.value)} />
        <input className="input w-24" placeholder="Odd máx" value={maxOdd} onChange={(e) => setMaxOdd(e.target.value)} />
        <Segmented
          value={grades}
          onChange={setGrades}
          options={[
            { value: "A", label: "Só A" },
            { value: "A,B", label: "A + B" },
            { value: "A,B,C", label: "A, B, C" },
          ]}
        />
        <label className="flex items-center gap-2 text-sm text-ink-2">
          <input type="checkbox" checked={includeWatch} onChange={(e) => setIncludeWatch(e.target.checked)} /> Incluir "em observação"
        </label>
      </div>

      {q.isLoading && <Loading />}
      {q.isError && <ErrorBox error={q.error} retry={() => q.refetch()} />}
      {q.data && q.data.total === 0 && (
        <Empty title="Nenhuma entrada atende aos filtros" detail="Isso é esperado em dias com poucos jogos de competições suportadas ou quando o mercado está bem precificado. O EdgeFut prefere não recomendar a forçar uma entrada." />
      )}
      {q.data && q.data.total > 0 && (
        <div className="card overflow-hidden p-0">
          <table className="w-full text-sm">
            <thead className="bg-bg text-left text-[11px] font-semibold uppercase tracking-wide text-ink-2">
              <tr>
                <th className="px-4 py-2.5">Jogo</th>
                <th className="px-3 py-2.5">Mercado</th>
                <th className="px-3 py-2.5 text-right">Odd</th>
                <th className="px-3 py-2.5 text-right">Modelo</th>
                <th className="px-3 py-2.5 text-right">Mercado</th>
                <th className="px-3 py-2.5 text-right">Edge</th>
                <th className="px-3 py-2.5 text-right">EV</th>
                <th className="px-3 py-2.5 text-center">Conf.</th>
                <th className="px-3 py-2.5 text-right">Score</th>
                <th className="px-3 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {q.data.rows.map((r, i) => (
                <tr key={i} className="cursor-pointer border-t border-line/70 transition hover:bg-gray-50" onClick={() => navigate(`/jogos/${r.event.id}`)}>
                  <td className="px-4 py-2.5">
                    <div className="text-[10px] font-semibold uppercase text-ink-3">
                      {r.event.competition_name} · {dayLabel(r.event.kickoff_utc)} {fmtTime(r.event.kickoff_utc)}
                    </div>
                    <div className="font-semibold">
                      {r.event.home_name} x {r.event.away_name}
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="text-[10px] font-semibold uppercase text-ink-3">{r.recommendation.market_label}</div>
                    <div className="font-medium">{r.recommendation.selection_name}</div>
                    <StatusChip status={r.recommendation.status} />
                  </td>
                  <td className="px-3 py-2.5 text-right font-bold tabular-nums">{odd(r.recommendation.odd)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{pct(r.recommendation.model_prob, 1)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-ink-2">{pct(r.recommendation.market_prob, 1)}</td>
                  <td className="px-3 py-2.5 text-right">
                    <EdgeValue value={r.recommendation.edge_pp} />
                  </td>
                  <td className={`px-3 py-2.5 text-right tabular-nums ${r.recommendation.ev_pct > 0 ? "text-success" : "text-danger"}`}>
                    {r.recommendation.ev_pct > 0 ? "+" : ""}
                    {r.recommendation.ev_pct.toFixed(1)}%
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    <GradeBadge grade={r.recommendation.confidence_grade} score={r.recommendation.confidence_score} />
                  </td>
                  <td className="px-3 py-2.5 text-right font-semibold tabular-nums">{Math.round(r.recommendation.opportunity_score)}</td>
                  <td className="px-3 py-2.5">
                    <button
                      className="btn-ghost"
                      title="Adicionar à múltipla"
                      onClick={(e) => {
                        e.stopPropagation();
                        addLeg({
                          event_id: r.event.id,
                          market_key: r.recommendation.market_key,
                          selection_key: r.recommendation.selection_key,
                          line: r.recommendation.line,
                          odd: r.recommendation.odd,
                          label: `${r.recommendation.market_label}: ${r.recommendation.selection_name}`,
                          event_label: `${r.event.home_name} x ${r.event.away_name}`,
                          model_prob: r.recommendation.model_prob,
                        });
                      }}
                    >
                      <Plus size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
