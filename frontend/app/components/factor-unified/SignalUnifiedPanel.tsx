/**
 * SignalUnifiedPanel — 统一信号面板
 *
 * 展示 4 源信号融合结果:
 *   - 因子引擎 (factor)
 *   - 情报汇流 (intel)
 *   - 三维确认 (confirm)
 *   - 决策融合 (fusion)
 *
 * 三区域布局: 方向仪表盘 | 信号源分解 | 共振/冲突可视化
 */
import { useState, useEffect, useCallback } from 'react'
import { apiRequest } from '@/lib/api'
import { ArrowUp, ArrowDown, Minus, Activity, Radio, AlertTriangle } from 'lucide-react'

interface SourceInfo {
  source_name: string
  direction: number
  confidence: number
  strength: number
  action: string
  weight: number
}

interface UnifiedData {
  symbol: string
  direction: number
  confidence: number
  strength: number
  action: string
  confluence_level: string
  source_count: number
  agreeing_sources: number
  conflicting_sources: number
  sources: Record<string, SourceInfo>
  regime: string
  reasoning: string
  timestamp: number
}

const ACTION_STYLES: Record<string, { label: string; color: string; bg: string }> = {
  buy: { label: 'BUY', color: 'text-emerald-400', bg: 'bg-emerald-500/20 border-emerald-500/30' },
  sell: { label: 'SELL', color: 'text-red-400', bg: 'bg-red-500/20 border-red-500/30' },
  hold: { label: 'HOLD', color: 'text-zinc-400', bg: 'bg-zinc-500/20 border-zinc-500/30' },
}

const CONFLUENCE_STYLES: Record<string, { label: string; color: string }> = {
  strong_resonance: { label: '强共振', color: 'text-emerald-400' },
  resonance: { label: '共振', color: 'text-green-400' },
  neutral: { label: '中性', color: 'text-zinc-400' },
  conflict: { label: '冲突', color: 'text-amber-400' },
  strong_conflict: { label: '强冲突', color: 'text-red-400' },
}

const SOURCE_ICONS: Record<string, string> = {
  factor: '\u2697\uFE0F',
  intel: '\U0001F4E1',
  confirm: '\u2705',
  fusion: '\U0001F9E0',
}

