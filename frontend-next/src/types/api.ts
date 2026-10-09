/**
 * 领域类型 — 与后端 FastAPI 响应对齐（R4 类型化整改）
 *
 * 约定：
 *  - 字段以后端 Pydantic schema 为源，`?` 表示可空/可能缺省，不臆造必填；
 *  - 新增类型一律放本文件；页面组件禁止再写 `(x: any)`。
 *  - 本文件从 lib/api.ts 迁移而来（原 Account/Position 等内联定义已移至此）。
 */

// ═══ 账户 ═══

export interface Account {
  id: number;
  name: string;
  account_type: string;
  current_cash: number;
  frozen_cash: number;
  initial_capital: number;
  is_active: boolean;
  auto_trading_enabled: boolean;
  trading_mode: string;
  selected_exchange: string;
  binance_market_type?: string;
  llm_config_id_deep?: number | null;
  exchange?: string;
  keys_configured?: boolean;
  llm_config_name?: string;
  llm_config_name_deep?: string;
  llm_config_id?: number | null;
  model?: string;
  base_url?: string;
  api_key_set?: boolean;
  has_mainnet_wallet?: boolean;
  wallet_address?: string;
}

// ═══ 模拟交易 ═══

export interface PaperBalance {
  account_id: number;
  initial_balance: number;
  total_equity: number;
  available_balance?: number;
  available_cash?: number;
  frozen_margin?: number;
  used_margin?: number;
  unrealized_pnl: number;
  realized_pnl: number;
  total_pnl: number;
  total_fee_paid?: number;
  return_pct: number;
}

export interface Position {
  id: number;
  account_id: number;
  symbol: string;
  side: string;
  entry_price: number;
  mark_price?: number;
  current_price?: number;
  size?: number;
  quantity: number;
  filled_quantity?: number;
  leverage: number;
  unrealized_pnl: number;
  margin: number;
  trade_nature: string;
  status: string;
  opened_at: string;
  closed_at?: string;
  close_reason?: string;
  tp_price?: number;
  sl_price?: number;
  pnl_pct?: number;
  strategy_id?: string;
  fee?: number;
  health_score?: number;
  add_count?: number;
  timeframe_tier?: "short" | "mid" | "long" | null;
  // 持仓时限（tier 复审点 + AI 可延长），见 backend/services/position_hold_time.py
  hold_age_hours?: number | null;
  max_hold_hours?: number | null;
  hold_remaining_hours?: number | null;
  hold_progress_pct?: number | null;
  hold_expired?: boolean;
  hold_near_timeout?: boolean;
  hold_ai_extended?: boolean;
  hold_ai_reviewable?: boolean;
  review_hold_hours?: number | null;
  absolute_cap_hours?: number | null;
  extendable_hours?: number | null;
  extend_step_hours_min?: number | null;
  extend_step_hours_max?: number | null;
}

export interface PaperOrder {
  id: number;
  account_id: number;
  symbol: string;
  side: string;
  order_type: string;
  status: string;
  price: number;
  quantity: number;
  filled_price?: number;
  filled_quantity?: number;
  leverage?: number;
  pnl?: number;
  fee?: number;
  close_reason?: string;
  trade_nature?: string;
  strategy_id?: string;
  created_at: string;
  filled_at?: string;
  entry_price?: number;
}

export interface PaperSummary {
  total_trades: number;
  total_orders?: number;
  total_closes?: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  realized_pnl?: number;
  total_fees?: number;
  return_pct: number;
  profit_factor: number;
  max_drawdown_pct?: number;
  avg_win?: number;
  avg_loss?: number;
  open_positions?: number;
  open_losing?: number;
  open_winning?: number;
}

/**
 * 模拟盘一屏数据（`GET /api/paper/dashboard/{id}`）。
 * [2026-09-18 前端刷新慢治理] 四节口径与单端点一致（后端同一份 helper + 对拍测试），
 * 前端用它把「4 个端点各 5s 轮询」收敛为「1 个请求 5s 轮询」。
 * 未初始化账户：`balance` 为 null 且带 `warnings`。
 */
export interface PaperDashboard {
  account_id: number;
  balance: PaperBalance | null;
  positions: Position[];
  orders: PaperOrder[];
  summary: PaperSummary;
  positions_status: "open" | "closed";
  generated_at: string;
  warnings?: string[];
}

// ═══ 全自动会话 ═══

