import { apiRequest } from './client'
import type { AIStrategy, SignalDetection } from './types'

export const getStrategies = (accountId: number) =>
  apiRequest<AIStrategy[]>(`/ai-strategy/list?account_id=${accountId}`)

export const getStrategyById = (id: number) =>
  apiRequest<AIStrategy>(`/ai-strategy/${id}`)

export const getSignalDefinitions = () =>
  apiRequest<any[]>('/signals/definitions')

export const getSignalDetections = (symbol?: string, limit = 30) => {
  const params = new URLSearchParams()
  if (symbol) params.append('symbol', symbol)
  params.append('limit', String(limit))
  return apiRequest<SignalDetection[]>(`/signals/detections?${params}`)
}
