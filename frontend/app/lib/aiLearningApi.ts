/**
 * @deprecated 本客户端已整合到 intelligentLearningApi.ts（Phase 5 统一收敛）。
 * 新代码请勿 import 本文件，改用 @/lib/intelligentLearningApi。
 * 保留本文件仅为兼容 AILearningCenter.tsx 等存量组件，后续随组件迁移逐步移除。
 *
 * AI Learning System API Client
 *
 * AI学习系统深度整合的前端API调用模块
 * 对应后端: rl_routes.py (扩展) + evolution_routes.py (新建)
 */

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') || '/api';

// ══════════════════════════════════════════════════
//  Types
// ══════════════════════════════════════════════════

export interface PortfolioKellyAllocation {
  symbol: string;
  kelly_fraction: number;
  adjusted_fraction: number;
  position_size: number;
  portfolio_fraction: number;
  risk_contribution: number;
  correlation_with_others: number;
  forced_adjustment: string;
}

export interface PortfolioKellyResult {
  allocations: PortfolioKellyAllocation[];
  total_risk: number;
  correlation_risk: number;
  forced_adjustments: string[];
}

export interface CoordinatorStatus {
  drl_available: boolean;
  drl_has_model: boolean;
  drl_model_version: string;
  kelly_available: boolean;
  risk_aggregator_available: boolean;
  last_training_params: string[];
  feature_flags: {
    drl_integration: boolean;
    kelly_position: boolean;
    evolution_feedback: boolean;
    portfolio_risk: boolean;
    coordinator: boolean;
    drl_shadow_mode: boolean;
  };
}

export interface EvolutionStatus {
  evolver_running: boolean;
  evolver_progress: Record<string, unknown>;
  scheduler_status: string;
}

export interface CorrelationMatrixResult {
  symbols: string[];
  matrix: number[][];
  computed_at: number | null;
}

export interface RegimeAnalysisResult {
  current_regime: string | null;
  regime_confidence: number;
  regime_distribution: Record<string, number>;
  recent_count: number;
  source?: string;
  anchor_symbol?: string;
}

export interface EvolutionHistoryResult {
  total: number;
  page: number;
  page_size: number;
  records: Array<{
    run_id: string;
    template_id: string;
    symbol: string;
    generation: number;
    sharpe_ratio: number;
    win_rate: number;
    max_drawdown: number;
    total_return: number;
    is_champion: boolean;
    status: string;
    created_at: string | null;
  }>;
}

// ══════════════════════════════════════════════════
//  P3 Learning Dashboard Types
// ══════════════════════════════════════════════════

/** P0-P3 全部 16 个 AI 特性开关 (Record 模式) */
export type P3FeatureFlags = Record<string, boolean>;

export interface DashboardOverview {
  factors: { loaded: number; categories: Record<string, number> };
  strategies: { active: number; evolving: number; promoted: number };
  memory: { total_lessons: number; key_themes: [string, number][] };
  evolution: { generation: number; last_run: string | null };
  opencode: { sessions_active: number };
  daily_trades?: number;
  daily_pnl?: number;
  uptime_hours: number;
  mlto?: {
    thesis_hit_rate?: number | null;
    premature_open_rate?: number | null;
    evidence_source_contribution?: Record<string, { weight?: number; wins?: number; losses?: number }>;
    thesis_drift_resets?: number;
    sample_count?: number;
  };
}

export interface DashboardHealth {
  causal_discovery: string;
  concept_drift: string;
  memory_decay: string;
  counterfactual_sandbox: string;
  trading_narrative: string;
  factor_discovery: string;
  factor_strategy_fusion: string;
  walk_forward_validator: string;
  cross_market_transfer: string;
  learning_ab_framework: string;
  overall_health: string;
}

export interface DashboardFactor {
  name: string;
  category: string;
  value: unknown;
  signal: string;
}