export interface SessionStatus {
  session_id: string;
  status: string;
  symbols: string[];
  active_count: number;
  trading_mode: string;
  account_id?: number;
  account_name?: string;
  paper_account_id?: number;
  paper_account_name?: string;
  trading_account_id?: number;
  total_pnl?: number;
  total_trades?: number;
  win_rate?: number;
  started_at?: string;
  stopped_at?: string;
  // 2026-07-20：补齐卡片展示与编辑所需字段
  auto_coin_enabled?: boolean;
  auto_coin_symbols?: string[];
  auto_coin_max_slots?: number; // 5~10，短线 AI，默认 5
  auto_coin_mid_enabled?: boolean;
  auto_coin_mid_max_slots?: number; // 1~5，中线 AI，默认 3
  auto_coin_mid_symbols?: string[];
  fixed_symbols_by_tier?: { short?: string[]; mid?: string[]; long?: string[] };
  backup_pool?: string[];
  risk_level?: string;
  risk_mode?: string;
  active_exchange?: string;
  arb_enabled?: boolean;
  paper_account_mode?: string;
  max_concurrent_strategies?: number;
  max_total_drawdown_pct?: number;
  daily_loss_limit_pct?: number;
  total_strategies_created?: number;
  created_at?: string;
}

/** 三周期单 tier 状态（/api/full-auto/tier-status/{session}）— 与后端契约一致 */
export interface TierInfo {
  label?: string;
  strategy_count?: number;
  active_count?: number;
  paused_count?: number;
  position_count?: number;
  position_count_mid?: number;
  position_count_long_only?: number;
  margin_used?: number;
  budget_allocated?: number;
  budget_max?: number;
  budget_utilization?: number;
  symbols?: string[];
  [key: string]: unknown;
}

export interface TierStatus {
  session_id?: string;
  total_equity?: number;
  tier_budget_allocation?: Record<"short" | "mid" | "long", number>;
  lanes?: {
    fixed_long?: string[];
    fixed_mid?: string[];
    fixed_short?: string[];
    ai_mid?: string[];
    auto_coin?: string[];
  };
  fixed_symbols_by_tier?: { short?: string[]; mid?: string[]; long?: string[] };
  auto_coin_mid_enabled?: boolean;
  auto_coin_mid_max_slots?: number;
  tiers?: Record<"short" | "mid" | "long", TierInfo>;
}

/** 三周期活动流单条（/api/full-auto/tier-activity/{session}） */
export interface TierActivityItem {
  id?: string;
  time: string;
  symbol: string;
  action: string;
  executed?: boolean;
  /** 风控放行/拦截结果（evaluate_verdict.allowed） */
  allowed?: boolean | null;
  confidence?: number;
  /** 风控拦截原因（evaluate_verdict.reason） */
  block_reason?: string;
  source?: string;
  reasoning?: string;
  direction?: string;
  tier_tag?: string;
  lane_note?: string;
  /** 去重窗口内相同决策的重复次数（后端聚合） */
  repeat?: number;
}

export interface TierActivity {
  short?: TierActivityItem[];
  mid?: TierActivityItem[];
  long?: TierActivityItem[];
}

/** P0-D：冷却快照（/api/full-auto/cooldowns/{session}） */
export interface CooldownFullCloseRow {
  symbol: string;
  tier: "short" | "mid" | "long" | "default";
  closed_side: "long" | "short";
  started_at?: string;
  multiplier?: number;
  same_dir_remain_sec: number;
  same_dir_remain: string;
  same_dir_reason: string;
  flip_remain_sec: number;
  flip_remain: string;
  flip_reason: string;
}

export interface CooldownReduceRow {
  symbol: string;
  side: string;
  tier: string;
  started_at?: string;
  multiplier?: number;
  remain_sec: number;
  remain: string;
  reason: string;
}

export interface CooldownAiReverseRow {
  symbol: string;
  started_at?: string;
  remain_sec: number;
  remain: string;
  reason: string;
}

export interface CooldownSnapshot {
  generated_at?: string;
  account_id?: number;
  session_id?: string;
  trading_account_id?: number;
  full_close?: CooldownFullCloseRow[];
  reduce?: CooldownReduceRow[];
  ai_reverse?: CooldownAiReverseRow[];
  tier_blocked?: Record<"short" | "mid" | "long", string[]>;
  /** P0-E：周期级日亏熔断快照（只读） */
  tier_circuit?: Record<"short" | "mid" | "long", TierCircuitState>;
}

export interface TierCircuitState {
  tier: "short" | "mid" | "long";
  day?: string;
  loss?: number | null;
  budget?: number | null;
  budget_pct?: number;
  equity?: number | null;
  frozen?: boolean;
  reason?: string;
  frozen_at?: number | null;
}

/** P0-D：门禁拦截事件流（/api/full-auto/events/{session}） */
export interface SessionEventItem {
  time?: string;
  event?: string;
  detail?: string;
  severity?: string;
  trace_id?: string;
}

export interface SessionEventsResponse {
  session_id?: string;
  mode?: string;
  total?: number;
  events?: SessionEventItem[];
}

/** 活跃策略（/api/strategies?account_id=...） */
export interface StrategyRecord {
  strategy_id?: string;
  symbol?: string;
  status?: string;
  tier?: string;
  [key: string]: unknown;
}

// ═══ 实盘交易（/api/live/*，R4 第二批类型化） ═══

