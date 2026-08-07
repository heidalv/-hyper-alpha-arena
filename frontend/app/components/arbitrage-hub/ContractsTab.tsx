import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { ArrowRightLeft, RefreshCw } from 'lucide-react'
import { fmt, getUnifiedPositions, type UnifiedPosition } from '@/lib/arbitrageApi'

export default function ContractsTab({ onRefresh }: { onRefresh?: () => void }) {
  const [positions, setPositions] = useState<UnifiedPosition[]>([])
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const res = await getUnifiedPositions('all')
      setPositions(res.positions || [])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const activeCount = positions.filter(p => ['active', 'holding'].includes(p.status)).length
  const totalPnl = positions.reduce((sum, p) => sum + Number(p.pnl || 0) + Number(p.rebate || 0), 0)

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <ArrowRightLeft className="w-5 h-5 text-blue-500" /> 合约交易
          </h2>
          <p className="text-sm text-muted-foreground">
            统一展示 Rebate/S1-S8 的合约腿和 V3 统计套利仓位；V3 保留为二级视图。
          </p>
        </div>
        <button
          onClick={() => { load(); onRefresh?.() }}
          className="px-3 py-2 rounded-lg bg-secondary hover:bg-secondary/80 text-sm flex items-center gap-1.5"
        >
          <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} /> 刷新
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Metric label="统一仓位" value={String(positions.length)} />
        <Metric label="活跃仓位" value={String(activeCount)} />
        <Metric label="合计收益" value={`$${fmt(totalPnl)}`} />
      </div>

      <div className="rounded-xl border border-border bg-card overflow-hidden">
        <div className="px-4 py-3 border-b border-border font-semibold">统一仓位列表</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-muted-foreground">
              <tr>
                <th className="text-left px-4 py-2">来源</th>
                <th className="text-left px-4 py-2">策略</th>
                <th className="text-left px-4 py-2">交易对</th>
                <th className="text-right px-4 py-2">名义金额</th>
                <th className="text-right px-4 py-2">PnL</th>
                <th className="text-right px-4 py-2">积分/返利</th>
                <th className="text-left px-4 py-2">状态</th>
              </tr>
            </thead>
            <tbody>
              {positions.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-muted-foreground">
                    暂无合约套利仓位
                  </td>
                </tr>
              ) : positions.map(p => (
                <tr key={`${p.source}-${p.id}`} className="border-t border-border">
                  <td className="px-4 py-2">
                    <span className={cn(
                      'px-2 py-1 rounded-full text-xs',
                      p.source === 'v3' ? 'bg-blue-500/10 text-blue-600' : 'bg-amber-500/10 text-amber-600'
                    )}>
                      {p.source}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-medium">{p.strategy_type}</td>
                  <td className="px-4 py-2">{p.symbol}</td>
                  <td className="px-4 py-2 text-right">${fmt(p.notional_usd)}</td>
                  <td className={cn('px-4 py-2 text-right', p.pnl >= 0 ? 'text-green-600' : 'text-red-600')}>
                    ${fmt(p.pnl)}
                  </td>
                  <td className="px-4 py-2 text-right">
                    {fmt(p.points, 0)} pts / ${fmt(p.rebate)}
                  </td>
                  <td className="px-4 py-2">{p.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-xl font-bold mt-1">{value}</div>
    </div>
  )
}
