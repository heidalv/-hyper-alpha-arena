/**
 * AssetCurveChart — 多账户权益曲线叠加
 *
 * 说明：当前系统尚无跨 paper/testnet/mainnet 统一的历史权益 API，
 * 这里按轮询节奏在浏览器端累积滚动窗口（最多 MAX_POINTS 个采样点），
 * 页面停留越久曲线越完整；刷新页面后从空白重新开始积累（诚实呈现，不伪造历史数据）。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
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
import WidgetShell from './WidgetShell'
import { LineChart as LineChartIcon } from 'lucide-react'
import type { WidgetProps } from '../types'

const MAX_POINTS = 180
const LINE_COLORS = ['#38bdf8', '#34d399', '#f472b6', '#fbbf24', '#a78bfa', '#f87171']

interface SamplePoint {
  t: number
  equity: number
}

export default function AssetCurveChart({ overviews }: WidgetProps) {
  const historyRef = useRef<Map<string, SamplePoint[]>>(new Map())
  const lastTsRef = useRef<Map<string, string>>(new Map())
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let changed = false
    for (const o of overviews) {
      if (o.error) continue
      const key = String(o.account_id)
      if (lastTsRef.current.get(key) === o.updated_at) continue
      lastTsRef.current.set(key, o.updated_at)
      const arr = historyRef.current.get(key) || []
      arr.push({ t: Date.now(), equity: o.equity })
      if (arr.length > MAX_POINTS) arr.shift()
      historyRef.current.set(key, arr)
      changed = true
    }
    if (changed) setTick((v) => v + 1)
  }, [overviews])

  const { rows, seriesKeys } = useMemo(() => {
    const activeKeys = overviews.filter((o) => !o.error).map((o) => String(o.account_id))
    const labelByKey = new Map(
      overviews.map((o) => [String(o.account_id), o.account_name || `#${o.account_id}`]),
    )
    const maxLen = Math.max(0, ...activeKeys.map((k) => historyRef.current.get(k)?.length || 0))
    const merged: Record<string, number | string>[] = []
    for (let i = 0; i < maxLen; i++) {
      const row: Record<string, number | string> = { tick: i }
      for (const key of activeKeys) {
        const arr = historyRef.current.get(key)
        const pt = arr?.[i]
        if (pt) row[labelByKey.get(key) || key] = pt.equity
      }
      merged.push(row)
    }
    return { rows: merged, seriesKeys: activeKeys.map((k) => labelByKey.get(k) || k) }
  }, [overviews, tick])

  const hasData = rows.length >= 2

  return (
    <WidgetShell title="权益曲线" icon={<LineChartIcon className="h-3.5 w-3.5" />}>
      {!hasData ? (
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
              width={56}
              domain={['auto', 'auto']}
              tickFormatter={(v) => `$${Number(v).toFixed(0)}`}
            />
            <Tooltip
              formatter={(value: number) => [`$${value.toFixed(2)}`, '']}
              contentStyle={{
                background: 'var(--card)',
                border: '1px solid var(--border)',
                borderRadius: 8,
                fontSize: 11,
              }}
            />
            {seriesKeys.length > 1 && <Legend wrapperStyle={{ fontSize: 10 }} />}
            {seriesKeys.map((key, idx) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
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
