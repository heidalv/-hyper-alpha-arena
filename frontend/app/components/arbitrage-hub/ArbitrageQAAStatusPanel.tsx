/**
 * QAA 管道状态 — 只读展示最近一次 WorkflowRun 与各策略 Agent 链
 */
import { useCallback, useEffect, useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'

interface QAAStep {
  agent_id: string
  action: string
  status: string
  duration_ms: number
}

interface StrategySpec {
  strategy_id: string
  name: string
  ai_decision_mode?: string
  coordination_group?: string
  macro_filter_required?: boolean
  qaa_agent_chain?: string[]
}

function statusBadgeClass(status: string): string {
  const s = (status || '').toLowerCase()
  if (s.includes('complete') || s.includes('success') || s === 'ok') {
    return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/20'
  }
  if (s.includes('fail') || s.includes('error')) {
    return 'bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/20'
  }
  if (s.includes('skip') || s.includes('hold')) {
    return 'bg-amber-500/10 text-amber-800 dark:text-amber-400 border-amber-500/20'
  }
  return 'bg-muted text-muted-foreground border-border/60'
}

export default function ArbitrageQAAStatusPanel() {
  const [loading, setLoading] = useState(true)
  const [lastRun, setLastRun] = useState<Record<string, unknown> | null>(null)
  const [strategies, setStrategies] = useState<StrategySpec[]>([])
  const [subPools, setSubPools] = useState<Record<string, { cap_usd: number; used_usd: number }>>({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/arbitrage-paper/qaa/last-run')
      if (res.ok) {
        const data = await res.json()
        setLastRun(data.last_run || null)
        setStrategies(data.strategies || [])
        setSubPools(data.strategy_sub_pools || {})
      }
    } catch {
      /* ignore */
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [load])

  const steps = (lastRun?.steps as QAAStep[]) || []
  const decision = (lastRun?.decision as Record<string, unknown>) || {}

  return (
    <div className="space-y-4 max-w-4xl">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-500" />
          <h3 className="text-lg font-semibold text-foreground">QAA 管道状态</h3>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border border-border bg-background text-foreground hover:bg-muted/50 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
          刷新
        </button>
      </div>

      {loading && (
        <p className="text-sm text-muted-foreground">加载中…</p>
      )}

      {/* 最近一次 Tick */}
      <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
        <p className="text-xs font-medium text-muted-foreground mb-3">最近一次 Tick</p>
        {lastRun && Object.keys(lastRun).length > 0 ? (
          <div className="space-y-2 text-sm">
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              <span className="text-muted-foreground">
                Run ID:{' '}
                <span className="font-mono text-foreground">{(lastRun.run_id as string) || '—'}</span>
              </span>
              <span className="text-muted-foreground">
                决策:{' '}
                <span className="font-medium text-foreground">{(decision.action as string) || '—'}</span>
              </span>
              <span className="text-muted-foreground">
                策略:{' '}
                <span className="font-medium text-foreground">
                  {(decision.strategy_id as string) || (decision.strategy as string) || '—'}
                </span>
              </span>
            </div>
            {(decision.reasoning as string) && (
              <p className="text-xs text-muted-foreground leading-relaxed">
                {decision.reasoning as string}
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            尚无 QAA tick 记录。在「启动配置」里启动 Paper 验证后，这里会显示 Agent 执行链。
          </p>
        )}
      </div>

      {/* Agent Steps 表格 */}
      {steps.length > 0 && (
        <div className="rounded-xl border border-border/60 bg-card overflow-hidden shadow-sm">
          <div className="px-4 py-2.5 border-b border-border/40 bg-muted/20">
            <p className="text-xs font-medium text-foreground">Agent 执行步骤</p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border/40 bg-muted/10 text-muted-foreground">
                  <th className="text-left p-3 font-medium">Agent</th>
                  <th className="text-left p-3 font-medium">Action</th>
                  <th className="text-left p-3 font-medium">Status</th>
                  <th className="text-right p-3 font-medium">耗时 ms</th>
                </tr>
              </thead>
              <tbody>
                {steps.map((s, i) => (
                  <tr
                    key={`${s.agent_id}-${i}`}
                    className="border-t border-border/30 hover:bg-muted/20 transition-colors"
                  >
                    <td className="p-3 font-mono text-foreground">{s.agent_id}</td>
                    <td className="p-3 text-foreground">{s.action}</td>
                    <td className="p-3">
                      <span
                        className={cn(
                          'inline-block px-2 py-0.5 rounded-full border text-[11px] font-medium',
                          statusBadgeClass(s.status),
                        )}
                      >
                        {s.status || '—'}
                      </span>
                    </td>
                    <td className="p-3 text-right text-muted-foreground tabular-nums">
                      {s.duration_ms ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* S1–S8 Agent 链 */}
      <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
        <p className="text-xs font-medium text-muted-foreground mb-3">S1–S8 Agent 链（只读）</p>
        <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
          {strategies.map(s => {
            const pool = subPools[s.strategy_id]
            return (
              <div
                key={s.strategy_id}
                className="rounded-lg border border-border/40 bg-muted/10 p-3 space-y-1.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center justify-center min-w-[2rem] px-1.5 py-0.5 rounded-md bg-blue-500/10 text-blue-700 dark:text-blue-400 text-xs font-bold">
                    {s.strategy_id}
                  </span>
                  <span className="text-sm font-medium text-foreground">{s.name}</span>
                  {s.macro_filter_required && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-800 dark:text-amber-400 border border-amber-500/20">
                      macro 过滤
                    </span>
                  )}
                </div>
                <div className="text-xs text-muted-foreground">
                  AI 模式: <span className="text-foreground">{s.ai_decision_mode || '—'}</span>
                  {' · '}
                  协调组: <span className="text-foreground">{s.coordination_group || '—'}</span>
                  {pool && (
                    <>
                      {' · '}
                      子池:{' '}
                      <span className="text-foreground">
                        ${pool.used_usd?.toFixed(0) ?? 0} / ${pool.cap_usd?.toFixed(0) ?? 0}
                      </span>
                    </>
                  )}
                </div>
                <div className="text-[11px] font-mono text-foreground/80 leading-relaxed break-all">
                  {(s.qaa_agent_chain || []).join(' → ') || '—'}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
