import React from 'react'
import { PriceDisplay } from '../ui/PriceDisplay'

interface PnlSummaryProps {
  dailyPnl: number
  dailyPnlPercent: number
  winRate: number
  totalTrades: number
}

export const PnlSummary: React.FC<PnlSummaryProps> = ({ dailyPnl, dailyPnlPercent, winRate, totalTrades }) => {
  return (
    <div className="mx-4 mt-3 grid grid-cols-2 gap-3">
      {/* Daily PnL */}
      <div className="p-3 bg-surface rounded-card border border-border">
        <span className="text-xs text-muted">今日盈亏</span>
        <div className="mt-1">
          <PriceDisplay value={dailyPnl} prefix={dailyPnl >= 0 ? '+$' : '-$'} showSign className="text-lg font-bold" />
        </div>
        <PriceDisplay value={dailyPnlPercent} suffix="%" className="text-xs" />
      </div>

      {/* Win Rate */}
      <div className="p-3 bg-surface rounded-card border border-border">
        <span className="text-xs text-muted">胜率</span>
        <div className="mt-1">
          <span className="text-lg font-bold tabular-nums text-fg">
            {winRate.toFixed(1)}%
          </span>
        </div>
        <span className="text-xs text-muted">{totalTrades} 笔交易</span>
      </div>
    </div>
  )
}
