/**
 * 套利系统 API 客户端 — Arbitrage + Rebate Arb 统一接口
 *
 * 遵循 exchangeApi.ts 模式：纯 fetch + 类型定义，无 axios
 */

/** Safe number formatter — prevents toFixed crash on undefined/null */
export function fmt(val: any, digits: number = 2): string {
  const n = Number(val)
  return Number.isFinite(n) ? n.toFixed(digits) : '0'.padEnd(digits ? digits + 2 : 1, digits ? '0' : '').slice(0, digits ? digits + 2 : 1)
}

export function num(val: any): number {
  const n = Number(val)
  return Number.isFinite(n) ? n : 0
}

// ════════════════════════════════════════════════════════
//  Arbitrage 系统类型
// ════════════════════════════════════════════════════════

export interface ArbitrageStatus {
  engine_enabled: boolean
  scanner_scan_count: number
  cached_opportunities: number
  circuit_breaker_active: boolean
}

export interface ArbitragePosition {
  position_id: string
  symbol: string
  strategy: string
  long_size: number
  short_size: number
  delta: number
  accumulated_funding: number
  status: string
  entry_time: string | null
  close_time: string | null
  close_reason: string | null
}

export interface ArbitrageOpportunity {
  opportunity_id: string
  symbol: string
  strategy: string
  expected_annual_yield: number
  risk_score: number
  confidence: number
  current_rate: number
  rate_24h_avg: number
}

export interface FeeSchedule {
  maker_rate: number
  taker_rate: number
  withdrawal_fee_usd: number
  slippage_bps: number
}

// ════════════════════════════════════════════════════════
//  Rebate Arb 系统类型
// ════════════════════════════════════════════════════════

export interface RebateStatus {
  engine_enabled: boolean
  mode: string
  scan_count: number
  execution_count: number
  active_positions: number
  total_rebate_pnl: number
  wash_trade_safe: boolean
  next_safe_interval_sec: number
}

export interface RebateOpportunity {
  strategy_type: string
  is_viable: boolean
  expected_monthly_value: number
  required_volume_usd: number
  risk_score: number
  confidence: number
  volume_value_ratio: number
  details: Record<string, any>
}

export interface RebatePosition {
  position_id: string
  strategy_type: string
  source_exchange: string
  target_exchange: string | null
  symbol: string
  side?: string
  size_coins?: number | null
  size_coins_display?: string | null
  leverage?: number
  side_a_size: number
  side_b_size: number
  entry_price?: number | null
  mark_price?: number | null
  bid?: number
  ask?: number
  spread_bps?: number
  current_pnl: number
  pnl_pct?: number
  funding_pnl?: number
  funding_rate?: number
  accumulated_rebate: number
  accumulated_points: number
  hold_duration_hours: number
  status: string
  paper_mode: boolean
  entry_time: number
  mtm_updated_at?: number
  open_cost?: Record<string, unknown>
  open_fees_paid?: number
  price_source?: string
  quote_exchange?: string
  execution_phase?: string
  rh_optimization_mode?: string
  rh_optimizer?: Record<string, unknown>
  rh_metrics?: {
    estimated_rh?: number
    round_volume_usd?: number
    estimated_cost_usd?: number
    rh_per_fee_usd?: number
    rh_per_margin_hour?: number
    round_quality_score?: number
    safety_score?: number
    hold_seconds?: number
    combined_multiplier?: number
    // ── Stage 6 净 EV 模型字段 ──
    points_value_usd?: number
    net_ev_usd?: number
    gross_fee_usd?: number
    funding_cost_usd?: number
    formula_version?: string
    stage6?: {
      trading_points?: number
      position_points?: number
      asset_points?: number
      pnl_points?: number
      team_boost?: number
      maker_ratio?: number
      maker_volume_usd?: number
      taker_volume_usd?: number
      points_value_usd?: number
      net_ev_usd?: number
      valuation_speculative?: boolean
    }
  }
  paper_ab_test_matrix?: Array<Record<string, number | string>>
  wash_trade_check?: Record<string, unknown>
  position_value?: number
  margin_usd?: number
  symbol_boost?: number
  rh_multiplier_stack?: Record<string, number>
  rh_target_hours?: number
  rh_hold_remaining_minutes?: number
  rh_time_bonus_active?: boolean
  rh_hold_progress_pct?: number
  estimated_round_rh?: number
  points_maximization_mode?: boolean
}

export interface ExchangeIncentiveSummary {
  exchange: string
  is_connected: boolean
  fee_tier: {
    tier_name: string
    maker_rate: number
    taker_rate: number
    rebate_rate: number
    effective_taker_cost: number
    net_maker_rate: number
  }
  points: {
    points_balance: number
    points_multiplier: number
    daily_points_rate: number
    airdrop_eligible: boolean
    estimated_airdrop_value: number
    qualification_pct: number
  }
  rebate: {
    current_rebate_rate: number
    projected_weekly_rebate: number
  }
  total_estimated_monthly_value: number
}

export interface CapitalAllocation {
  total_equity: number
  total_used: number
  allocations: Record<string, number>
  used: Record<string, number>
  utilization: Record<string, number>
  rebate_available: number
  total_utilization_pct: number
}

export interface WashTradeStatus {
  is_safe: boolean
  next_safe_interval_sec: number
  daily_volume_usd: number
  last_trade_ts: number
  trade_count_today: number
  risk_level: string
}

export interface RebateAnalytics {
  total_trades: number
  win_rate: number
  total_pnl: number
  total_rebate: number
  total_points: number
  net_pnl: number
  by_strategy: Record<string, { count: number; pnl: number; rebate: number }>
}

export interface RuleSyncGateState {
  rebate_pause: boolean
  v3_pause: boolean
  paused_strategies: string[]
  pause_reason: string
  allow_manual_override: boolean
  requires_code_change: boolean
  paused_at?: number | null
  is_rebate_paused: boolean
  is_v3_paused: boolean
}

export interface WashTradeTimelineItem {
  id: number
  ts: number
  exchange: string
  strategy_type?: string
  size_usd: number
  risk_score: number
  is_safe: boolean
  reason?: string
  metadata?: Record<string, any>
}

export interface UnifiedPosition {
  id: string
  source: 'rebate' | 'rebate_memory' | 'v3' | string
  strategy_type: string
  symbol: string
  exchange_a?: string | null
  exchange_b?: string | null
  side_a_size: number
  side_b_size: number
  notional_usd: number
  pnl: number
  rebate: number
  points: number
  status: string
  paper_mode: boolean
  entry_time?: number | string | null
  close_time?: number | string | null
  metadata?: Record<string, any>
}

export interface EvolutionProposal {
  id?: number
  source?: string
  strategy_type: string
  severity: 'low' | 'medium' | 'high' | string
  title: string
  change: Record<string, any>
  requires_manual_live_confirm: boolean
  related_event_id?: number | null
}

export interface RuleSource {
  source_id: string
  exchange: string
  rule_type: string
  title: string
  url: string
  affected_strategies: string[]
  auto_pause_enabled: boolean
}

export interface RuleChangeEvent {
  id: number
  source_id: string
  exchange: string
  rule_type: string
  severity: string
  affected_strategies: string[]
  diff_summary?: string
  analysis?: Record<string, any>
  status: string
  auto_pause_applied: boolean
  requires_code_change: boolean
  created_at?: string | null
}

export interface RuleSyncSchedulerStatus {
  enabled: boolean
  job_id: string
  interval_seconds: number
  registered: boolean
  next_run_time?: string | null
}

