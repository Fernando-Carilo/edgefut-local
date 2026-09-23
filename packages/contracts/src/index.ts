/**
 * Contratos TypeScript espelhando os schemas Pydantic do engine
 * (engine/edgefut/domain/analysis.py e engine/edgefut/api/schemas.py).
 * Datas chegam como ISO string.
 */

export type VenueStatus = "CONFIRMED_HOME" | "NEUTRAL" | "UNCONFIRMED";
export type Grade = "A" | "B" | "C" | "D";
export type RecommendationStatus = "RECOMMENDED" | "WATCH" | "NO_BET";
export type NoBetReason =
  | "LOW_DATA"
  | "LOW_CONFIDENCE"
  | "NO_EDGE"
  | "MODEL_DISAGREEMENT"
  | "UNRELIABLE_SOURCE"
  | "SMALL_SAMPLE"
  | "LINEUP_UNCERTAINTY"
  | "EXTREME_ODDS_MOVEMENT"
  | "UNSUPPORTED_COMPETITION";
export type Category = "GOLS" | "RESULTADO" | "ESCANTEIOS" | "CARTOES" | "FINALIZACOES" | "JOGADOR" | "OUTRO";

export interface Provenance {
  source: string;
  source_url: string | null;
  collected_at: string | null;
  confidence: number;
  sample_size: number;
  field: string | null;
  note: string | null;
}

export interface SourceAttempt {
  provider: string;
  status: string;
  detail: string | null;
  url: string | null;
}

export interface WindowStats {
  window: string;
  n: number;
  wins: number;
  draws: number;
  losses: number;
  points_per_game: number | null;
  goals_for: number | null;
  goals_against: number | null;
  shots_for: number | null;
  shots_against: number | null;
  sot_for: number | null;
  sot_against: number | null;
  conversion: number | null;
  corners_for: number | null;
  corners_against: number | null;
  cards: number | null;
  cards_against: number | null;
  clean_sheet_pct: number | null;
  btts_pct: number | null;
  over15_pct: number | null;
  over25_pct: number | null;
  over35_pct: number | null;
}

export interface RecentMatch {
  date: string;
  home: string;
  away: string;
  hg: number;
  ag: number;
  competition: string | null;
  result_for_team: "V" | "E" | "D" | null;
  neutral: boolean | null;
}

export interface TeamProfile {
  name: string;
  canonical: string | null;
  dataset_code: string | null;
  match_method: string;
  match_confidence: number;
  is_national: boolean;
  elo: number | null;
  strength_score: number | null;
  attack: number | null;
  defense: number | null;
  form: string[];
  recent: RecentMatch[];
  windows: Record<string, WindowStats>;
  sample_size: number;
  provenance: Provenance | null;
}

export interface H2HSummary {
  matches: number;
  home_wins: number;
  draws: number;
  away_wins: number;
  home_goals: number;
  away_goals: number;
  avg_goals: number | null;
  btts_pct: number | null;
  over25_pct: number | null;
  avg_corners: number | null;
  avg_cards: number | null;
  recent: RecentMatch[];
  provenance: Provenance | null;
  weight_note: string;
}

export interface VenueInfo {
  status: VenueStatus;
  neutral: boolean | null;
  name: string | null;
  city: string | null;
  country: string | null;
  source: string | null;
  confidence: number;
  note: string | null;
  home_advantage_weight: number;
}

export interface GoalsModelOutput {
  model_version: string;
  available: boolean;
  lambda_home: number | null;
  lambda_away: number | null;
  rho: number | null;
  p_home: number | null;
  p_draw: number | null;
  p_away: number | null;
  dist_home: number[];
  dist_away: number[];
  over: Record<string, number>;
  under: Record<string, number>;
  btts: number | null;
  top_scores: [string, number][];
  fit_matches: number;
  note: string | null;
}

export interface EloOutput {
  model_version: string;
  available: boolean;
  home_elo: number | null;
  away_elo: number | null;
  p_home: number | null;
  p_draw: number | null;
  p_away: number | null;
  home_advantage_points: number;
  matches_used: number;
  note: string | null;
}

