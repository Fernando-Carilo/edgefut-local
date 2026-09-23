import type { EvidenceLevel, FreshnessStatus, Grade, HealthStatus, NoBetReason, OpportunityLabel, RecommendationStatus, VenueStatus } from "@edgefut/contracts";
import { EVIDENCE_LABELS, FRESHNESS_LABELS, HEALTH_LABELS, LABEL_TEXT, NO_BET_LABELS } from "@edgefut/contracts";
import { pct, venueLabel } from "@edgefut/shared";
import clsx from "clsx";
import { AlertTriangle, Info, Loader2, ShieldCheck, ShieldOff } from "lucide-react";
import type { ReactNode } from "react";

/** Idade em segundos → "há 18 s" / "há 4 min" / "há 2 h". */
export function ageLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `há ${s} s`;
  const m = Math.round(s / 60);
  if (m < 90) return `há ${m} min`;
  const h = Math.round(m / 60);
  if (h < 48) return `há ${h} h`;
  return `há ${Math.round(h / 24)} d`;
}

const freshnessCls: Record<FreshnessStatus, string> = {
  FRESH: "bg-success-50 text-success",
  AGING: "bg-info-50 text-info",
  STALE: "bg-warning-50 text-warning",
  EXPIRED: "bg-danger-50 text-danger",
  UNAVAILABLE: "bg-gray-100 text-ink-3",
};

export function FreshnessChip({ status, ageSeconds, label, title }: { status: FreshnessStatus | null | undefined; ageSeconds?: number | null; label?: string; title?: string }) {
  if (!status) return null;
  return (
    <span className={clsx("chip", freshnessCls[status])} title={title ?? `${label ? `${label}: ` : ""}${FRESHNESS_LABELS[status]}`}>
      {label ? `${label} ` : ""}
      {FRESHNESS_LABELS[status]}
      {ageSeconds !== undefined && ageSeconds !== null ? ` · ${ageLabel(ageSeconds)}` : ""}
    </span>
  );
}

const healthCls: Record<HealthStatus, string> = {
  HEALTHY: "bg-success-50 text-success",
  DEGRADED: "bg-warning-50 text-warning",
  STALE: "bg-warning-50 text-warning",
  UNAVAILABLE: "bg-danger-50 text-danger",
};

export function HealthChip({ status }: { status: HealthStatus | null | undefined }) {
  if (!status) return <span className="chip bg-gray-100 text-ink-3">—</span>;
  return <span className={clsx("chip", healthCls[status])}>{HEALTH_LABELS[status]}</span>;
}

export function HealthDot({ status, className }: { status: HealthStatus | null | undefined; className?: string }) {
  const c = status === "HEALTHY" ? "bg-success" : status === "DEGRADED" || status === "STALE" ? "bg-warning" : status === "UNAVAILABLE" ? "bg-danger" : "bg-gray-300";
  return <span className={clsx("inline-block h-2 w-2 rounded-full", c, className)} />;
}

/** SAFE ≠ VALUE: alta probabilidade não implica valor e vice-versa. */
export function LabelChip({ label }: { label: OpportunityLabel | null | undefined }) {
  if (!label) return null;
  const cls = label === "VALUE" ? "bg-primary-50 text-primary" : label === "HIGH_PROBABILITY" ? "bg-info-50 text-info" : "bg-success-50 text-success";
  const tip =
    label === "VALUE"
      ? "VALUE: edge/EV acima do limiar. Não significa alta probabilidade de acerto."
      : label === "HIGH_PROBABILITY"
        ? "HIGH PROBABILITY: probabilidade do modelo alta. Não significa que a odd tenha valor."
        : "Probabilidade alta E edge positivo ao mesmo tempo.";
  return (
    <span className={clsx("chip", cls)} title={tip}>
      {LABEL_TEXT[label]}
    </span>
  );
}

const evidenceCls: Record<EvidenceLevel, string> = {
  SETTLED: "bg-success-50 text-success",
  BACKTEST_ODDS: "bg-info-50 text-info",
  MODEL_ONLY: "bg-warning-50 text-warning",
};

export function EvidenceChip({ level, compact }: { level: EvidenceLevel | null | undefined; compact?: boolean }) {
  if (!level) return null;
  const short = level === "SETTLED" ? "EVIDÊNCIA: SETTLED" : level === "BACKTEST_ODDS" ? "EVIDÊNCIA: BACKTEST" : "MODEL ONLY";
  return (
    <span className={clsx("chip", evidenceCls[level])} title={EVIDENCE_LABELS[level]}>
      {compact ? short : EVIDENCE_LABELS[level]}
    </span>
  );
}