export interface DashboardStrategy {
  template_id: string;
  name: string;
  symbol: string;
  tier: string;
  status: string;
  sharpe: number | null;
  win_rate: number | null;
  total_trades: number | null;
  lessons_count: number;
}

export interface DashboardExperiment {
  experiment_id?: string;
  control_group?: string;
  experiment_group?: string;
  status?: string;
  p_value?: number;
  winner?: string;
  [key: string]: unknown;
}

// ══════════════════════════════════════════════════
//  API Functions
// ══════════════════════════════════════════════════

/** 批量获取多币种Kelly仓位
 *  v3 整改：后端返回 forced_adjustment 可能为 null / correlation_with_others 后端未返，
 *  在客户端做一次字段适配，组件层可维持原 interface。 */
export async function getPortfolioKelly(symbols: string[]): Promise<PortfolioKellyResult> {
  const empty: PortfolioKellyResult = {
    allocations: [], total_risk: 0, correlation_risk: 0, forced_adjustments: [],
  };
  try {
    const res = await fetch(`${API_BASE}/rl/kelly/portfolio?symbols=${symbols.join(',')}`);
    if (!res.ok) return empty;
    const raw = await res.json();
    const allocations: PortfolioKellyAllocation[] = (raw?.allocations ?? []).map((a: any) => ({
      symbol: String(a.symbol ?? ''),
      kelly_fraction: Number(a.kelly_fraction ?? 0),
      adjusted_fraction: Number(a.adjusted_fraction ?? 0),
      position_size: Number(a.position_size ?? 0),
      portfolio_fraction: Number(a.portfolio_fraction ?? 0),
      risk_contribution: Number(a.risk_contribution ?? 0),
      // 后端目前未返 correlation_with_others，按 risk_contribution 的 0~1 归一降级展示
      correlation_with_others: Number(a.correlation_with_others ?? a.risk_contribution ?? 0),
      forced_adjustment: a.forced_adjustment ?? '',
    }));
    return {
      allocations,
      total_risk: Number(raw?.total_risk ?? 0),
      correlation_risk: Number(raw?.correlation_risk ?? 0),
      forced_adjustments: Array.isArray(raw?.forced_adjustments) ? raw.forced_adjustments : [],
    };
  } catch {
    return empty;
  }
}

/** 获取系统协调状态
 *  v3 整改：后端返回 {db_state, coordinator:{...}, tdi_injected, timestamp}，
 *  客户端展平 coordinator 子对象到顶层，保持组件原有字段访问。 */
export async function getCoordinatorStatus(): Promise<CoordinatorStatus> {
  const fallback: CoordinatorStatus = {
    drl_available: false, drl_has_model: false, drl_model_version: '',
    kelly_available: false, risk_aggregator_available: false,
    last_training_params: [],
    feature_flags: {
      drl_integration: false, kelly_position: false, evolution_feedback: false,
      portfolio_risk: false, coordinator: false, drl_shadow_mode: true,
    },
  };
  try {
    const res = await fetch(`${API_BASE}/rl/coordinator/status`);
    if (!res.ok) return fallback;
    const raw = await res.json();
    const c = raw?.coordinator ?? raw ?? {};
    const ff = c?.feature_flags ?? fallback.feature_flags;
    return {
      drl_available: !!c.drl_available,
      drl_has_model: !!c.drl_has_model,
      drl_model_version: String(c.drl_model_version ?? ''),
      kelly_available: !!c.kelly_available,
      risk_aggregator_available: !!c.risk_aggregator_available,
      last_training_params: Array.isArray(c.last_training_params) ? c.last_training_params : [],
      feature_flags: {
        drl_integration: !!ff.drl_integration,
        kelly_position: !!ff.kelly_position,
        evolution_feedback: !!ff.evolution_feedback,
        portfolio_risk: !!ff.portfolio_risk,
        coordinator: !!ff.coordinator,
        drl_shadow_mode: !!ff.drl_shadow_mode,
      },
    };
  } catch {
    return fallback;
  }
}

