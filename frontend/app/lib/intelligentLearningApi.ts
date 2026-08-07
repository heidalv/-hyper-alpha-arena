/**
 * Intelligent Learning Center — 统一 API 客户端
 *
 * Phase 5 整合: 合并 aiLearningApi.ts + opencodeApi.ts 的核心端点。
 * 消除前端多 API 客户端重复拉取相同数据的问题。
 */

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') || '/api';

// ─────────────────────────────────────────────
//  类型定义
// ─────────────────────────────────────────────

export interface OverviewResponse {
  evolution: {
    last_evolution_at?: string | null;
    last_evolution_type?: string | null;
    last_promoted_count?: number;
    last_best_fitness?: number;
    scheduler_active?: boolean;
    error?: string;
  };
  factors: {
    total?: number;
    active?: number;
    status?: string;
    error?: string;
  };
  strategies: {
    total?: number;
    active?: number;
    by_tier?: Record<string, number>;
    error?: string;
  };
  opencode: {
    sidecar_healthy?: boolean;
    sidecar_port?: number;
    open_insights?: number;
    pending_proposals?: number;
    error?: string;
  };
  learning_loop: {
    enabled?: boolean;
    paused?: boolean;
    registered?: boolean;
    last_tick_at?: string;
    error?: string;
  };
  hermes?: {
    maturity_score?: number;
    layers?: Record<string, unknown>;
    error?: string;
  };
  runtime_governor?: {
    pending_count?: number;
    pending?: Array<Record<string, unknown>>;
    error?: string;
  };
  knowledge_pool: {
    total_lessons?: number;
    by_category?: Record<string, number>;
    error?: string;
  };
  alerts: Array<{
    severity: string;
    title: string;
    source: string;
    category: string;
    created_at: string | null;
  }>;
  generated_at: string;
}

export interface KnowledgeResponse {
  total: number;
  filtered: number;
  items: Array<Record<string, any>>;
  error?: string;
}

// ─────────────────────────────────────────────
//  概览 API
// ─────────────────────────────────────────────

