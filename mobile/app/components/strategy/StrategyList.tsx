import React, { useState } from 'react'
import type { AIStrategy } from '../../api/types'
import { StrategyCard } from './StrategyCard'
import { BottomSheet } from '../ui/BottomSheet'

interface StrategyListProps {
  strategies: AIStrategy[]
}

export const StrategyList: React.FC<StrategyListProps> = ({ strategies }) => {
  const [expandedId, setExpandedId] = useState<number | null>(null)

  // Group by symbol
  const grouped: Record<string, AIStrategy[]> = {}
  strategies.forEach(s => {
    const key = s.primary_symbol
    if (!grouped[key]) grouped[key] = []
    grouped[key].push(s)
  })

  const expandedStrategy = strategies.find(s => s.id === expandedId)

  return (
    <div className="mx-4 mt-3 space-y-4">
      {Object.entries(grouped).map(([symbol, strats]) => (
        <div key={symbol}>
          <h3 className="text-sm font-medium text-muted mb-2">{symbol}</h3>
          <div className="space-y-2">
            {strats.map(strategy => (
              <StrategyCard
                key={strategy.id}
                strategy={strategy}
                onExpand={() => setExpandedId(strategy.id)}
              />
            ))}
          </div>
        </div>
      ))}

      {/* Detail BottomSheet */}
      <BottomSheet
        open={!!expandedStrategy}
        onClose={() => setExpandedId(null)}
        title={expandedStrategy?.name ?? ''}
      >
        {expandedStrategy && (
          <div className="pb-4 space-y-3">
            <div className="flex justify-between text-sm">
              <span className="text-muted">策略 ID</span>
              <span className="font-mono text-xs">{expandedStrategy.id}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted">状态</span>
              <span className={`font-medium ${expandedStrategy.status === 'active' ? 'text-profit' : expandedStrategy.status === 'paused' ? 'text-muted' : 'text-loss'}`}>
                {expandedStrategy.status}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted">累计 PnL</span>
              <span className={`font-medium tabular-nums ${expandedStrategy.total_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                {expandedStrategy.total_pnl >= 0 ? '+' : ''}${expandedStrategy.total_pnl.toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted">交易次数</span>
              <span className="tabular-nums">{expandedStrategy.total_trades}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted">创建时间</span>
              <span className="tabular-nums">{new Date(expandedStrategy.created_at).toLocaleDateString('zh-CN')}</span>
            </div>
          </div>
        )}
      </BottomSheet>
    </div>
  )
}