/** 手动触发协调优化 */
export async function triggerCoordinatedOptimization(reason: string = 'manual'): Promise<void> {
  await fetch(`${API_BASE}/rl/coordinator/optimize?reason=${reason}`, { method: 'POST' });
}

/** 获取进化系统状态 */
export async function getEvolutionStatus(): Promise<EvolutionStatus> {
  const fallback: EvolutionStatus = {
    evolver_running: false, evolver_progress: {}, scheduler_status: 'unknown',
  };
  try {
    const res = await fetch(`${API_BASE}/evolution/status`);
    if (!res.ok) return fallback;
    const raw = await res.json();
    return {
      evolver_running: !!raw?.evolver_running,
      evolver_progress:
        raw?.evolver_progress && typeof raw.evolver_progress === 'object'
          ? raw.evolver_progress
          : {},
      scheduler_status: String(raw?.scheduler_status ?? 'unknown'),
    };
  } catch {
    return fallback;
  }
}

/** 触发进化 */
export async function triggerEvolution(type: string, templateId?: string): Promise<void> {
  const params = templateId ? `?template_id=${templateId}` : '';
  await fetch(`${API_BASE}/evolution/trigger/${type}${params}`, { method: 'POST' });
}

/** 获取币种相关性矩阵 */
export async function getCorrelationMatrix(): Promise<CorrelationMatrixResult> {
  const fallback: CorrelationMatrixResult = { symbols: [], matrix: [], computed_at: null };
  try {
    const res = await fetch(`${API_BASE}/evolution/correlation-matrix`);
    if (!res.ok) return fallback;
    const raw = await res.json();
    return {
      symbols: Array.isArray(raw?.symbols) ? raw.symbols : [],
      matrix: Array.isArray(raw?.matrix) ? raw.matrix : [],
      computed_at: typeof raw?.computed_at === 'number' ? raw.computed_at : null,
    };
  } catch {
    return fallback;
  }
}

/** 获取当前市场状态分析 */
export async function getRegimeAnalysis(): Promise<RegimeAnalysisResult> {
  const fallback: RegimeAnalysisResult = {
    current_regime: null, regime_confidence: 0, regime_distribution: {},
    recent_count: 0, source: 'error',
  };
  try {
    const res = await fetch(`${API_BASE}/evolution/regime-analysis`);
    if (!res.ok) return fallback;
    const raw = await res.json();
    return {
      current_regime: raw?.current_regime ?? null,
      regime_confidence: Number(raw?.regime_confidence ?? 0),
      regime_distribution:
        raw?.regime_distribution && typeof raw.regime_distribution === 'object'
          ? raw.regime_distribution
          : {},
      recent_count: Number(raw?.recent_count ?? 0),
      source: raw?.source,
      anchor_symbol: raw?.anchor_symbol,
    };
  } catch {
    return fallback;
  }
}

/** 获取进化历史（分页） */
export async function getEvolutionHistory(
  templateId?: string, page: number = 1, pageSize: number = 20
): Promise<EvolutionHistoryResult> {
  const fallback: EvolutionHistoryResult = { total: 0, page, page_size: pageSize, records: [] };
  try {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (templateId) params.set('template_id', templateId);
    const res = await fetch(`${API_BASE}/evolution/history?${params}`);
    if (!res.ok) return fallback;
    const raw = await res.json();
    return {
      total: Number(raw?.total ?? 0),
      page: Number(raw?.page ?? page),
      page_size: Number(raw?.page_size ?? pageSize),
      records: Array.isArray(raw?.records) ? raw.records : [],
    };
  } catch {
    return fallback;
  }
}

// v3 整改: tier 分布
export interface TierDistributionResult {
  total: number;
  distribution: { short: number; mid: number; long: number; unknown: number };
  ratio: { short: number; mid: number; long: number };
  quota: { short: number; mid: number; long: number };
  deviation: { short: number; mid: number; long: number };
}

