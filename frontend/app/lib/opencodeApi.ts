/**
 * @deprecated 本客户端已整合到 intelligentLearningApi.ts（Phase 5 统一收敛）。
 * 新代码请勿 import 本文件，OpenCode 相关端点改用 @/lib/intelligentLearningApi。
 * 保留本文件仅为兼容 OpenCodeCenter.tsx 等存量组件，后续随组件迁移逐步移除。
 *
 * OpenCode 智能中心 API 客户端
 */

const BASE =
  `${(import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') || '/api'}/opencode`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

export interface OpenCodeBridgeStatus {
  enabled: boolean;
  last_error: string | null;
  last_ok_ts: number;
  server_url: string;
  model?: string;
}

export interface PaperPaceState {
  gear: string;
  manual_lock: boolean;
  tick_seconds: number;
  max_strategies_per_tick: number;
  max_symbols_per_tick: number;
  learning_review_every_n: number;
  learning_miner_every_n?: number;
  hold_timeout_multiplier: number;
  master_close_mode: string;
}

/** Paper 模拟盘节奏档位（快 → 慢） */
export const PAPER_PACE_GEARS = ['blitz', 'turbo', 'warm', 'balanced', 'conservative'] as const;
export type PaperPaceGear = (typeof PAPER_PACE_GEARS)[number];

export const PAPER_PACE_GEAR_META: Record<
  PaperPaceGear,
  { label: string; short: string; desc: string }
> = {
  blitz: {
    label: '闪电',
    short: '30s',
    desc: '每 30 秒一轮，最快试单节奏，适合快速积累样本',
  },
  turbo: {
    label: '极速',
    short: '最快',
    desc: '每 45 秒一轮，策略/币种最多，适合快速刷量与实验',
  },
  warm: {
    label: '偏快',
    short: '较快',
    desc: '每 60 秒一轮，节奏略快于默认',
  },
  balanced: {
    label: '均衡',
    short: '默认',
    desc: '每 90 秒一轮，速度与风控的平衡（推荐）',
  },
  conservative: {
    label: '保守',
    short: '最慢',
    desc: '每 120 秒一轮，交易最慢、风控最严',
  },
};

export function paperPaceGearLabel(gear: string | undefined | null): string {
  const key = (gear || 'balanced') as PaperPaceGear;
  return PAPER_PACE_GEAR_META[key]?.label ?? gear ?? '均衡';
}

export function formatPaperPaceKnobs(pace: PaperPaceState): string {
  return [
    `每轮 ${pace.tick_seconds} 秒`,
    `最多 ${pace.max_strategies_per_tick} 策略/轮`,
    `最多 ${pace.max_symbols_per_tick} 币种/轮`,
    `持仓超时 ×${pace.hold_timeout_multiplier}`,
  ].join(' · ');
}

export interface ShadowStatus {
  enabled: boolean;
  running: boolean;
  port: number;
  pid?: number | null;
  proposal_id?: number | null;
}

export interface ShadowCompareResult {
  ok: boolean;
  error?: string;
  proposal_id?: number | null;
  window?: string;
  domain?: string;
  main?: { win_rate: number; total_pnl: number; total_closed: number };
  shadow?: { win_rate: number; total_pnl: number; total_closed: number };
  delta?: Record<string, number>;
  verdict?: 'shadow_better' | 'main_better' | 'neutral';
}

export interface OpenCodeStatus {
  bridge: OpenCodeBridgeStatus;
  serve_healthy: boolean;
  pace: PaperPaceState;
  shadow: ShadowStatus;
  sidecar?: {
    autostart?: boolean;
    managed?: boolean;
    adopted?: boolean;
    running?: boolean;
    healthy?: boolean;
    healthy_via?: string;
    pid?: number | null;
    exe_found?: boolean;
    host?: string;
    port?: number;
  };
}

export interface InsightItem {
  id: number;
  severity: string;
  title: string;
  status: string;
  category?: string;
  window: string;
  domain: string;
  created_at: string;
}

export interface InsightsResponse {
  items: InsightItem[];
  open_major_count: number;
}

export interface InsightDetail extends InsightItem {
  source?: string;
  finding: Record<string, unknown>;
  resolved_at?: string | null;
}

export interface ProposalItem {
  id: number;
  title: string;
  status: string;
  patch_type: string;
  severity: string;
  created_at: string;
}

export interface ProposalDetail extends ProposalItem {
  source?: string;
  proposal: Record<string, unknown>;
  baseline: Record<string, unknown>;
  after: Record<string, unknown>;
  requires_paper_validation?: boolean;
  requires_manual_live_confirm?: boolean;
  applied_at?: string | null;
  validated_at?: string | null;
  updated_at?: string;
}

