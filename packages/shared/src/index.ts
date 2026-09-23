/** Formatadores e utilidades compartilhadas (pt-BR). */

const ptBR = "pt-BR";

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function pp(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)} pp`;
}

export function signedPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString(ptBR, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function odd(value: number | null | undefined): string {
  return num(value, 2);
}

export function money(value: number | null | undefined, currency = "BRL"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString(ptBR, { style: "currency", currency });
}

export function int(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(ptBR);
}

/** Datas do engine são UTC sem sufixo Z. */
export function parseUtc(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const hasZone = /Z$|[+-]\d{2}:\d{2}$/.test(iso);
  return new Date(hasZone ? iso : `${iso}Z`);
}

export function fmtDateTime(iso: string | null | undefined): string {
  const d = parseUtc(iso);
  if (!d) return "—";
  return d.toLocaleString(ptBR, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function fmtTime(iso: string | null | undefined): string {
  const d = parseUtc(iso);
  if (!d) return "—";
  return d.toLocaleTimeString(ptBR, { hour: "2-digit", minute: "2-digit" });
}

export function fmtDate(iso: string | null | undefined): string {
  const d = parseUtc(iso);
  if (!d) return "—";
  return d.toLocaleDateString(ptBR, { weekday: "short", day: "2-digit", month: "short" });
}

export function relativeTime(iso: string | null | undefined, now = new Date()): string {
  const d = parseUtc(iso);
  if (!d) return "—";
  const diff = Math.round((d.getTime() - now.getTime()) / 60000);
  const abs = Math.abs(diff);
  const label = abs < 60 ? `${abs} min` : abs < 60 * 48 ? `${Math.round(abs / 60)} h` : `${Math.round(abs / 1440)} d`;
  return diff >= 0 ? `em ${label}` : `há ${label}`;
}

export function dayLabel(iso: string): "Hoje" | "Amanhã" | string {
  const d = parseUtc(iso);
  if (!d) return "";
  const today = new Date();
  const dd = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const tt = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const delta = Math.round((dd - tt) / 86400000);
  if (delta === 0) return "Hoje";
  if (delta === 1) return "Amanhã";
  return fmtDate(iso);
}

export function venueLabel(status: "CONFIRMED_HOME" | "NEUTRAL" | "UNCONFIRMED"): string {
  return status === "CONFIRMED_HOME" ? "MANDANTE" : status === "NEUTRAL" ? "CAMPO NEUTRO" : "MANDANTE NÃO CONFIRMADO";
}

export function impliedProbability(o: number): number {
  return o > 0 ? 1 / o : 0;
}

export function expectedValuePct(prob: number, o: number): number {
  return (prob * o - 1) * 100;
}

export function kellyFraction(prob: number, o: number): number {
  const b = o - 1;
  if (b <= 0) return 0;
  return Math.max(0, (prob * b - (1 - prob)) / b);
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}
