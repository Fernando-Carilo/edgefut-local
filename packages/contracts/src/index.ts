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
  | "UNSUPPORTED_COMPETITION"
  | "STALE_DATA"
  | "QUALITY_GATE";
export type Category = "GOLS" | "RESULTADO" | "ESCANTEIOS" | "CARTOES" | "FINALIZACOES" | "JOGADOR" | "OUTRO";
export type FreshnessStatus = "FRESH" | "AGING" | "STALE" | "EXPIRED" | "UNAVAILABLE";
export type HealthStatus = "HEALTHY" | "DEGRADED" | "STALE" | "UNAVAILABLE";
export type MarginMethod = "MULTIPLICATIVE" | "SHIN";
export type OpportunityLabel = "HIGH_PROBABILITY" | "VALUE" | "HIGH_PROBABILITY_VALUE";
export type EvidenceLevel = "SETTLED" | "BACKTEST_ODDS" | "MODEL_ONLY";
export type ConfidenceGroup = "DATA_QUALITY" | "MODEL_AGREEMENT" | "CALIBRATION" | "HISTORICAL_SAMPLE" | "FRESHNESS" | "CONTEXT";

/** Frescor de um insumo: quando foi coletado, até quando vale e há quanto tempo existe. */
export interface Freshness {
  kind: string;
  label: string;
  collected_at: string | null;
  valid_until: string | null;
  age_seconds: number | null;
  status: FreshnessStatus;
  source: string | null;
  note: string | null;
}

/** Divergência entre fontes resolvida pelo Source Conflict Engine. */
export interface ConflictOut {
  id: number;
  field: string;
  field_label: string;
  source_a: string;
  value_a: unknown;
  source_b: string;
  value_b: unknown;
  selected_value: unknown;
  selected_source: string | null;
  resolution_method: string;
  resolution_label: string;
  confidence: number;
  created_at: string;
}

export interface LineMovement {
  opening_odd: number;
  current_odd: number;
  lowest_odd: number;
  highest_odd: number;
  absolute_move: number;
  percentage_move: number;
  implied_opening: number;
  implied_current: number;
  implied_probability_move_pp: number;
  direction: "up" | "down" | "flat";
  points: number;
  first_seen_at: string;
  last_seen_at: string;
  extreme: boolean;
}

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
  fair_method: MarginMethod | null;
  fair_multiplicative: number | null;
  fair_shin: number | null;
  opening_price: number | null;
  movement_pct: number | null;
  direction: "up" | "down" | "flat" | null;
  movement: LineMovement | null;
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
  margin_method: MarginMethod | null;
  shin_z: number | null;
  collected_at: string | null;
  source: string;
  source_url: string | null;
}

export interface ConfidenceComponent {
  name: string;
  weight: number;
  value: number;
  note: string | null;
  group: ConfidenceGroup;
}

/** EDGEFUT CONFIDENCE 0-100 com breakdown por grupo. */
export interface ConfidenceBreakdown {
  model_version: string;
  score: number;
  grade: Grade;
  components: ConfidenceComponent[];
  groups: Partial<Record<ConfidenceGroup, number>>;
}

export interface ScoreComponent {
  key: string;
  label: string;
  weight: number;
  value: number;
  note: string | null;
}

export interface OpportunityBreakdown {
  model_version: string;
  score: number;
  components: ScoreComponent[];
}

export interface QualityGateCheck {
  key: string;
  label: string;
  passed: boolean;
  detail: string | null;
}

export interface QualityGate {
  passed: boolean;
  checks: QualityGateCheck[];
  failed: string[];
}

export interface ModelRow {
  key: string;
  label: string;
  model_version: string;
  available: boolean;
  lambda_home: number | null;
  lambda_away: number | null;
  p_home: number | null;
  p_draw: number | null;
  p_away: number | null;
  over25: number | null;
  btts: number | null;
  weight: number | null;
  note: string | null;
}

export interface ModelComparison {
  model_version: string;
  rows: ModelRow[];
  consensus: ModelRow | null;
  max_disagreement_pp: number | null;
  disagreement_pairs: Record<string, number>;
  disagreement_scope: string[];
  excluded_low_weight: string[];
  weights_source: string;
  weights_group: string | null;
  weights_sample: number | null;
  note: string | null;
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
  model_prob_raw: number | null;
  model_prob_calibrated: number | null;
  calibration_group: string | null;
  calibration_reliable: boolean;
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
  label: OpportunityLabel | null;
  opportunity: OpportunityBreakdown | null;
  quality_gate: QualityGate | null;
  why: string[];
  why_not: string[];
  evidence: EvidenceLevel | null;
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
  bivariate_poisson: GoalsModelOutput | null;
  consensus: GoalsModelOutput | null;
  model_comparison: ModelComparison | null;
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
  freshness: Freshness[];
  freshness_status: FreshnessStatus | null;
  conflicts: ConflictOut[];
  conflicts_count: number;
  canonical_event_id: string | null;
  why_not: string[];
  quality_gate_passed: boolean;
  evidence: EvidenceLevel | null;
  cache_key: string | null;
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
  last_closing_lines?: string | null;
  last_performance_update?: string | null;
  last_calibration_update?: string | null;
  last_ensemble_weights?: string | null;
  last_live_poll?: string | null;
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
  movement: Record<string, LineMovement>;
  kickoff_utc: string | null;
  closing: Record<string, number>;
}

