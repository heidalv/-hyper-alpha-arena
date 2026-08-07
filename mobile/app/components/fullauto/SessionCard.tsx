import React from 'react'
import type { FullAutoSession } from '../../api/types'
import { Badge } from '../ui/Badge'
import { PriceDisplay } from '../ui/PriceDisplay'

interface SessionCardProps {
  session: FullAutoSession
}

const statusColors: Record<string, { dot: string; text: string; label: string }> = {
  running: { dot: 'bg-profit', text: 'text-profit', label: '运行中' },
  defensive: { dot: 'bg-warning', text: 'text-warning', label: '防守中' },
  paused: { dot: 'bg-muted', text: 'text-muted', label: '已暂停' },
  stopped: { dot: 'bg-loss', text: 'text-loss', label: '已停止' }
}

export const SessionCard: React.FC<SessionCardProps> = ({ session }) => {
  const statusInfo = statusColors[session.status] || statusColors.stopped
  const duration = session.duration_minutes ?? 0
  const hours = Math.floor(duration / 60)
  const minutes = duration % 60

  return (
    <div className="mx-4 mt-3 p-4 bg-surface rounded-card border border-border">
      {/* Status + Session ID */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className={`w-2.5 h-2.5 rounded-full ${statusInfo.dot}`} />
          <span className={`text-sm font-medium ${statusInfo.text}`}>{statusInfo.label}</span>
        </div>
        <Badge variant="neutral" className="font-mono text-xs">
          {session.session_id.slice(0, 12)}...
        </Badge>
      </div>

      {/* Duration */}
      <div className="text-xs text-muted mb-3">
        运行时长: {hours}h {minutes}m
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <span className="text-xs text-muted">总权益</span>
          <div className="text-lg font-bold tabular-nums">
            ${session.peak_balance.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
          </div>
        </div>
        <div>
          <span className="text-xs text-muted">总盈亏</span>
          <div className={`text-lg font-bold tabular-nums ${session.total_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
            {session.total_pnl >= 0 ? '+' : ''}${Math.abs(session.total_pnl).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
          </div>
        </div>
        <div>
          <span className="text-xs text-muted">当前回撤</span>
          <div className="text-base tabular-nums text-fg">
            {session.current_drawdown.toFixed(2)}%
          </div>
        </div>
        <div>
          <span className="text-xs text-muted">策略</span>
          <div className="text-base tabular-nums text-fg">
            活跃 {session.active_count ?? session.active_strategy_ids?.length ?? 0} / 暂停 {session.paused_count ?? 0}
          </div>
        </div>
      </div>
    </div>
  )
}
