import type { Interval, SampleQuality, Significance } from "@edgefut/contracts";
import { int } from "@edgefut/shared";
import clsx from "clsx";

const SIG_CLS: Record<Significance, string> = {
  "INSUFFICIENT DATA": "bg-gray-100 text-ink-2",
  "NO CLEAR ADVANTAGE": "bg-warning-50 text-warning",
  PROMISING: "bg-info-50 text-info",
  CONSISTENT: "bg-success-50 text-success",
};
const SQ_CLS: Record<SampleQuality, string> = {
  INSUFFICIENT: "bg-danger-50 text-danger",
  EARLY: "bg-warning-50 text-warning",
  MODERATE: "bg-info-50 text-info",
  STRONG: "bg-success-50 text-success",
};

export function SigChip({ s }: { s: Significance | string | null | undefined }) {
  if (!s) return <span className="chip bg-gray-100 text-ink-2">—</span>;
  return <span className={clsx("chip", SIG_CLS[s as Significance] ?? "bg-gray-100 text-ink-2")}>{s}</span>;
}

export function SampleChip({ q, n }: { q: SampleQuality | null | undefined; n?: number }) {
  if (!q) return null;
  return (
    <span className={clsx("chip", SQ_CLS[q])} title="INSUFFICIENT < 100 · EARLY < 300 · MODERATE < 1000 · STRONG ≥ 1000 (configurável)">
      {q}
      {n !== undefined ? ` · N ${int(n)}` : ""}
    </span>
  );
}

export function IntervalText({ v, digits = 4, signed = false, suffix = "" }: { v: Interval | null | undefined; digits?: number; signed?: boolean; suffix?: string }) {
  if (!v || v.point === null || v.point === undefined) return <span className="text-ink-3">—</span>;
  const f = (x: number | null) => (x === null ? "—" : signed ? `${x >= 0 ? "+" : ""}${x.toFixed(digits)}` : x.toFixed(digits));
  return (
    <span className="tabular-nums">
      {f(v.point)}
      {suffix}
      <span className="text-ink-3"> [{f(v.low)}, {f(v.high)}]</span>
    </span>
  );
}
