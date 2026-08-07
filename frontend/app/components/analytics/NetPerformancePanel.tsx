import { useEffect, useState, useCallback } from 'react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { RefreshCw, DollarSign, Scale, Percent, ShieldAlert } from 'lucide-react'

// ──────────────────────────────────────────────
// V5 净值扣费看板：Net Profit Factor / fee-gross 比 / 盈亏比
// 数据源: GET /api/analytics/net-performance
// ──────────────────────────────────────────────

interface BucketStats {
  trades: number
  gross_pnl: number
  fees: number
  net_pnl: number
  wins: number
  win_rate: number
  avg_win: number
  avg_loss: number
  net_profit_factor: number | null
  fee_gross_ratio: number | null
}

interface NetPerformanceData {
  days: number
  headline: {
    net_profit_factor: number | null
    fee_gross_ratio: number | null
    payoff_ratio: number | null
    net_pnl: number | null
    fees: number | null
    trades: number | null
    win_rate: number | null
  }
  by_close_reason: Record<string, BucketStats>
  by_nature: Record<string, BucketStats>
  by_symbol: Record<string, BucketStats>
  v5_runtime_gates: Record<string, unknown>
  error?: string
}

function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

function MetricCard({
  title,
  value,
  hint,
  good,
  icon,
}: {
  title: string
  value: string
  hint: string
  good: boolean | null
  icon: React.ReactNode
}) {
  const color = good === null ? undefined : good ? '#16a34a' : '#dc2626'
  return (
    <Card>
      <CardContent className="pt-5">
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <p className="text-sm font-medium text-muted-foreground">{title}</p>
            <p className="text-2xl font-bold" style={{ color }}>
              {value}
            </p>
            <p className="text-xs text-muted-foreground">{hint}</p>
          </div>
          <div className="text-muted-foreground">{icon}</div>
        </div>
      </CardContent>
    </Card>
  )
}

function BucketTable({
  title,
  data,
}: {
  title: string
  data: Record<string, BucketStats> | undefined
}) {
  const rows = Object.entries(data || {}).sort(
    (a, b) => a[1].net_pnl - b[1].net_pnl,
  )
  if (rows.length === 0) return null
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-muted-foreground">
                <th className="text-left py-1.5 pr-2 font-medium">维度</th>
                <th className="text-right py-1.5 px-2 font-medium">笔数</th>
                <th className="text-right py-1.5 px-2 font-medium">净盈亏</th>
                <th className="text-right py-1.5 px-2 font-medium">手续费</th>
                <th className="text-right py-1.5 px-2 font-medium">胜率</th>
                <th className="text-right py-1.5 px-2 font-medium">均盈/均亏</th>
                <th className="text-right py-1.5 pl-2 font-medium">NPF</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([key, b]) => (
                <tr key={key} className="border-b last:border-0">
                  <td className="py-1.5 pr-2 font-mono">{key}</td>
                  <td className="text-right py-1.5 px-2">{b.trades}</td>
                  <td
                    className="text-right py-1.5 px-2 font-medium"
                    style={{ color: b.net_pnl >= 0 ? '#16a34a' : '#dc2626' }}
                  >
                    {fmtUsd(b.net_pnl)}
                  </td>
                  <td className="text-right py-1.5 px-2">${b.fees.toFixed(0)}</td>
                  <td className="text-right py-1.5 px-2">
                    {(b.win_rate * 100).toFixed(0)}%
                  </td>
                  <td className="text-right py-1.5 px-2">
                    ${b.avg_win.toFixed(0)} / ${b.avg_loss.toFixed(0)}
                  </td>
                  <td className="text-right py-1.5 pl-2">
                    {b.net_profit_factor === null ? '∞' : b.net_profit_factor.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}

export default function NetPerformancePanel() {
  const [days, setDays] = useState('7')
  const [data, setData] = useState<NetPerformanceData | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`/api/analytics/net-performance?days=${days}`)
      const json = await res.json()
      setData(json)
    } catch {
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [days])

  useEffect(() => {
    load()
  }, [load])

  const h = data?.headline
  const gates = data?.v5_runtime_gates || {}
  const gateEntries = Object.entries(gates).filter(([k]) => !k.startsWith('_'))

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">净值扣费看板（V5）</h2>
          <p className="text-sm text-muted-foreground">
            所有指标按「扣除手续费后」口径统计 — 验收线: NPF &gt; 1、fee/gross ≤ 10%、均盈 ≥ 均亏
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={days} onValueChange={setDays}>
            <SelectTrigger className="w-28">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7">近 7 天</SelectItem>
              <SelectItem value="14">近 14 天</SelectItem>
              <SelectItem value="30">近 30 天</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MetricCard
          title="Net Profit Factor"
          value={h?.net_profit_factor != null ? h.net_profit_factor.toFixed(2) : '—'}
          hint="净赢总额 ÷ 净亏总额，>1 才赚钱"
          good={h?.net_profit_factor != null ? h.net_profit_factor > 1 : null}
          icon={<Scale className="h-6 w-6" />}
        />
        <MetricCard
          title="fee / gross"
          value={
            h?.fee_gross_ratio != null
              ? `${(h.fee_gross_ratio * 100).toFixed(1)}%`
              : '—'
          }
          hint="手续费占毛利比，验收 ≤10%"
          good={h?.fee_gross_ratio != null ? h.fee_gross_ratio <= 0.1 : null}
          icon={<Percent className="h-6 w-6" />}
        />
        <MetricCard
          title="盈亏比（均盈/均亏）"
          value={h?.payoff_ratio != null ? h.payoff_ratio.toFixed(2) : '—'}
          hint="平均盈利 ÷ 平均亏损，目标 ≥1.8"
          good={h?.payoff_ratio != null ? h.payoff_ratio >= 1 : null}
          icon={<DollarSign className="h-6 w-6" />}
        />
        <MetricCard
          title="净盈亏 / 笔数"
          value={`${fmtUsd(h?.net_pnl)} / ${h?.trades ?? '—'}笔`}
          hint={`手续费合计 ${fmtUsd(h?.fees ? -h.fees : null)} · 胜率 ${
            h?.win_rate != null ? (h.win_rate * 100).toFixed(0) + '%' : '—'
          }`}
          good={h?.net_pnl != null ? h.net_pnl > 0 : null}
          icon={<ShieldAlert className="h-6 w-6" />}
        />
      </div>

      {gateEntries.length > 0 && (
        <Card className="border-amber-400/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">⚙️ 反馈闭环已生效的动态门槛</CardTitle>
            <CardDescription>
              由每日归因自动写入，存在 data/v5_gates_rollback.flag 时自动回退基准值
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2 text-sm">
              {gateEntries.map(([k, v]) => (
                <span
                  key={k}
                  className="px-2 py-1 rounded bg-amber-100 dark:bg-amber-900/30 font-mono"
                >
                  {k} = {JSON.stringify(v)}
                </span>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid lg:grid-cols-2 gap-4">
        <BucketTable title="按平仓原因（close_reason）" data={data?.by_close_reason} />
        <BucketTable title="按交易性质（trade_nature）" data={data?.by_nature} />
      </div>
      <BucketTable title="按币种（symbol）" data={data?.by_symbol} />
    </div>
  )
}
