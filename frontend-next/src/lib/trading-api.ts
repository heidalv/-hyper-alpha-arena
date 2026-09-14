/**
 * trading-api — `/api/trading/*` 类型化封装（套利中心）
 *
 * 契约来源：《套利中心重构设计_含前端_V2》§2 + 后端 `lane_routes.py` / `trading_routes.py`
 * 在线实测（`http://127.0.0.1:8000/api/trading/*`）。
 *
 * 原则：
 *  - 只建模后端真实返回的字段，不发明字段；
 *  - 可为 null 的字段一律 `| null`，前端按「未验证/无数据」处理，绝不用 0 掩盖；
 *  - 所有请求走 `@/lib/api` 的 `apiRequest`（带鉴权、401 续期、超时），禁止裸 fetch。
 */

import { apiRequest } from "./api";

// ═══════════════════════════════════════════════════════
// 通用：as_of → 数据年龄（秒）
// as_of 可能为 UTC(+00:00) 或 +08:00，Date 解析均可得到正确 epoch。
// ═══════════════════════════════════════════════════════
export function ageFromAsOf(asOf?: string | null): number | null {
  if (!asOf) return null;
  const t = new Date(asOf).getTime();
  if (Number.isNaN(t)) return null;
  return Date.now() - t;
}

/** 把数据年龄（ms）格式化为人类可读，如 "12s前" / "3m12s前" / "—" */
export function fmtAgeMs(ms: number | null): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (ms < 0) return "刚刚";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s前`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m${r}s前`;
}

// ═══════════════════════════════════════════════════════
// 车道 / 晋升
// ═══════════════════════════════════════════════════════

export interface LaneFold {
  net_bp: number;
  t: number;
  /** 后端 fold 里 n 可能为 null */
  n: number | null;
}

/** 扣费后边际指标（缺字段 = 未验证，fail-closed） */
export interface LaneEdge {
  gross_bp?: number | null;
  cost_bp?: number | null;
  net_bp?: number | null;
  t?: number | null;
  n?: number | null;
  folds?: LaneFold[];
  spread_bp?: number | null;
  price_bp?: number | null;
  fee_bp?: number | null;
  max_dd_pct?: number | null;
  fill_rate_ratio?: number | null;
  source?: string | null;
  as_of?: string | null;
  note?: string | null;
}

export interface LaneHealth {
  data_age_sec?: number | null;
  breaker?: string | null;
  note?: string | null;
  toxic?: boolean;
  toxic_reason?: string;
  updated_at?: string | null;
}

/** 晋升判定矩阵（passed/failed/labels/progress_pct/ready/as_of） */
export interface PromotionState {
  passed: string[];
  failed: string[];
  labels: Record<string, string>;
  progress_pct: number;
  ready: boolean;
  as_of?: string | null;
}

/** 晋升阈值：key → {threshold, label}；threshold 可能为 null（如 edge_verified） */
export interface PromotionCriteriaEntry {
  threshold: number | null;
  label: string;
}
export type PromotionCriteria = Record<string, PromotionCriteriaEntry>;

export interface LaneRisk {
  budget_pct?: number | null;
  max_symbol_exposure_pct?: number | null;
  max_net_exposure_pct?: number | null;
  daily_loss_stop_pct?: number | null;
  daily_loss_tripped?: boolean;
  daily_loss_ts?: string | null;
  daily_loss_reason?: string | null;
  [key: string]: unknown;
}

export interface LaneMeta {
  name?: string;
  note?: string;
  venue?: string;
  symbols?: string[];
  edge_source?: string;
  paper_account_id?: number | null;
  shadow_equity?: number | null;
  params?: Record<string, number>;
  [key: string]: unknown;
}