export interface ArbitragePaperExchangeBalance {
  exchange: string
  allocated_usd: number
  available_usd: number
  frozen_usd: number
  asset_balances: Record<string, number>
  strategy_limits: Record<string, number>
}

export interface ArbitragePaperLedger {
  id: number
  exchange?: string | null
  action: string
  amount_usd: number
  balance_after?: number | null
  strategy_type?: string | null
  related_position_id?: string | null
  note?: string | null
  metadata?: Record<string, any>
  position_details?: {
    position_id?: string
    symbol?: string
    strategy_type?: string
    source_exchange?: string
    entry_time?: number | null
    close_time?: number | null
    hold_seconds?: number | null
    hold_hours?: number | null
    side_a_size?: number | null
    margin_usd?: number | null
    leverage?: number | null
    side?: string | null
    rh_earned?: number | null
    estimated_round_rh?: number | null
    symbol_boost?: number | null
    rh_optimization_mode?: string | null
    total_pnl?: number | null
    total_rebate?: number | null
    total_points?: number | null
    close_reason?: string | null
    rh_metrics?: Record<string, any>
  }
  created_at?: string | null
}

export interface ArbitragePaperAccount {
  id: number
  name: string
  owner_account_id?: number | null
  owner_account_name?: string | null
  total_equity: number
  available_balance: number
  frozen_balance: number
  realized_pnl: number
  estimated_points_value: number
  risk_profile: string
  allocation_preset?: string | null
  status: string
  metadata?: Record<string, any>
  exchange_balances: Record<string, ArbitragePaperExchangeBalance>
  ledger?: ArbitragePaperLedger[]
  trader_profile?: ArbitrageStartValidation['trader_profile']
}

export interface BindableArbitrageTrader {
  trader_account_id: number
  trader_name: string
  profile_id: number
  enabled_strategies: string[]
  arbitrage_paper_account_id?: number | null
  strategy_llm_config_id: number
  execution_llm_config_id: number
  available: boolean
  bound_to_this_account: boolean
}

export interface ArbitragePaperExchangeDashboard {
  exchange: string
  allocated_usd: number
  available_usd: number
  frozen_usd: number
  used_usd: number
  utilization_pct: number
  strategy_limits: Record<string, number>
  active_positions: number
  position_notional_usd: number
  unrealized_pnl: number
  accumulated_points: number
  points_earned_total: number
  estimated_value_usd: number
  risk_status: string
  recent_ledger: ArbitragePaperLedger[]
  status: string
}

export interface ArbitragePaperTradeRecord {
  position_id: string
  symbol: string
  strategy_type: string
  exchange: string
  side: string
  leverage: number
  margin_usd: number
  notional_usd: number
  status: string
  opened_at: number | null
  closed_at: number | null
  hold_hours: number | null
  realized_pnl: number
  fees_usd: number
  points_earned: number
  estimated_round_rh?: number | null
  close_reason?: string | null
}

export interface ArbitragePaperDashboard {
  account_id: number
  account: ArbitragePaperAccount
  session: ArbitragePaperSessionStatus
  summary: {
    total_equity: number
    available_balance: number
    frozen_balance: number
    realized_pnl: number
    estimated_points_value: number
    total_points_earned?: number
    active_positions: number
    position_notional_usd: number
    unrealized_pnl: number
    total_fees_paid?: number
    total_rebates_received?: number
    total_slippage_cost?: number
    status: string
    net_experiment_pnl?: number
    cash_per_point?: number | null
    recovery_mode?: boolean
  }
  exchanges: ArbitragePaperExchangeDashboard[]
  positions: RebatePosition[]
  s8_report?: {
    active: boolean
    mode: string
    recommendation: string
    positions: Array<{
      position_id?: string
      symbol?: string
      mode?: string
      estimated_round_rh?: number
      rh_metrics?: RebatePosition['rh_metrics']
      paper_ab_test_matrix?: Array<Record<string, number | string>>
    }>
    last_closed: Array<{
      position_id: string
      points: number
      pnl: number
      rebate: number
      hold_hours: number
      rh_per_hold_hour: number
      close_reason?: string | null
    }>
    cumulative_points?: number
    cumulative_points_value_usd?: number
    experiment_metrics?: {
      cash_per_point?: number | null
      samples?: number
      recovery_mode?: boolean
      recommended_mode?: string | null
      speculative_discount_learned?: number
      realized_cash_pnl?: number
      points_value_learned_discount?: number
      net_experiment_pnl?: number
      paper_stop_loss_notional_pct?: number
    }
    learning_memory?: {
      engine_status?: 'recovery_blocked' | 'collecting' | 'learning_active' | string
      status_note?: string
      updated_at?: string | null
      samples?: number
      lookback_days?: number
      learned?: {
        speculative_discount?: number | null
        stage6_hold_default_seconds?: number | null
        stage6_hold_default_hours?: number | null
        neutral_macro_position_scale?: number | null
      }
      hold_buckets?: Array<{
        label: string
        samples?: number
        score_per_hour?: number | null
        is_best?: boolean
      }>
      recent_rounds?: Array<{
        symbol?: string
        pnl_usd?: number
        points?: number
        hold_hours?: number
        direction?: string
        direction_correct?: boolean
        mode?: string
        created_at?: string | null
      }>
      memory_sources?: string[]
    }
    wash_safety: {
      current_daily_volume_usd?: number
      max_daily_volume_usd?: number
      remaining_daily_volume_usd?: number
      next_round_volume_usd?: number
      daily_budget_ok?: boolean
      timing_ok?: boolean
      wait_seconds?: number
      pattern_score?: number
      min_interval_seconds?: number
    }
  }
  trade_records?: ArbitragePaperTradeRecord[]
  /** @deprecated 已改用 trade_records，保留字段兼容旧客户端 */
  ledger: ArbitragePaperLedger[]
}

export interface ArbitragePaperPreset {
  preset_id: string
  name: string
  description?: string
  risk_profile: string
  total_equity_hint?: number
  exchange_ratios: Record<string, number>
  strategy_limits: Record<string, number>
  is_system: boolean
}

export interface ArbitrageStartValidation {
  success: boolean
  passed: boolean
  checks: Array<{ name: string; passed: boolean; message: string }>
  strategies: string[]
  account?: ArbitragePaperAccount
  trader_profile?: {
    profile_id: number
    trader_account_id: number
    account_name: string
    strategy_llm_config_id?: number
    execution_llm_config_id?: number
    enabled_strategies: string[]
    wash_trade_profile?: string
  } | null
  strategy_runtime?: Array<{
    strategy_id: string
    name: string
    execution_mode: string
    direction_rule: string
    hold_model: string
    summary: string
    paper_auto_executable: boolean
    passed: boolean
    message: string
  }>
}

// ════════════════════════════════════════════════════════
//  策略元数据（前端硬编码）
// ════════════════════════════════════════════════════════

export interface StrategyMeta {
  name: string
  exchanges: string[]
  roiRange: string
  drawdown: string
  priority: 'P0' | 'P1' | 'P2'
  description: string
  /** 策略怎么赚钱（一句话机制） */
  howItWorks: string
  /** 预计收益测算（基于 Paper/历史参数的区间说明，非承诺） */
  profitEstimate: string
  /** 最低建议资金（USD） */
  minCapitalUsd: number
  riskLevel: '低' | '中' | '高'
  risks: string[]
  suitableFor: string
  notSuitableFor: string
  defaultOn: boolean
  monitorOnly?: boolean
  /** M4 处置：已下线（不出现在可选列表中） */
  deprecated?: boolean
  /** M4 处置：当前不可启用的原因（显示在策略列表里） */
  disabledReason?: string
}