/** 获取活跃策略的 tier 分布（监控 mid-skew 偏斜） */
export async function getTierDistribution(accountId?: number): Promise<TierDistributionResult> {
  try {
    const params = accountId ? `?account_id=${accountId}` : '';
    const res = await fetch(`${API_BASE}/ai-strategies/stats/tier-distribution${params}`);
    return await res.json();
  } catch {
    return {
      total: 0,
      distribution: { short: 0, mid: 0, long: 0, unknown: 0 },
      ratio: { short: 0, mid: 0, long: 0 },
      quota: { short: 0.35, mid: 0.35, long: 0.30 },
      deviation: { short: 0, mid: 0, long: 0 },
    };
  }
}

export interface BlockReportItem {
  code: string;
  count: number;
  ratio: number;
  samples: string[];
}

export interface BlockReportResult {
  window_sec: number;
  total: number;
  top: BlockReportItem[];
}

/** 获取阻断事件 Top-N 原因（诊断"为什么今天没开单"） */
export async function getBlockReportTop(n: number = 3, hours: number = 24): Promise<BlockReportResult> {
  try {
    const res = await fetch(`${API_BASE}/system/block-report-top?n=${n}&hours=${hours}`);
    return await res.json();
  } catch {
    return { window_sec: hours * 3600, total: 0, top: [] };
  }
}

// ══════════════════════════════════════════════════
//  P3 Learning Dashboard API
// ══════════════════════════════════════════════════

const DASHBOARD = `${API_BASE}/learning/dashboard`;

/** 获取 P0-P3 全部 16 个 AI 特性开关 */
export async function getP3FeatureFlags(): Promise<P3FeatureFlags> {
  try {
    const res = await fetch(`${DASHBOARD}/feature-flags`);
    if (!res.ok) return {};
    return await res.json();
  } catch {
    return {};
  }
}

/** 切换单个 P0-P3 特性开关 */
export async function setP3FeatureFlag(key: string, value: boolean): Promise<boolean> {
  try {
    const res = await fetch(`${DASHBOARD}/feature-flags`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, value }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    return data?.status === 'updated';
  } catch {
    return false;
  }
}

/** 获取学习系统全局概览 */
export async function getDashboardOverview(): Promise<DashboardOverview | null> {
  try {
    const res = await fetch(`${DASHBOARD}/overview`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/** 获取 P0-P3 组件健康状态 */
export async function getDashboardHealth(): Promise<DashboardHealth | null> {
  try {
    const res = await fetch(`${DASHBOARD}/health`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/** 获取因子状态详情 */
export async function getDashboardFactors(): Promise<{ factors: DashboardFactor[]; factor_discovery: Record<string, unknown>; factor_fusion: Record<string, unknown> } | null> {
  try {
    const res = await fetch(`${DASHBOARD}/factors`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/** 获取策略与进化状态 */
export async function getDashboardStrategies(): Promise<{ templates: DashboardStrategy[]; evolution_progress: Record<string, unknown>; walk_forward: Record<string, unknown> } | null> {
  try {
    const res = await fetch(`${DASHBOARD}/strategies`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/** 获取进化进度详情 */
export async function getDashboardEvolution(): Promise<Record<string, unknown> | null> {
  try {
    const res = await fetch(`${DASHBOARD}/evolution`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/** 获取策略记忆 */
export async function getDashboardMemory(): Promise<Record<string, unknown> | null> {
  try {
    const res = await fetch(`${DASHBOARD}/memory`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/** 获取 A/B 实验状态 */
export async function getDashboardExperiments(): Promise<Record<string, unknown> | null> {
  try {
    const res = await fetch(`${DASHBOARD}/experiments`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/** 获取跨市场迁移状态 */
export async function getDashboardTransfer(): Promise<Record<string, unknown> | null> {
  try {
    const res = await fetch(`${DASHBOARD}/transfer`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}
