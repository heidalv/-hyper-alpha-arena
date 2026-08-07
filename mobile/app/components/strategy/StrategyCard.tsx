import React from 'react'
import type { AIStrategy } from '../../api/types'

interface StrategyCardProps {
  strategy: AIStrategy
  onExpand: () => void
}

export const StrategyCard: React.FC<StrategyCardProps> = ({ strategy, onExpand }) => {
  const isActive = strategy.status === 'active'

  return (
    <div
      className="p-3 bg-surface rounded-card border border-border flex items-center justify-between touch-feedback"
      onClick={onExpand}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full flex-shrink-0 ${isActive ? 'bg-profit' : 'bg-muted'}`} />
          <span className="text-sm font-medium truncate">{strategy.name}</span>
        </div>
        <span className="text-xs text-muted ml-4">
          {strategy.total_trades} 笔交易
        </span>
      </div>
      <div className="text-right flex-shrink-0 ml-3">
        <span className={`text-sm font-medium tabular-nums ${strategy.total_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
          {strategy.total_pnl >= 0 ? '+' : ''}${strategy.total_pnl.toFixed(2)}
        </span>
      </div>
    </div>
  )
}