export interface SRRReport {
  window: string;
  domain: string;
  generated_at: string;
  total_closed: number;
  win_rate: number;
  total_pnl: number;
  max_single_loss: number;
  master_close_loss_ratio: number;
  master_close_count: number;
  close_reason_breakdown: Record<string, Record<string, unknown>>;
  by_tier: Array<Record<string, unknown>>;
  by_nature: Array<Record<string, unknown>>;
  by_symbol: Array<Record<string, unknown>>;
  insights: Array<{
    severity: string;
    category: string;
    message: string;
    metric?: string;
    value?: number;
    threshold?: number;
  }>;
  rule_breaches: string[];
  arb?: Record<string, unknown> | null;
}

export interface OpenCodeConfig {
  OPENCODE_ENABLED: boolean;
  OPENCODE_SERVER_URL: string;
  OPENCODE_AGENT_PLAN: string;
  OPENCODE_AGENT_BUILD: string;
  OPENCODE_AUTO_APPLY_MINOR: boolean;
  OPENCODE_AUTO_REVIEW: boolean;
  OPENCODE_AGENT_REVIEW: string;
  OPENCODE_REVIEW_MODEL: string;
  OPENCODE_REVIEW_MIN_CONFIDENCE: number;
  OPENCODE_REVIEW_DEFER_RETRY_S: number;
  OPENCODE_PATCH_MAX_DELTA_PCT: number;
  OPENCODE_VALIDATION_HOURS: number;
  OPENCODE_MAJOR_ALERT_CHANNELS: string;
  OPENCODE_CLI_PATH: string;
  OPENCODE_REQUEST_TIMEOUT_S: number;
  OPENCODE_MAJOR_ALERT_COOLDOWN_S: number;
  OPENCODE_MODEL: string;
  OPENCODE_SMALL_MODEL: string;
  OPENCODE_SHADOW_PORT: number;
  OPENCODE_SHADOW_ENABLED: boolean;
  PAPER_PACE_DEFAULT_GEAR: string;
  note: string;
}

export type TuningData = Record<string, unknown>;

export function getStatus() {
  return request<OpenCodeStatus>('/status');
}

export function getConfig() {
  return request<OpenCodeConfig>('/config');
}

export function getInsights(limit = 20) {
  return request<InsightsResponse>(`/insights?limit=${limit}`);
}

export function getInsight(id: number) {
  return request<InsightDetail>(`/insights/${id}`);
}

export function getProposals(status?: string) {
  const q = status ? `?status=${encodeURIComponent(status)}` : '';
  return request<{ items: ProposalItem[] }>(`/proposals${q}`);
}

export function getProposal(id: number) {
  return request<ProposalDetail>(`/proposals/${id}`);
}

export function rollbackProposal(id: number) {
  return request<{ ok: boolean; proposal_id: number; status: string }>(
    `/proposals/${id}/rollback`,
    { method: 'POST' },
  );
}

export function applyProposal(id: number) {
  return request<{ proposal_id: number; status: string; applied: Record<string, unknown> }>(
    `/proposals/${id}/apply`,
    { method: 'POST' },
  );
}

export function rejectProposal(id: number, reason = '') {
  const q = reason ? `?reason=${encodeURIComponent(reason)}` : '';
  return request<{ proposal_id: number; status: string }>(
    `/proposals/${id}/reject${q}`,
    { method: 'POST' },
  );
}

export function backfillProposals() {
  return request<{ created: number }>('/proposals/backfill', { method: 'POST' });
}

export function reviewProposal(id: number) {
  return request<Record<string, unknown>>(`/proposals/${id}/review`, { method: 'POST' });
}

export function reviewAllProposals(limit = 10) {
  return request<{ reviewed: number; results: Record<string, unknown>[] }>(
    `/proposals/review-all?limit=${limit}`,
    { method: 'POST' },
  );
}

export function getStrategyRuntime(window = '24h', domain = 'ai') {
  return fetch(
    `/api/analytics/strategy-runtime?window=${encodeURIComponent(window)}&domain=${encodeURIComponent(domain)}`,
  ).then(async (res) => {
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json() as Promise<SRRReport>;
  });
}

export function triggerAnalyze(window = '24h', domain = 'ai') {
  return request<Record<string, unknown>>(
    `/analyze?window=${encodeURIComponent(window)}&domain=${encodeURIComponent(domain)}`,
    { method: 'POST' },
  );
}

export function listReports() {
  return request<{ files: string[]; dir: string }>('/reports/dir');
}

export function getReportContent(file: string) {
  return request<{
    file: string;
    format: string;
    content: string;
    parsed: unknown;
  }>(`/reports/content?file=${encodeURIComponent(file)}`);
}

export function getTuning() {
  return request<TuningData>('/tuning');
}

export function patchTuning(patches: Record<string, unknown>) {
  return request<{ applied: Record<string, unknown>; tuning: TuningData }>('/tuning', {
    method: 'PATCH',
    body: JSON.stringify({ patches }),
  });
}

export function patchPaperPace(gear: string, manual = true) {
  return request<PaperPaceState>('/paper-pace', {
    method: 'PATCH',
    body: JSON.stringify({ gear, manual }),
  });
}

export function unlockPaperPace() {
  return request<PaperPaceState>('/paper-pace/unlock', { method: 'POST' });
}