export interface CountDistribution {
  model_version: string;
  available: boolean;
  expected_home: number | null;
  expected_away: number | null;
  expected_total: number | null;
  likely_range: [number, number] | null;
  over: Record<string, number>;
  under: Record<string, number>;
  p_home_more: number | null;
  p_away_more: number | null;
  extra: Record<string, number>;
  sample_size: number;
  note: string | null;
  provenance: Provenance | null;
}

export interface SimulationOutput {
  model_version: string;
  simulations: number;
  seed: number;
  base_model: string;
  p_home: number;
  p_draw: number;
  p_away: number;
  p_home_draw: number;
  p_draw_away: number;
  p_home_away: number;
  p_dnb_home: number;
  p_dnb_away: number;
  over: Record<string, number>;
  under: Record<string, number>;
  btts: number;
  expected_goals_home: number;
  expected_goals_away: number;
  total_goals_dist: number[];
  top_scores: [string, number][];
  most_likely_score: string;
  team_totals_home_over: Record<string, number>;
  team_totals_away_over: Record<string, number>;
  first_goal: Record<string, number>;
  handicap_home: Record<string, number>;
}

export interface SelectionOdds {
  key: string;
  name: string;
  price: number;
  implied: number;
  fair: number | null;
  opening_price: number | null;
  movement_pct: number | null;
  direction: "up" | "down" | "flat" | null;
  model_prob: number | null;
  edge_pp: number | null;
  ev_pct: number | null;
}

export interface MarketOdds {
  market_key: string;
  label: string;
  line: number | null;
  selections: SelectionOdds[];
  overround: number | null;
  margin_removed: boolean;
  collected_at: string | null;
  source: string;
  source_url: string | null;
}

export interface ConfidenceComponent {
  name: string;
  weight: number;
  value: number;
  note: string | null;
}

export interface ConfidenceBreakdown {
  model_version: string;
  score: number;
  grade: Grade;
  components: ConfidenceComponent[];
}

export interface DataQualityCheck {
  ok: boolean;
  label: string;
  weight: number;
}

export interface DataQuality {
  score: number;
  checks: DataQualityCheck[];
}

export interface Recommendation {
  market_key: string;
  market_label: string;
  selection_key: string;
  selection_name: string;
  line: number | null;
  odd: number;
  model_prob: number;
  market_prob: number;
  market_prob_is_fair: boolean;
  edge_pp: number;
  ev_pct: number;
  confidence_score: number;
  confidence_grade: Grade;
  opportunity_score: number;
  status: RecommendationStatus;
  reasons: string[];
  explanation: string | null;
  category: Category;
}

export interface NoBetVerdict {
  no_bet: boolean;
  reason: NoBetReason | null;
  detail: string | null;
}

export interface EventSummary {
  id: number;
  home_name: string;
  away_name: string;
  kickoff_utc: string;
  competition_name: string | null;
  category_name: string | null;
  status: string;
  market_count: number;
  event_url: string | null;
  venue_status: VenueStatus;
  neutral_venue: boolean | null;
  odds_collected_at: string | null;
  main_odds: Record<string, number> | null;
  is_favorite: boolean;
  opportunity_score: number | null;
  confidence_grade: Grade | null;
  data_quality: number | null;
  best_market: string | null;
  no_bet_reason: NoBetReason | null;
  demo: boolean;
}

export interface MatchAnalysis {
  generated_at: string;
  pipeline_version: string;
  model_versions: Record<string, string>;
  event: EventSummary;
  venue: VenueInfo;
  home: TeamProfile;
  away: TeamProfile;
  h2h: H2HSummary | null;
  elo: EloOutput;
  poisson: GoalsModelOutput;
  dixon_coles: GoalsModelOutput;
  simulation: SimulationOutput | null;
  corners: CountDistribution;
  cards: CountDistribution;
  shots: CountDistribution;
  markets: MarketOdds[];
  recommendations: Recommendation[];
  no_bet: NoBetVerdict;
  data_quality: DataQuality;
  confidence: ConfidenceBreakdown;
  opportunity_score: number;
  model_disagreement_pp: number | null;
  explanation: string;
  sources: Provenance[];
  source_attempts: SourceAttempt[];
  snapshot_id: number | null;
  warnings: string[];
}

// ---- API ---------------------------------------------------------------

export interface HealthResponse {
  status: string;
  version: string;
  host: string;
  port: number;
  db_path: string;
  datasets: number;
  scheduler: SchedulerState;
  superbet_enabled: boolean;
  ollama_available: boolean;
}

