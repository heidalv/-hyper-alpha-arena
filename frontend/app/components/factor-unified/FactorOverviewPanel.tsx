/**
 * FactorOverviewPanel — 因子总览
 *
 * 显示指定交易对的实时因子值、信号方向和合成信号。
 * 对接 /api/factors/values/{symbol} 和 /api/factors/signals/{symbol}
 */
import { useState, useEffect, useCallback }from 'react'
import { apiRequest } from '@/lib/api'

/* ---------- 类型 ---------- */
interface FactorItem {
  name: string
  value: number
  normalized: number
  category: string
}

interface SignalDetail {
  direction: number
  strength: number
  category: string
}

interface SignalData {
  symbol: string
  direction: number
  strength: number
  confidence: number
  contributing_factors: number
  regime: string
  factor_details: Record<string, SignalDetail>
}

const CATEGORY_LABELS: Record<string, string> = {
  momentum: '动量',
  mean_reversion: '均值回归',
  volatility: '波动率',
  volume: '成交量',
  trend: '趋势',
  market_flow: '市场流向',
  sentiment: '情绪',
  derivatives: '衍生品',
  onchain: '链上',
  macro: '宏观',
  behavioral: '行为',
  strength: '强度',
  pattern: '形态',
  funding: '资金费率',
}

function getSignalLabel(value: number): { text: string; color: string } {
  if (value > 0.5) return { text: '偏多', color: '#22c55e' }
  if (value > 0.1) return { text: '轻微偏多', color: '#86efac' }
  if (value < -0.5) return { text: '偏空', color: '#ef4444' }
  if (value < -0.1) return { text: '轻微偏空', color: '#fca5a5' }
  return { text: '中性', color: '#94a3b8' }
}

function getDirectionBar(value: number) {
  const isPositive = value >= 0
  const strength = Math.abs(value)
  const barWidth = Math.max(strength * 80, 8)
  return (
    <div className="flex items-center gap-2 w-full">
      <span className="text-[10px] text-red-500 w-6 text-right shrink-0 font-bold">空</span>
      <div className="flex-1 h-3 rounded relative overflow-hidden" style={{ backgroundColor: 'rgba(255,255,255,0.05)' }}>
        <div
          className="absolute top-0 h-full rounded transition-all duration-300"
          style={{
            width: `${barWidth}%`,
            left: isPositive ? '50%' : `${50 - barWidth}%`,
            backgroundColor: isPositive ? '#22c55e' : '#ef4444',
          }}
        />
        <div className="absolute top-0 left-1/2 w-0.5 h-full" style={{ backgroundColor: 'rgba(255,255,255,0.25)' }} />
      </div>
      <span className="text-[10px] text-green-500 w-6 shrink-0 font-bold">多</span>
    </div>
  )
}

interface Props {
  symbol: string
}

export default function FactorOverviewPanel({ symbol }: Props) {
  const [factors, setFactors] = useState<FactorItem[]>([])
  const [signal, setSignal] = useState<SignalData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [fResp, sResp] = await Promise.all([
        apiRequest(`/factors/values/${symbol}`),
        apiRequest(`/factors/signals/${symbol}`),
      ])
      const fData = await fResp.json()
      const sData = await sResp.json()
      setFactors(fData.factors || [])
      setSignal(sData.direction !== undefined ? sData : null)
    } catch (e: any) {
      setError(e.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [symbol])

  useEffect(() => {
    fetchData()
    const timer = setInterval(fetchData, 30000)
    return () => clearInterval(timer)
  }, [fetchData])

  // 按类别分组
  const grouped = factors.reduce<Record<string, FactorItem[]>>((acc, f) => {
    const cat = f.category || 'other'
    if (!acc[cat]) acc[cat] = []
    acc[cat].push(f)
    return acc
  }, {})

  const sigLabel = signal ? getSignalLabel(signal.direction) : { text: '--', color: '#94a3b8' }

  return (
    <div className="space-y-4">
      {/* 合成信号卡片 */}
      {signal && (
        <div className="grid grid-cols-4 gap-3">
          <div className="rounded-lg border bg-card p-3">
            <div className="text-[10px] text-muted-foreground mb-1">合成方向</div>
            <div className="text-lg font-bold" style={{ color: sigLabel.color }}>
              {signal.direction >= 0 ? '+' : ''}{signal.direction.toFixed(3)}
            </div>
            <div className="text-[10px]" style={{ color: sigLabel.color }}>{sigLabel.text}</div>
          </div>
          <div className="rounded-lg border bg-card p-3">
            <div className="text-[10px] text-muted-foreground mb-1">信号强度</div>
            <div className="text-lg font-bold">{(signal.strength * 100).toFixed(1)}%</div>
            <div className="w-full h-1.5 bg-muted rounded-full mt-1">
              <div className="h-full bg-blue-500 rounded-full" style={{ width: `${signal.strength * 100}%` }} />
            </div>
          </div>
          <div className="rounded-lg border bg-card p-3">
            <div className="text-[10px] text-muted-foreground mb-1">置信度</div>
            <div className="text-lg font-bold">{(signal.confidence * 100).toFixed(1)}%</div>
            <div className="w-full h-1.5 bg-muted rounded-full mt-1">
              <div className="h-full bg-purple-500 rounded-full" style={{ width: `${signal.confidence * 100}%` }} />
            </div>
          </div>
          <div className="rounded-lg border bg-card p-3">
            <div className="text-[10px] text-muted-foreground mb-1">市场状态</div>
            <div className="text-lg font-bold">{signal.regime || '--'}</div>
            <div className="text-[10px] text-muted-foreground">{signal.contributing_factors} 个因子</div>
          </div>
        </div>
      )}

      {/* 因子列表 */}
      {loading && factors.length === 0 ? (
        <div className="text-center py-12 text-sm text-muted-foreground">加载中...</div>
      ) : error ? (
        <div className="text-center py-12 text-sm text-red-500">{error}</div>
      ) : (
        <div className="space-y-3">
          {Object.entries(grouped).map(([cat, items]) => (
            <div key={cat} className="rounded-lg border bg-card">
              <div className="px-4 py-2 border-b bg-muted/30">
                <span className="text-xs font-medium">
                  {CATEGORY_LABELS[cat] || cat} ({items.length})
                </span>
              </div>
              <div className="divide-y">
                {items.map(f => {
                  const dir = signal?.factor_details?.[f.name]
                  return (
                    <div key={f.name} className="flex items-center gap-4 px-4 py-2">
                      <span className="text-xs font-mono w-28 truncate" title={f.name}>
                        {f.name}
                      </span>
                      <span className="text-xs w-20 text-right tabular-nums">
                        {f.value.toFixed(4)}
                      </span>
                      {dir && (
                        <div className="flex-1">
                          {getDirectionBar(dir.direction)}
                        </div>
                      )}
                      {!dir && (
                        <div className="flex-1">
                          {getDirectionBar(f.normalized)}
                        </div>
                      )}
                      <span className="text-[10px] text-muted-foreground w-12 text-right">
                        {getSignalLabel(dir?.direction ?? f.normalized).text}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="flex justify-between items-center text-[10px] text-muted-foreground pt-2">
        <span>共 {factors.length} 个因子</span>
        <button
          onClick={fetchData}
          disabled={loading}
          className="text-xs text-blue-500 hover:text-blue-400 disabled:opacity-50"
        >
          {loading ? '刷新中...' : '刷新'}
        </button>
      </div>
    </div>
  )
}
