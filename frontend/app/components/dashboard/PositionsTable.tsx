import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'

export interface DashboardPosition {
  symbol: string
  side: string
  size: number
  entry_price: number
  mark_price: number
  unrealized_pnl: number
  leverage: number | null
  account_id: number
  pnl_pct?: number | null
}

interface PositionsTableProps {
  positions: DashboardPosition[]
  className?: string
}

function formatPrice(v: number): string {
  if (v >= 1000) return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (v >= 1) return v.toFixed(4)
  return v.toPrecision(4)
}

function formatPnl(v: number): string {
  const sign = v >= 0 ? '+' : ''
  return `${sign}$${v.toFixed(2)}`
}

function calcPnlPct(entry: number, mark: number, side: string): number {
  if (!entry || entry === 0) return 0
  const diff = side.toUpperCase() === 'LONG' || side.toUpperCase() === 'BUY'
    ? (mark - entry) / entry
    : (entry - mark) / entry
  return diff * 100
}

export default function PositionsTable({ positions, className }: PositionsTableProps) {
  if (positions.length === 0) {
    return (
      <div className={cn('bg-card border border-border rounded-lg flex items-center justify-center h-full', className)}>
        <span className="text-sm text-muted-foreground">暂无活跃持仓</span>
      </div>
    )
  }

  return (
    <div className={cn('bg-card border border-border rounded-lg flex flex-col h-full overflow-hidden', className)}>
      <div className="px-3 py-2.5 border-b border-border flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-foreground">
          活跃持仓
        </span>
        <Badge variant="outline" className="text-[10px] h-5">
          {positions.length}
        </Badge>
      </div>

      <div className="flex-1 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-card z-10">
            <tr className="text-muted-foreground border-b border-border">
              <th className="text-left px-3 py-2 font-medium">币种</th>
              <th className="text-left px-2 py-2 font-medium">方向</th>
              <th className="text-right px-2 py-2 font-medium">杠杆</th>
              <th className="text-right px-2 py-2 font-medium">入场价</th>
              <th className="text-right px-2 py-2 font-medium">标记价</th>
              <th className="text-right px-3 py-2 font-medium">盈亏</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((pos, idx) => {
              const isLong = pos.side.toUpperCase() === 'LONG' || pos.side.toUpperCase() === 'BUY'
              const pnlPct = pos.pnl_pct ?? calcPnlPct(pos.entry_price, pos.mark_price, pos.side)
              const pnlPositive = pos.unrealized_pnl >= 0

              return (
                <tr
                  key={`${pos.symbol}-${pos.account_id}-${idx}`}
                  className="border-b border-border/50 hover:bg-muted/30 transition-colors"
                >
                  <td className="px-3 py-2 font-medium text-foreground">
                    {pos.symbol.replace('/USDT:USDT', '').replace('/USD:USD', '')}
                  </td>
                  <td className="px-2 py-2">
                    <span
                      className={cn(
                        'inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold',
                        isLong
                          ? 'bg-emerald-500/10 text-emerald-400'
                          : 'bg-red-500/10 text-red-400'
                      )}
                    >
                      {isLong ? 'LONG' : 'SHORT'}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-right text-muted-foreground">
                    {pos.leverage ? `${pos.leverage}x` : '-'}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">
                    {formatPrice(pos.entry_price)}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums text-foreground">
                    {formatPrice(pos.mark_price)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className={cn('tabular-nums font-medium', pnlPositive ? 'text-emerald-400' : 'text-red-400')}>
                      {formatPnl(pos.unrealized_pnl)}
                    </div>
                    <div className={cn('text-[10px] tabular-nums', pnlPositive ? 'text-emerald-400/70' : 'text-red-400/70')}>
                      {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