export const STRATEGY_META: Record<string, StrategyMeta> = {
  S1: {
    name: 'Maker返佣对冲',
    exchanges: ['Asterdex', 'Binance'],
    roiRange: '50-80%',
    drawdown: '5-8%',
    priority: 'P0',
    description: 'Asterdex Maker + Binance 对冲，用返佣覆盖手续费',
    howItWorks: '在 Asterdex 挂 Maker（低费率+10%返佣），Binance 开对冲腿；净费率接近 0，主要靠返佣与 Rh/积分补偿。',
    profitEstimate: '300U 月刷量约 50-100 万 U 时，返佣+积分折算约 15-40U/月；需 Rh 补偿为正 EV 才值得开。',
    minCapitalUsd: 300,
    riskLevel: '中',
    risks: ['纯费率差常为负 EV，默认关闭', '对冲腿滑点与延迟', '需两所 API 同时稳定'],
    suitableFor: '已有 Asterdex+Binance 账户、追求交易积分/返佣覆盖',
    notSuitableFor: '只有单所账户、小资金且无法维持对冲',
    defaultOn: false,
    deprecated: true,
    disabledReason: '已下线：数学期望为负（月返佣 <$1 vs 成本 $40），与 S6 重复且更差',
  },
  S2: {
    name: 'VIP等级冲刺',
    exchanges: ['OKX'],
    roiRange: '30-80%',
    drawdown: '15-25%',
    priority: 'P1',
    description: '月末/季末集中刷量冲 OKX VIP 门槛',
    howItWorks: '短期集中刷量达到 VIP 等级（如 VIP4 需 30 日 1000 万 U），达标后享受 0% Maker 等长期费率优惠。',
    profitEstimate: '达标后长期节省手续费；冲刺期可能净亏 5-15% 权益作「门票」，适合 >5 万 U 资金池。',
    minCapitalUsd: 50_000,
    riskLevel: '高',
    risks: ['冲刺期回撤 15-25%', '未达标则纯损耗', '活动规则可能变更'],
    suitableFor: '大资金、长期高频交易者',
    notSuitableFor: '300U 小资金、短期 Paper 验证',
    defaultOn: false,
  },
  S3: {
    name: '积分挖矿',
    exchanges: ['Hyperliquid'],
    roiRange: '40-60%',
    drawdown: '10-15%',
    priority: 'P1',
    description: 'Hyperliquid 活跃交易积累 Points → HYPE 空投',
    howItWorks: '在 HL 保持日均交易活跃度赚 Points，赛季结束按积分比例分配 HYPE 代币。',
    profitEstimate: '100-300U 保守日积分 30-50 点，赛季 90 天折算约 20-60U 等值 HYPE（随币价波动）。',
    minCapitalUsd: 100,
    riskLevel: '中',
    risks: ['HYPE 价格波动', '赛季规则调整', '活跃度不足则积分归零趋势'],
    suitableFor: '有 HL 账户、能接受 10-15% 回撤的小中资金',
    notSuitableFor: '无法访问 HL、追求稳定月化现金流',
    defaultOn: false,
  },
  S4: {
    name: '交易竞赛套利',
    exchanges: ['OKX', 'Bybit', 'Gate.io'],
    roiRange: '15-60%',
    drawdown: '20-30%',
    priority: 'P2',
    description: '参与交易所交易赛/排行榜，用返利降低参赛成本',
    howItWorks: '识别奖池 ROI 合理的竞赛，控制刷量成本参赛，按排名瓜分奖池。',
    profitEstimate: '高度依赖当期活动；预期 ROI 15-60% 但方差极大，可能整期净亏。',
    minCapitalUsd: 5_000,
    riskLevel: '高',
    risks: ['活动截止/规则突变', '回撤 20-30%', '排名不确定'],
    suitableFor: '熟悉各所活动规则、资金 >5000U',
    notSuitableFor: '新手、小资金、求稳用户',
    defaultOn: false,
  },
  S5: {
    name: '资金费率+积分叠加',
    exchanges: ['Hyperliquid'],
    roiRange: '12-20%',
    drawdown: '3-5%',
    priority: 'P1',
    description: '持仓赚资金费率的同时叠加 HL 积分',
    howItWorks: '在资金费率有利时建仓，持仓期间同时积累 Points，双重收益。',
    profitEstimate: '费率收入约 1-3%/月 + 积分加成约 2%；合计 12-20% 年化区间，需费率方向正确。',
    minCapitalUsd: 500,
    riskLevel: '低',
    risks: ['费率反转', '持仓时间与积分效率的平衡'],
    suitableFor: '已有费率套利经验、能持仓数天以上',
    notSuitableFor: '纯刷量、无法承受隔夜持仓',
    defaultOn: false,
    deprecated: true,
    disabledReason: '已下线：funding 数据结构有缺陷、积分加成无依据，与 V3 资金费套利重复',
  },
  S6: {
    name: '跨所费率差',
    exchanges: ['Asterdex', 'Binance'],
    roiRange: '20-40%',
    drawdown: '5-8%',
    priority: 'P1',
    description: 'Asterdex 极低费率 vs 主流所费率差',
    howItWorks: 'Asterdex Maker 腿 + 主流所 Taker 对冲，组合净费率接近 0，赚返佣与交易积分。',
    profitEstimate: '与 S1 类似但侧重费率差；300U 配合 S8 时作为辅助，单独月化约 10-25U。',
    minCapitalUsd: 200,
    riskLevel: '中',
    risks: ['对冲失败敞口', '两所资金调配', '默认需与 S8 组合理解'],
    suitableFor: '已有 Asterdex+Binance、追求交易积分',
    notSuitableFor: '单所、无对冲经验',
    defaultOn: false,
    disabledReason: '已关闭：当前组合月化 EV 为负（is_viable 已收紧），待返佣条件改善后可重新评估',
  },
  S7: {
    name: '币安Alpha积分',
    exchanges: ['Binance'],
    roiRange: '50-150%',
    drawdown: '8-12%',
    priority: 'P0',
    description: 'Binance Alpha 积分兑换新币空投',
    howItWorks: '在 Alpha 平台交易赚积分，积分兑换新币；约 47.5% 项目毕业上主站，首日常有溢价。',
    profitEstimate: '单项目波动大；保守 50% / 激进 150% ROI 为历史区间，当前默认仅监控不计入执行。',
    minCapitalUsd: 300,
    riskLevel: '高',
    risks: ['API/规则频繁变更', '项目不毕业则积分贬值', 'monitor_only 不自动下单'],
    suitableFor: '有 Binance 账户、愿手动跟进 Alpha 项目',
    notSuitableFor: '希望全自动、无法承受规则变更',
    defaultOn: false,
    monitorOnly: true,
  },
  S8: {
    name: 'Asterdex Stage6 积分',
    exchanges: ['Asterdex'],
    roiRange: '10-50%+/月',
    drawdown: '5-10%',
    priority: 'P0',
    description: 'Stage 6 最优打法：Maker 优先 0 费率 + 动态持仓 + USDF 全仓资产积分',
    howItWorks: 'Stage 6 积分 = 交易(手续费+Maker流动性+币种加成) + 持仓(规模×时长，无上限) + 资产(USDF 全仓) + 盈亏积分。Maker 挂单 0% 费率开平，AI 定方向，按资金费率动态持仓 2-8 小时。',
    profitEstimate: '100-300U 配合 5-10x 杠杆，单轮净 EV 由 Stage6 模型实时估算；月化折算约 30-150U 等值 ASTER（随 epoch 与币价波动）。',
    minCapitalUsd: 100,
    riskLevel: '中',
    risks: ['杠杆价格波动（盈亏积分双向但真亏是真亏）', 'Maker 挂单可能超时回退 Taker（0.04%）', 'ASTER 价格与空投比例变化', '官方惩罚对冲刷分，必须单边'],
    suitableFor: '小资金首选、有 Asterdex 账户、能接受杠杆',
    notSuitableFor: '无法使用 Asterdex、拒绝杠杆',
    defaultOn: true,
  },
}