export function startShadow(proposalId: number) {
  return request<Record<string, unknown>>(`/shadow/start/${proposalId}`, { method: 'POST' });
}

export function stopShadow() {
  return request<ShadowStatus>('/shadow/stop', { method: 'POST' });
}

export function compareShadowSrr(window = '24h', domain = 'ai') {
  return request<ShadowCompareResult>(`/shadow/compare?window=${window}&domain=${domain}`);
}

export function getMasterClosePolicy() {
  return request<{ name: string; path: string; content: string }>('/policies/master_close');
}

// ── RuntimeGovernor：仲裁决策日志 / 参数所有权地图 / 提案成效漏斗 ──

export interface OwnershipCandidate {
  source: string;
  value: unknown;
  confidence?: number | null;
  reason?: string;
}

export interface OwnershipEntry {
  owner: string;
  target_value: unknown;
  effective_value: unknown;
  candidates: OwnershipCandidate[];
}

export interface GovernorDecision {
  ts?: string;
  key?: string;
  owner?: string;
  value?: unknown;
  reason?: string;
  [k: string]: unknown;
}

export interface ProposalFunnel {
  total: number;
  by_status: Record<string, number>;
  verdicts: {
    improved: number;
    neutral: number;
    degraded: number;
    unevaluated: number;
  };
  funnel: { created: number; applied: number; evaluated: number; improved: number };
  improve_rate: number | null;
  rollback_rate?: number | null;
  inconclusive_reasons?: Record<string, number>;
  paper_applying?: Array<{
    id: number;
    title?: string;
    applied_at?: string;
    age_hours?: number;
    post_apply_closed?: number;
  }>;
  training_phase?: Record<string, unknown>;
  validation_policy?: Record<string, unknown>;
}

export function getGovernorOwnership() {
  return request<{ ownership: Record<string, OwnershipEntry> }>('/governor/ownership');
}

export function getGovernorDecisions(limit = 50) {
  return request<{ decisions: GovernorDecision[] }>(`/governor/decisions?limit=${limit}`);
}

export function getProposalFunnel() {
  return request<ProposalFunnel>('/governor/funnel');
}

// ── 系统健康：log digest + health snapshot ──

export interface LogDigestEntry {
  logger: string;
  count: number;
  sample: string;
  pattern: string;
  severity_hint: string;
  last_seen?: string;
}

export interface LogDigest {
  generated_at: string;
  window_hours: number;
  total_errors: number;
  distinct_groups?: number;
  p0_count: number;
  has_log_errors: boolean;
  entries: LogDigestEntry[];
}

/** Alpha 助手角标：24h 内 distinct 错误类型数（非未读对话） */
export function assistantErrorBadgeCount(digest?: LogDigest | null): number {
  if (!digest?.has_log_errors) return 0;
  if (typeof digest.distinct_groups === 'number' && digest.distinct_groups > 0) {
    return digest.distinct_groups;
  }
  if (digest.entries?.length) return digest.entries.length;
  return digest.p0_count > 0 ? digest.p0_count : 0;
}

export interface HealthApiResult {
  ok: boolean;
  data?: Record<string, unknown>;
  error?: string;
}

export interface HealthSnapshot {
  overall_ok: boolean;
  ok_count: number;
  total: number;
  apis: Record<string, HealthApiResult>;
}

export interface HealthDigestResponse {
  log_digest: LogDigest;
  health_snapshot: HealthSnapshot;
}

export function getHealthDigest(windowHours = 24) {
  return request<HealthDigestResponse>(`/health/digest?window_hours=${windowHours}`);
}

export function getLogTail(lines = 200) {
  return request<{ path: string; lines: string[]; exists: boolean }>(
    `/health/log-tail?lines=${lines}`,
  );
}

export function triggerHealthEscalate() {
  return request<{
    digest_errors_24h: number;
    health_ok: number;
    escalation: { created: number; skipped_dedupe: number; resolved: number };
  }>('/health/escalate', { method: 'POST' });
}

export function startSidecar() {
  return request<{
    result: Record<string, unknown>;
    sidecar: OpenCodeStatus['sidecar'];
    serve_healthy: boolean;
  }>('/sidecar/start', { method: 'POST' });
}

export function ensureSidecar() {
  return request<{
    result: Record<string, unknown>;
    sidecar: OpenCodeStatus['sidecar'];
    serve_healthy: boolean;
  }>('/sidecar/ensure', { method: 'POST' });
}

export function getPromptTraces(limit = 30) {
  return request<{ entries: Array<Record<string, unknown>>; count: number }>(
    `/prompt-traces?limit=${limit}`,
  );
}

export function evaluateProposalsNow(force = false) {
  return request<{
    evaluated_this_run: number;
    evaluated_total: number;
    by_status: Record<string, number>;
    verdicts: Record<string, number>;
    force: boolean;
  }>(`/proposals/evaluate-now?force=${force ? 'true' : 'false'}`, { method: 'POST' });
}