export function GateChip({ passed, failed }: { passed: boolean; failed?: string[] }) {
  return (
    <span
      className={clsx("chip inline-flex items-center gap-1", passed ? "bg-success-50 text-success" : "bg-gray-100 text-ink-2")}
      title={passed ? "Passou em todos os checks do Quality Gate" : `Quality Gate: falhou em ${failed?.length ? failed.join(", ") : "algum check"}`}
    >
      {passed ? <ShieldCheck size={11} /> : <ShieldOff size={11} />}
      {passed ? "GATE OK" : "GATE"}
    </span>
  );
}

/** Contador do cabeçalho do Radar / Dashboard. */
export function Counter({ label, value, hint, accent, onClick }: { label: string; value: ReactNode; hint?: string; accent?: string; onClick?: () => void }) {
  return (
    <div className={clsx("card flex flex-col gap-0.5 px-3 py-2.5", onClick && "card-hover")} onClick={onClick} title={hint}>
      <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-3">{label}</span>
      <span className={clsx("text-xl font-bold tabular-nums leading-tight", accent)}>{value}</span>
      {hint && <span className="truncate text-[11px] text-ink-2">{hint}</span>}
    </div>
  );
}

export function Modal({ title, onClose, children, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/30 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className={clsx("card max-h-[90vh] w-full overflow-y-auto p-5", wide ? "max-w-3xl" : "max-w-xl")} onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-lg font-bold">{title}</h2>
          <button className="btn-ghost" onClick={onClose} aria-label="Fechar">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Card({ className, children, hover, onClick }: { className?: string; children: ReactNode; hover?: boolean; onClick?: () => void }) {
  return (
    <div className={clsx("card p-4", hover && "card-hover", className)} onClick={onClick}>
      {children}
    </div>
  );
}

export function SectionTitle({ title, subtitle, right }: { title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <div className="mb-3 flex items-end justify-between gap-3">
      <div>
        <h2 className="text-[13px] font-bold uppercase tracking-wider text-ink">{title}</h2>
        {subtitle && <p className="text-xs text-ink-2">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}

export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-ink-2">{subtitle}</p>}
      </div>
      {right && <div className="flex items-center gap-2">{right}</div>}
    </div>
  );
}

const gradeColors: Record<Grade, string> = {
  A: "bg-success-50 text-success border border-success/30",
  B: "bg-info-50 text-info border border-info/30",
  C: "bg-warning-50 text-warning border border-warning/30",
  D: "bg-gray-100 text-ink-2 border border-line",
};

export function GradeBadge({ grade, score, size = "md" }: { grade: Grade | null | undefined; score?: number; size?: "sm" | "md" | "lg" }) {
  if (!grade) return <span className="chip bg-gray-100 text-ink-3">—</span>;
  return (
    <span
      className={clsx("inline-flex items-center gap-1 rounded-md font-bold", gradeColors[grade], size === "lg" ? "px-2.5 py-1 text-base" : size === "sm" ? "px-1.5 py-0.5 text-[11px]" : "px-2 py-0.5 text-xs")}
      title={`Confiança ${grade}${score !== undefined ? ` · ${score.toFixed(0)}/100` : ""}`}
    >
      {grade}
      {score !== undefined && <span className="font-medium opacity-80">{score.toFixed(0)}</span>}
    </span>
  );
}

export function StatusChip({ status }: { status: RecommendationStatus }) {
  const map = {
    RECOMMENDED: "bg-success-50 text-success",
    WATCH: "bg-warning-50 text-warning",
    NO_BET: "bg-gray-100 text-ink-2",
  } as const;
  const label = { RECOMMENDED: "Recomendada", WATCH: "Em observação", NO_BET: "Não entrar" } as const;
  return <span className={clsx("chip", map[status])}>{label[status]}</span>;
}

export function VenueChip({ status, compact }: { status: VenueStatus; compact?: boolean }) {
  const cls =
    status === "CONFIRMED_HOME" ? "bg-success-50 text-success" : status === "NEUTRAL" ? "bg-info-50 text-info" : "bg-warning-50 text-warning";
  return (
    <span className={clsx("chip", cls)} title="Detecção de mando de campo">
      {compact && status === "UNCONFIRMED" ? "MANDO ?" : venueLabel(status)}
    </span>
  );
}

export function NoBetChip({ reason }: { reason: NoBetReason | null | undefined }) {
  if (!reason) return null;
  return (
    <span className="chip bg-gray-100 text-ink-2" title={reason}>
      NO BET · {NO_BET_LABELS[reason]}
    </span>
  );
}

export function DemoChip() {
  return <span className="chip bg-warning-50 text-warning">DEMO</span>;
}

export function EdgeValue({ value, digits = 1 }: { value: number | null | undefined; digits?: number }) {
  if (value === null || value === undefined) return <span className="text-ink-3">—</span>;
  const cls = value >= 3 ? "text-success" : value > 0 ? "text-ink" : "text-danger";
  return (
    <span className={clsx("font-semibold tabular-nums", cls)}>
      {value > 0 ? "+" : ""}
      {value.toFixed(digits)} pp
    </span>
  );
}

export function ProbBar({ home, draw, away, labels }: { home: number; draw: number; away: number; labels?: [string, string, string] }) {
  const h = Math.round(home * 100);
  const d = Math.round(draw * 100);
  const a = Math.max(0, 100 - h - d);
  void away;
  return (
    <div>
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-gray-100">
        <div className="bg-primary transition-all" style={{ width: `${h}%` }} />
        <div className="bg-ink-3 transition-all" style={{ width: `${d}%` }} />
        <div className="bg-info transition-all" style={{ width: `${a}%` }} />
      </div>
      <div className="mt-1 flex justify-between text-xs tabular-nums">
        <span className="font-semibold text-primary">{labels?.[0] ?? "1"} {h}%</span>
        <span className="text-ink-2">{labels?.[1] ?? "X"} {d}%</span>
        <span className="font-semibold text-info">{labels?.[2] ?? "2"} {a}%</span>
      </div>
    </div>
  );
}

export function MeterBar({ value, max = 100, color = "bg-primary", className }: { value: number; max?: number; color?: string; className?: string }) {
  const w = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className={clsx("h-2 w-full overflow-hidden rounded-full bg-gray-100", className)}>
      <div className={clsx("h-full rounded-full transition-all duration-300", color)} style={{ width: `${w}%` }} />
    </div>
  );
}

export function Score({ value, label }: { value: number | null | undefined; label: string }) {
  const v = value ?? 0;
  const color = v >= 80 ? "text-success" : v >= 65 ? "text-info" : v >= 50 ? "text-warning" : "text-ink-2";
  return (
    <div className="text-center">
      <div className={clsx("text-2xl font-bold tabular-nums", color)}>{value === null || value === undefined ? "—" : Math.round(v)}</div>
      <div className="label">{label}</div>
    </div>
  );
}

export function Stat({ label, value, hint, accent }: { label: string; value: ReactNode; hint?: string; accent?: string }) {
  return (
    <Card className="flex flex-col gap-1">
      <span className="label">{label}</span>
      <span className={clsx("text-2xl font-bold tabular-nums", accent)}>{value}</span>
      {hint && <span className="text-xs text-ink-2">{hint}</span>}
    </Card>
  );
}

export function Empty({ title, detail, icon }: { title: string; detail?: string; icon?: ReactNode }) {
  return (
    <div className="card flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <div className="text-ink-3">{icon ?? <Info size={28} />}</div>
      <div className="font-semibold">{title}</div>
      {detail && <div className="max-w-md text-sm text-ink-2">{detail}</div>}
    </div>
  );
}

export function Loading({ label = "Carregando…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-10 text-sm text-ink-2">
      <Loader2 className="animate-spin" size={16} /> {label}
    </div>
  );
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="card flex items-start gap-3 border-danger/30 bg-danger-50 p-4 text-sm">
      <AlertTriangle className="mt-0.5 text-danger" size={16} />
      <div className="flex-1">
        <div className="font-semibold text-danger">Não foi possível carregar</div>
        <div className="text-ink-2">{msg}</div>
      </div>
      {retry && (
        <button className="btn-outline" onClick={retry}>
          Tentar novamente
        </button>
      )}
    </div>
  );
}

export function Tooltip({ text, children }: { text: string; children: ReactNode }) {
  return (
    <span className="group relative inline-flex">
      {children}
      <span className="pointer-events-none absolute left-1/2 top-full z-30 mt-1.5 hidden w-64 -translate-x-1/2 rounded-lg bg-ink px-3 py-2 text-xs leading-relaxed text-white shadow-hover group-hover:block">
        {text}
      </span>
    </span>
  );
}

export function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line/70 py-1.5 text-sm last:border-0">
      <span className="text-ink-2">{k}</span>
      <span className="font-medium tabular-nums">{v}</span>
    </div>
  );
}

export function Pct({ v, d = 0 }: { v: number | null | undefined; d?: number }) {
  return <span className="tabular-nums">{pct(v, d)}</span>;
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-card p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={clsx(
            "rounded-md px-3 py-1 text-xs font-semibold transition duration-[180ms]",
            value === o.value ? "bg-ink text-white shadow-sm" : "text-ink-2 hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
