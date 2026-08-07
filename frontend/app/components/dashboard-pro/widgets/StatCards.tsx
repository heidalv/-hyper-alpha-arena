/**
 * 统计卡组：Equity / PnL / WinRate / ActivePositions
 *
 * 单账户 = 直接展示；多账户已选 = 聚合求和（胜率按交易数加权平均），
 * 并在副标题中标注参与聚合的账户数，避免"多选却看不出差异"的困惑。
 */
import { Wallet, TrendingUp, Percent, Layers } from 'lucide-react'
import { cn } from '@/lib/utils'
import AnimatedNumber from '@/components/ui/animated-number'
import type { WidgetProps } from '../types'

function CardShell({
  icon,
  title,
  children,
  subtitle,
}: {
  icon: React.ReactNode
  title: string
  children: React.ReactNode
  subtitle?: string
}) {
  return (
    <div className="h-full w-full rounded-lg border border-border/70 bg-card/80 backdrop-blur-sm p-3.5 flex flex-col gap-1 justify-center">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        {icon}
        <span className="text-[11px] font-medium uppercase tracking-wide">{title}</span>
      </div>
      {children}
      {subtitle && <span className="text-[10px] text-muted-foreground">{subtitle}</span>}
    </div>
  )
}

function useAggregates(overviews: WidgetProps['overviews']) {
  const valid = overviews.filter((o) => !o.error)
  const equity = valid.reduce((acc, o) => acc + (o.equity || 0), 0)
  const totalPnl = valid.reduce((acc, o) => acc + (o.total_pnl || 0), 0)
  const totalTrades = valid.reduce((acc, o) => acc + (o.total_trades || 0), 0)
  const weightedWins = valid.reduce((acc, o) => acc + (o.win_rate || 0) * (o.total_trades || 0), 0)
  const winRate = totalTrades > 0 ? weightedWins / totalTrades : 0
  const activePositions = valid.reduce((acc, o) => acc + (o.active_positions || 0), 0)
  return { equity, totalPnl, totalTrades, winRate, activePositions, accountCount: valid.length }
}

export function EquityCardWidget({ overviews }: WidgetProps) {
  const { equity, accountCount } = useAggregates(overviews)
  return (
    <CardShell
      icon={<Wallet className="h-3.5 w-3.5" />}
      title="总权益"
      subtitle={accountCount > 1 ? `${accountCount} 个账户合计` : undefined}
    >
      <div className="text-xl font-bold tabular-nums text-foreground">
        <AnimatedNumber value={equity} decimals={2} prefix="$" />
      </div>
    </CardShell>
  )
}

export function PnlCardWidget({ overviews }: WidgetProps) {
  const { totalPnl, accountCount } = useAggregates(overviews)
  const positive = totalPnl >= 0
  return (
    <CardShell
      icon={<TrendingUp className="h-3.5 w-3.5" />}
      title="总盈亏"
      subtitle={accountCount > 1 ? `${accountCount} 个账户合计` : undefined}
    >
      <div
        className={cn(
          'text-xl font-bold tabular-nums',
          positive ? 'text-emerald-400' : 'text-red-400',
        )}
      >
        <AnimatedNumber value={totalPnl} decimals={2} prefix={positive ? '+$' : '$'} />
      </div>
    </CardShell>
  )
}

export function WinRateCardWidget({ overviews }: WidgetProps) {
  const { winRate, totalTrades } = useAggregates(overviews)
  const good = winRate >= 50
  return (
    <CardShell
      icon={<Percent className="h-3.5 w-3.5" />}
      title="胜率"
      subtitle={`${totalTrades} 笔交易`}
    >
      <div className={cn('text-xl font-bold tabular-nums', good ? 'text-emerald-400' : 'text-amber-400')}>
        <AnimatedNumber value={winRate} decimals={1} suffix="%" />
      </div>
    </CardShell>
  )
}

export function ActivePositionsCardWidget({ overviews }: WidgetProps) {
  const { activePositions, accountCount } = useAggregates(overviews)
  return (
    <CardShell
      icon={<Layers className="h-3.5 w-3.5" />}
      title="持仓中"
      subtitle={accountCount > 1 ? `${accountCount} 个账户合计` : undefined}
    >
      <div className="text-xl font-bold tabular-nums text-foreground">{activePositions}</div>
    </CardShell>
  )
}