/** 按账户权益推荐策略组合（M4 处置后资源集中 S8 主力 + S3 次级） */
export function recommendStrategies(equityUsd: number): { strategies: string[]; reason: string } {
  if (equityUsd < 200) {
    return { strategies: ['S8'], reason: '资金 <200U：优先单所 S8 Stage6，降低复杂度。' }
  }
  if (equityUsd < 800) {
    return { strategies: ['S8'], reason: '300U 档默认：优先 S8 Stage6 积分；需要 HL 积分时可手动加 S3。' }
  }
  return { strategies: ['S3', 'S8'], reason: '中大资金：S8 主力 + S3 次级（S1/S5 已下线、S6 已关闭，资源集中正 EV 策略）。' }
}

/** 粗算选中策略的月化收益区间（USD，非承诺） */
export function estimateMonthlyUsd(equityUsd: number, strategyIds: string[]): { low: number; high: number; note: string } {
  let low = 0
  let high = 0
  for (const id of strategyIds) {
    const m = STRATEGY_META[id]
    if (!m || m.monitorOnly) continue
    const share = equityUsd / Math.max(strategyIds.filter(s => !STRATEGY_META[s]?.monitorOnly).length, 1)
    if (id === 'S8') { low += share * 0.10; high += share * 0.50 }
    else if (id === 'S3') { low += share * 0.05; high += share * 0.15 }
    else if (id === 'S6' || id === 'S1') { low += share * 0.03; high += share * 0.10 }
    else if (id === 'S5') { low += share * 0.01; high += share * 0.03 }
    else if (id === 'S7') { low += share * 0.08; high += share * 0.25 }
    else { low += share * 0.02; high += share * 0.08 }
  }
  return {
    low: Math.round(low),
    high: Math.round(high),
    note: '基于历史参数与 Paper 假设的粗算区间，实际受币价、规则、执行质量影响。',
  }
}

// M4 处置后：S1/S5 已下线，不再出现在策略组中
export const POINTS_ARB_STRATEGIES = ['S3', 'S7', 'S8'] as const
export const TRADE_POINTS_STRATEGIES = ['S2', 'S4', 'S6'] as const
export type PointsArbStrategy = typeof POINTS_ARB_STRATEGIES[number]
export type TradePointsStrategy = typeof TRADE_POINTS_STRATEGIES[number]

export function isPointsArbStrategy(strategyType: string): boolean {
  return POINTS_ARB_STRATEGIES.includes(strategyType as PointsArbStrategy)
}

export function isTradePointsStrategy(strategyType: string): boolean {
  return TRADE_POINTS_STRATEGIES.includes(strategyType as TradePointsStrategy)
}

export interface StrategyPlaybook {
  id: string
  name: string
  category: 'points_arb' | 'trade_points'
  capital_usd: number
  strategies: string[]
  risk: 'low' | 'medium' | 'high'
  summary: string
  default_leverage: number
  monitor_only?: boolean
}

export const STRATEGY_PLAYBOOKS: StrategyPlaybook[] = [
  {
    id: 'small_300u_points',
    name: '300U 小资金套利积分默认',
    category: 'points_arb',
    capital_usd: 300,
    strategies: ['S3', 'S8'],
    risk: 'medium',
    summary: '优先 S8 Asterdex Rh 与 S3 Hyperliquid 积分，Paper 验证后人工上 Live。',
    default_leverage: 5,
  },
  {
    id: 'balanced_trade_points',
    name: '交易积分均衡（已暂停）',
    category: 'trade_points',
    capital_usd: 1000,
    strategies: ['S6'],
    risk: 'medium',
    summary: 'S1 已下线（负 EV）、S6 已关闭（月化 EV 为负）；S2/S4 数据管线已修通但保持关闭，待条件改善后评估。',
    default_leverage: 3,
    monitor_only: true,
  },
  {
    id: 'alpha_monitor',
    name: 'S7 Binance Alpha 监控',
    category: 'points_arb',
    capital_usd: 300,
    strategies: ['S7'],
    risk: 'high',
    summary: '只监控规则变化与积分可得性，等 Rule Sync 上线稳定后再解除。',
    default_leverage: 1,
    monitor_only: true,
  },
]

export async function getRuleSyncGate(): Promise<RuleSyncGateState> {
  const res = await fetch('/api/rebate/rules/gate')
  if (!res.ok) {
    return {
      rebate_pause: false,
      v3_pause: false,
      paused_strategies: [],
      pause_reason: '',
      allow_manual_override: false,
      requires_code_change: false,
      paused_at: null,
      is_rebate_paused: false,
      is_v3_paused: false,
    }
  }
  return res.json()
}

export async function getRuleSources(): Promise<{ sources: RuleSource[] }> {
  const res = await fetch('/api/rebate/rules/sources')
  if (!res.ok) return { sources: [] }
  return res.json()
}

export async function getRuleStrategyParams(): Promise<{ strategies: Record<string, Record<string, any>> }> {
  const res = await fetch('/api/rebate/rules/strategy-params')
  if (!res.ok) return { strategies: {} }
  return res.json()
}

export async function getRuleSyncScheduler(): Promise<RuleSyncSchedulerStatus> {
  const res = await fetch('/api/rebate/rules/scheduler')
  if (!res.ok) {
    return { enabled: false, job_id: 'rebate_rule_sync_fetch_all', interval_seconds: 0, registered: false }
  }
  return res.json()
}

export async function getRuleChanges(status = '', limit = 100): Promise<{ count: number; events: RuleChangeEvent[] }> {
  const qs = new URLSearchParams()
  if (status) qs.set('status', status)
  qs.set('limit', String(limit))
  const res = await fetch(`/api/rebate/rules/changes?${qs.toString()}`)
  if (!res.ok) return { count: 0, events: [] }
  return res.json()
}

