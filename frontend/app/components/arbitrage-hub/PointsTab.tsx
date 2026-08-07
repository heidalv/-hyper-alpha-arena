/**
 * PointsTab — 积分中心
 *
 * 积分总览 + 各交易所积分卡片 + 策略执行明细 + 活跃积分仓位
 */
import React, { useState, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { Activity, Star, DollarSign, TrendingUp, RefreshCw } from 'lucide-react'
import {
  getPointsTransactions, STRATEGY_META, STRATEGY_PLAYBOOKS, fmt, num,
  POINTS_ARB_STRATEGIES, TRADE_POINTS_STRATEGIES,
  type PointsTransaction, type PointsSummary,
  type ArbitrageStatus, type ArbitragePosition, type ArbitrageOpportunity,
  type RebateStatus, type RebateOpportunity, type RebatePosition,
  type CapitalAllocation, type WashTradeStatus, type RebateAnalytics,
  type ExchangeIncentiveSummary, type StrategyConfigDetail, type RebateEvent,
} from '@/lib/arbitrageApi'

interface Props {
  arbStatus: ArbitrageStatus
  arbPositions: ArbitragePosition[]
  arbOpps: ArbitrageOpportunity[]
  rebStatus: RebateStatus
  rebOpps: RebateOpportunity[]
  rebPositions: RebatePosition[]
  rebCapital: CapitalAllocation
  washStatus: WashTradeStatus
  rebAnalytics: RebateAnalytics
  incentives: ExchangeIncentiveSummary[]
  strategyConfigs: Record<string, StrategyConfigDetail>
  events: RebateEvent[]
  notifications: Array<{ id: string; type: string; message: string; ts: number }>
  pointsSummary: PointsSummary | null
  mode?: 'points_arb' | 'trade_points'
  onRefresh?: () => void
}

export default function PointsTab({
  rebPositions,
  pointsSummary,
  mode = 'points_arb',
  onRefresh,
}: Props) {
  const [transactions, setTransactions] = useState<PointsTransaction[]>([])
  const [loadingTx, setLoadingTx] = useState(false)

  const strategyFilter = React.useMemo<string[]>(
    () => (mode === 'trade_points' ? [...TRADE_POINTS_STRATEGIES] : [...POINTS_ARB_STRATEGIES]),
    [mode],
  )
  const pageTitle = mode === 'trade_points' ? '交易积分' : '套利积分'

  const loadTransactions = React.useCallback(() => {
    setLoadingTx(true)
    return getPointsTransactions(undefined, undefined, 50)
      .then(res => {
        setTransactions(
          res.transactions.filter(tx => strategyFilter.includes(tx.strategy_type))
        )
      })
      .finally(() => setLoadingTx(false))
  }, [strategyFilter])

  useEffect(() => {
    loadTransactions()
  }, [loadTransactions])

  // 刷新按钮：同时重拉 transactions 与全局数据
  const handleRefresh = () => {
    loadTransactions()
    onRefresh?.()
  }

  // 筛选出积分相关的活跃仓位
  const activePointsPositions = rebPositions.filter(p =>
    strategyFilter.includes(p.strategy_type)
  )

  // 顶部汇总按当前策略组过滤（后端 by_strategy 聚合）
  const groupSummary = strategyFilter.reduce(
    (acc, sid) => {
      const s = pointsSummary?.by_strategy?.[sid]
      if (s) {
        acc.points += num(s.points_earned_total)
        acc.value += num(s.estimated_value_usd)
        acc.revenue += num(s.conversion_revenue_usd)
      }
      return acc
    },
    { points: 0, value: 0, revenue: 0 },
  )

  const riskDotColor = (status: 'healthy' | 'warning' | 'danger') => {
    if (status === 'healthy') return 'bg-green-500'
    if (status === 'warning') return 'bg-yellow-500'
    return 'bg-red-500'
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">{pageTitle}</h2>
        <p className="text-sm text-muted-foreground">
          {mode === 'trade_points'
            ? '聚焦 S2/S4/S6：VIP、竞赛与费率差（S1 已下线，S6 已关闭，当前均不自动执行）。'
            : '聚焦 S3/S7/S8：平台积分挖矿与空投资格（S5 已下线）。'}
        </p>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {STRATEGY_PLAYBOOKS.filter(p => p.category === mode).map(playbook => (
            <div key={playbook.id} className="rounded-xl border border-border bg-card p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-semibold">{playbook.name}</div>
                  <div className="text-xs text-muted-foreground mt-1">{playbook.summary}</div>
                </div>
                <span className={cn(
                  'text-[10px] px-2 py-1 rounded-full',
                  playbook.monitor_only ? 'bg-yellow-500/10 text-yellow-600' :
                  playbook.risk === 'high' ? 'bg-red-500/10 text-red-600' :
                  'bg-green-500/10 text-green-600'
                )}>
                  {playbook.monitor_only ? 'monitor_only' : playbook.risk}
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5 mt-3">
                {playbook.strategies.map(s => (
                  <span key={s} className="text-xs px-2 py-1 rounded bg-muted">
                    {s} · {STRATEGY_META[s]?.name || s}
                  </span>
                ))}
              </div>
              <div className="text-xs text-muted-foreground mt-3">
                资金 ${playbook.capital_usd} · 默认杠杆 {playbook.default_leverage}x · Live 必须人工确认
              </div>
            </div>
          ))}
        </div>
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="font-semibold">设置侧栏</div>
          <div className="text-xs text-muted-foreground mt-1">
            策略启停、风控覆盖和 AI 生成配置仍在「专用套利」与「规则同步」中统一确认。
          </div>
          <div className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between"><span>当前策略组</span><span>{strategyFilter.join('/')}</span></div>
            <div className="flex justify-between"><span>活跃仓位</span><span>{activePointsPositions.length}</span></div>
            <div className="flex justify-between"><span>推荐默认</span><span>{mode === 'points_arb' ? '300U S8（+S3）' : '暂无（均已关闭）'}</span></div>
          </div>
        </div>
      </div>
      {/* ── A. Points Overview Cards（P0 修复：按当前策略组过滤，不再两个 Tab 数字相同）── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <SummaryCard
          icon={<Star className="w-5 h-5 text-yellow-400" />}
          label={`积分获得 (${strategyFilter.join('/')})`}
          value={String(fmt(groupSummary.points, 2))}
          sub={`全部策略合计 ${fmt(pointsSummary?.total_points_earned ?? 0, 2)}`}
        />
        <SummaryCard
          icon={<DollarSign className="w-5 h-5 text-blue-400" />}
          label="估算价值"
          value={`$${fmt(groupSummary.value, 2)}`}
          sub={`全部策略合计 $${fmt(pointsSummary?.total_estimated_value_usd ?? 0, 2)}`}
        />
        <SummaryCard
          icon={<TrendingUp className="w-5 h-5 text-green-400" />}
          label="转换收益"
          value={`$${fmt(groupSummary.revenue, 2)}`}
          sub={`全部策略合计 $${fmt(pointsSummary?.total_conversion_revenue_usd ?? 0, 2)}`}
        />
      </div>

      {/* ── B. Per-Exchange Points Cards ── */}
      {pointsSummary && Object.keys(pointsSummary.exchanges).length > 0 && (
        <Section title={`交易所积分明细 (${Object.keys(pointsSummary.exchanges).length})`}>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {Object.entries(pointsSummary.exchanges).map(([exchange, info]) => (
              <div
                key={exchange}
                className="rounded-xl border border-border bg-muted/30 p-4 space-y-3"
              >
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold">{exchange}</h4>
                  <span
                    className={cn(
                      'w-2.5 h-2.5 rounded-full inline-block',
                      riskDotColor(info.risk_status)
                    )}
                    title={info.risk_status}
                  />
                </div>
                <div className="grid grid-cols-2 gap-y-2 text-xs">
                  <div className="text-muted-foreground">积分获得</div>
                  <div className="text-right font-mono font-medium">
                    {num(info.points_earned_total).toLocaleString()}
                  </div>

                  <div className="text-muted-foreground">估算价值</div>
                  <div className="text-right font-mono font-medium text-blue-400">
                    ${fmt(info.estimated_value_usd, 2)}
                  </div>

                  <div className="text-muted-foreground">仓位PnL</div>
                  <div
                    className={cn(
                      'text-right font-mono font-medium',
                      num(info.pnl_from_positions) >= 0 ? 'text-green-400' : 'text-red-400'
                    )}
                  >
                    {num(info.pnl_from_positions) >= 0 ? '+' : ''}
                    {fmt(info.pnl_from_positions, 2)}
                  </div>

                  <div className="text-muted-foreground">仓位数</div>
                  <div className="text-right font-mono font-medium">
                    {info.position_count}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* ── C. Points Strategy Execution Details ── */}
      <Section title="积分策略执行明细">
        <div className="flex items-center justify-end mb-2">
          <button
            onClick={handleRefresh}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-secondary hover:bg-secondary/80 rounded-lg text-xs transition-colors"
          >
            <RefreshCw className="w-3 h-3" />
            刷新
          </button>
        </div>
        {loadingTx ? (
          <div className="py-8 text-center text-muted-foreground text-sm animate-pulse">
            加载中...
          </div>
        ) : transactions.length === 0 ? (
          <EmptyState message="暂无积分策略执行记录" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b border-border text-xs">
                  <th className="text-left py-2 px-3">仓位ID</th>
                  <th className="text-left py-2 px-3">策略</th>
                  <th className="text-right py-2 px-3">积分</th>
                  <th className="text-right py-2 px-3">PnL</th>
                  <th className="text-right py-2 px-3">返利</th>
                  <th className="text-right py-2 px-3">持仓时长(h)</th>
                  <th className="text-left py-2 px-3">平仓原因</th>
                </tr>
              </thead>
              <tbody>
                {transactions.map((tx, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 px-3 font-mono text-xs">
                      {(tx.position_id ?? '').slice(0, 12)}...
                    </td>
                    <td className="py-2 px-3">
                      <span className="text-xs font-bold bg-blue-500/10 text-blue-400 px-2 py-0.5 rounded">
                        {tx.strategy_type}
                      </span>
                      <span className="ml-1.5 text-xs text-muted-foreground">
                        {STRATEGY_META[tx.strategy_type]?.name ?? ''}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-right font-mono">
                      {num(tx.points).toLocaleString()}
                    </td>
                    <td
                      className={cn(
                        'py-2 px-3 text-right font-mono',
                        num(tx.pnl) >= 0 ? 'text-green-400' : 'text-red-400'
                      )}
                    >
                      {num(tx.pnl) >= 0 ? '+' : ''}{fmt(tx.pnl, 2)}
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-green-400">
                      ${fmt(tx.rebate, 2)}
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-xs">
                      {fmt(tx.hold_hours, 1)}
                    </td>
                    <td className="py-2 px-3 text-xs text-muted-foreground">
                      {tx.close_reason ?? '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* ── D. Active Points Positions ── */}
      <Section title={`活跃积分仓位 (${activePointsPositions.length})`}>
        {activePointsPositions.length === 0 ? (
          <EmptyState message="暂无活跃积分仓位" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b border-border text-xs">
                  <th className="text-left py-2 px-3">仓位ID</th>
                  <th className="text-left py-2 px-3">策略</th>
                  <th className="text-left py-2 px-3">交易所</th>
                  <th className="text-right py-2 px-3">累计积分</th>
                  <th className="text-right py-2 px-3">PnL</th>
                  <th className="text-right py-2 px-3">持仓时长</th>
                </tr>
              </thead>
              <tbody>
                {activePointsPositions.map((p, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 px-3 font-mono text-xs">
                      {(p.position_id ?? '').slice(0, 12)}...
                    </td>
                    <td className="py-2 px-3">
                      <span className="text-xs font-bold bg-blue-500/10 text-blue-400 px-2 py-0.5 rounded">
                        {p.strategy_type}
                      </span>
                      <span className="ml-1.5 text-xs text-muted-foreground">
                        {STRATEGY_META[p.strategy_type]?.name ?? ''}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-xs">
                      {p.source_exchange}
                      {p.target_exchange ? ` -> ${p.target_exchange}` : ''}
                    </td>
                    <td className="py-2 px-3 text-right font-mono">
                      {num(p.accumulated_points).toLocaleString()}
                    </td>
                    <td
                      className={cn(
                        'py-2 px-3 text-right font-mono',
                        num(p.current_pnl) >= 0 ? 'text-green-400' : 'text-red-400'
                      )}
                    >
                      {num(p.current_pnl) >= 0 ? '+' : ''}{fmt(p.current_pnl, 2)}
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-xs">
                      {fmt(p.hold_duration_hours, 1)}h
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  )
}

// ──────────────────── Local Helper Components ────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-border bg-card overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-center gap-2">
        <Activity className="w-4 h-4 text-blue-500" />
        <h2 className="font-semibold text-sm">{title}</h2>
      </div>
      <div className="p-4">{children}</div>
    </div>
  )
}

function EmptyState({ message }: { message: string }) {
  return <div className="py-8 text-center text-muted-foreground text-sm">{message}</div>
}

function SummaryCard({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode
  label: string
  value: string
  sub: string
}) {
  return (
    <div className="rounded-xl border border-border bg-muted/30 p-4">
      <div className="flex items-center gap-2 mb-2">
        {icon}
        <span className="text-xs text-muted-foreground">{label}</span>
      </div>
      <div className="text-xl font-bold font-mono">{value}</div>
      {sub && <div className="text-xs text-muted-foreground mt-1">{sub}</div>}
    </div>
  )
}