export interface SchedulerState {
  last_events_sync: string | null;
  last_odds_sync: string | null;
  last_history_sync: string | null;
  last_settlement: string | null;
  last_radar_refresh: string | null;
  radar_running: boolean;
  errors: string[];
}

export type EventWindow = "today" | "tomorrow" | "48h" | "week";

export interface EventListResponse {
  events: EventSummary[];
  total: number;
  window: string;
  generated_at: string;
}

export interface EventDetailResponse {
  event: EventSummary;
  markets: MarketOdds[];
}

export interface VenueOverride {
  neutral: boolean;
  name?: string | null;
  city?: string | null;
  country?: string | null;
}

export interface OddsPoint {
  collected_at: string;
  price: number;
}

export interface OddsHistoryResponse {
  event_id: number;
  series: Record<string, OddsPoint[]>;
}

export interface RadarItem {
  event: EventSummary;
  recommendation: Recommendation | null;
  opportunity_score: number;
  confidence_grade: Grade;
  data_quality: number;
  no_bet_reason: NoBetReason | null;
}

export interface RadarCard {
  key: string;
  title: string;
  subtitle: string;
  items: RadarItem[];
}

export interface RadarResponse {
  generated_at: string;
  analyzed_events: number;
  refreshing: boolean;
  cards: RadarCard[];
}

export interface EntryRow {
  event: EventSummary;
  recommendation: Recommendation;
}

export interface EntriesResponse {
  rows: EntryRow[];
  total: number;
}

export interface DashboardResponse {
  greeting: string;
  user_name: string;
  analyzed_today: number;
  confidence_a: number;
  confidence_b: number;
  discarded_markets: number;
  last_update: string | null;
  top_opportunities: EntryRow[];
  popular_events: EventSummary[];
  scheduler: SchedulerState;
}

export interface SimulatorRequest {
  stake: number;
  odd: number;
  model_prob?: number | null;
}

export interface SimulatorResponse {
  stake: number;
  odd: number;
  gross_return: number;
  profit: number;
  implied_probability: number;
  model_probability: number | null;
  ev_pct: number | null;
  edge_pp: number | null;
  note: string;
}

export interface StakeRequest {
  bankroll: number;
  odd: number;
  model_prob: number;
  method: "fixed" | "percent" | "kelly";
  fixed_value?: number | null;
  percent?: number | null;
  kelly_fraction?: number;
  max_stake_pct?: number;
}

export interface StakeResponse {
  method: string;
  stake: number;
  stake_pct: number;
  kelly_full_pct: number;
  kelly_fraction_used: number;
  warning: string | null;
}

export interface MultipleLeg {
  event_id: number;
  market_key: string;
  selection_key: string;
  line?: number | null;
  odd: number;
  label?: string | null;
}

export interface MultipleResponse {
  legs: number;
  combined_odd: number;
  naive_probability: number;
  joint_probability: number | null;
  implied_probability: number;
  ev_pct: number | null;
  correlations: string[];
  warnings: string[];
  per_event: { event_id: number; legs: string[]; joint_probability: number; naive_probability: number; correlated: boolean }[];
}

export interface ChatResponse {
  answer: string;
  engine: "templates" | "ollama";
  event_id: number;
  grounded: boolean;
}

export interface SearchResponse {
  query: string;
  events: EventSummary[];
  teams: { name: string; next_event_id?: number | null; next_kickoff_utc?: string | null; competition?: string; dataset?: boolean }[];
  competitions: { id?: number; name: string; events?: number }[];
}

export interface SettingsModel {
  user_name: string;
  default_simulations: number;
  min_edge_pp: number;
  min_ev_pct: number;
  min_odd: number;
  max_odd: number;
  bankroll: number;
  kelly_fraction_max: number;
  ollama_enabled: boolean;
  ollama_model: string;
  events_refresh_min: number;
  odds_refresh_min: number;
}

export interface BootstrapStep {
  name: string;
  status: "running" | "ok" | "error";
  detail: string | null;
  at: string;
}

