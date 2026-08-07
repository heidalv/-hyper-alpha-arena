import PositionsTable, { type DashboardPosition } from '@/components/dashboard/PositionsTable'
import type { WidgetProps } from '../types'

/** 把多账户 overview 的持仓拍平为统一表格，附加账户标签便于多选对比时区分来源。 */
export default function PositionsTableWidget({ overviews }: WidgetProps) {
  const positions: DashboardPosition[] = overviews.flatMap((o) =>
    (o.positions || []).map((p) => ({
      symbol: p.symbol,
      side: p.side,
      size: p.size,
      entry_price: p.entry_price,
      mark_price: p.mark_price,
      unrealized_pnl: p.unrealized_pnl,
      leverage: p.leverage,
      account_id: o.account_id,
    })),
  )

  return <PositionsTable positions={positions} className="h-full" />
}