export async function ingestRuleSnapshot(payload: { source_id: string; content_text: string; title?: string; url?: string }) {
  const res = await fetch('/api/rebate/rules/ingest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return res.json()
}

export async function fetchRuleSource(sourceId: string) {
  const res = await fetch(`/api/rebate/rules/fetch/${sourceId}`, { method: 'POST' })
  return res.json()
}

export async function fetchAllRuleSources() {
  const res = await fetch('/api/rebate/rules/fetch-all', { method: 'POST' })
  return res.json()
}

export async function analyzeRuleChange(eventId: number) {
  const res = await fetch(`/api/rebate/rules/changes/${eventId}/analyze`, { method: 'POST' })
  return res.json()
}

export async function markRuleChange(eventId: number, status: 'pending' | 'analyzed' | 'applied' | 'dismissed') {
  const res = await fetch(`/api/rebate/rules/changes/${eventId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  return res.json()
}

export async function markEvolutionProposal(proposalId: number, status: 'pending' | 'paper_validated' | 'applied' | 'dismissed') {
  const res = await fetch(`/api/rebate/evolution/proposals/${proposalId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  return res.json()
}

export async function getWashTradeTimeline(limit = 100): Promise<{ count: number; timeline: WashTradeTimelineItem[] }> {
  const res = await fetch(`/api/rebate/wash-trade/timeline?limit=${limit}`)
  if (!res.ok) return { count: 0, timeline: [] }
  return res.json()
}

export async function getUnifiedPositions(status = 'all'): Promise<{ count: number; positions: UnifiedPosition[] }> {
  const res = await fetch(`/api/arbitrage/unified-positions?status=${status}`)
  if (!res.ok) return { count: 0, positions: [] }
  return res.json()
}

export async function getEvolutionProposals(): Promise<{ count: number; proposals: EvolutionProposal[]; summary: any }> {
  const res = await fetch('/api/rebate/evolution/proposals')
  if (!res.ok) return { count: 0, proposals: [], summary: {} }
  return res.json()
}

export async function runEvolutionBacktest(strategyType = 'S8') {
  const res = await fetch(`/api/rebate/evolution/backtest?strategy_type=${strategyType}`, { method: 'POST' })
  return res.json()
}

export async function generateEvolutionProposals() {
  const res = await fetch('/api/rebate/evolution/generate', { method: 'POST' })
  return res.json()
}

export async function pauseRuleSyncGate(payload: Partial<RuleSyncGateState> & { reason?: string; strategies?: string[] }) {
  const res = await fetch('/api/rebate/rules/pause', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return res.json()
}

export async function resumeRuleSyncGate(reason = 'manual_resume_from_ui') {
  const res = await fetch('/api/rebate/rules/resume', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason, risk_acknowledged: true }),
  })
  return res.json()
}

// ════════════════════════════════════════════════════════
//  Arbitrage API 函数
// ════════════════════════════════════════════════════════

export async function getArbitrageStatus(): Promise<ArbitrageStatus> {
  const res = await fetch('/api/arbitrage/status')
  if (!res.ok) return { engine_enabled: false, scanner_scan_count: 0, cached_opportunities: 0, circuit_breaker_active: false }
  return res.json()
}

export async function getArbitragePositions(status?: string): Promise<{ count: number; positions: ArbitragePosition[] }> {
  const url = status ? `/api/arbitrage/positions?status=${status}` : '/api/arbitrage/positions'
  const res = await fetch(url)
  if (!res.ok) return { count: 0, positions: [] }
  return res.json()
}

export async function getArbitrageOpportunities(symbol?: string): Promise<{ count: number; opportunities: ArbitrageOpportunity[] }> {
  const url = symbol ? `/api/arbitrage/opportunities?symbol=${symbol}` : '/api/arbitrage/opportunities'
  const res = await fetch(url)
  if (!res.ok) return { count: 0, opportunities: [] }
  return res.json()
}

export async function getFeeSchedules(): Promise<Record<string, FeeSchedule>> {
  const res = await fetch('/api/arbitrage/fee-schedules')
  if (!res.ok) return {}
  const data = await res.json()
  return data.exchanges ?? {}
}

// ── V3 新增: 跨交易所套利 ──

export async function getCrossArbSpreads(symbols?: string): Promise<{ spreads: any[]; timestamp: number }> {
  const url = symbols ? `/api/arbitrage/cross-arb/spreads?symbols=${symbols}` : '/api/arbitrage/cross-arb/spreads'
  const res = await fetch(url)
  if (!res.ok) return { spreads: [], timestamp: 0 }
  return res.json()
}

export async function getCrossArbFundingRates(symbols?: string): Promise<{ exchanges: string[]; comparison: any[]; timestamp: number }> {
  const url = symbols ? `/api/arbitrage/cross-arb/funding-rates?symbols=${symbols}` : '/api/arbitrage/cross-arb/funding-rates'
  const res = await fetch(url)
  if (!res.ok) return { exchanges: [], comparison: [], timestamp: 0 }
  return res.json()
}

export async function getCrossArbExposure(): Promise<{ total_equity: number; total_positions_notional: number; exposure_pct: number; exchanges: any[]; is_safe: boolean }> {
  const res = await fetch('/api/arbitrage/cross-arb/exposure')
  if (!res.ok) return { total_equity: 0, total_positions_notional: 0, exposure_pct: 0, exchanges: [], is_safe: true }
  return res.json()
}

// ── V3 新增: 监控 & 操作 ──

export async function getPositionMetrics(): Promise<{ count: number; metrics: any[] }> {
  const res = await fetch('/api/arbitrage/metrics')
  if (!res.ok) return { count: 0, metrics: [] }
  return res.json()
}

export async function getCapitalPool(): Promise<{ total_pool_usd: number; allocated_usd: number; available_usd: number; utilization_pct: number }> {
  const res = await fetch('/api/arbitrage/capital-pool')
  if (!res.ok) return { total_pool_usd: 0, allocated_usd: 0, available_usd: 0, utilization_pct: 0 }
  return res.json()
}

export async function closeArbPosition(positionId: string, reason?: string): Promise<{ ok: boolean; position_id: string }> {
  const res = await fetch(`/api/arbitrage/close/${positionId}?reason=${reason ?? 'manual'}`, { method: 'POST' })
  if (!res.ok) return { ok: false, position_id: positionId }
  return res.json()
}

export async function getArbPerformance(): Promise<{ total_positions: number; active_positions: number; closed_positions: number; strategy_breakdown: Record<string, number> }> {
  const res = await fetch('/api/arbitrage/performance')
  if (!res.ok) return { total_positions: 0, active_positions: 0, closed_positions: 0, strategy_breakdown: {} }
  return res.json()
}

export async function setArbitrageMode(mode: 'paper' | 'live'): Promise<{ ok: boolean; mode: string }> {
  const res = await fetch('/api/arbitrage/mode', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, confirm: true }),
  })
  if (!res.ok) return { ok: false, mode }
  return res.json()
}

// ════════════════════════════════════════════════════════
//  Rebate Arb API 函数
// ════════════════════════════════════════════════════════

export async function getRebateStatus(): Promise<RebateStatus> {
  const res = await fetch('/api/rebate/status')
  if (!res.ok) return { engine_enabled: false, mode: 'paper', scan_count: 0, execution_count: 0, active_positions: 0, total_rebate_pnl: 0, wash_trade_safe: true, next_safe_interval_sec: 0 }
  return res.json()
}

// ── [2026-07-06 Phase4] 积分项目生命周期（前端置灰死项目） ──
export interface PointsProgram {
  program_id: string
  exchange: string
  name: string
  status: 'active' | 'ended' | 'staking_only' | 'monitor_only' | 'upcoming' | string
  maker_rate: number
  taker_rate: number
  rebate_rate: number
  start_date: string | null
  end_date: string | null
  points_rule: string
  strategy_id: string | null
  notes: string
  is_active_now: boolean
}

export async function getPointsPrograms(): Promise<{ count: number; active_count: number; programs: PointsProgram[]; note?: string }> {
  const res = await fetch('/api/rebate/programs')
  if (!res.ok) return { count: 0, active_count: 0, programs: [] }
  return res.json()
}

export async function getRebateOpportunities(): Promise<{ count: number; opportunities: RebateOpportunity[] }> {
  const res = await fetch('/api/rebate/opportunities')
  if (!res.ok) return { count: 0, opportunities: [] }
  return res.json()
}

export async function getRebatePositions(status?: string): Promise<{ count: number; positions: RebatePosition[] }> {
  const url = status ? `/api/rebate/positions?status=${status}` : '/api/rebate/positions'
  const res = await fetch(url)
  if (!res.ok) return { count: 0, positions: [] }
  return res.json()
}

export async function closeRebatePosition(positionId: string, reason?: string): Promise<{ success: boolean; error?: string }> {
  const res = await fetch(`/api/rebate/positions/${positionId}/close?reason=${reason ?? 'manual'}`, { method: 'POST' })
  if (!res.ok) return { success: false, error: 'request failed' }
  return res.json()
}