export interface LaneSummary {
  lane_id: string;
  mode: string; // "paper" | "live" | "disabled"
  status: string; // "active" | "paused" | "stopped"
  edge: LaneEdge | null;
  risk: LaneRisk | null;
  health: LaneHealth | null;
  promotion: PromotionState | null;
  promotion_cached?: PromotionState | null;
  meta: LaneMeta | null;
  updated_at: string | null;
  // [F61 阶段2 新增] 今日/近7天盈亏与库存敞口；任一盈亏取不到时为 null（前端显示「无数据」，不冒充 0）
  pnl_today_usd?: number | null;
  fills_today?: number | null;
  pnl_7d_usd?: number | null;
  fills_7d?: number | null;
  notional_7d?: number | null;
  inventory_usd?: number | null;
  net_exposure_usd?: number | null;
  unrealized_usd?: number | null;
  open_symbols?: number | null;
  data_age_sec?: number | null;
  last_activity_at?: string | null;
}

export interface LanesResponse {
  items: LaneSummary[];
  count: number;
  criteria: PromotionCriteria;
  as_of?: string | null;
}

// ═══════════════════════════════════════════════════════
// 组合总览 / 归因
// ═══════════════════════════════════════════════════════

export interface PortfolioSummary {
  equity: number;
  equity_source: string;
  pnl_today_usd: number;
  pnl_today_pct: number;
  pnl_7d_usd: number;
  pnl_7d_pct: number;
  fills_today: number;
  notional_today: number;
  budget_used_usd: number;
  budget_used_pct: number;
  lanes_active: number;
  lanes_total: number;
  breakers_active: number;
  lane_budgets: Record<string, number>;
  as_of: string;
}

/** 六维归因（单条维度记录） */
export interface AttributionDim {
  spread_bp: number;
  funding_bp: number;
  price_bp: number;
  fee_bp: number;
  slippage_bp: number;
  net_bp: number;
  net_usd: number;
  points_usd: number;
  n: number;
  notional: number;
}

export interface AttributionByLane extends AttributionDim {
  lane_id: string;
}

export interface Attribution {
  days: number;
  lane_id: string | null;
  total: AttributionDim;
  by_lane: AttributionByLane[];
  as_of?: string | null;
}

// ═══════════════════════════════════════════════════════
// 逐日序列
// ═══════════════════════════════════════════════════════

export interface DailyPoint {
  date: string;
  n: number;
  net_usd: number;
  points_usd: number;
}

export interface DailySeries {
  days: number;
  lane_id: string | null;
  series: DailyPoint[];
  as_of?: string | null;
}

// ═══════════════════════════════════════════════════════
// 影子期
// ═══════════════════════════════════════════════════════

export interface ShadowSymbolState {
  symbol: string;
  qty: number;
  avg_px: number;
  avg_mid: number;
  opened_ts: number;
  last_ts: number;
  quote_bid: number;
  quote_ask: number;
  quote_mid: number;
  quote_ts: number;
  toxic_streak: number;
  mid_hist?: number[];
  vol_baseline_bp?: number;
}

export interface ShadowStatus {
  lane_id: string;
  venue: string;
  symbols: string[];
  equity: number;
  account_id: number | null;
  maker_fee_bp: number;
  ticks: number;
  fills: number;
  flattens: number;
  last_tick_ts: number;
  last_error: string;
  states: Record<string, ShadowSymbolState>;
  // [F85] 复利/账户字段（前端「账户总览」卡片）
  compound_ratio?: number;
  fill_notional?: number;
  account_equity?: number | null;
  /** [F90] 孤儿持仓：已移出宇宙但运行态里仍有仓位的币种（正常为空） */
  orphan_inventory?: Record<string, number>;
  /** [F92] 本进程产能：窗口秒数、成交速率（现在跑多快）、各币已消费成交桶标签 */
  process_window_sec?: number;
  fills_per_hour?: number | null;
  spread_buckets?: Record<string, number>;
  /** [F95] 闸门拦截分布（进程内累计）+ 双边/单边/未挂 报价计数 */
  skip_counts?: Record<string, number>;
  side_counts?: Record<string, number>;
  as_of?: string | null;
}

/** [F91] 双账对账：运行态持仓 vs 账本重建持仓（ok=false 必须显性告警） */
export interface ReconcileRow {
  symbol: string;
  runtime_qty: number;
  ledger_qty: number;
  diff_qty: number;
  diff_usd: number;
  mark_px: number;
  ok: boolean;
}

