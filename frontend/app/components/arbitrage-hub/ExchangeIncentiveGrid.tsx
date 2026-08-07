/**
 * ExchangeIncentiveGrid — 交易所激励数据网格
 *
 * 6个交易所的费率/积分/返利/活动数据以卡片网格形式展示
 */
import { cn } from '@/lib/utils'
import type { ExchangeIncentiveSummary } from '@/lib/arbitrageApi'
import { fmt, num } from '@/lib/arbitrageApi'

interface Props {
  incentives: ExchangeIncentiveSummary[]
}

export default function ExchangeIncentiveGrid({ incentives }: Props) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
      {incentives.map((inc) => (
        <div
          key={inc.exchange}
          className={cn(
            'rounded-xl border p-4',
            inc.is_connected ? 'border-border bg-muted/30' : 'border-border/50 bg-muted/10 opacity-60',
          )}
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-3">
            <span className="font-semibold text-sm">{inc.exchange.toUpperCase()}</span>
            <span className={cn(
              'text-xs font-medium px-2 py-0.5 rounded-full',
              inc.is_connected ? 'bg-green-500/20 text-green-400' : 'bg-gray-500/20 text-gray-400',
            )}>
              {inc.is_connected ? '已连接' : '未连接'}
            </span>
          </div>

          {/* Fee Tier */}
          <div className="space-y-1 text-xs mb-3">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Maker</span>
              <span className={cn('font-mono', num(inc.fee_tier?.maker_rate) <= 0.0001 ? 'text-green-400' : 'text-foreground')}>
                {fmt(num(inc.fee_tier?.maker_rate) * 100, 3)}%
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Taker</span>
              <span className="font-mono">{fmt(num(inc.fee_tier?.taker_rate) * 100, 3)}%</span>
            </div>
            {num(inc.fee_tier?.rebate_rate) > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">返利</span>
                <span className="font-mono text-green-400">{fmt(num(inc.fee_tier?.rebate_rate) * 100, 1)}%</span>
              </div>
            )}
            <div className="flex justify-between border-t border-border/30 pt-1">
              <span className="text-muted-foreground">净Taker成本</span>
              <span className={cn('font-mono font-bold', num(inc.fee_tier?.effective_taker_cost) < 0 ? 'text-green-400' : 'text-foreground')}>
                {fmt(num(inc.fee_tier?.effective_taker_cost) * 100, 4)}%
              </span>
            </div>
          </div>

          {/* Points */}
          {num(inc.points?.points_balance) > 0 && (
            <div className="text-xs text-muted-foreground border-t border-border/30 pt-2 mb-2">
              <div className="flex justify-between">
                <span>积分余额</span>
                <span className="font-mono text-foreground">{num(inc.points?.points_balance).toLocaleString()}</span>
              </div>
              {inc.points?.airdrop_eligible && (
                <div className="flex justify-between mt-0.5">
                  <span>空投预估</span>
                  <span className="font-mono text-green-400">${fmt(inc.points?.estimated_airdrop_value, 2)}</span>
                </div>
              )}
            </div>
          )}

          {/* Rebate */}
          {num(inc.rebate?.current_rebate_rate) > 0 && (
            <div className="text-xs text-muted-foreground border-t border-border/30 pt-2">
              <div className="flex justify-between">
                <span>当前返利率</span>
                <span className="font-mono text-green-400">{fmt(num(inc.rebate?.current_rebate_rate) * 100, 1)}%</span>
              </div>
              <div className="flex justify-between mt-0.5">
                <span>周预期返利</span>
                <span className="font-mono text-foreground">${fmt(inc.rebate?.projected_weekly_rebate, 2)}</span>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
