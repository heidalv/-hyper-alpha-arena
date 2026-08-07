// ── Core Types ──

export interface User {
  id: number
  username: string
}

export interface Account {
  id: number
  user_id: number
  name: string
  account_type: string
  initial_capital: number
  current_cash: number
  frozen_cash: number
}

export interface Overview {
  account: Account
  total_assets: number
  positions_value: number
  portfolio?: {
    total_assets: number
    positions_value: number
  }
}

export interface Position {
  id: number
  account_id: number
  symbol: string
  name: string
  market: string
  quantity: number
  available_quantity: number
  avg_cost: number
  last_price?: number | null
  market_value?: number | null
  unrealized_pnl?: number | null
  unrealized_pnl_pct?: number | null
  pnl_percent?: number | null
  leverage?: number
  side?: string
  stop_loss_price?: number | null
  take_profit_price?: number | null
  // 设计文档语义别名（止盈/止损快捷字段）
  take_profit?: number | null
  stop_loss?: number | null
}

export interface Order {
  id: number
  order_no: string
  symbol: string
  name: string
  market: string
  side: string
  order_type: string
  price?: number
  quantity: number
  filled_quantity: number
  status: string
}

export interface Trade {
  id: number
  order_id: number
  account_id: number
  symbol: string
  name: string
  market: string
  side: string
  price: number
  quantity: number
  commission: number
  trade_time: string
}

export interface AIDecision {
  id: number
  account_id: number
  decision_time: string
  reason: string
  operation: string
  symbol?: string
  prev_portion: number
  target_portion: number
  total_balance: number
  executed: string
  order_id?: number
}

export interface FullAutoSession {
  session_id: string
  account_id?: number
  account_name?: string
  status: 'running' | 'defensive' | 'paused' | 'stopped'
  pause_reason?: string | null
  symbols: string[]
  risk_level?: string
  trading_mode?: string
  total_pnl: number
  total_trades?: number
  win_rate?: number
  active_count?: number
  total_strategies_created?: number
  current_drawdown: number
  peak_balance: number
  max_total_drawdown_pct: number
  active_strategy_ids?: number[]
  terminated_strategy_ids?: number[]
  // 会话运行时长（分钟）与暂停策略数，部分后端返回
  duration_minutes?: number
  paused_count?: number
  events?: Array<{
    type: string
    message: string
    timestamp: string
  }>
  started_at?: string
  stopped_at?: string | null
  created_at: string
}

export interface AIStrategy {
  id: number
  name: string
  primary_symbol: string
  status: 'active' | 'paused' | 'terminated'
  total_pnl: number
  total_trades: number
  win_rate: number
  created_at: string
  genome?: Record<string, any>
}

export interface SignalDetection {
  id: number
  signal_name: string
  symbol: string
  direction: 'buy' | 'sell' | 'hold'
  confidence: number
  reason: string
  detected_at: string
}

export interface AssetCurvePoint {
  timestamp: string
  equity: number
  pnl: number
}

// 权益曲线单点（图表用），time 为 lightweight-charts 兼容的时间值
export interface EquityPoint {
  time: string | number
  value: number
}

// ── WebSocket message types ──

export interface WSMessage {
  type: string
  [key: string]: any
}

export interface WSSnapshot extends WSMessage {
  type: 'snapshot' | 'full_snapshot' | 'snapshot_fast' | 'snapshot_full'
  seq?: number
  overview?: Overview
  positions?: Position[]
  orders?: Order[]
  trades?: Trade[]
  ai_decisions?: AIDecision[]
  all_asset_curves?: Record<string, AssetCurvePoint[]>
}

export interface WSDelta extends WSMessage {
  type: 'delta'
  seq?: number
  changes?: {
    overview?: Partial<Overview>
    positions?: Array<Position & { _removed?: boolean }>
    orders?: any[]
    orders_removed?: number[]
    trades?: Trade[]
    ai_decisions?: AIDecision[]
    all_asset_curves?: Record<string, AssetCurvePoint[]>
  }
}