export interface LaneReconcile {
  lane_id: string;
  venue?: string;
  since?: string | null;
  ok: boolean;
  checked: number;
  mismatches: ReconcileRow[];
  rows: ReconcileRow[];
  rt_only?: string[];
  error?: string;
  as_of?: string | null;
}

export interface ShadowReportDaily {
  day: string | null;
  net_bp: number | null;
  fills: number | null;
}

export interface ShadowReportPromotion {
  eligible?: boolean;
  reason?: string;
}

export interface ShadowReportPerSymbol {
  n: number;
  net_bp: number;
  spread_bp: number;
  price_bp: number;
  fee_bp: number;
  notional: number;
  net_usd: number;
}

/** [F75] 成交速率统计（时代口径）：per_symbol_hour = 笔/标的/小时 */
export interface FillRateStats {
  fills: number;
  symbols: number;
  span_hours: number;
  per_symbol_hour: number | null;
  first_ts?: string;
  last_ts?: string;
}

export interface FillRateRatio {
  ratio?: number | null;
  actual_per_symbol_hour?: number | null;
  baseline_per_symbol_hour?: number | null;
  [key: string]: unknown;
}

export interface FlattenStats {
  flattens?: number;
  fills?: number;
  flatten_share?: number | null;
  flatten_price_bp?: number | null;
  [key: string]: unknown;
}

export interface ShadowReport {
  lane_id: string;
  venue: string;
  window_days: number;
  fills: number;
  flattens: number;
  notional: number;
  spread_bp: number | null;
  price_bp: number | null;
  fee_bp: number | null;
  net_bp: number | null;
  net_usd: number | null;
  per_symbol: Record<string, ShadowReportPerSymbol>;
  daily: ShadowReportDaily[];
  maker_fee_bp: number;
  // [F75/F85] 速率/平仓/回撤/权益（做市产能视图数据源）
  fill_rate_ratio?: FillRateRatio | null;
  fill_rate_stats?: FillRateStats | null;
  flatten_stats?: FlattenStats | null;
  max_dd_pct?: number | null;
  equity?: number | null;
  // [F61 阶段2] promotion 现在与 lane 的 promotion 同形状（passed/failed/labels/progress_pct/ready/as_of）
  promotion?: PromotionState | null;
  as_of?: string | null;
}

// ═══════════════════════════════════════════════════════
// 持仓
// ═══════════════════════════════════════════════════════

export interface Position {
  lane_id: string;
  symbol: string;
  qty: number;
  /** [F61 阶段3] 后端由持仓符号推导的 side（long/short/flat）；前端直接用，不再用 qty 符号推导 */
  side?: string;
  notional_usd: number;
  avg_px: number;
  avg_mid: number;
  mark_px: number;
  unrealized_usd: number;
  opened_at: string | null;
  last_fill_at: string | null;
  hold_sec: number;
  fills: number;
  notional: number;
  spread_bp: number;
  price_bp: number;
  fee_bp: number;
  funding_bp: number;
  slippage_bp: number;
  net_bp: number;
  realized_usd: number;
  points_usd: number;
}

export interface PositionsResponse {
  items: Position[];
  count: number;
  open_count: number;
  net_exposure_usd: number;
  gross_exposure_usd: number;
  unrealized_usd: number;
  as_of: string;
}

// ═══════════════════════════════════════════════════════
// 机会
// ═══════════════════════════════════════════════════════

export type OpportunityKind = "market_making" | "funding_carry";
export type OpportunityConfidence = "measured" | "unverified";

export interface Opportunity {
  kind: OpportunityKind;
  symbol: string;
  venue: string;
  gross_bp: number;
  cost_bp: number;
  net_bp: number;
  theoretical_net_bp?: number | null;
  capacity_usd: number;
  confidence: OpportunityConfidence;
  executable: boolean;
  reason: string;
  note: string;
  direction?: string | null;
  // [F61 阶段2 新增，资金费 carry 专属] 数据质量标记 + 资金费统计
  suspect?: boolean;
  breakeven_days?: number | null;
  annualized_pct?: number | null;
  funding_mean_bp?: number | null;
  funding_positive_ratio?: number | null;
  funding_t?: number | null;
  funding_samples?: number | null;
  funding_days?: number | null;
  period_hours?: number | null;
  spot_leg_available?: boolean | null;
}

