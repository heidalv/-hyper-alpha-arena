import React from 'react'
import type { Position } from '../../api/types'
import { PriceDisplay } from '../ui/PriceDisplay'
import { Badge } from '../ui/Badge'

interface SymbolGridProps {
  positions: Position[]
  onSymbolClick?: (symbol: string) => void
}

export const SymbolGrid: React.FC<SymbolGridProps> = ({ positions, onSymbolClick }) => {
  return (
    <div className="mx-4 mt-3">
      <h3 className="text-sm text-muted mb-2">持仓</h3>
      {positions.length === 0 ? (
        <div className="p-6 bg-surface rounded-card border border-border text-center text-muted text-sm">
          暂无持仓
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          {positions.map(pos => {
            const pnlPercent = pos.pnl_percent ?? 0
            const isProfit = pnlPercent >= 0
            return (
              <div
                key={pos.id}
                className="p-3 bg-surface rounded-card border border-border cursor-pointer touch-feedback"
                style={{ borderLeftWidth: '3px', borderLeftColor: isProfit ? '#00dc82' : '#ef4444' }}
                onClick={() => onSymbolClick?.(pos.symbol)}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-base font-bold">{pos.symbol}</span>
                  <Badge variant={isProfit ? 'success' : 'danger'}>
                    {isProfit ? '▲' : '▼'} {Math.abs(pnlPercent).toFixed(1)}%
                  </Badge>
                </div>

                <div className="flex items-center gap-2 text-sm">
                  <span className={pos.side === 'long' ? 'text-profit' : 'text-loss'}>
                    {pos.side === 'long' ? 'Long' : 'Short'}
                  </span>
                  {pos.leverage && (
                    <span className="text-muted">{pos.leverage}x</span>
                  )}
                </div>

                {pos.last_price && (
                  <div className="mt-1 text-sm tabular-nums text-muted">
                    ${pos.last_price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
