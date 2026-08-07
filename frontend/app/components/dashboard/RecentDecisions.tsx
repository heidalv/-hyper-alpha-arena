import { useState, useEffect, useCallback, useRef } from 'react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { ChevronRight } from 'lucide-react'
import { getArenaModelChat, type ArenaModelChatEntry } from '@/lib/api'
import { useTradingMode } from '@/contexts/TradingModeContext'
import { usePageActive } from '@/hooks/usePageActive'

interface RecentDecisionsProps {
  onNavigate?: (page: string) => void
  className?: string
}

const OP_CONFIG: Record<string, { label: string; cls: string }> = {
  BUY:   { label: 'BUY',   cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' },
  SELL:  { label: 'SELL',  cls: 'bg-red-500/15 text-red-400 border-red-500/30' },
  CLOSE: { label: 'CLOSE', cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30' },
  HOLD:  { label: 'HOLD',  cls: 'bg-muted text-muted-foreground border-border' },
}

function formatTimeAgo(dateStr: string | null | undefined): string {
  if (!dateStr) return ''
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins}分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}小时前`
  return `${Math.floor(hours / 24)}天前`
}

export default function RecentDecisions({ onNavigate, className }: RecentDecisionsProps) {
  const { tradingMode } = useTradingMode()
  const pageActive = usePageActive()
  const [entries, setEntries] = useState<ArenaModelChatEntry[]>([])
  const [loading, setLoading] = useState(true)
  const loadRef = useRef<() => void>(() => {})

  const loadData = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true)
      const res = await getArenaModelChat({ limit: 5, trading_mode: tradingMode })
      setEntries(res.entries || [])
    } catch {
      // silently fail
    } finally {
      if (!silent) setLoading(false)
    }
  }, [tradingMode])

  loadRef.current = () => loadData(true)

  useEffect(() => {
    loadData()
  }, [loadData])

  useEffect(() => {
    if (!pageActive) return
    const timer = setInterval(() => loadRef.current(), 15000)
    return () => clearInterval(timer)
  }, [pageActive])

  return (
    <div className={cn('bg-card border border-border rounded-lg flex flex-col overflow-hidden', className)}>
      <div className="px-3 py-2.5 border-b border-border flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-foreground">
          最近 AI 决策
        </span>
        <Badge variant="outline" className="text-[10px] h-5">
          {entries.length}
        </Badge>
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <span className="text-xs text-muted-foreground">加载中...</span>
          </div>
        ) : entries.length === 0 ? (
          <div className="flex items-center justify-center py-8">
            <span className="text-xs text-muted-foreground">暂无 AI 决策</span>
          </div>
        ) : (
          <div className="divide-y divide-border/50">
            {entries.map(entry => {
              const op = entry.operation?.toUpperCase() || 'HOLD'
              const config = OP_CONFIG[op] || OP_CONFIG.HOLD

              return (
                <div key={entry.id} className="px-3 py-2.5 hover:bg-muted/30 transition-colors">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge className={cn('text-[10px] h-4 px-1.5 border', config.cls)}>
                      {config.label}
                    </Badge>
                    <span className="text-xs font-medium text-foreground">
                      {entry.symbol || '-'}
                    </span>
                    <span className="text-[10px] text-muted-foreground ml-auto flex-shrink-0">
                      {formatTimeAgo(entry.decision_time)}
                    </span>
                  </div>
                  <p className="text-[11px] text-muted-foreground line-clamp-2 leading-relaxed">
                    {entry.reason || '无理由'}
                  </p>
                  {(entry.short_bias || entry.mid_bias || entry.long_bias) && (
                    <div className="flex flex-wrap items-center gap-1 mt-1">
                      {[
                        { key: 'short', bias: entry.short_bias, conf: entry.short_confidence, label: '短期', border: 'border-amber-500/25', bg: 'bg-amber-500/5' },
                        { key: 'mid', bias: entry.mid_bias, conf: entry.mid_confidence, label: '中期', border: 'border-blue-500/25', bg: 'bg-blue-500/5' },
                        { key: 'long', bias: entry.long_bias, conf: entry.long_confidence, label: '长期', border: 'border-purple-500/25', bg: 'bg-purple-500/5' },
                      ].map(({ key, bias, conf, label, border, bg }) => {
                        if (!bias) return null  // 无数据不显示
                        const hasSignal = bias !== 'neutral' || (conf != null && conf > 0)
                        const arrow = bias === 'bullish' ? '▲' : bias === 'bearish' ? '▼' : '─'
                        const ac = bias === 'bullish' ? 'text-emerald-500' : bias === 'bearish' ? 'text-red-500' : 'text-gray-400'
                        const pct = hasSignal && conf != null ? `${(conf * 100).toFixed(0)}%` : ''
                        return (
                          <span key={key} className={`inline-flex items-center gap-0.5 px-1 py-0.5 rounded border text-[10px] leading-none ${border} ${bg} ${!hasSignal ? 'opacity-40' : ''}`}>
                            <span className="text-muted-foreground">{label}</span>
                            <span className={ac}>{arrow}</span>
                            {pct && <span className="text-muted-foreground tabular-nums">{pct}</span>}
                          </span>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {onNavigate && (
        <button
          className="px-3 py-2 border-t border-border flex items-center justify-center gap-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
          onClick={() => onNavigate('atas-v2')}
        >
          查看全部 <ChevronRight className="h-3 w-3" />
        </button>
      )}
    </div>
  )
}
