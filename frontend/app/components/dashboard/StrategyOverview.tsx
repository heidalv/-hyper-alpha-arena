import { useState, useEffect, useCallback, useRef } from 'react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { ChevronRight } from 'lucide-react'
import { usePageActive } from '@/hooks/usePageActive'

interface SessionListItem {
  session_id: string
  account_id: number
  status: string
  symbols: string[]
  total_pnl: number
  total_trades: number
  win_rate: number
  active_count?: number
  started_at?: string
}

interface StrategyOverviewProps {
  onNavigate?: (page: string) => void
  className?: string
}

export default function StrategyOverview({ onNavigate, className }: StrategyOverviewProps) {
  const pageActive = usePageActive()
  const [sessions, setSessions] = useState<SessionListItem[]>([])
  const [loading, setLoading] = useState(true)
  const loadRef = useRef<() => void>(() => {})

  const loadSessions = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true)
      const res = await fetch('/api/full-auto/sessions')
      if (res.ok) {
        const data = await res.json()
        setSessions(data)
      }
    } catch {
      // silently fail
    } finally {
      if (!silent) setLoading(false)
    }
  }, [])

  loadRef.current = () => loadSessions(true)

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  useEffect(() => {
    if (!pageActive) return
    const timer = setInterval(() => loadRef.current(), 10000)
    return () => clearInterval(timer)
  }, [pageActive])

  const runningSession = sessions.find(s => s.status === 'running')
  const totalPnl = sessions.reduce((acc, s) => acc + (s.total_pnl || 0), 0)
  const totalTrades = sessions.reduce((acc, s) => acc + (s.total_trades || 0), 0)
  const totalWins = sessions.reduce((acc, s) => acc + Math.round((s.win_rate || 0) * (s.total_trades || 0)), 0)
  const avgWinRate = totalTrades > 0 ? (totalWins / totalTrades) * 100 : 0
  const activeCount = runningSession?.active_count ?? 0

  const pnlPositive = totalPnl >= 0

  return (
    <div className={cn('bg-card border border-border rounded-lg flex flex-col overflow-hidden', className)}>
      <div className="px-3 py-2.5 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-foreground">
            策略表现
          </span>
          <Badge className="text-[9px] h-4 bg-sky-500/15 text-sky-400 border-sky-500/30 px-1">
            模拟
          </Badge>
        </div>
        {runningSession && (
          <Badge className="text-[10px] h-5 bg-emerald-500/15 text-emerald-400 border-emerald-500/30">
            运行中
          </Badge>
        )}
      </div>

      <div className="p-3 flex-1">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <span className="text-xs text-muted-foreground">加载中...</span>
          </div>
        ) : sessions.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <span className="text-xs text-muted-foreground">暂无策略数据</span>
          </div>
        ) : (
          <div className="space-y-3">
            {/* PnL */}
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">总盈亏</span>
              <span className={cn('text-sm font-bold tabular-nums', pnlPositive ? 'text-emerald-400' : 'text-red-400')}>
                {pnlPositive ? '+' : ''}${totalPnl.toFixed(2)}
              </span>
            </div>

            {/* Active strategies */}
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">活跃策略</span>
              <span className="text-sm font-semibold text-foreground">{activeCount}</span>
            </div>

            {/* Total trades */}
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">总交易</span>
              <span className="text-sm font-semibold text-foreground">{totalTrades}</span>
            </div>

            {/* Win rate bar */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-muted-foreground">胜率</span>
                <span className="text-xs font-semibold text-foreground">{avgWinRate.toFixed(1)}%</span>
              </div>
              <div className="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                <div
                  className={cn(
                    'h-full rounded-full transition-all duration-500',
                    avgWinRate >= 50 ? 'bg-emerald-500' : 'bg-amber-500'
                  )}
                  style={{ width: `${Math.min(avgWinRate, 100)}%` }}
                />
              </div>
            </div>

            {/* Recent sessions list */}
            {sessions.slice(0, 3).map(session => (
              <div
                key={session.session_id}
                className="flex items-center gap-2 py-1 border-t border-border/50"
              >
                <div
                  className={cn(
                    'h-1.5 w-1.5 rounded-full flex-shrink-0',
                    session.status === 'running' ? 'bg-emerald-400' : 'bg-muted-foreground/40'
                  )}
                />
                <div className="flex-1 min-w-0">
                  <span className="text-[11px] text-foreground truncate block">
                    {session.symbols?.slice(0, 2).join(', ') || 'N/A'}
                  </span>
                </div>
                <span className={cn(
                  'text-[11px] tabular-nums font-medium',
                  session.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                )}>
                  {session.total_pnl >= 0 ? '+' : ''}${session.total_pnl?.toFixed(2) || '0.00'}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {onNavigate && (
        <button
          className="px-3 py-2 border-t border-border flex items-center justify-center gap-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
          onClick={() => onNavigate('atas-v2')}
        >
          查看详情 <ChevronRight className="h-3 w-3" />
        </button>
      )}
    </div>
  )
}