export async function getRebateCapital(): Promise<CapitalAllocation> {
  const res = await fetch('/api/rebate/capital')
  if (!res.ok) return { total_equity: 0, allocations: {}, used: {}, utilization: {}, rebate_available: 0, total_utilization_pct: 0 }
  return res.json()
}

export async function getWashTradeStatus(): Promise<WashTradeStatus> {
  const res = await fetch('/api/rebate/wash-trade/status')
  if (!res.ok) return { is_safe: true, next_safe_interval_sec: 0, daily_volume_usd: 0, last_trade_ts: 0, trade_count_today: 0, risk_level: 'low' }
  return res.json()
}

export async function getRebateAnalytics(): Promise<RebateAnalytics> {
  const res = await fetch('/api/rebate/analytics')
  if (!res.ok) return { total_trades: 0, win_rate: 0, total_pnl: 0, total_rebate: 0, total_points: 0, net_pnl: 0, by_strategy: {} }
  return res.json()
}

export async function triggerRebateScan(): Promise<{ triggered: boolean; total_evaluated: number; viable_count: number }> {
  const res = await fetch('/api/rebate/scan', { method: 'POST' })
  if (!res.ok) return { triggered: false, total_evaluated: 0, viable_count: 0 }
  return res.json()
}

export async function emergencyCloseAll(): Promise<{ success: boolean; closed_count: number }> {
  const res = await fetch('/api/rebate/emergency/close-all', { method: 'POST' })
  if (!res.ok) return { success: false, closed_count: 0 }
  return res.json()
}

export async function getExchangeIncentives(): Promise<{ count: number; exchanges: ExchangeIncentiveSummary[] }> {
  const res = await fetch('/api/rebate/incentives')
  if (!res.ok) return { count: 0, exchanges: [] }
  return res.json()
}

// ── 实时资金费矩阵 + delta-neutral 净EV机会 ──

export interface FundingMatrixRow {
  symbol: string
  /** {exchange: 小时/结算费率(小数)} */
  venues: Record<string, number>
}

export interface FundingMatrixCombo {
  symbol: string
  long_exchange: string
  short_exchange: string
  long_funding_per_day: number
  short_funding_per_day: number
  net_funding_per_day: number
  gross_funding_apr: number
  fee_drag: number
  breakeven_days: number | null
  net_apr_at_horizon: number
  horizon_days: number
  points_long_leg: boolean
  points_program_id: string | null
  notes: string
  /** SDN 自适应持有期视角（后端叠加） */
  sdn_horizon_days?: number
  sdn_horizon_adaptive?: boolean
  sdn_net_apr?: number
  sdn_viable?: boolean
  sdn_min_net_apr?: number
}

export interface FundingMatrixResponse {
  as_of: number
  multi_venue: boolean
  horizon_days: number
  use_taker: boolean
  venue_count: number
  symbol_count: number
  combo_count: number
  /** {exchange: [覆盖的symbol,...]} */
  venues: Record<string, string[]>
  matrix: FundingMatrixRow[]
  combos: FundingMatrixCombo[]
  error?: string
}

export async function getFundingMatrix(
  horizonDays = 7, useTaker = true, minNetApr = -1e9,
): Promise<FundingMatrixResponse> {
  const qs = new URLSearchParams({
    horizon_days: String(horizonDays),
    use_taker: String(useTaker),
    min_net_apr: String(minNetApr),
  })
  const res = await fetch(`/api/rebate/funding-matrix?${qs.toString()}`)
  if (!res.ok) {
    return {
      as_of: 0, multi_venue: false, horizon_days: horizonDays, use_taker: useTaker,
      venue_count: 0, symbol_count: 0, combo_count: 0, venues: {}, matrix: [], combos: [],
      error: `HTTP ${res.status}`,
    }
  }
  return res.json()
}

// ── 多场所采集器健康度（各场所连通状态 / 连续失败 / 告警态） ──

export interface FundingCollectorVenueDiag {
  status: string
  count?: number
  elapsed_ms?: number
  via?: string | null
  error?: string
}

export interface FundingCollectorStatus {
  enabled: boolean
  interval_seconds?: number
  alert_threshold?: number
  has_report: boolean
  offline?: boolean | null
  rows_written?: number | null
  symbols_covered?: string[]
  venues_with_data?: string[]
  as_of?: number | null
  as_of_iso?: string | null
  elapsed_ms?: number | null
  venue_report: Record<string, FundingCollectorVenueDiag>
  consecutive_failures?: Record<string, number>
  alerted_venues?: string[]
  error?: string
}

export async function getFundingCollectorStatus(): Promise<FundingCollectorStatus> {
  const res = await fetch('/api/rebate/funding-collector/status')
  if (!res.ok) {
    return { enabled: false, has_report: false, venue_report: {}, error: `HTTP ${res.status}` }
  }
  return res.json()
}

// ── Phase C/D 新增 API ──

export interface CircuitBreakerStatus {
  is_tripped: boolean
  active_breakers: Record<string, { triggered_at: number; cooldown_until: number; remaining_seconds: number; reason: string }>
  count: number
}

export interface IncentiveFreshness {
  exchanges: Record<string, { last_update: number; age_seconds: number | null; health: string; error: string | null; has_data: boolean }>
  cache: { hits: number; misses: number; hit_rate: number }
  aggregator: { fetch_count: number; exchanges_with_data: number; exchanges_with_errors: number }
}

export interface ReconciliationReport {
  timestamp: number
  duration_ms: number
  memory_positions: number
  db_positions: number
  exchange_positions: number
  is_consistent: boolean
  issues_total: number
  critical: number
  warnings: number
  issues: Array<{ severity: string; type: string; position_id: string; exchange: string; description: string; action: string }>
}

export interface RebateConfig {
  loaded: boolean
  engine?: { min_monthly_value: number; max_position_usd: number; max_total_volume_7d: number; max_holding_days: number; default_paper_mode: boolean }
  risk_gate?: { max_daily_volume_per_exchange: number; max_weekly_volume_per_exchange: number; max_daily_loss_pct: number }
  exchanges_enabled?: string[]
  strategies_enabled?: string[]
  current_mode?: 'paper' | 'live'
  paper_account_id?: number | null
}

export async function executeRebateStrategy(
  strategy_type: string, size_usd: number, symbol?: string, mode?: 'paper' | 'live'
): Promise<{ success: boolean; position_id?: string; strategy_type?: string; paper_mode?: boolean; error?: string }> {
  const res = await fetch('/api/rebate/execute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy_type, size_usd, symbol: symbol ?? '', mode }),
  })
  if (!res.ok) return { success: false, error: 'request failed' }
  return res.json()
}

export async function getIncentiveFreshness(): Promise<IncentiveFreshness> {
  const res = await fetch('/api/rebate/incentives/freshness')
  if (!res.ok) return { exchanges: {}, cache: { hits: 0, misses: 0, hit_rate: 0 }, aggregator: { fetch_count: 0, exchanges_with_data: 0, exchanges_with_errors: 0 } }
  return res.json()
}

export async function refreshIncentiveData(): Promise<{ success: boolean; exchanges_fetched: number; exchanges?: string[] }> {
  const res = await fetch('/api/rebate/incentives/refresh', { method: 'POST' })
  if (!res.ok) return { success: false, exchanges_fetched: 0 }
  return res.json()
}

export async function getCircuitBreakers(): Promise<CircuitBreakerStatus> {
  const res = await fetch('/api/rebate/risk/breakers')
  if (!res.ok) return { is_tripped: false, active_breakers: {}, count: 0 }
  return res.json()
}

