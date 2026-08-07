import { apiRequest } from './client'
import type { FullAutoSession } from './types'

export const getSessions = () =>
  apiRequest<FullAutoSession[]>('/full-auto/sessions')

export const getSessionStatus = (sessionId: string) =>
  apiRequest<FullAutoSession>(`/full-auto/status/${sessionId}`)

export const startSession = (data: {
  symbols: string[]
  account_id: number
  paper_account_id?: number
  risk_level?: string
  risk_mode?: string
  trading_mode?: string
  auto_coin_enabled?: boolean
  max_total_drawdown_pct?: number
  max_concurrent_strategies?: number
  daily_loss_limit_pct?: number
  health_check_interval?: number
  min_strategy_lifetime_days?: number
  consecutive_loss_elimination?: number
}) =>
  apiRequest<{ session_id: string; message: string }>('/full-auto/start', {
    method: 'POST',
    body: JSON.stringify(data),
  })

export const pauseSession = (sessionId: string) =>
  apiRequest<{ message: string }>(`/full-auto/pause/${sessionId}`, { method: 'POST' })

export const resumeSession = (sessionId: string) =>
  apiRequest<{ message: string }>(`/full-auto/resume/${sessionId}`, { method: 'POST' })

export const stopSession = (sessionId: string) =>
  apiRequest<{ message: string }>(`/full-auto/stop/${sessionId}`, { method: 'POST' })

export const deleteSession = (sessionId: string) =>
  apiRequest<{ message: string }>(`/full-auto/${sessionId}`, { method: 'DELETE' })

export const healthCheck = (sessionId: string) =>
  apiRequest<any>(`/full-auto/health-check/${sessionId}`, { method: 'POST' })

export const getTierStatus = (sessionId: string) =>
  apiRequest<any>(`/full-auto/tier-status/${sessionId}`)
