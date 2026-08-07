/**
 * OverviewTab — 总览仪表盘
 *
 * 6格状态卡片 + 交易所激励数据网格 + 收益概览
 */
import React from 'react'
import { cn } from '@/lib/utils'
import {
  ArrowRightLeft, Coins, Activity, Shield, AlertTriangle,
  TrendingUp, Link2, Bell,
} from 'lucide-react'
import type {
  ArbitrageStatus, ArbitragePosition, ArbitrageOpportunity,
  RebateStatus, RebateOpportunity, RebatePosition,
  RebateAnalytics, CapitalAllocation, WashTradeStatus,
  ExchangeIncentiveSummary, ArbitragePaperSessionStatus,
} from '@/lib/arbitrageApi'
import { type StrategyConfigDetail, type RebateEvent, type PointsSummary, fmt, num, formatRebateEventMessage } from '@/lib/arbitrageApi'
import ExchangeIncentiveGrid from './ExchangeIncentiveGrid'

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
  notifications: Array<{id: string; type: string; message: string; ts: number}>
  pointsSummary: PointsSummary | null
  paperSession?: ArbitragePaperSessionStatus | null
  onRefresh?: () => void
  onNavigate?: (tab: string) => void
}

export default function OverviewTab({
  arbStatus, arbPositions, arbOpps,
  rebStatus, rebOpps, rebPositions,
  rebCapital, washStatus, rebAnalytics, incentives,
  events, notifications,
  paperSession, onNavigate,
}: Props) {
  const totalPnl = rebAnalytics.net_pnl ?? 0

  // S8 Rh 进度（活跃 S8 仓位累计积分）
  const s8Positions = rebPositions.filter(p => p.strategy_type === 'S8')
  const s8Points = s8Positions.reduce((acc, p) => acc + num(p.accumulated_points), 0)

  return (
    <div className="space-y-6">
      {/* Paper 摘要卡片（M6：权益 / 运行策略 / 最近 tick / S8 Rh 进度一行直达） */}
      <div className={cn(
        'rounded-xl border p-4',
        paperSession?.running ? 'border-green-500/30 bg-green-500/5' : 'border-border bg-muted/20',
      )}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
            <span className="font-semibold flex items-center gap-2">
              <span className={cn(
                'inline-block w-2 h-2 rounded-full',
                paperSession?.running ? 'bg-green-500 animate-pulse' : 'bg-gray-400',
              )} />
              Paper 验证{paperSession?.running ? '运行中' : '未运行'}
            </span>
            {paperSession?.running ? (
              <>
                <span className="text-muted-foreground">
                  权益 <span className="text-foreground font-mono">${fmt(num(paperSession.last_tick?.account_equity), 0)}</span>
                </span>
                <span className="text-muted-foreground">
                  策略 <span className="text-foreground">{paperSession.strategies?.join(' / ') || '-'}</span>
                </span>
                <span className="text-muted-foreground">
                  tick <span className="text-foreground font-mono">#{paperSession.tick_count ?? 0}</span>
                  {paperSession.last_tick ? `（${paperSession.last_tick.viable_count ?? 0} 个可行）` : ''}
                </span>
                <span className="text-muted-foreground">
                  S8 Rh 进度 <span className="text-foreground font-mono">{fmt(s8Points, 1)}</span> 分
                  {s8Positions.length > 0 ? `（${s8Positions.length} 仓）` : ''}
                </span>
              </>
            ) : (
              <span className="text-muted-foreground text-xs">
                到「启动配置」选择账户与策略后启动 Paper 验证（S8 Stage6 为默认主力策略）。
              </span>
            )}
          </div>
          {onNavigate && (
            <div className="flex gap-2">
              <button
                onClick={() => onNavigate('start_config')}
                className="text-xs px-3 py-1.5 rounded-lg bg-green-600 hover:bg-green-700 text-white"
              >
                {paperSession?.running ? '查看启动配置' : '去启动'}
              </button>
              {paperSession?.running && (
                <button
                  onClick={() => onNavigate('points_arb')}
                  className="text-xs px-3 py-1.5 rounded-lg bg-secondary hover:bg-secondary/80"
                >
                  查看积分明细
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Status Cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatusCard
          icon={<ArrowRightLeft className="w-4 h-4" />}
          label="套利引擎"
          value={arbStatus.engine_enabled ? '运行中' : '已停止'}
          ok={arbStatus.engine_enabled}
        />
        <StatusCard
          icon={<Coins className="w-4 h-4" />}
          label="积分引擎"
          value={rebStatus.engine_enabled ? '运行中' : '已停止'}
          ok={rebStatus.engine_enabled}
        />
        <StatusCard
          icon={<Activity className="w-4 h-4" />}
          label="套利仓位"
          value={`${arbPositions.length}`}
        />
        <StatusCard
          icon={<Coins className="w-4 h-4" />}
          label="积分仓位"
          value={`${rebPositions.length}`}
        />
        <StatusCard
          icon={<TrendingUp className="w-4 h-4" />}
          label="套利机会"
          value={`${arbOpps.length + rebOpps.filter(o => o.is_viable).length}`}
        />
        <StatusCard
          icon={<Shield className="w-4 h-4" />}
          label="风控状态"
          value={arbStatus.circuit_breaker_active ? '熔断中' : washStatus.risk_level === 'low' ? '安全' : '告警'}
          ok={!arbStatus.circuit_breaker_active && washStatus.risk_level === 'low'}
        />
      </div>

      {/* Exchange Incentive Grid */}
      <Section title="交易所激励数据">
        {incentives.length === 0 ? (
          <EmptyState message="暂无交易所激励数据" />
        ) : (
          <ExchangeIncentiveGrid incentives={incentives} />
        )}
      </Section>

      {/* PnL Overview */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="rounded-xl border border-border bg-muted/30 p-4">
          <div className="text-lg font-bold font-mono">{num(totalPnl) >= 0 ? '+' : ''}{fmt(totalPnl, 2)}</div>
          <div className="text-xs text-muted-foreground">累计净收益 (积分返利)</div>
        </div>
        <div className="rounded-xl border border-border bg-muted/30 p-4">
          <div className="text-lg font-bold font-mono">{rebAnalytics.total_trades}</div>
          <div className="text-xs text-muted-foreground">总交易次数</div>
        </div>
        <div className="rounded-xl border border-border bg-muted/30 p-4">
          <div className="text-lg font-bold font-mono">{fmt(num(rebAnalytics.win_rate) * 100, 1)}%</div>
          <div className="text-xs text-muted-foreground">胜率</div>
        </div>
      </div>

      {/* Event Notifications */}
      <div className="rounded-2xl border border-border bg-card overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-center gap-2">
          <Bell className="w-4 h-4 text-yellow-500" />
          <h2 className="font-semibold text-sm">最新事件</h2>
        </div>
        <div className="p-4">
          {events.length === 0 ? (
            <div className="py-6 text-center text-muted-foreground text-sm">暂无事件</div>
          ) : (
            <div className="space-y-2">
              {events.slice(0, 5).map((ev, i) => {
                const timeStr = new Date(ev.ts * 1000).toLocaleTimeString('zh-CN', {
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                  hour12: false,
                })
                const typeColor: Record<string, string> = {
                  trade: 'bg-green-500/10 text-green-400',
                  alert: 'bg-red-500/10 text-red-400',
                  rebate: 'bg-yellow-500/10 text-yellow-400',
                  position: 'bg-blue-500/10 text-blue-400',
                  system: 'bg-purple-500/10 text-purple-400',
                  execution_failed: 'bg-red-500/10 text-red-400',
                  config_changed: 'bg-purple-500/10 text-purple-400',
                }
                const badgeCls = typeColor[ev.type] ?? 'bg-muted text-muted-foreground'
                const typeLabel: Record<string, string> = {
                  config_changed: '策略配置',
                  execution_failed: '执行失败',
                }
                const summary = formatRebateEventMessage(ev)

                return (
                  <div key={i} className="flex items-start gap-3 py-2 border-b border-border/30 last:border-0">
                    <span className="text-xs font-mono text-muted-foreground whitespace-nowrap pt-0.5">{timeStr}</span>
                    <span className={`text-xs px-2 py-0.5 rounded whitespace-nowrap ${badgeCls}`}>
                      {typeLabel[ev.type] ?? ev.type}
                    </span>
                    <span className="text-xs text-foreground/80 truncate">{summary || '--'}</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Active Opportunities Quick View */}
      <Section title={`套利机会速览 (${arbOpps.length + rebOpps.length})`}>
        {arbOpps.length === 0 && rebOpps.length === 0 ? (
          <EmptyState message="暂无套利机会" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b border-border text-xs">
                  <th className="text-left py-2 px-3">来源</th>
                  <th className="text-left py-2 px-3">币对/策略</th>
                  <th className="text-right py-2 px-3">预期收益</th>
                  <th className="text-right py-2 px-3">风险评分</th>
                  <th className="text-right py-2 px-3">置信度</th>
                </tr>
              </thead>
              <tbody>
                {arbOpps.slice(0, 5).map((o, i) => (
                  <tr key={`arb-${i}`} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 px-3"><span className="text-xs bg-blue-500/10 text-blue-400 px-2 py-0.5 rounded">套利</span></td>
                    <td className="py-2 px-3 font-mono">{o.symbol} <span className="text-muted-foreground text-xs">{o.strategy}</span></td>
                    <td className="py-2 px-3 text-right font-mono text-green-400">{fmt(num(o.expected_annual_yield) * 100, 2)}%</td>
                    <td className="py-2 px-3 text-right font-mono">{fmt(o.risk_score, 2)}</td>
                    <td className="py-2 px-3 text-right font-mono">{fmt(o.confidence, 2)}</td>
                  </tr>
                ))}
                {rebOpps.filter(o => o.is_viable).slice(0, 5).map((o, i) => (
                  <tr key={`reb-${i}`} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 px-3"><span className="text-xs bg-yellow-500/10 text-yellow-400 px-2 py-0.5 rounded">积分</span></td>
                    <td className="py-2 px-3 font-mono">{o.strategy_type}</td>
                    <td className="py-2 px-3 text-right font-mono text-green-400">${fmt(o.expected_monthly_value, 0)}/月</td>
                    <td className="py-2 px-3 text-right font-mono">{fmt(o.risk_score, 2)}</td>
                    <td className="py-2 px-3 text-right font-mono">{fmt(o.confidence, 2)}</td>
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

// ── Sub Components ──

function StatusCard({ icon, label, value, ok }: { icon: React.ReactNode; label: string; value: string; ok?: boolean }) {
  return (
    <div className={cn(
      'rounded-xl border p-4',
      ok === true ? 'border-green-500/30 bg-green-500/5' :
      ok === false ? 'border-red-500/30 bg-red-500/5' :
      'border-border bg-muted/30',
    )}>
      <div className="flex items-center gap-2 mb-1">
        {icon}
        <span className="text-xs text-muted-foreground">{label}</span>
      </div>
      <div className="text-lg font-bold">{value}</div>
    </div>
  )
}

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
