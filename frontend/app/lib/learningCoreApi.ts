/**
 * 统一进化学习内核（Athena / Evolution Nexus）前端客户端
 *
 * 对接后端 /api/learning/* 与 WebSocket learning_events 频道。
 * 供"进化中枢"统一工作台使用（血缘实时管线 / 概览 / 触发）。
 */

import { apiRequest } from './api'
import { wsSubscribe, wsSend, type WsMessage } from './wsManager'

// ── 类型 ──

export type EvolutionStage =
  | 'hypothesis'
  | 'validate'
  | 'evolve'
  | 'learn'
  | 'rl_decide'
  | 'deploy'
  | 'observe'
  | 'feedback'

export type EvolutionStatus =
  | 'pending'
  | 'passed'
  | 'rejected'
  | 'deployed'
  | 'rolled_back'

export interface EvolutionEnvelope {
  envelope_id: string
  lineage_id: string
  parent_id: string | null
  stage: EvolutionStage
  source: string
  symbol: string | null
  status: EvolutionStatus
  payload: Record<string, unknown>
  metrics: Record<string, unknown>
  created_at: string
}

export interface LineageSummary {
  lineage_id: string
  node_count: number
  started_at: string
  updated_at: string
  latest_stage?: EvolutionStage
  latest_status?: EvolutionStatus
  latest_source?: string
  symbol?: string | null
}

export interface LearningOverview {
  core: { flags: Record<string, boolean>; ledger: LedgerStats }
  hypothesis: Record<string, unknown>
  hermes: Record<string, unknown>
  learning_loop: Record<string, unknown>
  evolution: Record<string, unknown>
  backends: Record<string, unknown>
  generated_at: string
}

export interface LedgerStats {
  total_envelopes: number
  total_lineages: number
  by_stage: Record<string, number>
  by_status: Record<string, number>
}

// ── REST ──

export async function getLearningOverview(): Promise<LearningOverview> {
  const res = await apiRequest('/learning/overview')
  return res.json()
}

export async function getRecentLineages(limit = 30): Promise<LineageSummary[]> {
  const res = await apiRequest(`/learning/lineages?limit=${limit}`)
  const data = await res.json()
  return data.lineages ?? []
}

export async function getLineage(lineageId: string): Promise<EvolutionEnvelope[]> {
  const res = await apiRequest(`/learning/lineage?lineage_id=${encodeURIComponent(lineageId)}`)
  const data = await res.json()
  return data.events ?? []
}

export async function getRecentEvents(limit = 100, stage?: EvolutionStage): Promise<EvolutionEnvelope[]> {
  const q = stage ? `?limit=${limit}&stage=${stage}` : `?limit=${limit}`
  const res = await apiRequest(`/learning/events${q}`)
  const data = await res.json()
  return data.events ?? []
}

export async function getLearningFlags(): Promise<{ flags: Record<string, boolean>; keys: string[] }> {
  const res = await apiRequest('/learning/flags')
  return res.json()
}

export async function setLearningFlag(key: string, value: boolean) {
  const res = await apiRequest('/learning/flags', {
    method: 'POST',
    body: JSON.stringify({ key, value }),
  })
  return res.json()
}

export async function runHypothesisCycle(symbols?: string[], regime?: string) {
  const res = await apiRequest('/learning/hypothesis/run', {
    method: 'POST',
    body: JSON.stringify({ symbols: symbols ?? null, regime: regime ?? null }),
  })
  return res.json()
}

export async function runHermesTask(task: string) {
  const res = await apiRequest(`/learning/hermes/run/${task}`, { method: 'POST' })
  return res.json()
}

// ── WebSocket 实时血缘事件 ──

/**
 * 订阅内核血缘事件（EvolutionEnvelope）。返回取消订阅函数。
 * 自动发送 subscribe_learning_events，并过滤出 type=learning_event 且带 data 的消息。
 */
export function subscribeLearningEvents(onEvent: (env: EvolutionEnvelope) => void): () => void {
  const unsub = wsSubscribe((msg: WsMessage) => {
    if (msg?.type === 'learning_event' && msg?.data && typeof msg.data === 'object') {
      onEvent(msg.data as unknown as EvolutionEnvelope)
    }
  })
  wsSend({ type: 'subscribe_learning_events' })
  return () => {
    try {
      wsSend({ type: 'unsubscribe_learning_events' })
    } catch {
      // ignore
    }
    unsub()
  }
}