export async function resetCircuitBreakers(rule_id?: string): Promise<{ success: boolean; reset_rule: string }> {
  const url = rule_id ? `/api/rebate/risk/breakers/reset?rule_id=${rule_id}` : '/api/rebate/risk/breakers/reset'
  const res = await fetch(url, { method: 'POST' })
  if (!res.ok) return { success: false, reset_rule: '' }
  return res.json()
}

export async function runReconciliation(): Promise<ReconciliationReport> {
  const res = await fetch('/api/rebate/reconcile')
  if (!res.ok) return { timestamp: 0, duration_ms: 0, memory_positions: 0, db_positions: 0, exchange_positions: 0, is_consistent: false, issues_total: 0, critical: 0, warnings: 0, issues: [] }
  return res.json()
}

export async function switchRebateMode(
  mode: 'paper' | 'live', paperAccountId?: number | null
): Promise<{ success: boolean; mode?: string; paper_account_id?: number | null; error?: string }> {
  const res = await fetch('/api/rebate/mode', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, paper_account_id: paperAccountId ?? null }),
  })
  if (!res.ok) return { success: false, error: 'request failed' }
  return res.json()
}

export async function getRebateConfig(): Promise<RebateConfig> {
  const res = await fetch('/api/rebate/config')
  if (!res.ok) return { loaded: false }
  return res.json()
}

export interface PaperAccount {
  id: number
  name: string
  trading_mode: string
  account_type: string
}

export async function getPaperAccounts(): Promise<PaperAccount[]> {
  const res = await fetch('/api/account/list')
  if (!res.ok) return []
  const data = await res.json()
  const accounts = Array.isArray(data) ? data : (data.accounts ?? [])
  return accounts.filter((a: any) => a.account_type === 'PAPER' || a.trading_mode === 'paper')
}

// ──────────── Arbitrage Dedicated Paper Accounts ────────────

export async function getBindableArbitrageTraders(paperAccountId?: number): Promise<BindableArbitrageTrader[]> {
  const qs = paperAccountId ? `?paper_account_id=${paperAccountId}` : ''
  const res = await fetch(`/api/arbitrage-paper/bindable-traders${qs}`)
  if (!res.ok) return []
  const data = await res.json()
  return data.traders || []
}

export async function bindArbitragePaperTrader(
  accountId: number,
  traderAccountId: number,
): Promise<{ success: boolean; account?: ArbitragePaperAccount; error?: string }> {
  const res = await fetch(`/api/arbitrage-paper/accounts/${accountId}/bind-trader`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trader_account_id: traderAccountId }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) return { success: false, error: data.detail || data.error || `HTTP ${res.status}` }
  return data
}

export async function unbindArbitragePaperTrader(
  accountId: number,
): Promise<{ success: boolean; account?: ArbitragePaperAccount; error?: string }> {
  const res = await fetch(`/api/arbitrage-paper/accounts/${accountId}/unbind-trader`, { method: 'POST' })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) return { success: false, error: data.detail || data.error || `HTTP ${res.status}` }
  return data
}

export async function getArbitragePaperPresets(): Promise<ArbitragePaperPreset[]> {
  const res = await fetch('/api/arbitrage-paper/presets')
  if (!res.ok) return []
  const data = await res.json()
  return data.presets || []
}

export async function getArbitragePaperAccounts(ownerAccountId?: number | null): Promise<ArbitragePaperAccount[]> {
  const qs = ownerAccountId ? `?owner_account_id=${ownerAccountId}` : ''
  const res = await fetch(`/api/arbitrage-paper/accounts${qs}`)
  if (!res.ok) return []
  const data = await res.json()
  return data.accounts || []
}

export async function createArbitragePaperAccount(payload: {
  name: string
  total_equity: number
  owner_account_id?: number | null
  preset_id?: string
  risk_profile?: string
}): Promise<{ success: boolean; account?: ArbitragePaperAccount; error?: string }> {
  const res = await fetch('/api/arbitrage-paper/accounts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) return { success: false, error: await safeError(res) }
  return res.json()
}

export async function updateArbitragePaperAccount(
  id: number,
  payload: { name?: string; risk_profile?: string },
): Promise<{ success: boolean; account?: ArbitragePaperAccount; error?: string }> {
  const res = await fetch(`/api/arbitrage-paper/accounts/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) return { success: false, error: await safeError(res) }
  return res.json()
}

export async function resetArbitragePaperAccount(
  id: number,
  payload: { total_equity: number; preset_id?: string; clear_ledger?: boolean },
): Promise<{ success: boolean; account?: ArbitragePaperAccount; error?: string }> {
  const res = await fetch(`/api/arbitrage-paper/accounts/${id}/reset`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) return { success: false, error: await safeError(res) }
  return res.json()
}

export async function deleteArbitragePaperAccount(
  id: number,
): Promise<{ success: boolean; deleted_account_id?: number; error?: string }> {
  const res = await fetch(`/api/arbitrage-paper/accounts/${id}`, { method: 'DELETE' })
  if (!res.ok) return { success: false, error: await safeError(res) }
  return res.json()
}

export async function getArbitragePaperAccount(id: number): Promise<ArbitragePaperAccount | null> {
  const res = await fetch(`/api/arbitrage-paper/accounts/${id}`)
  if (!res.ok) return null
  return res.json()
}

export async function getArbitragePaperDashboard(id: number): Promise<ArbitragePaperDashboard | null> {
  const res = await fetch(`/api/arbitrage-paper/accounts/${id}/dashboard`)
  if (!res.ok) return null
  return res.json()
}

export async function updateArbitragePaperBalances(
  id: number,
  balances: Record<string, number>,
): Promise<{ success: boolean; account?: ArbitragePaperAccount; error?: string }> {
  const res = await fetch(`/api/arbitrage-paper/accounts/${id}/balances`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ balances }),
  })
  if (!res.ok) return { success: false, error: await safeError(res) }
  return res.json()
}

export async function applyArbitragePaperPreset(
  id: number,
  preset_id: string,
  total_equity?: number,
): Promise<{ success: boolean; account?: ArbitragePaperAccount; error?: string }> {
  const res = await fetch(`/api/arbitrage-paper/accounts/${id}/apply-preset`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ preset_id, total_equity }),
  })
  if (!res.ok) return { success: false, error: await safeError(res) }
  return res.json()
}

export async function validateArbitragePaperStart(
  id: number,
  strategies: string[],
): Promise<ArbitrageStartValidation> {
  const res = await fetch(`/api/arbitrage-paper/accounts/${id}/validate-start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategies }),
  })
  if (!res.ok) return { success: false, passed: false, checks: [{ name: '请求失败', passed: false, message: await safeError(res) }], strategies }
  return res.json()
}

export interface ArbitragePaperStartResult {
  success: boolean
  passed?: boolean
  error?: string
  account_id?: number
  strategies?: string[]
  status?: string
  checks?: ArbitrageStartValidation['checks']
  session?: ArbitragePaperSessionStatus
  scan?: {
    triggered?: boolean
    total_evaluated?: number
    viable_count?: number
    auto_executed?: boolean
    account_equity?: number
    top_strategy?: string
    error?: string
  }
  account?: ArbitragePaperAccount
}

export interface ArbitragePaperSessionStatus {
  running: boolean
  account_id?: number
  strategies?: string[]
  started_at?: number
  last_tick_at?: number | null
  tick_count?: number
  interval_seconds?: number
  last_tick?: {
    account_equity?: number
    total_evaluated?: number
    viable_count?: number
    auto_executed?: boolean
    top_strategy?: string
    auto_exec_error?: string
  }
}

