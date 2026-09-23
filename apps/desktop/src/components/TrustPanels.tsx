import { useQuery } from "@tanstack/react-query";
import type { ConfidenceBreakdown, ConfidenceGroup, ConflictOut, Freshness, LineMovement, MatchAnalysis, ModelComparison, Recommendation } from "@edgefut/contracts";
import { CONFIDENCE_GROUP_LABELS, EVIDENCE_LABELS } from "@edgefut/contracts";
import { fmtDateTime, num, odd, pct } from "@edgefut/shared";
import clsx from "clsx";
import { Check, ChevronDown, ChevronUp, GitCompareArrows, Info, ShieldAlert, X } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";

import { EvidenceChip, ExposureChip, FreshnessChip, GateChip, LabelChip, MeterBar, Modal, StateChip, Tooltip } from "./ui";

// ---------------------------------------------------------------- EDGEFUT CONFIDENCE
const GROUP_ORDER: ConfidenceGroup[] = ["DATA_QUALITY", "MODEL_AGREEMENT", "CALIBRATION", "HISTORICAL_SAMPLE", "FRESHNESS", "CONTEXT"];

export function ConfidenceIndicator({ c, size = "lg" }: { c: ConfidenceBreakdown; size?: "md" | "lg" }) {
  const [open, setOpen] = useState(false);
  const v = c.score;
  const color = v >= 80 ? "text-success" : v >= 65 ? "text-info" : v >= 50 ? "text-warning" : "text-danger";
  const ring = v >= 80 ? "border-success" : v >= 65 ? "border-info" : v >= 50 ? "border-warning" : "border-danger";
  return (
    <>
      <button className="group flex items-center gap-3 rounded-xl border border-line bg-card px-3 py-2 text-left transition hover:border-ink-3" onClick={() => setOpen(true)} title="Ver breakdown da confiança">
        <div className={clsx("grid place-items-center rounded-full border-4 font-extrabold tabular-nums", ring, color, size === "lg" ? "h-16 w-16 text-2xl" : "h-12 w-12 text-lg")}>{Math.round(v)}</div>
        <div>
          <div className="text-[10px] font-bold uppercase tracking-wider text-ink-3">EDGEFUT CONFIDENCE</div>
          <div className="text-sm font-semibold">
            Grau {c.grade} <span className="font-normal text-ink-2">· 0–100</span>
          </div>
          <div className="mt-1 flex gap-0.5">
            {GROUP_ORDER.map((g) => {
              const gv = c.groups[g];
              return <span key={g} className={clsx("h-1.5 w-5 rounded-sm", gv === undefined ? "bg-gray-100" : gv >= 80 ? "bg-success" : gv >= 60 ? "bg-info" : gv >= 40 ? "bg-warning" : "bg-danger")} title={`${CONFIDENCE_GROUP_LABELS[g]}: ${gv !== undefined ? Math.round(gv) : "—"}`} />;
            })}
          </div>
        </div>
      </button>
      {open && (
        <Modal title={`EDGEFUT CONFIDENCE · ${Math.round(v)}/100 (${c.grade})`} onClose={() => setOpen(false)} wide>
          <p className="mb-3 text-xs text-ink-2">
            A confiança mede quanto <b>dá para acreditar</b> nesta análise — não a probabilidade de acerto. Vem de {c.components.length} componentes agrupados em 6 dimensões ({c.model_version}). Cada componente vale 0–1 e é ponderado; o total é normalizado para 0–100.
          </p>
          <div className="grid gap-4 md:grid-cols-2">
            {GROUP_ORDER.map((g) => {
              const comps = c.components.filter((k) => k.group === g);
              if (comps.length === 0) return null;
              const gv = c.groups[g];
              return (
                <div key={g} className="rounded-lg border border-line p-3">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-xs font-bold uppercase tracking-wide">{CONFIDENCE_GROUP_LABELS[g]}</span>
                    <span className={clsx("text-sm font-bold tabular-nums", gv !== undefined && gv >= 80 ? "text-success" : gv !== undefined && gv >= 60 ? "text-info" : gv !== undefined && gv >= 40 ? "text-warning" : "text-danger")}>{gv !== undefined ? Math.round(gv) : "—"}</span>
                  </div>
                  <MeterBar value={gv ?? 0} color={gv !== undefined && gv >= 80 ? "bg-success" : gv !== undefined && gv >= 60 ? "bg-info" : gv !== undefined && gv >= 40 ? "bg-warning" : "bg-danger"} className="mb-2" />
                  <div className="space-y-1.5">
                    {comps.map((k, i) => (
                      <div key={i} className="text-xs">
                        <div className="flex justify-between gap-2">
                          <span>{k.name}</span>
                          <span className="shrink-0 tabular-nums text-ink-2">
                            {pct(k.value)} × peso {k.weight}
                          </span>
                        </div>
                        {k.note && <div className="text-[11px] text-ink-3">{k.note}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </Modal>
      )}
    </>
  );
}

// ---------------------------------------------------------------- Frescor
export function FreshnessStrip({ items, overall }: { items: Freshness[]; overall: MatchAnalysis["freshness_status"] }) {
  if (items.length === 0) return null;
  // colapsa itens repetidos do mesmo tipo (ex.: forma mandante/visitante) mostrando o pior estado
  const rank = { FRESH: 0, AGING: 1, STALE: 2, EXPIRED: 3, UNAVAILABLE: 4 } as const;
  const byKind = new Map<string, Freshness>();
  items.forEach((f) => {
    const cur = byKind.get(f.kind);
    if (!cur || rank[f.status] > rank[cur.status]) byKind.set(f.kind, f);
  });
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Tooltip text="Frescor de cada insumo: quando foi coletado e até quando vale. Odds: FRESH < 10 min, AGING < 45 min, STALE < 6 h, depois EXPIRED — e dados EXPIRED nunca entram silenciosamente numa recomendação.">
        <span className="inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-ink-3">
          Frescor <Info size={11} />
        </span>
      </Tooltip>
      {[...byKind.values()].map((f) => (
        <FreshnessChip key={f.kind} status={f.status} ageSeconds={f.age_seconds} label={f.label} title={`${f.label}: ${f.status}${f.collected_at ? ` · coletado ${fmtDateTime(f.collected_at)}` : ""}${f.valid_until ? ` · válido até ${fmtDateTime(f.valid_until)}` : ""}${f.source ? ` · fonte ${f.source}` : ""}${f.note ? `\n${f.note}` : ""}`} />
      ))}
      {overall && overall !== "FRESH" && <span className="text-[11px] text-warning">estado geral: {overall}</span>}
    </div>
  );
}

// ---------------------------------------------------------------- Divergências
function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function ConflictsButton({ eventId, count, canonicalId }: { eventId: number; count: number; canonicalId: string | null }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className={clsx("btn-ghost text-xs", count > 0 ? "text-warning" : "text-ink-3")} onClick={() => setOpen(true)} title="Ver como cada divergência entre fontes foi resolvida">
        <GitCompareArrows size={13} /> {count} divergência{count === 1 ? "" : "s"} de dados resolvida{count === 1 ? "" : "s"}
      </button>
      {open && <ConflictsModal eventId={eventId} canonicalId={canonicalId} onClose={() => setOpen(false)} />}
    </>
  );
}

function ConflictsModal({ eventId, canonicalId, onClose }: { eventId: number; canonicalId: string | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ["conflicts", eventId], queryFn: () => api.conflicts(eventId) });
  const d = q.data;
  return (
    <Modal title="Source Conflict Engine" onClose={onClose} wide>
      <p className="mb-3 text-xs text-ink-2">
        Quando duas fontes discordam sobre o mesmo campo, o EdgeFut não escolhe em silêncio: registra as duas versões, a regra usada e a confiança da escolha. Evento canônico:{" "}
        <span className="font-mono">{d?.canonical_event_id ?? canonicalId ?? "—"}</span>
        {d?.home_canonical && (
          <>
            {" "}
            · {d.home_canonical} × {d.away_canonical}
          </>
        )}
        {d?.duplicate_of && <span className="text-warning"> · duplicado de #{d.duplicate_of}</span>}
      </p>
      {!d ? (
        <div className="text-sm text-ink-2">Carregando…</div>
      ) : d.conflicts.length === 0 ? (
        <div className="text-sm text-ink-2">Nenhuma divergência registrada para este evento.</div>
      ) : (
        <div className="space-y-2">
          {d.conflicts.map((c) => (
            <ConflictRow key={c.id} c={c} />
          ))}
        </div>
      )}
    </Modal>
  );
}

function ConflictRow({ c }: { c: ConflictOut }) {
  const aSel = c.selected_source === c.source_a;
  const bSel = c.selected_source === c.source_b;
  return (
    <div className="rounded-lg border border-line p-3 text-xs">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="font-bold">{c.field_label}</span>
        <span className="text-ink-3">{fmtDateTime(c.created_at)}</span>
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        <div className={clsx("rounded-md border p-2", aSel ? "border-success/40 bg-success-50/40" : "border-line bg-bg")}>
          <div className="flex items-center justify-between">
            <span className="font-semibold">{c.source_a}</span>
            {aSel && <Check size={12} className="text-success" />}
          </div>
          <div className="break-all font-mono text-[11px] text-ink-2">{fmtVal(c.value_a)}</div>
        </div>
        <div className={clsx("rounded-md border p-2", bSel ? "border-success/40 bg-success-50/40" : "border-line bg-bg")}>
          <div className="flex items-center justify-between">
            <span className="font-semibold">{c.source_b}</span>
            {bSel && <Check size={12} className="text-success" />}
          </div>
          <div className="break-all font-mono text-[11px] text-ink-2">{fmtVal(c.value_b)}</div>
        </div>
      </div>
      <div className="mt-1.5 text-ink-2">
        Regra: <b>{c.resolution_label}</b> ({c.resolution_method}) · confiança {pct(c.confidence)} · escolhido: <span className="font-mono">{fmtVal(c.selected_value)}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Comparação de modelos
export function ModelComparisonTable({ mc }: { mc: ModelComparison }) {
  const rows = [...mc.rows, ...(mc.consensus ? [mc.consensus] : [])];
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-[10px] font-semibold uppercase tracking-wide text-ink-2">
            <tr>
              <th className="py-1.5 pr-2">Modelo</th>
              <th className="pr-2 text-right">Peso</th>
              <th className="pr-2 text-right">λ casa</th>
              <th className="pr-2 text-right">λ fora</th>
              <th className="pr-2 text-right">1</th>
              <th className="pr-2 text-right">X</th>
              <th className="pr-2 text-right">2</th>
              <th className="pr-2 text-right">Over 2.5</th>
              <th className="pr-2 text-right">BTTS</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const excluded = mc.excluded_low_weight.includes(r.key);
              const isCons = r.key === "consensus";
              return (
                <tr key={r.key} className={clsx("border-t border-line/70", isCons && "bg-bg font-semibold", excluded && "text-ink-3")} title={[r.note, excluded ? "Peso < 10% no walk-forward: exibido, mas fora do veto de divergência." : ""].filter(Boolean).join("\n")}>
                  <td className="py-1.5 pr-2">
                    {r.label}
                    <div className="font-mono text-[10px] font-normal text-ink-3">{r.model_version}</div>
                  </td>
                  <td className="pr-2 text-right tabular-nums">{r.weight !== null && !isCons ? <span className={clsx(excluded && "text-warning")}>{pct(r.weight, 1)}</span> : isCons ? "—" : "—"}</td>
                  <td className="pr-2 text-right tabular-nums">{num(r.lambda_home, 2)}</td>
                  <td className="pr-2 text-right tabular-nums">{num(r.lambda_away, 2)}</td>
                  <td className="pr-2 text-right tabular-nums">{pct(r.p_home, 1)}</td>
                  <td className="pr-2 text-right tabular-nums">{pct(r.p_draw, 1)}</td>
                  <td className="pr-2 text-right tabular-nums">{pct(r.p_away, 1)}</td>
                  <td className="pr-2 text-right tabular-nums">{pct(r.over25, 1)}</td>
                  <td className="pr-2 text-right tabular-nums">{pct(r.btts, 1)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-2">
        <span>
          Divergência máxima no 1X2: <b className={clsx((mc.max_disagreement_pp ?? 0) > 10 ? "text-warning" : "text-ink")}>{mc.max_disagreement_pp !== null ? `${mc.max_disagreement_pp.toFixed(1)} pp` : "—"}</b>
          {mc.disagreement_scope.length > 0 && <span className="text-ink-3"> (entre {mc.disagreement_scope.map((k) => mc.rows.find((r) => r.key === k)?.label ?? k).join(" e ")})</span>}
        </span>
        <span>
          Pesos: <b>{mc.weights_source}</b>
          {mc.weights_sample ? ` · N=${mc.weights_sample.toLocaleString("pt-BR")}` : ""}
        </span>
        {mc.excluded_low_weight.length > 0 && (
          <Tooltip text="Modelos com peso < 10% no walk-forward desta competição seguem visíveis para transparência, mas não acionam o veto de divergência — senão um modelo fraco bloquearia os fortes.">
            <span className="inline-flex items-center gap-1 text-warning">
              <Info size={11} /> {mc.excluded_low_weight.map((k) => mc.rows.find((r) => r.key === k)?.label ?? k).join(", ")} fora do veto
            </span>
          </Tooltip>
        )}
        {Object.entries(mc.disagreement_pairs).map(([k, v]) => (
          <span key={k} className="rounded bg-bg px-1.5 py-0.5 tabular-nums text-ink-3">
            {k.split("|").map((m) => mc.rows.find((r) => r.key === m)?.label ?? m).join(" × ")}: {v.toFixed(1)} pp
          </span>
        ))}
      </div>
      {mc.note && <div className="mt-1 text-[11px] text-ink-3">{mc.note}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- WHY / WHY NOT
export function WhyBlock({ title, lines, tone }: { title: string; lines: string[]; tone: "ok" | "warn" }) {
  if (lines.length === 0) return null;
  return (
    <div className={clsx("rounded-lg border p-3", tone === "ok" ? "border-success/30 bg-success-50/30" : "border-warning/30 bg-warning-50/30")}>
      <div className={clsx("mb-1 text-[11px] font-bold uppercase tracking-wide", tone === "ok" ? "text-success" : "text-warning")}>{title}</div>
      <ul className="space-y-0.5 text-xs leading-relaxed">
        {lines.map((l, i) => (
          <li key={i} className="flex gap-1.5">
            <span className="mt-[5px] h-1 w-1 shrink-0 rounded-full bg-current opacity-60" />
            <span>{l}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function EventWhyNot({ a }: { a: MatchAnalysis }) {
  if (a.why_not.length === 0) return null;
  return (
    <div className="mt-2">
      <WhyBlock title="Por que não" lines={a.why_not} tone="warn" />
    </div>
  );
}

// ---------------------------------------------------------------- Recomendação detalhada
export function RecommendationDetail({ r, evidence }: { r: Recommendation; evidence: MatchAnalysis["evidence"] }) {
  const [open, setOpen] = useState(false);
  const gate = r.quality_gate;
  const opp = r.opportunity;
  const raw = r.model_prob_raw ?? r.model_prob;
  const cal = r.model_prob_calibrated;
  return (
    <div className={clsx("rounded-xl border p-4", r.status === "RECOMMENDED" ? "border-success/40 bg-card" : r.status === "WATCH" ? "border-warning/40 bg-card" : "border-line bg-card")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="label">{r.market_label}</div>
          <div className="text-lg font-bold">
            {r.selection_name}
            {r.line !== null && !r.selection_name.includes(String(r.line)) ? ` ${r.line}` : ""} <span className="odd-pill ml-1 align-middle text-base">{odd(r.odd)}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <StateChip state={r.state} text={r.state_text} />
            {r.label && r.label !== r.state && <LabelChip label={r.label} />}
            {r.is_primary === false && r.primary_of && (
              <span className="chip bg-gray-100 text-ink-2" title={`Alternativa da mesma tese que ${r.primary_of.replace("|", " · ")} — não é uma oportunidade adicional.`}>
                ALTERNATIVA
              </span>
            )}
            {gate && <GateChip passed={gate.passed} failed={gate.failed} />}
            <EvidenceChip level={r.evidence ?? evidence} compact />
            <span className={clsx("chip", r.status === "RECOMMENDED" ? "bg-success-50 text-success" : r.status === "WATCH" ? "bg-warning-50 text-warning" : "bg-gray-100 text-ink-2")}>{r.status === "RECOMMENDED" ? "Recomendada" : r.status === "WATCH" ? "Em observação" : "Não entrar"}</span>
          </div>
        </div>
        <div className="grid grid-cols-4 gap-3 text-center text-xs">
          <div>
            <div className="text-[10px] font-semibold uppercase text-ink-3">Modelo</div>
            <div className="text-base font-bold tabular-nums">{pct(r.model_prob, 1)}</div>
            <div className="text-[10px] text-ink-3">{cal !== null && r.calibration_reliable ? "CALIBRATED" : "RAW"}</div>
          </div>
          <div>
            <div className="text-[10px] font-semibold uppercase text-ink-3">{r.market_prob_is_fair ? "Justa" : "Mercado"}</div>
            <div className="text-base font-bold tabular-nums">{pct(r.market_prob, 1)}</div>
            <div className="text-[10px] text-ink-3">{r.market_prob_is_fair ? "margem removida" : "implícita"}</div>
          </div>
          <div>
            <div className="text-[10px] font-semibold uppercase text-ink-3">Edge</div>
            <div className={clsx("text-base font-bold tabular-nums", r.edge_pp >= 3 ? "text-success" : r.edge_pp > 0 ? "text-ink" : "text-danger")}>
              {r.edge_pp > 0 ? "+" : ""}
              {r.edge_pp.toFixed(1)} pp
            </div>
            <div className="text-[10px] text-ink-3">
              EV {r.ev_pct > 0 ? "+" : ""}
              {r.ev_pct.toFixed(1)}%
            </div>
          </div>
          <div>
            <div className="text-[10px] font-semibold uppercase text-ink-3">Opportunity</div>
            <div className="text-base font-bold tabular-nums">{Math.round(r.opportunity_score)}</div>
            <div className="text-[10px] text-ink-3">confiança {r.confidence_grade} {Math.round(r.confidence_score)}</div>
          </div>
        </div>
      </div>

      <div className="mt-3 grid gap-2 md:grid-cols-2">
        <WhyBlock title="Why this bet" lines={r.why} tone="ok" />
        <WhyBlock title="Why not / limitações" lines={r.why_not} tone="warn" />
      </div>

      {r.state === "MODEL_ONLY" && (
        <div className="mt-2 rounded-lg bg-warning-50/60 px-3 py-2 text-xs text-warning">
          <b>MODEL ONLY:</b> {r.state_text ?? "Probabilidade calculada, mas sem preço de mercado válido para determinar valor."} Não existe edge, EV, VALUE nem ROI aqui.
        </div>
      )}
      {r.price && r.state !== "MODEL_ONLY" && <PriceTargetBlock r={r} />}

      <div className="mt-2 rounded-lg bg-bg px-3 py-2 text-xs">
        <span className="font-semibold">RAW vs CALIBRATED:</span> probabilidade crua {pct(raw, 1)}
        {cal !== null ? (
          <>
            {" "}
            → calibrada {pct(cal, 1)} ({r.calibration_group ?? "grupo"}; {r.calibration_reliable ? "usada na recomendação" : "amostra insuficiente — NÃO usada"})
          </>
        ) : (
          <> · sem calibrador para este mercado ainda ({r.calibration_group ?? "sem grupo"}): a recomendação usa a probabilidade crua.</>
        )}
      </div>

      <button className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-ink-2 hover:text-ink" onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />} {open ? "Ocultar" : "Ver"} Quality Gate e Opportunity Score
      </button>
      {open && (
        <div className="mt-2 grid gap-3 md:grid-cols-2">
          {gate && (
            <div className="rounded-lg border border-line p-3">
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-[11px] font-bold uppercase tracking-wide">Quality Gate</span>
                <span className={clsx("chip", gate.passed ? "bg-success-50 text-success" : "bg-danger-50 text-danger")}>{gate.passed ? `${gate.checks.length}/${gate.checks.length} OK` : `${gate.failed.length} falha(s)`}</span>
              </div>
              <div className="space-y-1">
                {gate.checks.map((c) => (
                  <div key={c.key} className="flex items-start gap-2 text-xs">
                    {c.passed ? <Check size={13} className="mt-0.5 shrink-0 text-success" /> : <X size={13} className="mt-0.5 shrink-0 text-danger" />}
                    <div>
                      <span className={c.passed ? "" : "font-semibold text-danger"}>{c.label}</span>
                      {c.detail && <div className="text-[11px] text-ink-3">{c.detail}</div>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {opp && (
            <div className="rounded-lg border border-line p-3">
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-[11px] font-bold uppercase tracking-wide">Opportunity Score · {opp.model_version}</span>
                <span className="text-sm font-bold tabular-nums">{Math.round(opp.score)}/100</span>
              </div>
              <div className="space-y-1.5">
                {opp.components.map((c) => (
                  <div key={c.key} className="text-xs">
                    <div className="flex justify-between gap-2">
                      <span>{c.label}</span>
                      <span className="shrink-0 tabular-nums text-ink-2">
                        {pct(c.value)} × {c.weight}
                      </span>
                    </div>
                    <MeterBar value={c.value * 100} color="bg-ink-2" className="mt-0.5 h-1.5" />
                    {c.note && <div className="text-[11px] text-ink-3">{c.note}</div>}
                  </div>
                ))}
              </div>
              <div className="mt-2 text-[11px] text-ink-3">Pesos configuráveis em Configurações → Opportunity Score. A ordenação do Radar usa este número, nunca a odd.</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Price target (§23–25)
function PriceTargetBlock({ r }: { r: Recommendation }) {
  const p = r.price!;
  const gap = p.price_gap_pct;
  const sens = p.edge_sensitivity;
  const watching = r.reasons.includes("WATCHING_PRICE");
  return (
    <div className={clsx("mt-2 rounded-lg px-3 py-2 text-xs", watching ? "bg-warning-50/60" : "bg-bg")}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="font-semibold">PRICE TARGET:</span>
        <span>
          break-even <b className="tabular-nums">{odd(p.break_even_odd)}</b>
        </span>
        <span>
          odd mínima aceitável <b className="tabular-nums">{p.min_acceptable_odd ? odd(p.min_acceptable_odd) : "—"}</b> <span className="text-ink-3">(limitada por {p.min_odd_reason === "edge" ? "edge mínimo" : "EV mínimo"})</span>
        </span>
        <span>
          atual <b className="tabular-nums">{odd(r.odd)}</b>{" "}
          {gap !== undefined && (
            <span className={clsx("tabular-nums", gap >= 0 ? "text-success" : "text-danger")}>
              ({gap >= 0 ? "+" : ""}{gap.toFixed(1)}% vs mínimo)
            </span>
          )}
        </span>
        {r.oos && (
          <span title={`Prova out-of-sample neste mercado: N=${r.oos.n}${r.oos.min ? ` (mín. ${r.oos.min})` : ""}${r.oos.source ? ` · fonte ${r.oos.source}` : ""}`}>
            OOS <b>{r.oos.verdict ?? (r.oos.n >= (r.oos.min ?? 0) ? "OK" : "INSUFICIENTE")}</b> <span className="text-ink-3">N={r.oos.n}</span>
          </span>
        )}
      </div>
      {watching && <div className="mt-1 font-semibold text-warning">Probabilidade interessante, mas preço atual não oferece margem suficiente.</div>}
      {sens && (
        <div className="mt-1 text-ink-2">
          Sensibilidade ±{p.edge_sensitivity_pp ?? 3} pp na probabilidade: edge {sens.prob_minus.edge_pp > 0 ? "+" : ""}{sens.prob_minus.edge_pp.toFixed(1)} pp / EV {sens.prob_minus.ev_pct > 0 ? "+" : ""}{sens.prob_minus.ev_pct.toFixed(1)}% (−) · {sens.prob_plus.edge_pp > 0 ? "+" : ""}{sens.prob_plus.edge_pp.toFixed(1)} pp / EV {sens.prob_plus.ev_pct > 0 ? "+" : ""}{sens.prob_plus.ev_pct.toFixed(1)}% (+)
          {p.edge_survives_minus !== undefined && (
            <span className={clsx("ml-1 font-semibold", p.edge_survives_minus ? "text-success" : "text-warning")}>{p.edge_survives_minus ? "· edge sobrevive ao cenário pessimista" : "· edge NÃO sobrevive ao cenário pessimista"}</span>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Clusters / exposição (§3–4, §28)
export function ClustersPanel({ a }: { a: MatchAnalysis }) {
  const clusters = a.clusters ?? [];
  const actionable = clusters.filter((c) => c.state === "VALUE" || c.state === "VALUE_CANDIDATE");
  // chaves vêm do engine no formato Python (`None`, `2.5`, `1.0`) — normalizamos aqui
  const pyKey = (r: Recommendation) => `${r.market_key}|${r.selection_key}|${r.line === null ? "None" : Number.isInteger(r.line) ? r.line.toFixed(1) : String(r.line)}`;
  const byKey = new Map<string, Recommendation>(a.recommendations.map((r) => [pyKey(r), r]));
  const name = (k: string | null) => {
    if (!k) return "—";
    const r = byKey.get(k);
    return r ? `${r.market_label}: ${r.selection_name}${r.line !== null && !r.selection_name.includes(String(r.line)) ? ` ${r.line}` : ""} @ ${odd(r.odd)}` : k.replace(/\|/g, " · ");
  };
  const rank: Record<string, number> = { VALUE: 0, VALUE_CANDIDATE: 1, OBSERVATION: 2, MODEL_ONLY: 3 };
  const visible = clusters
    .filter((c) => c.state && c.state in rank)
    .sort((x, y) => (rank[x.state as string] ?? 9) - (rank[y.state as string] ?? 9) || y.alternatives.length - x.alternatives.length)
    .slice(0, 8);
  if (clusters.length === 0) return null;
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <ExposureChip level={a.exposure?.level} note={a.exposure?.note} />
        <span className="text-xs text-ink-2">
          {actionable.length} tese(s) acionável(is) de {clusters.length} · {a.exposure?.note}
        </span>
      </div>
      {visible.length === 0 ? (
        <div className="text-xs text-ink-3">Nenhuma tese com edge ou probabilidade relevante neste jogo.</div>
      ) : (
        <div className="grid gap-2 md:grid-cols-2">
          {visible.map((c) => (
            <div key={c.cluster_id} className={clsx("rounded-lg border p-3 text-xs", actionable.includes(c) ? "border-primary/40" : "border-line")}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold">{c.label}</span>
                <StateChip state={c.state} />
              </div>
              <div className="mt-1">
                <span className="text-ink-3">Primária: </span>
                <b>{name(c.primary)}</b>
              </div>
              {c.alternatives.length > 0 && (
                <div className="mt-0.5 text-ink-2">
                  <span className="text-ink-3">Alternativas (mesma tese, não somam): </span>
                  {c.alternatives.slice(0, 5).map(name).join(" · ")}
                  {c.alternatives.length > 5 ? ` · +${c.alternatives.length - 5}` : ""}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {visible.length < clusters.filter((c) => c.state && c.state in rank).length && <div className="mt-1 text-[11px] text-ink-3">Mostrando {visible.length} teses; as demais estão na tabela de mercados.</div>}
      {a.exposure && a.exposure.correlated_pairs.length > 0 && (
        <div className="mt-2 text-[11px] text-warning">Teses correlacionadas entre si: {a.exposure.correlated_pairs.map((p) => p.join(" ↔ ")).join(" · ")} — não são independentes.</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- WHY MODEL CHANGED (§39)
export function WhyChangedPanel({ a }: { a: MatchAnalysis }) {
  const c = a.changes;
  if (!c) return null;
  const driverLabel: Record<string, string> = {
    ODDS_MOVED: "Odds mudaram",
    NEW_MATCHES: "Jogos novos na amostra",
    RATINGS_CHANGED: "Forças reestimadas",
    MODEL_VERSION: "Versão do modelo",
    CHAMPION_CHANGED: "Campeão trocado",
    CALIBRATION: "Calibração",
    NONE: "Sem mudança relevante",
  };
  return (
    <div className="text-xs">
      <div className="flex flex-wrap items-center gap-1">
        {c.status === "FIRST_ANALYSIS" ? (
          <span className="chip bg-gray-100 text-ink-2">PRIMEIRA ANÁLISE</span>
        ) : (
          <>
            {c.drivers.map((d) => (
              <span key={d} className={clsx("chip", d === "NONE" ? "bg-gray-100 text-ink-2" : d === "ODDS_MOVED" ? "bg-info-50 text-info" : "bg-warning-50 text-warning")}>
                {driverLabel[d] ?? d}
              </span>
            ))}
            {c.previous_age_hours !== undefined && c.previous_age_hours !== null && <span className="text-ink-3">vs snapshot #{c.previous_snapshot_id} há {c.previous_age_hours} h</span>}
          </>
        )}
      </div>
      <p className="mt-1 leading-relaxed text-ink-2">{c.text}</p>
      {c.selections.length > 0 && (
        <table className="mt-2 w-full">
          <thead className="text-left text-[10px] font-semibold uppercase tracking-wide text-ink-3">
            <tr>
              <th className="py-1">Seleção</th>
              <th className="text-right">Prob. antes → depois</th>
              <th className="text-right">Odd</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {c.selections.slice(0, 6).map((s) => (
              <tr key={s.key} className="border-t border-line/70">
                <td className="py-1">{s.market_label}: {s.selection_name}</td>
                <td className="text-right tabular-nums">
                  {pct(s.prob_before, 1)} → <b>{pct(s.prob_after, 1)}</b> <span className={s.delta_pp >= 0 ? "text-success" : "text-danger"}>({s.delta_pp >= 0 ? "+" : ""}{s.delta_pp.toFixed(1)} pp)</span>
                </td>
                <td className="text-right tabular-nums">{s.odd_before ? odd(s.odd_before) : "—"} → {odd(s.odd_after)}</td>
                <td>{s.state_before ?? "—"} → <b>{s.state_after ?? "—"}</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Line movement
export function MovementLine({ m, name }: { m: LineMovement; name: string }) {
  const down = m.direction === "down";
  const flat = m.direction === "flat";
  return (
    <div className={clsx("flex items-center justify-between gap-2 rounded-md px-3 py-1.5", m.extreme ? "bg-danger-50/60" : "bg-bg")} title={`${m.points} coletas · primeira ${fmtDateTime(m.first_seen_at)} · última ${fmtDateTime(m.last_seen_at)} · mín ${odd(m.lowest_odd)} · máx ${odd(m.highest_odd)}`}>
      <span className="font-medium">{name}</span>
      <span className="text-right tabular-nums">
        <span>
          {odd(m.opening_odd)} → <b>{odd(m.current_odd)}</b>{" "}
          <span className={clsx("text-xs", flat ? "text-ink-3" : down ? "text-primary" : "text-info")}>
            {m.percentage_move > 0 ? "+" : ""}
            {m.percentage_move.toFixed(1)}%
          </span>
        </span>
        <span className="block text-[11px] text-ink-2">
          implícita {pct(m.implied_opening, 1)} → {pct(m.implied_current, 1)} ({m.implied_probability_move_pp > 0 ? "+" : ""}
          {m.implied_probability_move_pp.toFixed(1)} pp)
        </span>
        {m.extreme && <span className="chip bg-danger-50 text-danger">movimento extremo</span>}
      </span>
    </div>
  );
}

export function EvidenceBanner({ level }: { level: MatchAnalysis["evidence"] }) {
  if (!level || level === "SETTLED") return null;
  return (
    <div className={clsx("flex items-start gap-2 rounded-lg border px-3 py-2 text-xs", level === "MODEL_ONLY" ? "border-warning/30 bg-warning-50/40 text-warning" : "border-info/30 bg-info-50/40 text-info")}>
      <ShieldAlert size={14} className="mt-0.5 shrink-0" />
      <span>
        <b>{EVIDENCE_LABELS[level]}.</b>{" "}
        {level === "MODEL_ONLY"
          ? "Esta competição não tem odds históricas nos datasets: o edge mostrado nunca foi verificado contra o mercado. Trate como hipótese, não como vantagem comprovada."
          : "Há odds históricas para esta competição: o edge pode ser verificado em Backtest Lab (walk-forward, anti-leakage). Ainda não há apostas liquidadas suficientes para ROI/CLV reais."}
      </span>
    </div>
  );
}
