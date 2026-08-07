/**
 * 交易所抽象层 & 跨交易所套利 API
 */

export interface ExchangeBalance {
  exchange: string
  total_equity: number
  available_balance: number
  frozen_margin: number
  unrealized_pnl: number
  margin_ratio: number
}

export interface ExchangePosition {
  symbol: string
  side: string
  size: number
  entry_price: number
  mark_price?: number
  unrealized_pnl: number
  leverage: number
  exchange: string
}

export interface ExchangeStatus {
  exchange: string
  connected: boolean
  supports_spot?: boolean
  supports_futures?: boolean
  total_equity?: number | null
  available_balance?: number | null
  error?: string
}

export interface CrossExchangeSpread {
  symbol: string
  exchange_a: string
  exchange_b: string
  price_a: number
  price_b: number
  spread_pct: number
  direction: string
}

export interface CrossExchangeTrade {
  id: string
  symbol: string
  strategy: string
  status: string
  pnl: number
}

export interface CrossExchangeExposure {
  total_equity: number
  total_positions_notional: number
  exposure_pct: number
  total_exposure_pct?: number
  active_trades?: number
  exchanges: { exchange: string; equity: number; positions_notional?: number; position_count?: number; error?: string }[]
  is_safe: boolean
}

export interface LegRiskStatus {
  trade_id: string
  leg_exchange: string
  retries: number
  max_retries: number
  status: 'healthy' | 'retrying' | 'emergency_close'
}

// ── API Functions ──

export async function getExchangeStatuses(): Promise<ExchangeStatus[]> {
  const res = await fetch('/api/exchange/statuses')
  if (!res.ok) return []
  return res.json()
}

export async function getExchangeBalance(exchange: string): Promise<ExchangeBalance> {
  const res = await fetch(`/api/exchange/${exchange}/balance`)
  if (!res.ok) throw new Error('Failed to fetch balance')
  return res.json()
}

export async function getExchangePositions(exchange: string): Promise<ExchangePosition[]> {
  const res = await fetch(`/api/exchange/${exchange}/positions`)
  if (!res.ok) return []
  return res.json()
}

export async function getAllPositions(): Promise<ExchangePosition[]> {
  const res = await fetch('/api/exchange/positions/all')
  if (!res.ok) return []
  return res.json()
}

export async function scanCrossExchangeSpreads(): Promise<CrossExchangeSpread[]> {
  const res = await fetch('/api/exchange/cross-arb/spreads')
  if (!res.ok) return []
  const data = await res.json()
  return data.spreads ?? data
}

export async function getCrossExchangeTrades(): Promise<CrossExchangeTrade[]> {
  const res = await fetch('/api/exchange/cross-arb/trades')
  if (!res.ok) return []
  return res.json()
}

export async function getCrossExchangeExposure(): Promise<CrossExchangeExposure> {
  const res = await fetch('/api/exchange/cross-arb/exposure')
  if (!res.ok) return {
    total_equity: 0, total_positions_notional: 0, exposure_pct: 0,
    total_exposure_pct: 0, active_trades: 0,
    exchanges: [], is_safe: true,
  }
  const data = await res.json()
  return {
    ...data,
    total_exposure_pct: data.total_exposure_pct ?? data.exposure_pct ?? 0,
    active_trades: data.active_trades ?? 0,
  }
}

export async function getLegRiskStatuses(): Promise<LegRiskStatus[]> {
  return []
}

// ── Rebate/Points Arbitrage Types ──

export interface RebateStatus {
  engine_enabled: boolean
  mode: string
  scan_count: number
  execution_count: number
  active_positions: number
  total_rebate_pnl: number
  wash_trade_safe: boolean
  next_safe_interval_sec: number
  error?: string
}

export interface RebateOpportunity {
  strategy_type: string
  is_viable: boolean
  expected_monthly_value: number
  required_volume_usd: number
  risk_score: number
  confidence: number
  volume_value_ratio: number
  details: Record<string, unknown>
}

export interface RebatePosition {
  position_id: string
  strategy_type: string
  source_exchange: string
  target_exchange: string | null
  symbol: string
  side_a_size: number
  side_b_size: number
  current_pnl: number
  accumulated_rebate: number
  accumulated_points: number
  hold_duration_hours: number
  status: string
  paper_mode: boolean
}

export interface RebateCapital {
  total_equity: number
  allocations: Record<string, number>
  used: Record<string, number>
  utilization: Record<string, number>
  rebate_available: number
  total_utilization_pct: number
}

export interface RebateAnalytics {
  total_trades: number
  win_rate: number
  total_pnl: number
  total_rebate: number
  total_points: number
  net_pnl: number
  by_strategy: Record<string, { count: number; pnl: number; rebate: number }>
  error?: string
}

// ── Rebate API Functions ──

export async function getRebateStatus(): Promise<RebateStatus> {
  const res = await fetch('/api/rebate/status')
  if (!res.ok) return { engine_enabled: false, mode: 'paper', scan_count: 0, execution_count: 0, active_positions: 0, total_rebate_pnl: 0, wash_trade_safe: false, next_safe_interval_sec: 0 }
  return res.json()
}

export async function getRebateOpportunities(): Promise<RebateOpportunity[]> {
  const res = await fetch('/api/rebate/opportunities')
  if (!res.ok) return []
  const data = await res.json()
  return data.opportunities ?? []
}

export async function getRebatePositions(): Promise<RebatePosition[]> {
  const res = await fetch('/api/rebate/positions')
  if (!res.ok) return []
  const data = await res.json()
  return data.positions ?? []
}

export async function getRebateCapital(): Promise<RebateCapital | null> {
  const res = await fetch('/api/rebate/capital')
  if (!res.ok) return null
  return res.json()
}

export async function getRebateAnalytics(): Promise<RebateAnalytics> {
  const res = await fetch('/api/rebate/analytics')
  if (!res.ok) return { total_trades: 0, win_rate: 0, total_pnl: 0, total_rebate: 0, total_points: 0, net_pnl: 0, by_strategy: {} }
  return res.json()
}

export async function triggerRebateScan(): Promise<{ triggered: boolean; viable_count: number; error?: string }> {
  const res = await fetch('/api/rebate/scan', { method: 'POST' })
  if (!res.ok) return { triggered: false, viable_count: 0, error: 'request_failed' }
  return res.json()
}

export async function closeRebatePosition(positionId: string): Promise<{ success: boolean; error?: string }> {
  const res = await fetch(`/api/rebate/positions/${positionId}/close`, { method: 'POST' })
  if (!res.ok) return { success: false, error: 'request_failed' }
  return res.json()
}

export async function emergencyCloseAllRebate(): Promise<{ success: boolean; closed_count: number }> {
  const res = await fetch('/api/rebate/emergency/close-all', { method: 'POST' })
  if (!res.ok) return { success: false, closed_count: 0 }
  return res.json()
}