export default function SignalUnifiedPanel({ symbol }: { symbol: string }) {
  const [data, setData] = useState<UnifiedData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const resp = await apiRequest(`/signals/unified/${symbol}`)
      const json = await resp.json()
      if (json.error) {
        setError(json.error)
      } else {
        setData(json)
        setError(null)
      }
    } catch (e: any) {
      setError(e.message || '请求失败')
    } finally {
      setLoading(false)
    }
  }, [symbol])

  useEffect(() => {
    setLoading(true)
    fetchData()
    const timer = setInterval(fetchData, 30000)
    return () => clearInterval(timer)
  }, [fetchData])

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center py-16 text-muted-foreground text-sm">
        正在加载统一信号...
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="flex items-center justify-center py-16 text-red-400 text-sm">
        {error}
      </div>
    )
  }

  if (!data) return null

  const actionStyle = ACTION_STYLES[data.action] || ACTION_STYLES.hold
  const confStyle = CONFLUENCE_STYLES[data.confluence_level] || CONFLUENCE_STYLES.neutral

  return (
    <div className="space-y-4">
      {/* 区域 1: 方向仪表盘 */}
      <div className="rounded-lg border bg-card p-5">
        <div className="flex items-start justify-between">
          <div className="space-y-3">
            {/* Action 大字 */}
            <div className="flex items-center gap-3">
              <span className={`text-3xl font-bold ${actionStyle.color}`}>
                {actionStyle.label}
              </span>
              <span className={`px-2 py-0.5 rounded text-xs border ${actionStyle.bg} ${actionStyle.color}`}>
                {confStyle.label}
              </span>
            </div>

            {/* Direction 数值 */}
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">方向值:</span>
              <span className="font-mono font-medium">
                {data.direction > 0 ? '+' : ''}{data.direction.toFixed(4)}
              </span>
              {data.direction > 0.05 && <ArrowUp className="w-4 h-4 text-emerald-400" />}
              {data.direction < -0.05 && <ArrowDown className="w-4 h-4 text-red-400" />}
              {Math.abs(data.direction) <= 0.05 && <Minus className="w-4 h-4 text-zinc-400" />}
            </div>

            {/* Regime */}
            <div className="text-xs text-muted-foreground">
              体制: <span className="text-foreground">{data.regime}</span>
              {' \u00B7 '}
              信号源: <span className="text-foreground">{data.source_count}</span>
              {' \u00B7 '}
              一致: <span className="text-emerald-400">{data.agreeing_sources}</span>
              {' / '}
              冲突: <span className="text-red-400">{data.conflicting_sources}</span>
            </div>
          </div>

          {/* Confidence + Strength 进度条 */}
          <div className="w-48 space-y-2">
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-muted-foreground">置信度</span>
                <span className="font-mono">{(data.confidence * 100).toFixed(1)}%</span>
              </div>
              <div className="h-2 rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full rounded-full bg-blue-500 transition-all duration-500"
                  style={{ width: `${Math.max(data.confidence * 100, 1)}%` }}
                />
              </div>
            </div>
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-muted-foreground">强度</span>
                <span className="font-mono">{(data.strength * 100).toFixed(1)}%</span>
              </div>
              <div className="h-2 rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full rounded-full bg-purple-500 transition-all duration-500"
                  style={{ width: `${Math.max(data.strength * 100, 1)}%` }}
                />
              </div>
            </div>
          </div>
        </div>

        {/* Reasoning */}
        {data.reasoning && (
          <div className="mt-3 pt-3 border-t text-xs text-muted-foreground">
            {data.reasoning}
          </div>
        )}
      </div>

      {/* 区域 2: 信号源分解 */}
      <div className="grid grid-cols-4 gap-3">
        {Object.entries(data.sources).map(([id, src]) => {
          const srcAction = ACTION_STYLES[src.action] || ACTION_STYLES.hold
          return (
            <div key={id} className="rounded-lg border bg-card p-3 space-y-2">
              {/* 源头部 */}
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">
                  {SOURCE_ICONS[id] || '\u2022'} {src.source_name}
                </span>
                <span className="text-xs text-muted-foreground">
                  w={src.weight.toFixed(2)}
                </span>
              </div>

              {/* 方向指示 */}
              <div className="flex items-center gap-2">
                <div className={`text-lg font-bold ${srcAction.color}`}>
                  {src.action.toUpperCase()}
                </div>
                <span className="text-xs font-mono text-muted-foreground">
                  {src.direction > 0 ? '+' : ''}{src.direction.toFixed(3)}
                </span>
              </div>

              {/* Confidence 进度条 */}
              <div>
                <div className="flex justify-between text-xs mb-0.5">
                  <span className="text-muted-foreground">置信度</span>
                  <span className="font-mono">{(src.confidence * 100).toFixed(0)}%</span>
                </div>
                <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                  <div
                    className="h-full rounded-full bg-blue-400/70 transition-all duration-300"
                    style={{ width: `${Math.max(src.confidence * 100, 1)}%` }}
                  />
                </div>
              </div>

              {/* Strength */}
              <div>
                <div className="flex justify-between text-xs mb-0.5">
                  <span className="text-muted-foreground">强度</span>
                  <span className="font-mono">{(src.strength * 100).toFixed(0)}%</span>
                </div>
                <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                  <div
                    className="h-full rounded-full bg-purple-400/70 transition-all duration-300"
                    style={{ width: `${Math.max(src.strength * 100, 1)}%` }}
                  />
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* 无信号源时的提示 */}
      {data.source_count === 0 && (
        <div className="text-center py-8 text-muted-foreground text-sm">
          暂无可用信号源数据
        </div>
      )}

      {/* 区域 3: 共振/冲突可视化 */}
      {data.source_count > 1 && (
        <div className="rounded-lg border bg-card p-4">
          <div className="flex items-center gap-2 mb-3 text-sm font-medium">
            {data.conflicting_sources > 0 ? (
              <AlertTriangle className="w-4 h-4 text-amber-400" />
            ) : (
              <Activity className="w-4 h-4 text-emerald-400" />
            )}
            <span>信号一致性分析</span>
          </div>

          {/* 方向柱状图 */}
          <div className="flex items-end gap-2 h-16">
            {Object.entries(data.sources).map(([id, src]) => {
              const height = Math.max(Math.abs(src.direction) * 100, 4)
              const isPositive = src.direction > 0
              const isConflict =
                data.conflicting_sources > 0 &&
                ((data.direction > 0 && src.direction < -0.05) ||
                  (data.direction < 0 && src.direction > 0.05))
              return (
                <div key={id} className="flex-1 flex flex-col items-center gap-1">
                  <div
                    className="w-full rounded-sm transition-all duration-300"
                    style={{
                      height: `${height}%`,
                      backgroundColor: isConflict
                        ? 'rgb(245 158 11 / 0.6)'
                        : isPositive
                        ? 'rgb(52 211 153 / 0.6)'
                        : 'rgb(248 113 113 / 0.6)',
                    }}
                  />
                  <span className="text-[10px] text-muted-foreground truncate w-full text-center">
                    {src.source_name}
                  </span>
                </div>
              )
            })}
          </div>

          {/* 共振统计 */}
          <div className="mt-3 flex items-center gap-4 text-xs text-muted-foreground">
            <span>
              一致源: <span className="text-emerald-400">{data.agreeing_sources}</span>
            </span>
            <span>
              冲突源: <span className="text-red-400">{data.conflicting_sources}</span>
            </span>
            <span>
              共振等级: <span className={confStyle.color}>{confStyle.label}</span>
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