export interface ConflictsResponse {
  event_id: number;
  canonical_event_id: string | null;
  home_canonical: string | null;
  away_canonical: string | null;
  duplicate_of: number | null;
  count: number;
  conflicts: ConflictOut[];
}

export interface RadarItem {
  event: EventSummary;
  recommendation: Recommendation | null;
  opportunity_score: number;
  confidence_grade: Grade;
  data_quality: number;
  no_bet_reason: NoBetReason | null;
  label: OpportunityLabel | null;
  quality_gate_passed: boolean;
  freshness_status: FreshnessStatus | null;
  evidence: EvidenceLevel | null;
  why: string[];
}

export interface RadarSummary {
  last_update: string | null;
  events_found: number;
  with_sufficient_data: number;
  analyzed: number;
  quality_gate_passed: number;
  confidence_a: number;
  confidence_b: number;
  high_probability: number;
  value: number;
  watch: number;
  no_bet: number;
  stale: number;
  alerts_unread: number;
  no_bet_by_reason: Record<string, number>;
  gate_passed_by_evidence: Partial<Record<EvidenceLevel, number>>;
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
  summary: RadarSummary | null;
  thresholds: Record<string, number>;
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
  morning_summary: string;
  cta: string;
  events_found: number;
  quality_gate_passed: number;
  watch: number;
  no_bet: number;
  alerts_unread: number;
  health_overall: HealthStatus | null;
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
  margin_method: MarginMethod;
  live_poll_seconds: number;
  gate_min_data_quality: number;
  gate_min_confidence: number;
  gate_min_sample: number;
  gate_max_disagreement_pp: number;
  gate_max_edge_pp_uncalibrated: number;
  high_probability_min: number;
  opportunity_weights: Record<string, number>;
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
  has_odds?: boolean;
  evidence?: EvidenceLevel;
  coverage?: { corners_pct: number; shots_pct: number; cards_pct: number };
}

/** Fontes V2: um card por fonte. */
export interface SourceCard {
  key: string;
  name: string;
  kind: string;
  status: HealthStatus;
  summary: string;
  freshness: Freshness | null;
  last_ok: string | null;
  avg_latency_ms: number | null;
  error_rate: number | null;
  requests_24h: number | null;
  provides: string[];
  does_not_provide: string[];
  url: string | null;
  note: string | null;
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
  cards: SourceCard[];
  datasets: DatasetInfo[];
  dataset_states: { code: string; provider: string; source_url: string; rows: number; last_success_at: string | null; last_error: string | null }[];
  hosts: Record<string, { consecutive_failures: number; circuit_open: boolean; opened_at: number | null }>;
  competitions: CompetitionInfo[];
  log: SourceLogRow[];
  scheduler: SchedulerState;
  paths: { root: string; processed: string; cache: string };
}

export interface ModelRegistryRow {
  id: number;
  model_id: string;
  version: string;
  created_at: string;
  training_window: string | null;
  features: string[] | null;
  parameters: Record<string, unknown> | null;
  metrics: Record<string, unknown> | null;
  active: boolean;
  deprecated: boolean;
  notes: string | null;
}

export interface EnsembleWeightGroup {
  scores: Record<string, { logloss: number; n: number }>;
  weights: Record<string, number>;
  sample: number;
  computed_at: string;
}

export interface ModelsResponse {
  versions: Record<string, string>;
  app_version: string;
  models: { key: string; version: string; description: string }[];
  registry: ModelRegistryRow[];
  ensemble_weights: Record<string, EnsembleWeightGroup>;
  margin_method: MarginMethod;
  parameters: Record<string, number>;
  fitted: { elo: string[]; dixon_coles: string[]; bivariate_poisson: string[] };
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
  scheme: "expanding" | "rolling";
  train_window_days?: number | null;
  closing_for_clv: boolean;
  stake: number;
}

export interface BacktestWindow {
  train_start: string | null;
  train_end: string | null;
  test_start: string;
  test_end: string;
  n_train: number;
  n_test: number;
  evaluated: number;
  fitted: boolean;
  metrics: BacktestMetrics;
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
  sample_status?: "OK" | "INSUFFICIENT_SAMPLE";
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
  windows: BacktestWindow[];
  leakage_checks: number;
  decision_odds: string;
  equity_curve: number[];
}

export interface PerformanceResponse {
  generated_at: string;
  settled_snapshots: number;
  recommended_bets: number;
  bets_with_closing_line: number;
  overall: BacktestMetrics;
  equity_curve: number[];
  by_market: Record<string, BacktestMetrics>;
  by_competition: Record<string, BacktestMetrics>;
  model_1x2_all_selections: BacktestMetrics;
  min_sample: number;
  note: string;
}

