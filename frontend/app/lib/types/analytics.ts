/**
 * Analytics & Performance Types
 * Type definitions for performance metrics, trade reviews, and learning insights
 */

// ============================================================================
// Performance Metrics
// ============================================================================

export interface PerformanceMetrics {
  period_start: string
  period_end: string
  total_trades: number
  winning_trades: number
  losing_trades: number
  total_pnl: number
  total_pnl_pct: number
  avg_win: number
  avg_loss: number
  avg_trade_pnl: number
  best_trade_pct: number
  worst_trade_pct: number
  max_drawdown: number
  max_drawdown_pct: number
  current_drawdown: number
  volatility: number
  sharpe_ratio: number
  sortino_ratio: number
  calmar_ratio: number
  var_95: number
  win_rate: number
  profit_factor: number
  expectancy: number
  recovery_factor: number
  risk_reward_ratio: number
  expectancy_ratio: number
  avg_holding_period: number
  longest_holding_period: number
  trades_per_day: number
  consecutive_wins: number
  consecutive_losses: number
  avg_time_to_first_profit: number
  final_equity: number
  initial_equity: number
  max_equity: number
  min_equity: number
  by_symbol: Record<string, SymbolPerformance>
}

export interface SymbolPerformance {
  trades: number
  wins: number
  pnl: number
  pnl_pct: number
  win_rate: number
}

export interface PerformanceSummary {
  status: 'no_data' | 'analyzed'
  period: {
    start: string
    end: string
  }
  returns: {
    total_pnl: number
    total_pnl_pct: number
    avg_trade_pnl: number
    best_trade: number
    worst_trade: number
  }
  risk: {
    max_drawdown_pct: number
    current_drawdown: number
    volatility: number
    sharpe_ratio: number
    sortino_ratio: number
    var_95: number
  }
  efficiency: {
    win_rate: number
    profit_factor: number
    expectancy: number
    avg_holding_hours: number
  }
  consistency: {
    consecutive_wins: number
    consecutive_losses: number
    trades_per_day: number
  }
}

// ============================================================================
// Trade Review
// ============================================================================

export type ReviewDimensionType = 
  | 'entry_quality'
  | 'exit_quality'
  | 'risk_management'
  | 'market_regime'
  | 'timing'
  | 'position_sizing'
  | 'emotion_control'
  | 'discipline'

export type ReviewStatus = 'pending' | 'in_progress' | 'completed' | 'flagged'

export interface TradeReviewDimension {
  dimension: ReviewDimensionType
  score: number
  weight: number
  weighted_score: number
  comments: string[]
  issues: string[]
  suggestions: string[]
}

export interface TradeReview {
  trade_id: string
  symbol: string
  side: 'long' | 'short'
  entry_price: number
  exit_price: number
  quantity: number
  entry_time: string
  exit_time: string
  pnl: number
  pnl_pct: number
  status: ReviewStatus
  overall_score: number
  max_score: number
  dimensions: Record<string, TradeReviewDimension>
  conclusion: string
  lessons_learned: string[]
  improvement_actions: string[]
  stop_loss?: number
  take_profit?: number
  initial_stop_pct?: number
  actual_stop_pct?: number
  market_regime_entry?: string
  market_regime_exit?: string
  regime_change?: boolean
  ai_confidence?: number
  ai_reasoning?: string
  factor_weights?: Record<string, number>
  reviewed_at?: string
  reviewer?: string
}

export interface ReviewSummary {
  total_reviews: number
  avg_overall_score: number
  score_distribution: {
    excellent: number
    good: number
    acceptable: number
    poor: number
  }
  total_pnl: number
  avg_pnl: number
  win_rate: number
  dimension_averages: Record<string, number>
}

export interface ReviewFilters {
  symbol?: string
  status?: ReviewStatus
  min_score?: number
  max_score?: number
  start_date?: string
  end_date?: string
}

// ============================================================================
// Learning Insights
// ============================================================================

export type InsightType = 
  | 'factor_performance'
  | 'market_regime'
  | 'entry_pattern'
  | 'exit_pattern'
  | 'risk_pattern'
  | 'timing_pattern'

export interface LearningInsight {
  insight_type: InsightType
  title: string
  description: string
  evidence: string[]
  recommendation: string
  confidence: number
  supporting_trades: number
  created_at: string
  applicable: boolean
}

