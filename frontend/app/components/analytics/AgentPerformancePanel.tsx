import { useCallback, useEffect, useState } from 'react'
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
import {
  RefreshCw,
  TrendingUp,
  Clock,
  Target,
} from 'lucide-react'

// ──────────────────────────────────────────────
// Long Agent 绩效看板（中周期已合并到长线）
// 数据源: GET /api/analytics/by-agent
// ──────────────────────────────────────────────

interface AgentStats {
  trades: number
  net_pnl: number
  gross_pnl: number
  fees: number
  wins: number
  win_rate: number
  profit_factor: number | null
  avg_win: number
  avg_loss: number
  avg_hold_hours: number | null
  scenario_hit_rate?: number | null
}

interface ByAgentData {
  days: number
  agents: {
    // swing 已合并到 trend_follow（中长线统一归因），字段保留仅用于向后兼容历史持仓
    swing?: AgentStats
    trend_follow?: AgentStats
  }
  error?: string
}

const AGENT_META = {
  trend_follow: {
    title: '长线 Agent · TrendAgent (含中周期)',
    subtitle: '长线趋势 · 中长线统一归因 · 让利润奔跑',
    icon: TrendingUp,
    accent: '#7c3aed',
    pfTarget: 2.0,
    holdHint: '目标 >12h 盈利持仓',
  },
} as const

function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `${(v * 100).toFixed(0)}%`
}

function StatRow({
  label,
  value,
  good,
}: {
  label: string
  value: string
  good?: boolean | null
}) {
  const color =
    good === undefined || good === null
      ? undefined
      : good
        ? '#16a34a'
        : '#dc2626'
  return (
    <div className="flex items-center justify-between py-1.5 border-b last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-semibold tabular-nums" style={{ color }}>
        {value}
      </span>
    </div>
  )
}

function AgentCard({
  agentKey,
  stats,
}: {
  agentKey: keyof typeof AGENT_META
  stats: AgentStats | undefined
}) {
  const meta = AGENT_META[agentKey]
  const Icon = meta.icon
  const s = stats || {
    trades: 0,
    net_pnl: 0,
    gross_pnl: 0,
    fees: 0,
    wins: 0,
    win_rate: 0,
    profit_factor: null,
    avg_win: 0,
    avg_loss: 0,
    avg_hold_hours: null,
  }

  const payoff =
    s.avg_win && s.avg_loss ? s.avg_win / s.avg_loss : null
  const pfGood =
    s.profit_factor != null ? s.profit_factor >= meta.pfTarget : null
  const pnlGood = s.net_pnl > 0

  return (
    <Card className="overflow-hidden">
      <div className="h-1" style={{ backgroundColor: meta.accent }} />
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base flex items-center gap-2">
              <Icon className="h-4 w-4" style={{ color: meta.accent }} />
              {meta.title}
            </CardTitle>
            <CardDescription className="mt-1">{meta.subtitle}</CardDescription>
          </div>
          <div
            className="text-2xl font-bold tabular-nums shrink-0"
            style={{ color: pnlGood ? '#16a34a' : s.net_pnl < 0 ? '#dc2626' : undefined }}
          >
            {fmtUsd(s.net_pnl)}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-0">
        <StatRow
          label="成交笔数 / 胜率"
          value={`${s.trades} 笔 · ${fmtPct(s.win_rate)}`}
          good={s.win_rate >= 0.5 ? true : s.trades > 0 ? false : null}
        />
        <StatRow
          label="Profit Factor（净）"
          value={s.profit_factor != null ? s.profit_factor.toFixed(2) : '—'}
          good={pfGood}
        />
        <StatRow
          label="均盈 / 均亏"
          value={`$${s.avg_win.toFixed(0)} / $${s.avg_loss.toFixed(0)}`}
          good={payoff != null ? payoff >= 1.8 : null}
        />
        <StatRow
          label="平均持仓时长"
          value={
            s.avg_hold_hours != null
              ? `${s.avg_hold_hours}h`
              : '—'
          }
        />
        {agentKey === 'trend_follow' && (
          <StatRow
            label="Scenario 命中率"
            value={fmtPct(s.scenario_hit_rate)}
            good={
              s.scenario_hit_rate != null
                ? s.scenario_hit_rate >= 0.5
                : null
            }
          />
        )}
        <StatRow label="手续费合计" value={`$${s.fees.toFixed(0)}`} />
        <p className="text-xs text-muted-foreground pt-2 flex items-center gap-1">
          <Clock className="h-3 w-3" />
          {meta.holdHint}
          <>
            {' · '}
            <Target className="h-3 w-3 inline" />
            PF 验收 ≥ {meta.pfTarget}
          </>
        </p>
      </CardContent>
    </Card>
  )
}

export default function AgentPerformancePanel() {
  const [days, setDays] = useState('30')
  const [data, setData] = useState<ByAgentData | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`/api/analytics/by-agent?days=${days}`)
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

  const trend = data?.agents?.trend_follow
  const totalTrades = trend?.trades || 0
  const totalNet = trend?.net_pnl || 0

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">长线 Agent 绩效</h2>
          <p className="text-sm text-muted-foreground">
            TrendAgent 中长线统一归因 — 净扣费口径，含持仓时长与场景预测命中率
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
              <SelectItem value="90">近 90 天</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {data?.error && (
        <Card className="border-red-300 bg-red-50">
          <CardContent className="pt-4 text-sm text-red-700">{data.error}</CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <Card>
          <CardContent className="pt-5">
            <p className="text-sm text-muted-foreground">长线 合计净盈亏</p>
            <p
              className="text-2xl font-bold tabular-nums"
              style={{ color: totalNet >= 0 ? '#16a34a' : '#dc2626' }}
            >
              {fmtUsd(totalNet)}
            </p>
            <p className="text-xs text-muted-foreground mt-1">{totalTrades} 笔成交</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-5">
            <p className="text-sm text-muted-foreground">Profit Factor</p>
            <p className="text-2xl font-bold tabular-nums">
              {trend?.profit_factor != null ? trend.profit_factor.toFixed(2) : '—'}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {trend?.trades || 0} 笔 · 胜率 {fmtPct(trend?.win_rate)}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-5">
            <p className="text-sm text-muted-foreground">长线 场景命中</p>
            <p className="text-2xl font-bold tabular-nums">
              {fmtPct(trend?.scenario_hit_rate)}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              基于 TrendPredictionRecord 平仓评分
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <AgentCard agentKey="trend_follow" stats={trend} />
      </div>
    </div>
  )
}
