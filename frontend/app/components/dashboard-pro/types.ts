/**
 * 交易矩阵仪表盘 — 共享类型定义
 */

export type TradingModeKind = 'paper' | 'testnet' | 'mainnet'

/** 一个「账户 x 交易所 x 模式」组合 —— 多选对比的基本单元 */
export interface AccountSelection {
  account_id: number
  exchange: string
  trading_mode: TradingModeKind
  /** 前端展示用标签，如 "AI-1 · Hyperliquid · 实盘" */
  label: string
}

export interface DashboardPositionDTO {
  symbol: string
  side: string
  size: number
  entry_price: number
  mark_price: number
  unrealized_pnl: number
  leverage: number | null
}

/** 对应后端 /api/dashboard/overview 单个账户结果 */
export interface AccountOverview {
  account_id: number
  exchange: string
  trading_mode: TradingModeKind
  account_name: string | null
  equity: number
  available_cash: number
  unrealized_pnl: number
  realized_pnl: number
  total_pnl: number
  win_rate: number
  total_trades: number
  active_positions: number
  positions: DashboardPositionDTO[]
  error: string | null
  updated_at: string
}

export type WsConnStatus = 'idle' | 'connecting' | 'open' | 'error' | 'closed'

/** 网格中的单个 widget 实例（持久化对象） */
export interface WidgetInstance {
  id: string
  type: string
  x: number
  y: number
  w: number
  h: number
  config?: Record<string, unknown>
}

export interface DashboardLayoutDTO {
  id: number
  name: string
  is_active: boolean
  widgets: WidgetInstance[]
  selected_accounts: AccountSelection[]
  created_at: string | null
  updated_at: string | null
}

/** 所有 widget 组件共享的 props */
export interface WidgetProps {
  overviews: AccountOverview[]
  selections: AccountSelection[]
  wsStatusByAccount: Record<number, WsConnStatus>
  config?: Record<string, unknown>
  onConfigChange?: (config: Record<string, unknown>) => void
}