export async function getOverview(): Promise<OverviewResponse> {
  const res = await fetch(`${API_BASE}/intelligent-learning/overview`);
  if (!res.ok) throw new Error(`Overview API error: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
//  知识池 API
// ─────────────────────────────────────────────

export async function queryKnowledge(params?: {
  categories?: string;
  sources?: string;
  limit?: number;
}): Promise<KnowledgeResponse> {
  const searchParams = new URLSearchParams();
  if (params?.categories) searchParams.set('categories', params.categories);
  if (params?.sources) searchParams.set('sources', params.sources);
  if (params?.limit) searchParams.set('limit', String(params.limit));
  const qs = searchParams.toString();
  const res = await fetch(`${API_BASE}/intelligent-learning/knowledge${qs ? '?' + qs : ''}`);
  if (!res.ok) throw new Error(`Knowledge API error: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
//  进化系统 API（从 aiLearningApi.ts 继承）
// ─────────────────────────────────────────────

export async function getEvolutionStatus() {
  const res = await fetch(`${API_BASE}/evolution/status`);
  if (!res.ok) throw new Error(`Evolution status error: ${res.status}`);
  return res.json();
}

export async function triggerEvolution(type: 'manual' | 'emergency') {
  const res = await fetch(`${API_BASE}/evolution/trigger/${type}`, { method: 'POST' });
  if (!res.ok) throw new Error(`Evolution trigger error: ${res.status}`);
  return res.json();
}

export async function getEvolutionHistory(params?: { template_id?: string; page?: number; page_size?: number }) {
  const sp = new URLSearchParams();
  if (params?.template_id) sp.set('template_id', params.template_id);
  if (params?.page) sp.set('page', String(params.page));
  if (params?.page_size) sp.set('page_size', String(params.page_size));
  const qs = sp.toString();
  const res = await fetch(`${API_BASE}/evolution/history${qs ? '?' + qs : ''}`);
  if (!res.ok) throw new Error(`Evolution history error: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
//  LearningLoop API（从 aiLearningApi.ts 继承）
// ─────────────────────────────────────────────

export async function getCoordinatorStatus() {
  const res = await fetch(`${API_BASE}/rl/coordinator/status`);
  if (!res.ok) throw new Error(`Coordinator status error: ${res.status}`);
  return res.json();
}

export async function getKellyPortfolio() {
  const res = await fetch(`${API_BASE}/rl/kelly/portfolio`);
  if (!res.ok) throw new Error(`Kelly portfolio error: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
//  OpenCode API（从 opencodeApi.ts 继承核心）
// ─────────────────────────────────────────────

export async function getOpenCodeStatus() {
  const res = await fetch(`${API_BASE}/opencode/status`);
  if (!res.ok) throw new Error(`OpenCode status error: ${res.status}`);
  return res.json();
}

export async function getOpenCodeInsights(params?: { limit?: number }) {
  const sp = new URLSearchParams();
  if (params?.limit) sp.set('limit', String(params.limit));
  const qs = sp.toString();
  const res = await fetch(`${API_BASE}/opencode/insights${qs ? '?' + qs : ''}`);
  if (!res.ok) throw new Error(`OpenCode insights error: ${res.status}`);
  return res.json();
}

export async function getOpenCodeProposals(params?: { status?: string }) {
  const sp = new URLSearchParams();
  if (params?.status) sp.set('status', params.status);
  const qs = sp.toString();
  const res = await fetch(`${API_BASE}/opencode/proposals${qs ? '?' + qs : ''}`);
  if (!res.ok) throw new Error(`OpenCode proposals error: ${res.status}`);
  return res.json();
}

export async function triggerOpenCodeAnalyze() {
  const res = await fetch(`${API_BASE}/opencode/analyze`, { method: 'POST' });
  if (!res.ok) throw new Error(`OpenCode analyze error: ${res.status}`);
  return res.json();
}

export async function getOpenCodeProposalDetail(id: number) {
  const res = await fetch(`${API_BASE}/opencode/proposals/${id}`);
  if (!res.ok) throw new Error(`OpenCode proposal detail error: ${res.status}`);
  return res.json();
}

export async function applyOpenCodeProposal(id: number) {
  const res = await fetch(`${API_BASE}/opencode/proposals/${id}/apply`, { method: 'POST' });
  if (!res.ok) throw new Error(`OpenCode apply proposal error: ${res.status}`);
  return res.json();
}

export async function rejectOpenCodeProposal(id: number, reason = '') {
  const q = reason ? `?reason=${encodeURIComponent(reason)}` : '';
  const res = await fetch(`${API_BASE}/opencode/proposals/${id}/reject${q}`, { method: 'POST' });
  if (!res.ok) throw new Error(`OpenCode reject proposal error: ${res.status}`);
  return res.json();
}

export async function reviewOpenCodeProposal(id: number) {
  const res = await fetch(`${API_BASE}/opencode/proposals/${id}/review`, { method: 'POST' });
  if (!res.ok) throw new Error(`OpenCode review proposal error: ${res.status}`);
  return res.json();
}

export async function reviewAllOpenCodeProposals(limit = 10) {
  const res = await fetch(`${API_BASE}/opencode/proposals/review-all?limit=${limit}`, { method: 'POST' });
  if (!res.ok) throw new Error(`OpenCode review all error: ${res.status}`);
  return res.json();
}

export async function rollbackOpenCodeProposal(id: number) {
  const res = await fetch(`${API_BASE}/opencode/proposals/${id}/rollback`, { method: 'POST' });
  if (!res.ok) throw new Error(`OpenCode rollback proposal error: ${res.status}`);
  return res.json();
}

export async function evaluateOpenCodeProposalsNow(force = false) {
  const res = await fetch(`${API_BASE}/opencode/proposals/evaluate-now?force=${force ? 'true' : 'false'}`, { method: 'POST' });
  if (!res.ok) throw new Error(`OpenCode evaluate proposals error: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
//  学习仪表板 API
// ─────────────────────────────────────────────

export async function getDashboardOverview() {
  const res = await fetch(`${API_BASE}/learning/dashboard/overview`);
  if (!res.ok) throw new Error(`Dashboard overview error: ${res.status}`);
  return res.json();
}

export async function getDashboardHealth() {
  const res = await fetch(`${API_BASE}/learning/dashboard/health`);
  if (!res.ok) throw new Error(`Dashboard health error: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
//  学习后端 + 配置 API (L1-L5 后端架构对齐)
// ─────────────────────────────────────────────

/** 单个学习后端状态（对应 BackendRegistry.status()） */
export interface BackendStatus {
  name: string;
  enabled: boolean;
  priority: number;
}

/** 学习中心统一配置快照（对应 LearningConfig） */
export interface LearningConfigSnapshot {
  loop_enabled: boolean;
  drl_retrain_auto: boolean;
  enable_coordinator: boolean;
  enable_kelly_position: boolean;
  nsga2_enabled: boolean;
  factor_strategy_joint: boolean;
  concept_drift_detection: boolean;
  causal_discovery: boolean;
}

/** GET /api/learning/dashboard/feature-flags 的完整返回 */
export interface FeatureFlagsResponse {
  // P0-P3 大写 env 开关
  [flagKey: string]: boolean | Record<string, unknown> | LearningConfigSnapshot | BackendStatus[] | undefined;
  // L5 新增的嵌套字段
  _learning_config?: LearningConfigSnapshot;
  _backends?: BackendStatus[];
}

/** 获取特性开关 + 后端注册表状态 + 统一配置 */
export async function getFeatureFlags(): Promise<FeatureFlagsResponse> {
  const res = await fetch(`${API_BASE}/learning/dashboard/feature-flags`);
  if (!res.ok) throw new Error(`Feature flags error: ${res.status}`);
  return res.json();
}

/** 仅获取学习后端注册表状态（11 个后端） */
export async function getBackendsStatus(): Promise<BackendStatus[]> {
  const data = await getFeatureFlags();
  return data._backends || [];
}

/** 仅获取学习中心统一配置快照 */
export async function getLearningConfig(): Promise<LearningConfigSnapshot | null> {
  const data = await getFeatureFlags();
  return data._learning_config || null;
}

/** 设置单个 P0-P3 特性开关 */
export async function setFeatureFlag(flagKey: string, enabled: boolean): Promise<void> {
  const res = await fetch(`${API_BASE}/learning/dashboard/feature-flags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: flagKey, value: enabled }),
  });
  if (!res.ok) throw new Error(`Set feature flag error: ${res.status}`);
}

/** RuntimeGovernor 待审批 */
export async function getGovernorPending() {
  const res = await fetch(`${API_BASE}/gap-closure/runtime/pending`);
  if (!res.ok) throw new Error(`Governor pending error: ${res.status}`);
  return res.json();
}

export async function approveGovernorPatch(patchId: string) {
  const res = await fetch(`${API_BASE}/gap-closure/runtime/approve/${patchId}`, { method: 'POST' });
  if (!res.ok) throw new Error(`Governor approve error: ${res.status}`);
  return res.json();
}

export async function rejectGovernorPatch(patchId: string) {
  const res = await fetch(`${API_BASE}/gap-closure/runtime/reject/${patchId}`, { method: 'POST' });
  if (!res.ok) throw new Error(`Governor reject error: ${res.status}`);
  return res.json();
}

/** Hermes 仪表盘 */
export async function getHermesDashboard() {
  const res = await fetch(`${API_BASE}/hermes/dashboard`);
  if (!res.ok) throw new Error(`Hermes dashboard error: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
//  阶段2(S2-11) 学习三通道看板 API
// ─────────────────────────────────────────────

export interface WisdomRankedItem {
  id: number;
  type?: string | null;
  tier?: string | null;
  template_id?: string | null;
  effectiveness?: number | null;
  evaluation_count?: number;
  quality_hit_count?: number;
  applied_count?: number;
  strength?: number;
}

export interface WisdomLoopResponse {
  ranked?: WisdomRankedItem[];
  report?: {
    total?: number;
    active?: number;
    deactivated?: number;
    by_type?: Record<string, { count: number; avg_effectiveness: number; total_applied: number; active: number }>;
    top_wisdom?: Array<Record<string, unknown>>;
  };
  error?: string;
}

export interface QaaDomainHeartbeat {
  enabled: boolean;
  interval_sec: number;
  description: string;
  last_run_at: number;
  last_status: string;
  last_error: string;
  run_count: number;
}

export interface QaaSchedulerResponse {
  enabled?: boolean;
  domains?: Record<string, QaaDomainHeartbeat>;
  error?: string;
}

export interface ParamDomainChange {
  param_key: string;
  direction: string;
  old: [number, number];
  new: [number, number];
  n_patterns: number;
}

export interface ParamDomainResponse {
  cfg?: {
    enabled?: boolean;
    expand_ratio?: number;
    expand_max?: number;
    min_samples?: number;
    min_confidence?: number;
  };
  patterns?: {
    total?: number;
    by_direction?: Record<string, number>;
    by_key?: Record<string, { increase: number; decrease: number; avg_pnl_impact: number; n: number }>;
  };
  base_ranges?: Record<string, [number, number]>;
  expanded_ranges?: Record<string, [number, number]>;
  changes?: ParamDomainChange[];
  expanded_count?: number;
  error?: string;
}

export interface DecisionChainWisdom {
  type?: string | null;
  tier?: string | null;
  template_id?: string | null;
  effectiveness?: number | null;
  evaluation_count?: number;
  quality_hit_count?: number;
  is_active?: boolean | null;
}

export interface DecisionChainItem {
  id: number;
  decision_time?: string | null;
  symbol?: string | null;
  operation?: string | null;
  decision_source?: string | null;
  realized_pnl?: number | null;
  wisdom_ids: number[];
  wisdoms: DecisionChainWisdom[];
}

export interface DecisionChainResponse {
  chain?: DecisionChainItem[];
  total_decisions?: number;
  sampled?: number;
  wisdom_covered?: number;
  error?: string;
}

export interface CoinFeedbackResponse {
  ic_weights?: {
    weights?: Record<string, number>;
    ics?: Record<string, number>;
    n_samples?: number;
    enabled?: boolean;
    note?: string;
    computed_at?: number;
  };
  injected?: {
    total?: number;
    with_snapshot?: number;
    hit_24h?: number;
    hit_72h?: number;
    hit_rate_24h?: number;
    hit_rate_72h?: number;
    by_symbol?: Record<string, { n: number; hit24: number; hit72: number; hit_rate_24h: number }>;
  };
  error?: string;
}

/** 通道一：wisdom 闭环看板 */
export async function getWisdomLoop(): Promise<WisdomLoopResponse> {
  const res = await fetch(`${API_BASE}/intelligent-learning/wisdom-loop`);
  if (!res.ok) throw new Error(`Wisdom loop error: ${res.status}`);
  return res.json();
}

/** 通道二：参数域扩展状态 */
export async function getParamDomain(): Promise<ParamDomainResponse> {
  const res = await fetch(`${API_BASE}/intelligent-learning/param-domain`);
  if (!res.ok) throw new Error(`Param domain error: ${res.status}`);
  return res.json();
}

/** 通道三：QAA 调度统一心跳 */
export async function getQaaScheduler(): Promise<QaaSchedulerResponse> {
  const res = await fetch(`${API_BASE}/intelligent-learning/qaa-scheduler`);
  if (!res.ok) throw new Error(`QAA scheduler error: ${res.status}`);
  return res.json();
}

/** 决策链路视图 */
export async function getDecisionChain(limit = 20): Promise<DecisionChainResponse> {
  const res = await fetch(`${API_BASE}/intelligent-learning/decision-chain?limit=${limit}`);
  if (!res.ok) throw new Error(`Decision chain error: ${res.status}`);
  return res.json();
}

/** 选币反馈面板 */
export async function getCoinFeedback(): Promise<CoinFeedbackResponse> {
  const res = await fetch(`${API_BASE}/intelligent-learning/coin-feedback`);
  if (!res.ok) throw new Error(`Coin feedback error: ${res.status}`);
  return res.json();
}
