import React from 'react'
import type { Position } from '../../api/types'
import { Badge } from '../ui/Badge'
import { SwipeAction } from '../ui/SwipeAction'
import { PriceDisplay } from '../ui/PriceDisplay'

interface PositionCardProps {
  position: Position
  onClose: () => void
  onAdjust: () => void
}

export const PositionCard: React.FC<PositionCardProps> = ({ position, onClose, onAdjust }) => {
  const pnlPercent = position.pnl_percent ?? 0
  const isProfit = pnlPercent >= 0
  const currentPrice = position.last_price
  const avgCost = position.avg_cost

  return (
    <SwipeAction actionLabel="平仓" actionVariant="danger" onAction={onClose}>
      <div
        className="p-4 bg-surface rounded-card border border-border"
        onClick={onAdjust}
      >
        {/* Header: Symbol + Direction + Leverage */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold">{position.symbol}</span>
            <span className={`text-sm font-medium px-2 py-0.5 rounded ${position.side === 'long' ? 'bg-profit/20 text-profit' : 'bg-loss/20 text-loss'}`}>
              {position.side === 'long' ? 'Long' : 'Short'}
            </span>
          </div>
          {position.leverage && (
            <Badge variant="neutral">{position.leverage}x</Badge>
          )}
        </div>

        {/* Price info */}
        <div className="space-y-1.5 text-sm">
          <div className="flex justify-between">
            <span className="text-muted">入场</span>
            <span className="tabular-nums">${avgCost.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted">当前</span>
            <div className="flex items-center gap-2">
              {currentPrice && (
                <span className="tabular-nums">${currentPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
              )}
              <PriceDisplay value={pnlPercent} suffix="%" className="text-sm font-medium" />
            </div>
          </div>
          <div className="flex justify-between">
            <span className="text-muted">数量</span>
            <span className="tabular-nums">{position.quantity} {position.symbol.split('/')[0]}</span>
          </div>
        </div>

        {/* Divider */}
        <div className="h-px bg-border my-3" />

        {/* PnL + TP/SL */}
        <div className="flex items-center justify-between">
          <div>
            <span className="text-muted text-xs">盈亏</span>
            <div className={`text-lg font-bold tabular-nums ${isProfit ? 'text-profit' : 'text-loss'}`}>
              {position.unrealized_pnl && position.unrealized_pnl >= 0 ? '+' : ''}${position.unrealized_pnl?.toFixed(2) ?? '—'}
            </div>
          </div>
          <div className="text-right">
            {position.take_profit && (
              <div className="text-xs text-muted">止盈 <span className="tabular-nums text-fg">${position.take_profit.toLocaleString()}</span></div>
            )}
            {position.stop_loss && (
              <div className="text-xs text-muted">止损 <span className="tabular-nums text-fg">${position.stop_loss.toLocaleString()}</span></div>
            )}
          </div>
        </div>
      </div>
    </SwipeAction>
  )
}