export interface OpportunitiesResponse {
  venue: string;
  items: Opportunity[];
  count: number;
  executable_count: number;
  suspect_count: number;
  filters: { min_days: number; tradable_only: boolean };
  as_of: string;
}

// ═══════════════════════════════════════════════════════
// 风险与资金
// ═══════════════════════════════════════════════════════

export interface RiskLimits {
  max_net_exposure_pct: number;
  max_symbol_exposure_pct?: number | null;
  daily_loss_stop_pct: number;
  [key: string]: unknown;
}

/** 熔断历史条目（risk/breakers.history 与 risk/summary.breaker_history） */
export interface BreakerHistoryEntry {
  lane_id: string;
  breaker: BreakerKind;
  trips: number;
  last_ts: string | null;
}

export interface RiskSummary {
  equity: number;
  equity_source: string;
  max_symbol: string | null;
  max_symbol_exposure_usd: number;
  max_symbol_exposure_pct: number;
  net_exposure_usd: number;
  gross_exposure_usd: number;
  net_exposure_pct: number;
  unrealized_usd: number;
  worst_day: string | null;
  worst_day_usd: number;
  daily_series: DailyPoint[];
  breaker_history: BreakerHistoryEntry[];
  breaker_history_total: number;
  limits: RiskLimits;
  as_of: string;
}

export type BreakerKind = "data" | "fee" | "daily_loss" | "toxic_flow";
export type BreakerState = "ok" | "tripped";

export interface BreakerRow {
  lane_id: string;
  breaker: BreakerKind;
  state: BreakerState;
  ts: string | null;
  reason: string;
}

export interface BreakersResponse {
  items: BreakerRow[];
  tripped: BreakerRow[];
  history: BreakerHistoryEntry[];
  history_total_30d: number;
  as_of: string;
}

// ═══════════════════════════════════════════════════════
// 配置
// ═══════════════════════════════════════════════════════

export interface FeeRow {
  exchange: string;
  maker_bp: number;
  taker_bp: number;
  maker_pct: number | null;
  taker_pct: number | null;
  round_trip_maker_bp: number;
  round_trip_taker_bp: number;
  min_notional_usd: number | null;
  source: string;
}

export interface FeesResponse {
  items: FeeRow[];
  count: number;
  as_of: string;
}

export interface LaneConfig {
  lane_id: string;
  mode: string;
  params: Record<string, number>;
  limits: Record<string, number>;
  editable_keys: string[];
  source: string;
  as_of: string;
}

export interface ConfirmationResponse {
  ok: boolean;
  lane_id?: string;
  breaker?: string;
  as_of: string;
}

// ═══════════════════════════════════════════════════════
// [F61 阶段3] 数据源健康 / 资金池 / 组合级熔断演练
// ═══════════════════════════════════════════════════════

export type DataSourceKind = "orderbook" | "trades" | "funding" | "spot";

export interface DataSourceEntry {
  source: DataSourceKind;
  exchange: string | null;
  rows: number;
  last_ts: number | null; // epoch ms
  age_sec: number | null;
  stale: boolean;
  note?: string | null;
}

export interface DataSourcesResponse {
  items: DataSourceEntry[];
  count: number;
  stale_count: number;
  as_of: string;
}

export interface CapitalPoolAccount {
  account_id: number;
  name: string;
  total_equity: number;
  available_balance: number;
  frozen_balance: number;
  status: string;
  preset: string | null;
}

export interface CapitalPoolResponse {
  items: CapitalPoolAccount[];
  count: number;
  total_equity: number;
  as_of: string;
}

export interface DrillSkippedLane {
  lane_id: string;
  reason: string;
}

export interface DrillResponse {
  ok: boolean;
  enabled: boolean;
  affected: string[];
  skipped: DrillSkippedLane[];
  as_of: string;
}

