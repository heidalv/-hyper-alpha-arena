/**
 * AgentMonitorPanel — Agent 运行时监控面板
 *
 * 三大区块：
 * A. Agent 状态总览网格 (3×3)
 * B. 执行频次图表 (SVG 柱状图)
 * C. 实时滚动日志面板 (Tab 切换 + 自动滚动)
 *
 * 数据来源：
 * - REST: /api/monitor/agents/overview (10s 轮询)
 * - REST: /api/monitor/agents/frequency (60s 轮询)
 * - REST: /api/monitor/agents/logs (初始加载)
 * - WS: subscribe_agent_monitor (实时日志推送)
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { Button } from '../ui/button'
import {
  Bot, RefreshCw, Activity, AlertTriangle, CheckCircle2,
  XCircle, Clock, Zap, ChevronDown, ChevronUp,
  Pause, Play, Filter,
} from 'lucide-react'
import { wsSubscribe, wsSend } from '@/lib/wsManager'

// ── Types ──

interface AgentOverview {
  agent_id: string
  display_name: string
  llm_level: string
  status: string
  call_count: number
  success_count: number
  failure_count: number
  timeout_count: number
  success_rate: number
  last_exec_ts: number | null
  last_exec_ago_sec: number | null
  last_exec_duration_ms: number
  last_error: string
  last_error_ts: number | null
  health_score: number
  circuit_breaker_state: string
  circuit_breaker_failures: number
}

interface OverviewData {
  status: string
  uptime_seconds: number
  total_agents: number
  healthy: number
  warning: number
  critical: number
  agents: AgentOverview[]
}

interface LogEntry {
  ts: number
  level: string
  agent_id: string
  action: string
  message: string
}

interface FrequencyData {
  agents: string[]
  hours: string[]
  matrix: Record<string, number[]>
  total_calls: Record<string, number>
}

interface AgentDetail {
  agent_id: string
  display_name?: string
  llm_level?: string
  status: string
  health_score: number
  success_rate?: number
  call_count?: number
  llm?: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    last_latency_ms: number
  }
  circuit_breaker?: Record<string, unknown>
  latency?: Record<string, Record<string, number>>
  recent_logs?: LogEntry[]
}

// ── Constants ──

const POLL_OVERVIEW_MS = 10_000
const POLL_FREQ_MS = 60_000
const POLL_LOGS_MS = 3_000
const MAX_LOGS = 500

const STATUS_COLORS: Record<string, string> = {
  idle: '#22c55e',
  running: '#3b82f6',
  error: '#ef4444',
  stopped: '#6b7280',
}

const LEVEL_COLORS: Record<string, string> = {
  INFO: 'text-slate-400',
  WARN: 'text-yellow-400',
  ERROR: 'text-red-400',
  DEBUG: 'text-slate-600',
}

const AGENT_COLORS: Record<string, string> = {
  market_data: '#3b82f6',
  factor_engine: '#8b5cf6',
  intel_signal: '#06b6d4',
  risk_control: '#ef4444',
  mt_orchestrator: '#f59e0b',
  master_controller: '#ec4899',
  trade_execution: '#10b981',
  signal_bus: '#6366f1',
  genetic_optimizer: '#84cc16',
}

// ── Helper ──

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`
  if (seconds < 3600) return `${(seconds / 60).toFixed(0)}m ${(seconds % 60).toFixed(0)}s`
  return `${(seconds / 3600).toFixed(1)}h`
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

function formatHourLabel(hour: string): string {
  // "2026-07-16T10" → "16日 10:00"
  const parts = hour.split('T')
  if (parts.length < 2) return hour
  const day = parts[0].slice(-2)
  return `${day}日 ${parts[1]}:00`
}

// ── Sub-components ──

function StatusDot({ status }: { status: string }) {
  const color = STATUS_COLORS[status] || '#6b7280'
  return (
    <span
      className="inline-block w-2.5 h-2.5 rounded-full flex-shrink-0"
      style={{ backgroundColor: color, boxShadow: `0 0 6px ${color}` }}
    />
  )
}

function HealthRing({ score }: { score: number }) {
  const radius = 16
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (score / 100) * circumference
  const color = score >= 80 ? '#22c55e' : score >= 60 ? '#eab308' : score >= 30 ? '#f97316' : '#ef4444'

  return (
    <div className="relative w-10 h-10 flex items-center justify-center">
      <svg className="w-10 h-10 -rotate-90" viewBox="0 0 40 40">
        <circle cx="20" cy="20" r={radius} fill="none" stroke="#1e293b" strokeWidth="3" />
        <circle
          cx="20" cy="20" r={radius} fill="none" stroke={color} strokeWidth="3"
          strokeDasharray={circumference} strokeDashoffset={offset}
          strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.5s ease' }}
        />
      </svg>
      <span className="absolute text-xs font-bold" style={{ color }}>{score.toFixed(0)}</span>
    </div>
  )
}

function AgentCard({
  agent,
  onClick,
  selected,
}: {
  agent: AgentOverview
  onClick: () => void
  selected: boolean
}) {
  const isLowHealth = agent.health_score < 60
  return (
    <div
      onClick={onClick}
      className={`cursor-pointer rounded-lg border p-3 transition-all hover:border-slate-500 ${
        selected ? 'border-blue-500 bg-blue-950/30' :
        isLowHealth ? 'border-red-900/50 bg-red-950/10' :
        'border-slate-700/50 bg-[#1a1a2e]'
      }`}
    >
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <StatusDot status={agent.status} />
          <span className="text-sm font-medium text-white truncate">{agent.display_name}</span>
        </div>
        <HealthRing score={agent.health_score} />
      </div>
      <div className="flex items-center gap-3 text-xs text-slate-400">
        <span className="flex items-center gap-1">
          <Activity className="w-3 h-3" />
          {agent.call_count}
        </span>
        <span className="flex items-center gap-1" style={{ color: agent.success_rate >= 0.9 ? '#22c55e' : '#f59e0b' }}>
          <CheckCircle2 className="w-3 h-3" />
          {(agent.success_rate * 100).toFixed(0)}%
        </span>
        {agent.failure_count > 0 && (
          <span className="flex items-center gap-1 text-red-400">
            <XCircle className="w-3 h-3" />
            {agent.failure_count}
          </span>
        )}
        {agent.llm_level !== 'NONE' && (
          <span className="flex items-center gap-1 text-purple-400">
            <Zap className="w-3 h-3" />
            {agent.llm_level}
          </span>
        )}
      </div>
      {agent.last_exec_ago_sec !== null && (
        <div className="mt-1 text-xs text-slate-500 flex items-center gap-1">
          <Clock className="w-3 h-3" />
          {formatDuration(agent.last_exec_ago_sec)} ago
        </div>
      )}
      {agent.circuit_breaker_state !== 'closed' && agent.circuit_breaker_state !== 'unknown' && (
        <div className="mt-1 text-xs px-1.5 py-0.5 rounded bg-red-900/40 text-red-300 inline-block">
          CB: {agent.circuit_breaker_state} ({agent.circuit_breaker_failures})
        </div>
      )}
    </div>
  )
}

function AgentDetailDrawer({ detail, onClose, onReset }: { detail: AgentDetail | null; onClose: () => void; onReset: (agentId: string) => void }) {
  if (!detail) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="bg-[#1a1a2e] border border-slate-700 rounded-xl max-w-2xl w-full mx-4 max-h-[80vh] overflow-auto p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-white flex items-center gap-2">
            <Bot className="w-5 h-5 text-blue-400" />
            {detail.display_name || detail.agent_id}
          </h3>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => onReset(detail.agent_id)} className="text-xs">
              <RefreshCw className="w-3 h-3 mr-1" />
              重置错误
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose}>关闭</Button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 mb-4">
          <div className="bg-slate-800/40 rounded p-2">
            <div className="text-xs text-slate-500">状态</div>
            <div className="text-sm text-white flex items-center gap-1">
              <StatusDot status={detail.status} />
              {detail.status}
            </div>
          </div>
          <div className="bg-slate-800/40 rounded p-2">
            <div className="text-xs text-slate-500">健康度</div>
            <div className="text-sm font-bold" style={{ color: detail.health_score >= 60 ? '#22c55e' : '#ef4444' }}>
              {detail.health_score} / 100
            </div>
          </div>
          <div className="bg-slate-800/40 rounded p-2">
            <div className="text-xs text-slate-500">调用次数</div>
            <div className="text-sm text-white">{detail.call_count ?? 0}</div>
          </div>
          <div className="bg-slate-800/40 rounded p-2">
            <div className="text-xs text-slate-500">成功率</div>
            <div className="text-sm text-white">{((detail.success_rate ?? 1) * 100).toFixed(1)}%</div>
          </div>
        </div>

        {/* LLM Stats */}
        {detail.llm && detail.llm.total_tokens > 0 && (
          <div className="mb-4">
            <div className="text-xs text-slate-500 mb-1">LLM Token 消耗</div>
            <div className="bg-purple-950/30 rounded p-3 text-xs space-y-1">
              <div className="flex justify-between">
                <span className="text-slate-400">Prompt Tokens</span>
                <span className="text-purple-300">{detail.llm.prompt_tokens.toLocaleString()}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Completion Tokens</span>
                <span className="text-purple-300">{detail.llm.completion_tokens.toLocaleString()}</span>
              </div>
              <div className="flex justify-between font-bold">
                <span className="text-slate-300">Total</span>
                <span className="text-purple-200">{detail.llm.total_tokens.toLocaleString()}</span>
              </div>
              {detail.llm.last_latency_ms > 0 && (
                <div className="flex justify-between">
                  <span className="text-slate-400">最近延迟</span>
                  <span className="text-purple-300">{detail.llm.last_latency_ms.toFixed(0)}ms</span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Latency Percentiles */}
        {detail.latency && Object.keys(detail.latency).length > 0 && (
          <div className="mb-4">
            <div className="text-xs text-slate-500 mb-1">延迟百分位 (ms)</div>
            <div className="bg-slate-800/40 rounded p-2 overflow-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-slate-400 border-b border-slate-700">
                    <th className="text-left py-1 px-2">Action</th>
                    <th className="text-right py-1 px-2">P50</th>
                    <th className="text-right py-1 px-2">P95</th>
                    <th className="text-right py-1 px-2">P99</th>
                    <th className="text-right py-1 px-2">Count</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(detail.latency).map(([key, pct]) => (
                    <tr key={key} className="border-b border-slate-800/50">
                      <td className="text-left py-1 px-2 text-slate-300">{key}</td>
                      <td className="text-right py-1 px-2 text-slate-400">{pct.p50?.toFixed(0)}</td>
                      <td className="text-right py-1 px-2 text-yellow-400">{pct.p95?.toFixed(0)}</td>
                      <td className="text-right py-1 px-2 text-orange-400">{pct.p99?.toFixed(0)}</td>
                      <td className="text-right py-1 px-2 text-slate-500">{pct.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function FrequencyChart({ data }: { data: FrequencyData | null }) {
  if (!data || !data.hours.length) {
    return <div className="text-xs text-slate-500 text-center py-8">暂无频次数据</div>
  }

  const hours = data.hours
  const maxVal = Math.max(
    1,
    ...Object.values(data.matrix).flat()
  )

  return (
    <div className="overflow-auto">
      <div className="min-w-[600px]">
        {/* Legend */}
        <div className="flex flex-wrap gap-2 mb-2">
          {data.agents.map(aid => (
            <span key={aid} className="flex items-center gap-1 text-xs text-slate-400">
              <span className="inline-block w-2 h-2 rounded-sm" style={{ backgroundColor: AGENT_COLORS[aid] || '#6b7280' }} />
              {aid}
            </span>
          ))}
        </div>

        {/* Chart */}
        <div className="flex items-end gap-1 h-32 bg-slate-900/40 rounded p-2">
          {hours.map((hour, idx) => {
            let stackHeight = 0
            return (
              <div key={hour} className="flex-1 flex flex-col-reverse min-w-0 group relative">
                {data.agents.map(aid => {
                  const val = data.matrix[aid]?.[idx] || 0
                  if (val === 0) return null
                  const h = (val / maxVal) * 100
                  stackHeight += val
                  return (
                    <div
                      key={aid}
                      style={{
                        height: `${h}%`,
                        backgroundColor: AGENT_COLORS[aid] || '#6b7280',
                        minHeight: val > 0 ? '2px' : 0,
                      }}
                      className="w-full transition-all"
                      title={`${aid}: ${val}`}
                    />
                  )
                })}
                {/* Tooltip */}
                {stackHeight > 0 && (
                  <div className="absolute bottom-full mb-1 left-1/2 -translate-x-1/2 bg-black/80 text-white text-xs px-2 py-1 rounded whitespace-nowrap opacity-0 group-hover:opacity-100 pointer-events-none z-10">
                    {formatHourLabel(hour)}: {stackHeight} calls
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {/* X-axis labels */}
        <div className="flex gap-1 mt-1">
          {hours.map((hour, idx) => (
            <div key={hour} className="flex-1 text-center text-xs text-slate-600 truncate">
              {idx % Math.ceil(hours.length / 12) === 0 ? formatHourLabel(hour) : ''}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function LogPanel({
  logs,
  agentFilter,
  levelFilter,
  autoScroll,
  onToggleAutoScroll,
}: {
  logs: LogEntry[]
  agentFilter: string
  levelFilter: string | null
  autoScroll: boolean
  onToggleAutoScroll: () => void
}) {
  const logEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  const filtered = logs.filter(log => {
    if (agentFilter !== 'all' && log.agent_id !== agentFilter) return false
    if (levelFilter && log.level !== levelFilter) return false
    return true
  })

  useEffect(() => {
    if (autoScroll && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [filtered, autoScroll])

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between mb-2 gap-2">
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-slate-500" />
          <select
            value={agentFilter}
            onChange={() => {}} /* controlled by parent */
            disabled
            className="bg-slate-800 text-slate-300 text-xs rounded px-2 py-1 border border-slate-700"
          >
            <option value="all">全部 Agent</option>
          </select>
          <span className="text-xs text-slate-500">
            ({filtered.length} 条{logs.length > MAX_LOGS ? ` / ${logs.length} 总计` : ''})
          </span>
        </div>
        <Button variant="ghost" size="sm" onClick={onToggleAutoScroll} className="text-xs">
          {autoScroll ? <Pause className="w-3 h-3 mr-1" /> : <Play className="w-3 h-3 mr-1" />}
          {autoScroll ? '暂停滚动' : '恢复滚动'}
        </Button>
      </div>

      {/* Log lines */}
      <div
        ref={scrollContainerRef}
        className="flex-1 overflow-auto bg-black/40 rounded-lg border border-slate-800 p-3 font-mono text-xs space-y-0.5"
        style={{ maxHeight: '400px', minHeight: '200px' }}
      >
        {filtered.length === 0 ? (
          <div className="text-slate-600 text-center py-8">暂无日志</div>
        ) : (
          filtered.slice(-MAX_LOGS).map((log, idx) => (
            <div key={`${log.ts}-${idx}`} className="flex items-start gap-2 hover:bg-slate-800/30 px-1 rounded">
              <span className="text-slate-600 flex-shrink-0">{formatTime(log.ts)}</span>
              <span className={`flex-shrink-0 font-bold w-12 ${LEVEL_COLORS[log.level] || 'text-slate-400'}`}>
                [{log.level}]
              </span>
              <span className="flex-shrink-0 text-blue-400 w-32 truncate" title={log.agent_id}>
                [{log.agent_id}]
              </span>
              <span className="text-slate-300 break-all">{log.message}</span>
            </div>
          ))
        )}
        <div ref={logEndRef} />
      </div>
    </div>
  )
}

// ── Main Component ──

export default function AgentMonitorPanel() {
  const [overview, setOverview] = useState<OverviewData | null>(null)
  const [frequency, setFrequency] = useState<FrequencyData | null>(null)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null)
  const [detail, setDetail] = useState<AgentDetail | null>(null)
  const [agentFilter, setAgentFilter] = useState<string>('all')
  const [levelFilter, setLevelFilter] = useState<string | null>(null)
  const [autoScroll, setAutoScroll] = useState(true)
  const [showFrequency, setShowFrequency] = useState(true)
  const wsUnsubRef = useRef<(() => void) | null>(null)
  const overviewTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const freqTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const logTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  // Fetch overview
  const fetchOverview = useCallback(async () => {
    try {
      const res = await fetch('/api/monitor/agents/overview')
      if (res.ok) setOverview(await res.json())
    } catch (e) {
      console.error('[AgentMonitor] overview fetch error:', e)
    }
  }, [])

  // Fetch frequency
  const fetchFrequency = useCallback(async () => {
    try {
      const res = await fetch('/api/monitor/agents/frequency?hours=24')
      if (res.ok) setFrequency(await res.json())
    } catch (e) {
      console.error('[AgentMonitor] frequency fetch error:', e)
    }
  }, [])

  // Fetch logs (polling as WS backup)
  const fetchLogs = useCallback(async () => {
    try {
      const res = await fetch('/api/monitor/agents/logs?limit=200')
      if (res.ok) {
        const data = await res.json()
        if (data.logs) {
          setLogs(prev => {
            // Merge: keep most recent unique by ts+agent_id+message
            const merged = [...prev, ...data.logs]
            const seen = new Set<string>()
            const unique = merged
              .sort((a, b) => a.ts - b.ts)
              .filter(l => {
                const key = `${l.ts}-${l.agent_id}-${l.message}`
                if (seen.has(key)) return false
                seen.add(key)
                return true
              })
              .slice(-MAX_LOGS)
            return unique
          })
        }
      }
    } catch (e) {
      console.error('[AgentMonitor] logs fetch error:', e)
    }
  }, [])

  // Fetch agent detail
  const fetchDetail = useCallback(async (agentId: string) => {
    try {
      const res = await fetch(`/api/monitor/agents/${agentId}`)
      if (res.ok) {
        const d = await res.json()
        setDetail(d)
      }
    } catch (e) {
      console.error('[AgentMonitor] detail fetch error:', e)
    }
  }, [])

  // Initial load + polling
  useEffect(() => {
    setLoading(true)
    Promise.all([fetchOverview(), fetchFrequency(), fetchLogs()]).finally(() => setLoading(false))

    overviewTimer.current = setInterval(fetchOverview, POLL_OVERVIEW_MS)
    freqTimer.current = setInterval(fetchFrequency, POLL_FREQ_MS)
    logTimer.current = setInterval(fetchLogs, POLL_LOGS_MS)

    return () => {
      if (overviewTimer.current) clearInterval(overviewTimer.current)
      if (freqTimer.current) clearInterval(freqTimer.current)
      if (logTimer.current) clearInterval(logTimer.current)
    }
  }, [fetchOverview, fetchFrequency, fetchLogs])

  // WebSocket subscription for real-time updates
  useEffect(() => {
    wsSend({ type: 'subscribe_agent_monitor' })

    const unsub = wsSubscribe((msg) => {
      if (msg.type === 'agent_monitor_update') {
        const data = msg.data as { agents?: AgentOverview[]; uptime_seconds?: number } | undefined
        const agents = data?.agents
        const uptime = data?.uptime_seconds
        if (agents && agents.length > 0) {
          setOverview(prev => ({
            status: 'healthy',
            uptime_seconds: uptime ?? prev?.uptime_seconds ?? 0,
            total_agents: agents.length,
            healthy: agents.filter(a => a.health_score >= 60).length,
            warning: agents.filter(a => a.health_score >= 30 && a.health_score < 60).length,
            critical: agents.filter(a => a.health_score < 30).length,
            agents: agents,
          }))
        }
      }
    })
    wsUnsubRef.current = unsub

    return () => {
      unsub()
      wsSend({ type: 'unsubscribe_agent_monitor' })
    }
  }, [])

  const handleAgentClick = useCallback((agentId: string) => {
    setSelectedAgent(prev => prev === agentId ? null : agentId)
    setAgentFilter(prev => prev === agentId ? 'all' : agentId)
    fetchDetail(agentId)
  }, [fetchDetail])

  const handleReset = useCallback(async (agentId: string) => {
    try {
      await fetch(`/api/monitor/agents/${agentId}/reset`, { method: 'POST' })
      fetchOverview()
      fetchDetail(agentId)
    } catch (e) {
      console.error('[AgentMonitor] reset error:', e)
    }
  }, [fetchOverview, fetchDetail])

  const handleRefresh = useCallback(() => {
    setLoading(true)
    Promise.all([fetchOverview(), fetchFrequency(), fetchLogs()]).finally(() => setLoading(false))
  }, [fetchOverview, fetchFrequency, fetchLogs])

  const agents = overview?.agents ?? []
  const uptimeStr = overview ? formatDuration(overview.uptime_seconds) : '--'

  return (
    <div className="p-4 space-y-4 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot className="w-5 h-5 text-blue-400" />
          <h2 className="text-lg font-bold text-white">Agent 运行时监控</h2>
          {overview && (
            <div className="flex items-center gap-3 ml-4 text-xs">
              <span className="text-slate-400">运行: {uptimeStr}</span>
              <span className="flex items-center gap-1 text-green-400">
                <CheckCircle2 className="w-3 h-3" />
                {overview.healthy} 健康
              </span>
              {overview.warning > 0 && (
                <span className="flex items-center gap-1 text-yellow-400">
                  <AlertTriangle className="w-3 h-3" />
                  {overview.warning} 警告
                </span>
              )}
              {overview.critical > 0 && (
                <span className="flex items-center gap-1 text-red-400">
                  <XCircle className="w-3 h-3" />
                  {overview.critical} 异常
                </span>
              )}
            </div>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={handleRefresh} disabled={loading} className="text-xs">
          <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

      {/* Section A: Agent Status Grid */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <Activity className="w-4 h-4 text-blue-400" />
          <span className="text-sm font-medium text-slate-300">Agent 状态总览</span>
        </div>
        <div className="grid grid-cols-3 gap-3">
          {agents.map(agent => (
            <AgentCard
              key={agent.agent_id}
              agent={agent}
              onClick={() => handleAgentClick(agent.agent_id)}
              selected={selectedAgent === agent.agent_id}
            />
          ))}
          {agents.length === 0 && (
            <div className="col-span-3 text-center text-slate-500 py-8">
              {loading ? '加载中...' : '暂无 Agent 数据'}
            </div>
          )}
        </div>
      </div>

      {/* Section B: Frequency Chart */}
      <Card className="bg-[#1a1a2e] border-slate-700/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm text-slate-300 flex items-center justify-between">
            <span className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-blue-400" />
              执行频次统计 (24h)
            </span>
            <Button variant="ghost" size="sm" onClick={() => setShowFrequency(!showFrequency)} className="text-xs">
              {showFrequency ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            </Button>
          </CardTitle>
        </CardHeader>
        {showFrequency && (
          <CardContent>
            <FrequencyChart data={frequency} />
          </CardContent>
        )}
      </Card>

      {/* Section C: Real-time Log Panel */}
      <Card className="bg-[#1a1a2e] border-slate-700/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm text-slate-300 flex items-center gap-2">
            <Activity className="w-4 h-4 text-blue-400" />
            实时滚动日志
          </CardTitle>
        </CardHeader>
        <CardContent>
          {/* Agent Tab Filter */}
          <div className="flex flex-wrap gap-1 mb-2">
            <button
              onClick={() => setAgentFilter('all')}
              className={`px-2 py-1 text-xs rounded transition-colors ${
                agentFilter === 'all' ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
              }`}
            >
              全部
            </button>
            {agents.map(a => (
              <button
                key={a.agent_id}
                onClick={() => setAgentFilter(a.agent_id)}
                className={`px-2 py-1 text-xs rounded transition-colors ${
                  agentFilter === a.agent_id ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                }`}
              >
                {a.agent_id}
              </button>
            ))}
          </div>

          {/* Level Filter */}
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs text-slate-500">级别:</span>
            {['INFO', 'WARN', 'ERROR'].map(lvl => (
              <button
                key={lvl}
                onClick={() => setLevelFilter(levelFilter === lvl ? null : lvl)}
                className={`px-2 py-0.5 text-xs rounded ${
                  levelFilter === lvl ? 'bg-slate-600 text-white' : 'bg-slate-800 text-slate-400'
                } ${lvl === 'ERROR' ? 'hover:text-red-400' : lvl === 'WARN' ? 'hover:text-yellow-400' : ''}`}
              >
                {lvl}
              </button>
            ))}
            {levelFilter && (
              <button onClick={() => setLevelFilter(null)} className="text-xs text-slate-500 hover:text-white">
                清除
              </button>
            )}
          </div>

          <LogPanel
            logs={logs}
            agentFilter={agentFilter}
            levelFilter={levelFilter}
            autoScroll={autoScroll}
            onToggleAutoScroll={() => setAutoScroll(!autoScroll)}
          />
        </CardContent>
      </Card>

      {/* Detail Drawer */}
      <AgentDetailDrawer detail={detail} onClose={() => { setDetail(null); setSelectedAgent(null); setAgentFilter('all') }} onReset={handleReset} />
    </div>
  )
}
