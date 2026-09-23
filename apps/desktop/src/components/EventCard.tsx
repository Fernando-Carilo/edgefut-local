import type { EventSummary, Recommendation } from "@edgefut/contracts";
import { dayLabel, fmtTime, odd, pct } from "@edgefut/shared";
import clsx from "clsx";
import { Heart, Plus } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { useUi } from "@/store/ui";

import { DemoChip, EdgeValue, GradeBadge, NoBetChip, StatusChip, VenueChip } from "./ui";

export function EventRow({ event, onFavorite }: { event: EventSummary; onFavorite?: (id: number, on: boolean) => void }) {
  const navigate = useNavigate();
  const mo = event.main_odds;
  return (
    <div className="card card-hover flex items-center gap-4 px-4 py-3" onClick={() => navigate(`/jogos/${event.id}`)}>
      <div className="w-16 shrink-0 text-center">
        <div className="text-xs font-semibold text-ink-2">{dayLabel(event.kickoff_utc)}</div>
        <div className="text-base font-bold tabular-nums">{fmtTime(event.kickoff_utc)}</div>
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[11px] font-semibold uppercase tracking-wide text-ink-3">{event.competition_name}</div>
        <div className="flex items-center gap-2 truncate text-[15px] font-semibold">
          <span className="truncate">{event.home_name}</span>
          <span className="text-ink-3">x</span>
          <span className="truncate">{event.away_name}</span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <VenueChip status={event.venue_status} compact />
          {event.demo && <DemoChip />}
          {event.no_bet_reason && <NoBetChip reason={event.no_bet_reason} />}
          {event.best_market && !event.no_bet_reason && <span className="chip bg-success-50 text-success">{event.best_market}</span>}
        </div>
      </div>
      <div className="hidden items-center gap-1.5 md:flex">
        {mo ? (
          (["HOME", "DRAW", "AWAY"] as const).map((k) => (
            <span key={k} className="odd-pill" title={`${k === "HOME" ? "1" : k === "DRAW" ? "X" : "2"} · implícita ${pct(1 / mo[k])}`}>
              {odd(mo[k])}
            </span>
          ))
        ) : (
          <span className="text-xs text-ink-3">{event.market_count} mercados</span>
        )}
      </div>
      <div className="flex w-24 shrink-0 flex-col items-end gap-1">
        {event.confidence_grade ? <GradeBadge grade={event.confidence_grade} /> : <span className="text-[11px] text-ink-3">{event.no_bet_reason ? "" : "não analisado"}</span>}
        {event.opportunity_score !== null && event.opportunity_score !== undefined && (
          <span className="text-[11px] text-ink-2">
            Score <b className="tabular-nums">{Math.round(event.opportunity_score)}</b>
          </span>
        )}
      </div>
      {onFavorite && (
        <button
          className={clsx("btn-ghost", event.is_favorite && "text-primary")}
          onClick={(e) => {
            e.stopPropagation();
            onFavorite(event.id, !event.is_favorite);
          }}
          title={event.is_favorite ? "Remover dos favoritos" : "Favoritar"}
        >
          <Heart size={16} fill={event.is_favorite ? "currentColor" : "none"} />
        </button>
      )}
    </div>
  );
}

export function RecommendationCard({ event, rec, compact }: { event: EventSummary; rec: Recommendation; compact?: boolean }) {
  const navigate = useNavigate();
  const addLeg = useUi((s) => s.addLeg);
  return (
    <div className="card card-hover flex flex-col gap-2 p-4" onClick={() => navigate(`/jogos/${event.id}`)}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[11px] font-semibold uppercase tracking-wide text-ink-3">{event.competition_name}</div>
          <div className="truncate text-sm font-semibold">
            {event.home_name} <span className="text-ink-3">x</span> {event.away_name}
          </div>
          <div className="text-xs text-ink-2">
            {dayLabel(event.kickoff_utc)} · {fmtTime(event.kickoff_utc)}
          </div>
        </div>
        <GradeBadge grade={rec.confidence_grade} score={rec.confidence_score} />
      </div>
      <div className="flex items-center justify-between rounded-lg bg-bg px-3 py-2">
        <div className="min-w-0">
          <div className="label">{rec.market_label}</div>
          <div className="truncate text-sm font-bold">
            {rec.selection_name}
            {rec.line !== null && !rec.selection_name.includes(String(rec.line)) ? ` ${rec.line}` : ""}
          </div>
        </div>
        <span className="odd-pill bg-card text-base">{odd(rec.odd)}</span>
      </div>
      <div className={clsx("grid gap-2 text-xs", compact ? "grid-cols-3" : "grid-cols-4")}>
        <Mini k="Modelo" v={pct(rec.model_prob)} />
        <Mini k={rec.market_prob_is_fair ? "Mercado (justa)" : "Mercado"} v={pct(rec.market_prob)} />
        <Mini k="Edge" v={<EdgeValue value={rec.edge_pp} />} />
        {!compact && <Mini k="EV" v={<span className={rec.ev_pct > 0 ? "text-success" : "text-danger"}>{rec.ev_pct > 0 ? "+" : ""}{rec.ev_pct.toFixed(1)}%</span>} />}
      </div>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <StatusChip status={rec.status} />
          <span className="text-[11px] text-ink-2">
            Score <b className="tabular-nums">{Math.round(rec.opportunity_score)}</b>
          </span>
        </div>
        {rec.status !== "NO_BET" && (
          <button
            className="btn-ghost text-xs"
            title="Adicionar ao construtor de múltiplas"
            onClick={(e) => {
              e.stopPropagation();
              addLeg({
                event_id: event.id,
                market_key: rec.market_key,
                selection_key: rec.selection_key,
                line: rec.line,
                odd: rec.odd,
                label: `${rec.market_label}: ${rec.selection_name}`,
                event_label: `${event.home_name} x ${event.away_name}`,
                model_prob: rec.model_prob,
              });
            }}
          >
            <Plus size={14} /> Múltipla
          </button>
        )}
      </div>
    </div>
  );
}

function Mini({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-3">{k}</div>
      <div className="font-semibold tabular-nums">{v}</div>
    </div>
  );
}