// ═══════════════════════════════════════════════════════
// [F61 阶段4] 统一模拟账户视图
// ═══════════════════════════════════════════════════════

export interface UnifiedAccount {
  account_id: number;
  name: string;
  total_equity: number;
  available_balance: number;
  frozen_balance: number;
  realized_pnl: number;
  status: string;
  preset: string | null;
}

export interface UnifiedExchange {
  exchange: string;
  allocated_usd: number;
  available_usd: number;
  frozen_usd: number;
  /** 各策略配额（%） */
  strategy_limits: Record<string, number>;
  /** 各策略预算（USD） */
  strategy_budgets: Record<string, number>;
}

export interface UnifiedStrategy {
  strategy_type: string;
  net_usd: number;
  pnl_usd: number;
  fee_usd: number;
  rebate_usd: number;
  slippage_usd: number;
  capital_usd: number;
  entries: number;
  fills?: number | null;
}

export interface UnifiedExposure {
  mm_notional_usd: number;
  mm_notional_pct: number;
}

export interface UnifiedAccountResponse {
  account: UnifiedAccount;
  exchanges: UnifiedExchange[];
  strategies: UnifiedStrategy[];
  positions: { mm: Position[]; count: number };
  exposure: UnifiedExposure;
  window_days: number;
  as_of: string;
}

// ═══════════════════════════════════════════════════════
// 查询函数
// ═══════════════════════════════════════════════════════