export interface LearningRecommendation {
  category: string
  priority: 'high' | 'medium' | 'low'
  action: string
  rationale: string
  expected_impact: string
  implementation: string
}

export interface LearningReport {
  generated_at: string
  insights_count: number
  recommendations_count: number
  top_insights: Array<{
    type: string
    title: string
    confidence: number
    supporting_trades: number
    recommendation: string
  }>
  actionable_recommendations: Array<{
    category: string
    priority: string
    action: string
    rationale: string
    implementation: string
  }>
  factor_performance_summary: Record<string, {
    sample_count: number
    avg_positive: number
    avg_negative: number
  }>
  regime_performance_summary: Record<string, {
    trades: number
    win_rate: number
    avg_pnl: number
  }>
}

export interface FactorPerformanceData {
  values: number[]
  outcomes: number[]
  positive_trades: number[]
  negative_trades: number[]
}

export interface RegimePerformanceData {
  trades: number
  wins: number
  losses: number
  pnl: number
  avg_pnl: number
  win_rate: number
}

// ============================================================================
// Factor Analysis
// ============================================================================

export type FactorCategory = 
  | 'momentum'
  | 'mean_reversion'
  | 'volatility'
  | 'volume'
  | 'trend'
  | 'market_flow'
  | 'strength'
  | 'pattern'

export interface FactorValue {
  name: string
  value: number
  normalized: number
  category: FactorCategory
  timestamp?: string
}

export interface AdaptiveWeights {
  weights: Record<string, number>
  regime: string
  confidence: number
  transition_smoothed: boolean
}

export interface FactorContext {
  factor_values: Record<string, number>
  adaptive_weights: Record<string, number>
  market_regime: string
  regime_confidence: number
  selected_factors: string[]
}

export interface ExecutionParameters {
  position_size_pct: number
  stop_loss_pct: number
  take_profit_pct: number
  trailing_stop: boolean
  time_stop: boolean
  leverage: number
  risk_reward_ratio: number
}

export interface AdaptiveParameters {
  market_regime: string
  regime_confidence: number
  factor_weights: Record<string, number>
  factor_summary: string
  execution_parameters: ExecutionParameters
  execution_summary: string
}

export interface MarketRegime {
  regime: string
  direction: string
  confidence: number
  indicators: Record<string, number>
}

// ============================================================================
// SL/TP & Position Sizing
// ============================================================================

export interface SLTPStrategy {
  use_trailing_stop: boolean
  use_time_stop: boolean
  use_volatility_adjustment: boolean
  trailing_activation_pct: number
  trailing_distance_pct: number
  tp1_distance_pct: number
  tp2_distance_pct: number
  tp3_distance_pct: number
  tp1_close_pct: number
  tp2_close_pct: number
  tp3_close_pct: number
}

export interface SLTPSummary {
  initial_stop: {
    price: number
    distance_pct: number
    reason: string
  }
  trailing_stop: {
    price: number | null
    type: string | null
  }
  breakeven_stop: {
    price: number | null
  }
  final_stop: number
  take_profit_levels: Record<string, { price: number; close_pct: number }>
  risk_reward_ratio: {
    tp1_rr: number
    tp2_rr: number
    tp3_rr: number
  }
}

export interface PositionSizeResult {
  size: number
  size_pct: number
  risk_amount: number
  risk_pct: number
  leverage: number
  kelly_pct: number
  confidence: number
  adjustment_reasons: string[]
  warnings: string[]
}

// ============================================================================
// Report Configuration
// ============================================================================

export type ReportFormat = 'markdown' | 'html' | 'json'

export interface ReportConfig {
  title: string
  period_days: number
  include_charts: boolean
  include_details: boolean
  format: ReportFormat
}

// ============================================================================
// Chart Data Types
// ============================================================================

export interface EquityCurveDataPoint {
  timestamp: string
  equity: number
  drawdown?: number
}

export interface PnHDistributionData {
  range: string
  count: number
  percentage: number
}

export interface MonthlyReturnData {
  month: string
  return_pct: number
  trades: number
}

export interface WinLossStreakData {
  type: 'win' | 'loss'
  length: number
  count: number
}

export interface SymbolPerformanceData {
  symbol: string
  pnl: number
  win_rate: number
  trades: number
  avg_return: number
}

export interface FactorWeightData {
  factor: string
  weight: number
  category: FactorCategory
}

export interface RegimePerformanceChartData {
  regime: string
  win_rate: number
  avg_pnl: number
  trade_count: number
}
