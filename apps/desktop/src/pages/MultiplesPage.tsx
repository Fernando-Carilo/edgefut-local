import { useMutation, useQuery } from "@tanstack/react-query";
import { impliedProbability, money, odd, pct } from "@edgefut/shared";
import clsx from "clsx";
import { AlertTriangle, Trash2 } from "lucide-react";
import { useState } from "react";

import { Card, Empty, KV, PageHeader, SectionTitle } from "@/components/ui";
import { api } from "@/lib/api";
import { useUi } from "@/store/ui";

export function MultiplesPage() {
  const legs = useUi((s) => s.builder);
  const removeLeg = useUi((s) => s.removeLeg);
  const clear = useUi((s) => s.clearBuilder);
  const evalQ = useQuery({
    queryKey: ["multiple", legs.map((l) => `${l.event_id}|${l.market_key}|${l.selection_key}|${l.line}|${l.odd}`).join(",")],
    queryFn: () => api.multiples(legs.map(({ event_id, market_key, selection_key, line, odd: o, label }) => ({ event_id, market_key, selection_key, line, odd: o, label }))),
    enabled: legs.length > 0,
  });
  const combined = legs.reduce((acc, l) => acc * l.odd, 1);
  const r = evalQ.data;

  return (
    <div className="space-y-5">
      <PageHeader title="Múltiplas" subtitle="Construtor com detecção de correlação. Prefira 2–3 pernas; nenhuma aposta é feita por aqui." />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <SectionTitle title={`Pernas (${legs.length}/6)`} subtitle="Adicione seleções pela página do jogo ou pela tabela de entradas" right={legs.length > 0 && <button className="btn-ghost text-xs" onClick={clear}>Limpar</button>} />
          {legs.length === 0 ? (
            <Empty title="Nenhuma perna adicionada" detail="Use o botão “Múltipla” nos cards de recomendação ou o “+” em qualquer mercado." />
          ) : (
            <div className="space-y-2">
              {legs.map((l, i) => (
                <div key={i} className="flex items-center gap-3 rounded-lg border border-line px-3 py-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs text-ink-2">{l.event_label}</div>
                    <div className="truncate text-sm font-semibold">{l.label}</div>
                    {l.model_prob !== null && l.model_prob !== undefined && <div className="text-[11px] text-ink-3">modelo {pct(l.model_prob, 1)} · implícita {pct(impliedProbability(l.odd), 1)}</div>}
                  </div>
                  <span className="odd-pill">{odd(l.odd)}</span>
                  <button className="btn-ghost" onClick={() => removeLeg(i)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
          {legs.length > 3 && (
            <div className="mt-3 flex items-center gap-2 rounded-lg bg-warning-50 px-3 py-2 text-xs text-warning">
              <AlertTriangle size={14} /> Múltiplas com mais de 3 pernas multiplicam a margem da casa e reduzem drasticamente a probabilidade real de acerto.
            </div>
          )}
        </Card>
        <div className="space-y-4">
          <Card>
            <SectionTitle title="Avaliação" subtitle="Probabilidade conjunta via simulação quando as pernas são do mesmo jogo" />
            <KV k="Odd combinada" v={odd(r?.combined_odd ?? combined)} />
            <KV k="Prob. implícita" v={pct(r?.implied_probability ?? (legs.length ? 1 / combined : null), 1)} />
            <KV k="Prob. modelo (independente)" v={pct(r?.naive_probability, 1)} />
            <KV k="Prob. modelo (conjunta)" v={pct(r?.joint_probability, 1)} />
            <KV k="EV" v={r?.ev_pct !== null && r?.ev_pct !== undefined ? <span className={r.ev_pct > 0 ? "text-success" : "text-danger"}>{r.ev_pct > 0 ? "+" : ""}{r.ev_pct.toFixed(1)}%</span> : "—"} />
            {r?.correlations.map((c, i) => (
              <div key={i} className="mt-2 flex items-start gap-2 rounded-lg bg-warning-50 px-3 py-2 text-xs text-warning">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" /> {c}
              </div>
            ))}
            {r?.warnings.map((w, i) => (
              <div key={i} className="mt-2 text-xs text-ink-2">
                {w}
              </div>
            ))}
            {r?.per_event.filter((p) => p.legs.length > 1).map((p) => (
              <div key={p.event_id} className="mt-2 text-xs text-ink-2">
                Evento {p.event_id}: conjunta {pct(p.joint_probability, 1)} vs independente {pct(p.naive_probability, 1)} {p.correlated && <b className="text-warning">(correlacionadas)</b>}
              </div>
            ))}
          </Card>
          <ValueSimulator defaultOdd={r?.combined_odd ?? (legs.length ? combined : 2)} defaultProb={r?.joint_probability ?? null} />
        </div>
      </div>
    </div>
  );
}

export function ValueSimulator({ defaultOdd, defaultProb }: { defaultOdd: number; defaultProb: number | null }) {
  const [stake, setStake] = useState("50");
  const [o, setO] = useState(String(defaultOdd.toFixed(2)));
  const [prob, setProb] = useState(defaultProb !== null ? String((defaultProb * 100).toFixed(1)) : "");
  const [bankroll, setBankroll] = useState("1000");
  const sim = useMutation({ mutationFn: () => api.simulator({ stake: Number(stake), odd: Number(o), model_prob: prob ? Number(prob) / 100 : null }) });
  const stakeQ = useMutation({ mutationFn: () => api.stake({ bankroll: Number(bankroll), odd: Number(o), model_prob: Number(prob) / 100, method: "kelly", kelly_fraction: 0.25 }) });
  const run = () => {
    sim.mutate();
    if (prob) stakeQ.mutate();
  };
  return (
    <Card>
      <SectionTitle title="Simulador de valor" subtitle="Apenas simulação — o EdgeFut não realiza apostas" />
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs">
          <span className="label">Stake (R$)</span>
          <input className="input" value={stake} onChange={(e) => setStake(e.target.value)} />
        </label>
        <label className="text-xs">
          <span className="label">Odd</span>
          <input className="input" value={o} onChange={(e) => setO(e.target.value)} />
        </label>
        <label className="text-xs">
          <span className="label">Prob. modelo (%)</span>
          <input className="input" value={prob} onChange={(e) => setProb(e.target.value)} placeholder="opcional" />
        </label>
        <label className="text-xs">
          <span className="label">Banca (R$)</span>
          <input className="input" value={bankroll} onChange={(e) => setBankroll(e.target.value)} />
        </label>
      </div>
      <button className="btn-primary mt-3 w-full justify-center" onClick={run} disabled={sim.isPending}>
        Simular
      </button>
      {sim.data && (
        <div className="mt-3">
          <KV k="Retorno bruto" v={money(sim.data.gross_return)} />
          <KV k="Lucro" v={<span className="text-success">{money(sim.data.profit)}</span>} />
          <KV k="Prob. implícita" v={pct(sim.data.implied_probability, 1)} />
          {sim.data.model_probability !== null && <KV k="Prob. modelo" v={pct(sim.data.model_probability, 1)} />}
          {sim.data.ev_pct !== null && <KV k="EV" v={<span className={clsx(sim.data.ev_pct > 0 ? "text-success" : "text-danger")}>{sim.data.ev_pct > 0 ? "+" : ""}{sim.data.ev_pct.toFixed(1)}%</span>} />}
          {stakeQ.data && (
            <>
              <KV k="Stake sugerida (Kelly ¼)" v={`${money(stakeQ.data.stake)} (${stakeQ.data.stake_pct.toFixed(2)}%)`} />
              {stakeQ.data.warning && <div className="mt-1 text-xs text-warning">{stakeQ.data.warning}</div>}
            </>
          )}
          <div className="mt-2 text-[11px] text-ink-3">{sim.data.note}</div>
        </div>
      )}
    </Card>
  );
}
