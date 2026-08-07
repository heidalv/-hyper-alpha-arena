import React from 'react'
import type { Overview } from '../../api/types'
import { PriceDisplay } from '../ui/PriceDisplay'

interface AccountCardProps {
  overview: Overview
}

export const AccountCard: React.FC<AccountCardProps> = ({ overview }) => {
  const totalAssets = overview.total_assets
  const positionsValue = overview.positions_value
  const availableCash = overview.account.current_cash
  const initialCapital = overview.account.initial_capital
  const totalPnl = totalAssets - initialCapital
  const totalPnlPercent = initialCapital > 0 ? (totalPnl / initialCapital) * 100 : 0

  return (
    <div className="mx-4 mt-3 p-4 bg-surface rounded-card border border-border">
      {/* Total equity */}
      <div className="mb-3">
        <span className="text-sm text-muted">总权益</span>
        <div className="text-2xl font-bold tabular-nums mt-1">
          ${totalAssets.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </div>
      </div>

      {/* PnL */}
      <div className="flex items-center gap-2 mb-4">
        <PriceDisplay value={totalPnl} prefix={totalPnl >= 0 ? '+$' : '-$'} showSign percent={false} />
        <span className="text-sm">
          <PriceDisplay value={totalPnlPercent} prefix={totalPnlPercent >= 0 ? '(' : '(-'} suffix=")" percent showSign />
        </span>
      </div>

      {/* Divider */}
      <div className="h-px bg-border mb-3" />

      {/* Available / Positions */}
      <div className="flex justify-between">
        <div>
          <span className="text-xs text-muted">可用资金</span>
          <div className="text-base font-medium tabular-nums">
            ${availableCash.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
          </div>
        </div>
        <div className="text-right">
          <span className="text-xs text-muted">持仓价值</span>
          <div className="text-base font-medium tabular-nums">
            ${positionsValue.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
          </div>
        </div>
      </div>
    </div>
  )
}
