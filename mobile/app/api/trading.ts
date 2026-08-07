import { apiRequest } from './client'
import type { Position, Order, Trade, AIDecision, Overview } from './types'

export interface PaperPosition {
  id: number
  symbol: string
  side: string
  size: number
  entry_price: number
  mark_price: number
  leverage: number
  margin: number
  unrealized_pnl: number
  tp_price: number
  sl_price: number
  liquidation_price?: number
  timeframe_tier: string
  trade_nature: string
  opened_at: string
  status: string
  close_reason?: string
}

// ── Account & Overview ──
export const getAccounts = () => apiRequest<any[]>('/accounts')
export const getAccountById = (id: number) => apiRequest<any>(`/accounts/${id}`)

// ── Paper Trading Positions (真实模拟持仓) ──
export const getPaperPositions = (accountId: number) =>
  apiRequest<PaperPosition[]>(`/paper/positions/${accountId}`)

// ── Positions ──
export const getPositions = (accountId: number) =>
  apiRequest<{ positions: any[] }>(`/arena/positions?account_id=${accountId}`)

// ── Trades ──
export const getTrades = (accountId: number, limit = 50) =>
  apiRequest<any[]>(`/arena/trades?account_id=${accountId}&limit=${limit}`)

// ── Orders ──
export const placeBuy = (accountId: number, symbol: string, quantity: number, leverage = 5, side: string = 'buy') =>
  apiRequest<any>('/arena/buy', {
    method: 'POST',
    body: JSON.stringify({ account_id: accountId, symbol, quantity, leverage, side }),
  })

export const placeSell = (accountId: number, symbol: string, quantity: number, side: string = 'sell') =>
  apiRequest<any>('/arena/sell', {
    method: 'POST',
    body: JSON.stringify({ account_id: accountId, symbol, quantity, side }),
  })

// ── PnL ──
export const updatePnl = () => apiRequest<any>('/arena/update-pnl', { method: 'POST' })

// ── Analytics ──
export const getAnalytics = (accountId: number) =>
  apiRequest<any>(`/arena/analytics?account_id=${accountId}`)

// ── AI Decisions ──
export const getAIDecisions = (accountId: number, limit = 20) =>
  apiRequest<AIDecision[]>(`/arena/ai-decisions?account_id=${accountId}&limit=${limit}`)