export interface BootstrapStatus {
  running: boolean;
  done: boolean;
  steps: BootstrapStep[];
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface ProviderInfo {
  key: string;
  name: string;
  kind: string;
  enabled: boolean;
  url: string;
  dynamic: boolean;
  notes: string;
  stats: Record<string, unknown> | null;
}

export interface DatasetInfo {
  code: string;
  label: string;
  competitions?: number;
  rows: number;
  first_date?: string | null;
  last_date?: string | null;
  collected_at?: string | null;
  source?: string;
  source_url?: string;
  coverage?: { corners_pct: number; shots_pct: number; cards_pct: number };
}

export interface SourceLogRow {
  provider: string;
  url: string;
  status: string;
  http_status: number | null;
  latency_ms: number | null;
  error: string | null;
  collected_at: string;
}

export interface CompetitionInfo {
  id: number;
  name: string;
  category: string | null;
  dataset_code: string | null;
  is_national_teams: boolean;
  is_womens: boolean;
  supported: boolean;
}

export interface SourcesResponse {
  generated_at: string;
  providers: ProviderInfo[];
  datasets: DatasetInfo[];
  dataset_states: { code: string; provider: string; source_url: string; rows: number; last_success_at: string | null; last_error: string | null }[];
  hosts: Record<string, { consecutive_failures: number; circuit_open: boolean; opened_at: number | null }>;
  competitions: CompetitionInfo[];
  log: SourceLogRow[];
  scheduler: SchedulerState;
  paths: { root: string; processed: string; cache: string };
}

export interface ModelsResponse {
  versions: Record<string, string>;
  app_version: string;
  models: { key: string; version: string; description: string }[];
  parameters: Record<string, number>;
  fitted: { elo: string[]; dixon_coles: string[] };
}

export interface BacktestRequest {
  dataset_code: string;
  market: "1X2" | "TOTAL_GOALS_2.5" | "ALL";
  model: string;
  min_odd: number;
  max_odd: number;
  min_edge_pp: number;
  min_team_games: number;
  since?: string | null;
  until?: string | null;
  refit_every_days: number;
  use_closing_odds: boolean;
  stake: number;
}

export interface BacktestMetrics {
  bets: number;
  wins: number;
  losses: number;
  hit_rate: number | null;
  brier: number | null;
  log_loss: number | null;
  roi: number | null;
  yield_pct: number | null;
  profit: number;
  staked: number;
  avg_edge_pp: number | null;
  max_drawdown: number | null;
  clv_pct: number | null;
  avg_odd: number | null;
}

export interface BacktestResponse {
  request: Record<string, unknown>;
  model_version: string;
  matches_evaluated: number;
  bets: Record<string, unknown>[];
  metrics: BacktestMetrics;
  by_selection: Record<string, BacktestMetrics>;
  calibration_bins: { bin: string; predicted: number; observed: number; n: number }[];
  note: string | null;
  equity_curve: number[];
}

export interface PerformanceResponse {
  settled_snapshots: number;
  recommended_bets: number;
  overall: BacktestMetrics;
  equity_curve: number[];
  by_market: Record<string, BacktestMetrics>;
  model_1x2_all_selections: BacktestMetrics;
  note: string;
}

export interface HistoryRow {
  id: number;
  event_id: number;
  created_at: string;
  kickoff_utc: string;
  home_name: string;
  away_name: string;
  competition_name: string | null;
  model_version: string;
  confidence_grade: Grade | null;
  data_quality: number | null;
  no_bet_reason: NoBetReason | null;
  recommended: number;
  best: Recommendation | null;
  result: Record<string, unknown> | null;
  settled_at: string | null;
}

export interface HistoryResponse {
  snapshots: HistoryRow[];
  total: number;
}

export interface LiveResponse {
  available: boolean;
  events: EventSummary[];
  reason: string;
}

export const NO_BET_LABELS: Record<NoBetReason, string> = {
  LOW_DATA: "Dados insuficientes",
  LOW_CONFIDENCE: "Confiança baixa",
  NO_EDGE: "Sem edge",
  MODEL_DISAGREEMENT: "Modelos divergem",
  UNRELIABLE_SOURCE: "Fonte não confiável",
  SMALL_SAMPLE: "Amostra pequena",
  LINEUP_UNCERTAINTY: "Escalação incerta",
  EXTREME_ODDS_MOVEMENT: "Movimento extremo de odds",
  UNSUPPORTED_COMPETITION: "Competição não suportada",
};