export interface LiveBalance {
  total_equity?: number;
  available_balance?: number;
  unrealized_pnl?: number;
  frozen_margin?: number;
  position_count?: number;
  keys_configured?: boolean;
}

export interface LivePosition {
  symbol: string;
  side: string;
  entry_price?: number;
  mark_price?: number;
  last_price?: number;
  size: number;
  leverage?: number;
  margin?: number;
  unrealized_pnl?: number;
  liquidation_price?: number | null;
  margin_type?: string | null;   // cross/isolated
  isolated?: boolean | null;
  maint_margin?: number;
  margin_ratio?: number;         // 保证金率%
  tier?: string[] | null;        // 周期归属 short/mid/long
  tp_price?: number | null;      // 交易所条件单止盈价（无则未挂）
  sl_price?: number | null;      // 交易所条件单止损价（无则未挂）
}

export interface LiveOrder {
  symbol: string;
  side: string;
  type?: string;
  price?: number;
  amount?: number;
  filled?: number;
  status?: string;
}

/** live 下单/平仓的统一返回 */
export interface LiveOrderResult {
  success?: boolean;
  symbol?: string;
  side?: string;
  result?: { message?: string };
}

// ═══ Asterdex 实盘真实收入账本（GET /api/live/asterdex/points/{account_id}） ═══
// [2026-09-03] 替代已结束的 Stage 6 Rh 积分面板：只展示交易所真实流水 + Trade & Earn 门槛进度。

export interface AsterLedgerAsset {
  asset?: string;
  wallet_balance?: number;
  available?: number;
  collateral_ratio?: number | null;
  margin_available?: boolean;
  usd_value?: number;
}

export interface AsterLedger {
  window_days?: number;
  account?: {
    multi_assets_mode?: boolean | null;
    total_margin_usd?: number;
    usdf_balance?: number;
    usdf_share?: number;
    eligible_collateral_usd?: number;
    assets?: AsterLedgerAsset[];
  };
  fees?: {
    maker_rate?: number;
    taker_rate?: number;
    rate_source?: "api" | "schedule";
    commission_paid_usd?: number;
    funding_fee_usd?: number;
    realized_pnl_usd?: number;
  };
  execution?: {
    trades?: number;
    notional_usd?: number;
    maker_notional_usd?: number;
    maker_ratio?: number | null;
    maker_ratio_count?: number | null;
    fee_saved_est_usd?: number;
    symbols?: string[];
    symbols_truncated?: boolean;
  };
  trade_and_earn?: {
    active?: boolean;
    week_start?: string;
    week_end?: string;
    volume_usd?: number;
    volume_threshold_usd?: number;
    volume_progress?: number;
    active_days?: number;
    active_days_threshold?: number;
    eligible_now?: boolean;
    blockers?: string[];
    usdf_counted_usd?: number;
    usdf_cap_usd?: number;
    reference_weekly_reward_usd?: number;
    reference_apy?: { deposit?: number; trading?: number; note?: string };
    reference?: boolean;
    requires_multi_assets_mode?: boolean;
    source_url?: string;
  };
  rewards?: {
    usdf_received?: number;
    by_type?: Record<string, Record<string, number>>;
    count?: number;
    note?: string;
  };
  programs?: { stage6?: { status?: string; note?: string } };
  errors?: string[];
}

export interface AsterLedgerResponse {
  keys_configured?: boolean;
  exchange?: string;
  message?: string;
  ledger?: AsterLedger | null;
  policy?: {
    maker_first?: boolean;
    maker_first_tiers?: string[];
    maker_timeout_s?: number;
    enabled?: boolean;
    reason?: string;
  };
  fetched_at?: string;
}

// ═══ AI 决策（/api/atas/decisions、/api/arena/model-chat） ═══

export interface AtasDecision {
  id?: number | string;
  created_at?: string;
  symbol?: string;
  operation?: string;
  target_portion?: number;
  executed?: boolean;
  reasoning?: string;
}

export interface AiDecisionEntry {
  id?: number | string;
  operation?: string;
  action?: string;
  decision?: string;
  confidence?: number;
  tier?: string;
  trade_nature?: string;
  agent_source?: string;
  decision_time?: string;
  created_at?: string;
  symbol?: string;
  executed?: boolean;
  reasoning?: string;
  stop_loss_price?: number | null;
  take_profit_price?: number | null;
  leverage?: number | null;
  short_bias?: string | number;
  mid_bias?: string | number;
  long_bias?: string | number;
}

/** /api/full-auto/tick-intervals */
export interface TickIntervals {
  intervals?: { short?: number; mid?: number; long?: number; coordinator?: number };
  labels?: Record<string, string>;
  mid_mode?: string;
  brain_mode?: string;
  mid_open_authority?: string;
  scalp_open_disabled?: boolean;
  chart_required?: boolean;
  long_loop_sec?: number;
  long_entry_sec?: number;
  long_decision?: string;
}
