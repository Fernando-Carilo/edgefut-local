import type {
  AlertsResponse,
  BacktestRequest,
  BacktestResponse,
  BootstrapStatus,
  CalibrationResponse,
  ChatResponse,
  ConflictsResponse,
  CoverageResponse,
  DashboardResponse,
  DecayLatestResponse,
  DriftReport,
  GovernanceResponse,
  ReplayLatestResponse,
  ShadowReport,
  EntriesResponse,
  EventDetailResponse,
  EventListResponse,
  EventWindow,
  HealthResponse,
  HistoryResponse,
  JobsResponse,
  LiveResponse,
  MatchAnalysis,
  ModelsResponse,
  MultipleLeg,
  MultipleResponse,
  OddsHistoryResponse,
  PerformanceResponse,
  RadarResponse,
  SearchResponse,
  SettingsModel,
  SimulatorRequest,
  SimulatorResponse,
  SourcesResponse,
  StakeRequest,
  StakeResponse,
  SystemHealthResponse,
  VenueOverride,
} from "@edgefut/contracts";

/** O engine escuta somente em loopback; a porta pode ser sobrescrita via VITE_ENGINE_PORT. */
const PORT = import.meta.env.VITE_ENGINE_PORT ?? "8765";
export const API_BASE = `http://127.0.0.1:${PORT}`;

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    let code: string | undefined;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
      code = body.code;
    } catch {
      /* corpo não-JSON */
    }
    throw new ApiError(res.status, detail, code);
  }
  return (await res.json()) as T;
}

const qs = (params: Record<string, string | number | boolean | null | undefined>) => {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  });
  const s = q.toString();
  return s ? `?${s}` : "";
};

export const api = {
  health: () => request<HealthResponse>("/health"),
  bootstrap: () => request<BootstrapStatus>("/bootstrap"),
  runBootstrap: (minimal = false) => request<BootstrapStatus>(`/bootstrap${qs({ minimal })}`, { method: "POST" }),

  events: (p: { window?: EventWindow; competition?: string; search?: string; supported_only?: boolean }) =>
    request<EventListResponse>(`/events${qs(p)}`),
  event: (id: number) => request<EventDetailResponse>(`/events/${id}`),
  analysis: (id: number, p: { simulations?: number; refresh?: boolean } = {}) =>
    request<MatchAnalysis>(`/events/${id}/analysis${qs(p)}`),
  oddsHistory: (id: number) => request<OddsHistoryResponse>(`/events/${id}/odds/history`),
  conflicts: (id: number) => request<ConflictsResponse>(`/events/${id}/conflicts`),
  setVenue: (id: number, body: VenueOverride) =>
    request<{ ok: boolean; venue: unknown }>(`/events/${id}/venue`, { method: "PATCH", body: JSON.stringify(body) }),
  favorite: (id: number, on: boolean) =>
    request<{ ok: boolean; favorite: boolean }>(`/events/${id}/favorite`, { method: on ? "POST" : "DELETE" }),
  syncEvents: () => request<Record<string, unknown>>("/events/sync", { method: "POST" }),
  syncOdds: (id: number) => request<Record<string, unknown>>(`/events/${id}/sync-odds`, { method: "POST" }),

  radar: (hours = 48) => request<RadarResponse>(`/radar${qs({ hours })}`),
  entries: (p: {
    window?: EventWindow;
    competition?: string;
    market?: string;
    min_odd?: number;
    max_odd?: number;
    grades?: string;
    include_watch?: boolean;
  }) => request<EntriesResponse>(`/entries${qs(p)}`),
  dashboard: () => request<DashboardResponse>("/dashboard"),

  simulator: (body: SimulatorRequest) => request<SimulatorResponse>("/simulator", { method: "POST", body: JSON.stringify(body) }),
  stake: (body: StakeRequest) => request<StakeResponse>("/bankroll/stake", { method: "POST", body: JSON.stringify(body) }),
  multiples: (legs: MultipleLeg[]) =>
    request<MultipleResponse>("/multiples/evaluate", { method: "POST", body: JSON.stringify({ legs }) }),
  chat: (event_id: number, question: string) =>
    request<ChatResponse>("/chat", { method: "POST", body: JSON.stringify({ event_id, question }) }),
  search: (q: string) => request<SearchResponse>(`/search${qs({ q })}`),
  backtest: (body: BacktestRequest) => request<BacktestResponse>("/backtest", { method: "POST", body: JSON.stringify(body) }),
  performance: () => request<PerformanceResponse>("/performance"),
  settle: () => request<Record<string, unknown>>("/performance/settle", { method: "POST" }),
  history: (limit = 100) => request<HistoryResponse>(`/history${qs({ limit })}`),
  historyDetail: (id: number) => request<Record<string, unknown>>(`/history/${id}`),

  sources: () => request<SourcesResponse>("/sources"),
  refreshSource: (
    what: "events" | "odds" | "history" | "settle" | "radar" | "closing_lines" | "performance" | "calibration" | "alerts" | "live_poll",
    force = false,
  ) => request<Record<string, unknown>>(`/sources/refresh${qs({ what, force })}`, { method: "POST" }),
  systemHealth: () => request<SystemHealthResponse>("/health/system"),
  jobs: (p: { job?: string; limit?: number } = {}) => request<JobsResponse>(`/jobs${qs(p)}`),
  runJob: (job: string) => request<Record<string, unknown>>(`/jobs/${job}/run`, { method: "POST" }),
  alerts: (p: { limit?: number; unread_only?: boolean } = {}) => request<AlertsResponse>(`/alerts${qs(p)}`),
  markAlertsRead: (ids?: number[]) =>
    request<{ ok: boolean; marked: number }>("/alerts/read", { method: "POST", body: JSON.stringify({ ids: ids ?? null }) }),
  calibration: (market_key?: string) => request<CalibrationResponse>(`/calibration${qs({ market_key })}`),
  models: () => request<ModelsResponse>("/models"),
  settings: () => request<SettingsModel>("/settings"),
  saveSettings: (body: SettingsModel) => request<SettingsModel>("/settings", { method: "PUT", body: JSON.stringify(body) }),
  live: (poll = false) => request<LiveResponse>(`/live${qs({ poll })}`),

  // Iteração 3 — validação preditiva (somente leitura, exceto replay/decay que disparam jobs em background)
  replayLatest: (international = false) => request<ReplayLatestResponse>(`/validation/replay/latest${qs({ international })}`),
  startReplay: (body: { international?: boolean; start?: string; window_days?: number; scheme?: "expanding" | "rolling" }) =>
    request<{ ok: boolean; started?: boolean; skipped?: boolean; correlation_id?: string }>("/validation/replay", { method: "POST", body: JSON.stringify(body) }),
  decayLatest: () => request<DecayLatestResponse>("/validation/decay/latest"),
  shadow: (live = true) => request<ShadowReport>(`/validation/shadow${qs({ live })}`),
  drift: (live = false) => request<DriftReport>(`/validation/drift${qs({ live })}`),
  coverage: () => request<CoverageResponse>("/validation/coverage"),
  governance: () => request<GovernanceResponse>("/validation/governance"),
};
