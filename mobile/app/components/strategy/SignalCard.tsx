import React from 'react'
import type { SignalDetection } from '../../api/types'
import { Badge } from '../ui/Badge'

interface SignalCardProps {
  signal: SignalDetection
}

const directionConfig: Record<string, { icon: string; color: string; label: string }> = {
  buy: { icon: '🟢', color: 'text-profit', label: 'BUY' },
  sell: { icon: '🔴', color: 'text-loss', label: 'SELL' },
  hold: { icon: '⚪', color: 'text-muted', label: 'HOLD' }
}

export const SignalCard: React.FC<SignalCardProps> = ({ signal }) => {
  const config = directionConfig[signal.direction] || directionConfig.hold
  const time = new Date(signal.detected_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })

  return (
    <div className="p-3 bg-surface rounded-card border border-border">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span>{config.icon}</span>
          <span className="text-base font-bold">{signal.symbol}</span>
          <span className={`text-sm font-bold ${config.color}`}>{config.label}</span>
        </div>
        <Badge variant={signal.confidence >= 60 ? 'success' : signal.confidence >= 40 ? 'warning' : 'neutral'}>
          {signal.confidence}%
        </Badge>
      </div>
      <div className="flex items-center justify-between text-xs text-muted">
        <span>{signal.signal_name}</span>
        <span>{time}</span>
      </div>
      {signal.reason && (
        <div className="mt-1.5 text-xs text-muted">{signal.reason}</div>
      )}
    </div>
  )
}