export interface CalibrationBucket {
  bucket: string;
  lower: number;
  upper: number;
  predicted: number | null;
  actual: number | null;
  sample: number;
}

export interface CalibrationGroup {
  group_key: string;
  market_key: string;
  group: string;
  n: number;
  reliable: boolean;
  brier_raw: number | null;
  brier_calibrated: number | null;
  fitted_at: string | null;
  method: string;
  min_n: number;
}

export interface CalibrationResponse {
  generated_at: string;
  min_n: number;
  groups: CalibrationGroup[];
  diagram: { market_key: string | null; buckets: CalibrationBucket[]; sample: number; brier: number | null; reliable: boolean; min_n: number; model_version: string };
  note: string;
}

export interface ComponentHealth {
  key: string;
  name: string;
  status: HealthStatus;
  summary: string;
  last_update: string | null;
  freshness: Freshness | null;
  details: Record<string, unknown>;
  note: string | null;
}

export interface SystemHealthResponse {
  generated_at: string;
  overall: HealthStatus;
  components: ComponentHealth[];
}

export interface JobRun {
  id: number;
  job: string;
  label: string;
  correlation_id: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  status: "running" | "ok" | "error" | "skipped" | string;
  records_processed: number | null;
  errors: string[] | null;
  detail: Record<string, unknown> | null;
}

export interface JobsResponse {
  generated_at: string;
  scheduler_running: boolean;
  scheduled: { id: string; label: string; next_run_at: string | null; trigger: string }[];
  labels: Record<string, string>;
  last_by_job: Record<string, JobRun>;
  runs: JobRun[];
  state: SchedulerState;
}

export type AlertKind = "ODD_MOVEMENT" | "DATA_QUALITY_CHANGE" | "MODEL_CONFIDENCE_CHANGE" | "OPPORTUNITY_APPEARED" | "OPPORTUNITY_LOST";

export interface AlertRow {
  id: number;
  kind: AlertKind;
  kind_label: string;
  event_id: number | null;
  title: string;
  detail: Record<string, unknown> | null;
  severity: "info" | "warning";
  created_at: string;
  read_at: string | null;
}

export interface AlertsResponse {
  generated_at: string;
  kinds: Record<AlertKind, string>;
  unread: number;
  alerts: AlertRow[];
}

export interface LiveEvent {
  event_id: number;
  home_name: string;
  away_name: string;
  kickoff_utc: string;
  competition_name: string | null;
  category_name: string | null;
  status: string;
  period: string | null;
  minute: number | null;
  stoppage_time: string | null;
  home_score: number | null;
  away_score: number | null;
  stats: Record<string, number | null>;
  market_count: number;
  markets: MarketOdds[];
  odds_collected_at: string | null;
  tracked: boolean;
  event_url: string | null;
  movement: Record<string, { from: number; to: number; pct: number }>;
  full_markets: boolean;
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

/** Modo AO VIVO é observação: nunca traz recomendações. */
export interface LiveResponse {
  available: boolean;
  mode: "OBSERVATION_ONLY";
  updated_at: string | null;
  age_seconds: number | null;
  freshness: Freshness;
  poll_interval_s: number;
  next_poll_at: string | null;
  backoff_s: number;
  consecutive_errors: number;
  last_error: string | null;
  polls: number;
  events: LiveEvent[];
  notice: string;
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
  STALE_DATA: "Dados expirados",
  QUALITY_GATE: "Quality gate",
};

export const FRESHNESS_LABELS: Record<FreshnessStatus, string> = {
  FRESH: "Fresco",
  AGING: "Envelhecendo",
  STALE: "Desatualizado",
  EXPIRED: "Expirado",
  UNAVAILABLE: "Indisponível",
};

export const HEALTH_LABELS: Record<HealthStatus, string> = {
  HEALTHY: "Saudável",
  DEGRADED: "Degradado",
  STALE: "Desatualizado",
  UNAVAILABLE: "Indisponível",
};

export const LABEL_TEXT: Record<OpportunityLabel, string> = {
  HIGH_PROBABILITY: "HIGH PROBABILITY",
  VALUE: "VALUE",
  HIGH_PROBABILITY_VALUE: "HIGH PROBABILITY + VALUE",
};

export const EVIDENCE_LABELS: Record<EvidenceLevel, string> = {
  SETTLED: "Apostas liquidadas (ROI/CLV medidos)",
  BACKTEST_ODDS: "Backtest com odds reais possível",
  MODEL_ONLY: "Só modelo: nunca comparado ao mercado",
};

export const CONFIDENCE_GROUP_LABELS: Record<ConfidenceGroup, string> = {
  DATA_QUALITY: "Qualidade dos dados",
  MODEL_AGREEMENT: "Concordância entre modelos",
  CALIBRATION: "Calibração",
  HISTORICAL_SAMPLE: "Amostra histórica",
  FRESHNESS: "Frescor",
  CONTEXT: "Contexto",
};
