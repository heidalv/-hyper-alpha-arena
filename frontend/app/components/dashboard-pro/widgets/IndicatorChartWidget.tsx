/**
 * IndicatorChartWidget — 接入 FactorService 的指标自选叠加图
 *
 * /api/factors/values/{symbol} 只返回"当前时刻快照"，没有历史序列 API；
 * 这里按轮询节奏在浏览器端滚动累积每个已选因子的 normalized 值（-1~1 可比尺度），
 * 与 AssetCurveChart 采用同一"诚实累积，不伪造历史"的原则。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'
import { apiRequest } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Activity } from 'lucide-react'
import WidgetShell from './WidgetShell'
import type { WidgetProps } from '../types'

const LINE_COLORS = ['#38bdf8', '#f472b6', '#fbbf24', '#34d399']
const MAX_POINTS = 120
// 后端全量因子重计算单次可能耗时数秒到数十秒（见 factor_service 的 TTL 缓存注释），
// 轮询间隔与后端缓存 TTL（60s）对齐，避免刚好卡在缓存过期边缘反复触发重计算。
const POLL_MS = 45000

interface FactorItem {
  name: string
  value: number
  normalized: number
  category: string
}

export default function IndicatorChartWidget({ config, onConfigChange }: WidgetProps) {
  const symbol = (config?.symbol as string) || 'BTC'
  const timeframe = (config?.timeframe as string) || '15m'
  const selected = (config?.factors as string[]) || []

  const [available, setAvailable] = useState<FactorItem[]>([])
  const [loading, setLoading] = useState(false)
  const historyRef = useRef<Map<string, { t: number; v: number }[]>>(new Map())
  const [tick, setTick] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiRequest(`/factors/values/${encodeURIComponent(symbol)}?timeframe=${timeframe}`)
      const data = await res.json()
      const factors: FactorItem[] = data.factors || []
      setAvailable(factors)

      const activeNames = selected.length > 0 ? selected : factors.slice(0, 2).map((f) => f.name)
      if (selected.length === 0 && activeNames.length > 0 && onConfigChange) {
        onConfigChange({ ...config, symbol, timeframe, factors: activeNames })
      }
      const now = Date.now()
      for (const f of factors) {
        if (!activeNames.includes(f.name)) continue
        const arr = historyRef.current.get(f.name) || []
        arr.push({ t: now, v: f.normalized })
        if (arr.length > MAX_POINTS) arr.shift()
        historyRef.current.set(f.name, arr)
      }
      setTick((v) => v + 1)
    } catch {
      // 静默失败，下一轮轮询重试
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, timeframe, JSON.stringify(selected)])

  useEffect(() => {
    load()
    const timer = setInterval(load, POLL_MS)
    return () => clearInterval(timer)
  }, [load])

  const toggleFactor = (name: string) => {
    if (!onConfigChange) return
    const next = selected.includes(name)
      ? selected.filter((n) => n !== name)
      : selected.length >= 3
        ? selected
        : [...selected, name]
    onConfigChange({ ...config, symbol, timeframe, factors: next })
  }

  const activeNames = selected.length > 0 ? selected : available.slice(0, 2).map((f) => f.name)

  const rows = useMemo(() => {
    const maxLen = Math.max(0, ...activeNames.map((n) => historyRef.current.get(n)?.length || 0))
    const merged: Record<string, number>[] = []
    for (let i = 0; i < maxLen; i++) {
      const row: Record<string, number> = { tick: i }
      for (const name of activeNames) {
        const arr = historyRef.current.get(name)
        const pt = arr?.[i]
        if (pt) row[name] = pt.v
      }
      merged.push(row)
    }
    return merged
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, JSON.stringify(activeNames)])

  return (
    <WidgetShell
      title={`指标 · ${symbol}`}
      icon={<Activity className="h-3.5 w-3.5" />}
      badge={loading ? <span className="text-[10px] text-muted-foreground">刷新中…</span> : undefined}
      footer={
        <div className="px-2 py-1.5 flex items-center gap-1 flex-wrap max-h-14 overflow-y-auto">
          {available.slice(0, 24).map((f) => {
            const active = activeNames.includes(f.name)
            return (
              <button
                key={f.name}
                onClick={() => toggleFactor(f.name)}
                className={cn(
                  'text-[10px] px-1.5 py-0.5 rounded border transition-colors',
                  active
                    ? 'border-primary/60 bg-primary/10 text-foreground'
                    : 'border-border text-muted-foreground hover:bg-muted/40',
                )}
              >
                {f.name}
              </button>
            )
          })}
        </div>
      }
    >
      {rows.length < 2 ? (
        <div className="h-full flex items-center justify-center">
          <span className="text-xs text-muted-foreground">积累采样中，稍候将显示走势…</span>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" opacity={0.3} />
            <XAxis dataKey="tick" tick={false} axisLine={false} />
            <YAxis
              tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
              width={40}
              domain={[-1, 1]}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--card)',
                border: '1px solid var(--border)',
                borderRadius: 8,
                fontSize: 11,
              }}
            />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            {activeNames.map((name, idx) => (
              <Line
                key={name}
                type="monotone"
                dataKey={name}
                stroke={LINE_COLORS[idx % LINE_COLORS.length]}
                strokeWidth={1.75}
                dot={false}
                isAnimationActive={false}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </WidgetShell>
  )
}