export async function startArbitragePaperVerification(
  id: number,
  strategies: string[],
): Promise<ArbitragePaperStartResult> {
  const res = await fetch(`/api/arbitrage-paper/accounts/${id}/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategies }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    return {
      success: false,
      passed: false,
      error: data.detail || data.error || `HTTP ${res.status}`,
      checks: data.checks,
      strategies: data.strategies || strategies,
    }
  }
  return data
}

export async function getArbitragePaperSession(): Promise<ArbitragePaperSessionStatus> {
  const res = await fetch('/api/arbitrage-paper/session')
  if (!res.ok) return { running: false }
  return res.json()
}

export async function stopArbitragePaperVerification(
  id: number,
): Promise<{ success: boolean; error?: string; status?: string; account?: ArbitragePaperAccount }> {
  const res = await fetch(`/api/arbitrage-paper/accounts/${id}/stop`, { method: 'POST' })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    return { success: false, error: data.detail || data.error || `HTTP ${res.status}` }
  }
  return data
}

async function safeError(res: Response): Promise<string> {
  try {
    const data = await res.json()
    return data.detail || data.error || `HTTP ${res.status}`
  } catch {
    return `HTTP ${res.status}`
  }
}

// ──────────── Strategy Config ────────────

export interface StrategyConfigDetail {
  enabled: boolean
  params: Record<string, number | string | boolean>
  risk_overrides: Record<string, any>
}

export async function getStrategyConfigs(): Promise<Record<string, StrategyConfigDetail>> {
  const res = await fetch('/api/rebate/config/strategies')
  if (!res.ok) return {}
  const data = await res.json()
  return data.strategies || {}
}

export async function patchStrategyConfig(
  strategyId: string,
  patch: { params?: Record<string, any>; risk_overrides?: Record<string, any>; enabled?: boolean }
): Promise<{ success: boolean; changes?: any }> {
  const res = await fetch(`/api/rebate/config/strategies/${strategyId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) return { success: false }
  return res.json()
}

export async function patchEngineConfig(
  patch: Record<string, number>
): Promise<{ success: boolean; changes?: any }> {
  const res = await fetch('/api/rebate/config/engine', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) return { success: false }
  return res.json()
}

export async function patchRiskGateConfig(
  patch: Record<string, number>
): Promise<{ success: boolean; changes?: any }> {
  const res = await fetch('/api/rebate/config/risk-gate', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) return { success: false }
  return res.json()
}

// ──────────── Events ────────────

export interface RebateEvent {
  ts: number
  type: string
  data: Record<string, any>
}

/** 将 Rebate 引擎事件格式化为可读中文（避免原始 JSON 截断） */
export function formatRebateEventMessage(ev: RebateEvent): string {
  const { type, data } = ev
  switch (type) {
    case 'position_opened':
      return `新仓位: ${data.strategy_type ?? 'unknown'} ${data.symbol ?? ''} (${data.side ?? ''})`
    case 'position_closed':
      return `仓位关闭: ${data.position_id ?? ''} PnL=${data.pnl ?? 0}`
    case 'scan_completed':
      return `扫描完成: ${data.evaluated ?? 0} 评估, ${data.viable ?? 0} 可行`
    case 'circuit_breaker':
      return `熔断器触发: ${data.rule ?? ''} ${data.reason ?? ''}`
    case 'mode_changed':
      return `模式切换: ${data.mode ?? ''}`
    case 'config_changed': {
      const enabled = (data.enabled_strategies as string[] | undefined) ?? []
      const disabled = (data.disabled_strategies as string[] | undefined) ?? []
      if (enabled.length || disabled.length) {
        const parts: string[] = []
        if (enabled.length) parts.push(`已启用 ${enabled.join('、')}`)
        if (disabled.length) parts.push(`已关闭 ${disabled.join('、')}`)
        return `策略配置更新：${parts.join('；')}`
      }
      return '策略配置已更新'
    }
    case 'execution_failed': {
      const strategy = data.strategy ?? data.strategy_type ?? ''
      const reason = data.reason ?? data.error ?? '未知原因'
      return strategy ? `执行失败 ${strategy}：${reason}` : `执行失败：${reason}`
    }
    case 'execution_skipped': {
      const strategy = data.strategy ?? data.strategy_type ?? ''
      const reason = data.reason ?? '本轮跳过'
      return strategy ? `本轮跳过 ${strategy}：${reason}` : `本轮跳过：${reason}`
    }
    case 'error':
      return `错误: ${data.message ?? '未知错误'}`
    default:
      return type
  }
}

export async function getRebateEvents(
  since: number, limit?: number
): Promise<{ events: RebateEvent[]; latest_ts: number }> {
  const url = limit
    ? `/api/rebate/events?since=${since}&limit=${limit}`
    : `/api/rebate/events?since=${since}`
  const res = await fetch(url)
  if (!res.ok) return { events: [], latest_ts: since }
  return res.json()
}

// ──────────── Points ────────────

export interface PointsExchangeSummary {
  points_earned_total: number
  estimated_value_usd: number
  pnl_from_positions: number
  position_count: number
  risk_status: 'healthy' | 'warning' | 'danger'
}

export interface PointsStrategySummary {
  points_earned_total: number
  estimated_value_usd: number
  conversion_revenue_usd: number
  position_count: number
}

export interface PointsSummary {
  exchanges: Record<string, PointsExchangeSummary>
  by_strategy?: Record<string, PointsStrategySummary>
  total_points_earned: number
  total_estimated_value_usd: number
  total_conversion_revenue_usd: number
}

export interface PointsTransaction {
  position_id: string
  strategy_type: string
  points: number
  pnl: number
  rebate: number
  hold_hours: number
  close_reason: string | null
}

export async function getPointsSummary(): Promise<PointsSummary> {
  const res = await fetch('/api/rebate/points/summary')
  if (!res.ok) return { exchanges: {}, total_points_earned: 0, total_estimated_value_usd: 0, total_conversion_revenue_usd: 0 }
  return res.json()
}

export async function getPointsTransactions(
  exchange?: string, strategy?: string, limit?: number
): Promise<{ transactions: PointsTransaction[]; count: number }> {
  const params = new URLSearchParams()
  if (exchange) params.set('exchange', exchange)
  if (strategy) params.set('strategy', strategy)
  if (limit) params.set('limit', String(limit))
  const qs = params.toString()
  const res = await fetch(`/api/rebate/points/transactions${qs ? '?' + qs : ''}`)
  if (!res.ok) return { transactions: [], count: 0 }
  return res.json()
}

// ──────────── AI Config ────────────

export interface AiConfigGenerateRequest {
  risk_profile: 'conservative' | 'balanced' | 'aggressive'
  total_equity: number
  target_exchanges: string[]
  goal?: string
}

export interface AiGeneratedConfig {
  engine: Record<string, number>
  risk_gate: Record<string, number>
  strategies: Record<string, {
    enabled: boolean
    params: Record<string, any>
    risk_overrides: Record<string, any>
  }>
  reasoning: string
}

export interface AiConfigGenerateResponse {
  success: boolean
  source: 'llm' | 'fallback' | 'error'
  config: AiGeneratedConfig | null
  reasoning: string
  error?: string
}

export async function aiGenerateConfig(
  req: AiConfigGenerateRequest
): Promise<AiConfigGenerateResponse> {
  const res = await fetch('/api/rebate/config/ai-generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!res.ok) return { success: false, source: 'error', config: null, reasoning: '', error: 'request failed' }
  return res.json()
}