export const tradingApi = {
  // 车道
  lanes: () => apiRequest<LanesResponse>("/trading/lanes"),
  lane: (laneId: string) => apiRequest<LaneSummary>(`/trading/lanes/${laneId}`),
  promotion: (laneId: string) =>
    apiRequest<{ lane_id: string; mode: string; promotion: PromotionState }>(
      `/trading/lanes/${laneId}/promotion`
    ),
  seedLanes: () => apiRequest<{ seeded: number; items: LaneSummary[] }>("/trading/lanes/seed", { method: "POST" }),

  // 影子期
  shadowStatus: (laneId: string) => apiRequest<ShadowStatus>(`/trading/lanes/${laneId}/shadow`),
  shadowReport: (laneId: string, days: number) =>
    apiRequest<ShadowReport>(`/trading/lanes/${laneId}/shadow/report?days=${days}`),
  shadowTick: (laneId: string) =>
    apiRequest<ShadowStatus>(`/trading/lanes/${laneId}/shadow/tick`, { method: "POST" }),
  /** [F91] 双账对账（运行态 vs 账本重建） */
  laneReconcile: (laneId: string) =>
    apiRequest<LaneReconcile>(`/trading/lanes/${laneId}/reconcile`),

  // 组合
  portfolioSummary: () => apiRequest<PortfolioSummary>("/trading/portfolio/summary"),
  attribution: (days: number, laneId?: string) =>
    apiRequest<Attribution>(`/trading/portfolio/attribution?days=${days}${laneId ? `&lane_id=${encodeURIComponent(laneId)}` : ""}`),
  dailySeries: (days: number, laneId?: string) =>
    apiRequest<DailySeries>(`/trading/portfolio/series?days=${days}${laneId ? `&lane_id=${encodeURIComponent(laneId)}` : ""}`),

  // 持仓
  positions: (days = 30, laneId?: string, symbol?: string) => {
    const q = new URLSearchParams({ days: String(days) });
    if (laneId) q.set("lane_id", laneId);
    if (symbol) q.set("symbol", symbol);
    return apiRequest<PositionsResponse>(`/trading/positions?${q.toString()}`);
  },

  // 机会
  opportunities: (venue = "asterdex", limit = 40, minDays = 7, tradableOnly = true) => {
    const q = new URLSearchParams({
      venue: encodeURIComponent(venue),
      limit: String(limit),
      min_days: String(minDays),
      tradable_only: String(tradableOnly),
    });
    return apiRequest<OpportunitiesResponse>(`/trading/opportunities?${q.toString()}`);
  },

  // 风险
  riskSummary: (days = 30) => apiRequest<RiskSummary>(`/trading/risk/summary?days=${days}`),
  riskBreakers: () => apiRequest<BreakersResponse>("/trading/risk/breakers"),
  resetBreaker: (laneId: string, breaker: string) =>
    apiRequest<ConfirmationResponse>("/trading/risk/breakers/reset", {
      method: "POST",
      body: JSON.stringify({ lane_id: laneId, breaker }),
    }),

  // [F61 阶段3] 数据源健康
  configDataSources: () => apiRequest<DataSourcesResponse>("/trading/config/datasources"),

  // [F61 阶段3] 资金池（各模拟账户余额）
  capitalPool: () => apiRequest<CapitalPoolResponse>("/trading/capital/pool"),

  // [F61 阶段3] 组合级熔断演练（真写 lane health.drill；仅 paper 生效，实盘进 skipped）
  riskDrill: (enable: boolean, lanes?: string[], reason?: string) =>
    apiRequest<DrillResponse>("/trading/risk/drill", {
      method: "POST",
      body: JSON.stringify({
        enable,
        ...(lanes && lanes.length ? { lanes } : {}),
        ...(reason ? { reason } : {}),
      }),
    }),

  // [F61 阶段4] 统一模拟账户视图
  unifiedAccount: (accountId?: number, days = 30) => {
    const q = new URLSearchParams({ days: String(days) });
    if (accountId) q.set("account_id", String(accountId));
    return apiRequest<UnifiedAccountResponse>(`/trading/account/unified?${q.toString()}`);
  },

  // 配置
  configFees: () => apiRequest<FeesResponse>("/trading/config/fees"),
  laneConfig: (laneId: string) => apiRequest<LaneConfig>(`/trading/config/lanes/${laneId}`),
  updateLaneConfig: (laneId: string, params: Record<string, number>) =>
    apiRequest<ConfirmationResponse>(`/trading/config/lanes/${laneId}`, {
      method: "PATCH",
      body: JSON.stringify({ params }),
    }),

  // 车道模式/状态
  setMode: (laneId: string, mode: string) =>
    apiRequest<LaneSummary>(`/trading/lanes/${laneId}/mode`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  setStatus: (laneId: string, status: string) =>
    apiRequest<LaneSummary>(`/trading/lanes/${laneId}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),
};

// 独立函数导出（便于个别页面按需 import）
export const getTradingLanes = () => tradingApi.lanes();
export const getTradingLane = (laneId: string) => tradingApi.lane(laneId);
export const getTradingPromotion = (laneId: string) => tradingApi.promotion(laneId);
export const getShadowStatus = (laneId: string) => tradingApi.shadowStatus(laneId);
export const getShadowReport = (laneId: string, days: number) => tradingApi.shadowReport(laneId, days);
export const getPortfolioSummary = () => tradingApi.portfolioSummary();
export const getAttribution = (days: number, laneId?: string) => tradingApi.attribution(days, laneId);
export const getDailySeries = (days: number, laneId?: string) => tradingApi.dailySeries(days, laneId);
export const getPositions = (days = 30, laneId?: string, symbol?: string) => tradingApi.positions(days, laneId, symbol);
export const getOpportunities = (venue = "asterdex", limit = 40, minDays = 7, tradableOnly = true) =>
  tradingApi.opportunities(venue, limit, minDays, tradableOnly);
export const getRiskSummary = (days = 30) => tradingApi.riskSummary(days);
export const getRiskBreakers = () => tradingApi.riskBreakers();
export const getConfigFees = () => tradingApi.configFees();
export const getLaneConfig = (laneId: string) => tradingApi.laneConfig(laneId);
export const getConfigDataSources = () => tradingApi.configDataSources();
export const getCapitalPool = () => tradingApi.capitalPool();
export const getRiskDrill = (enable: boolean, lanes?: string[], reason?: string) =>
  tradingApi.riskDrill(enable, lanes, reason);
export const getUnifiedAccount = (accountId?: number, days = 30) =>
  tradingApi.unifiedAccount(accountId, days);
