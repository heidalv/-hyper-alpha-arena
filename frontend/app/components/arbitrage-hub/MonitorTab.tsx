/**
 * MonitorTab — 统一监控
 *
 * 两套系统合并的仓位监控 + 风险面板 + 告警
 */
import React, { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { Activity, Shield, AlertTriangle, AlertCircle } from 'lucide-react'
import type {
  ArbitrageStatus, ArbitragePosition, ArbitrageOpportunity,
  RebateStatus, RebateOpportunity, RebatePosition,
  RebateAnalytics, CapitalAllocation, WashTradeStatus,
  ExchangeIncentiveSummary, WashTradeTimelineItem,
  StrategyConfigDetail, RebateEvent, PointsSummary,
} from '@/lib/arbitrageApi'
import { fmt, getWashTradeTimeline, num } from '@/lib/arbitrageApi'

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
  focus?: 'wash_trade'
  onRefresh?: () => void
}

export default function MonitorTab({
  arbStatus, arbPositions,
  rebStatus, rebPositions, washStatus,
  rebCapital, focus, onRefresh,
}: Props) {
  const [timeline, setTimeline] = useState<WashTradeTimelineItem[]>([])

  useEffect(() => {
    if (focus !== 'wash_trade') return
    let cancelled = false
    getWashTradeTimeline(80).then(res => {
      if (!cancelled) setTimeline(res.timeline || [])
    }).catch(() => {
      if (!cancelled) setTimeline([])
    })
    return () => { cancelled = true }
  }, [focus])

  // Build alerts from both systems
  const alerts: { level: 'warning' | 'critical' | 'info'; message: string }[] = []

  if (arbStatus.circuit_breaker_active) {
    alerts.push({ level: 'critical', message: '套利引擎熔断器已触发，所有新开仓已暂停' })
  }
  if (washStatus.risk_level === 'high') {
    alerts.push({ level: 'critical', message: '刷量风险等级为 HIGH，交易已暂停' })
  } else if (washStatus.risk_level === 'medium') {
    alerts.push({ level: 'warning', message: '刷量风险等级为 MEDIUM，请注意交易频率' })
  }

  arbPositions.forEach(p => {
    if (Math.abs(p.delta) > 0.02) {
      alerts.push({ level: 'warning', message: `${p.symbol} Delta偏差 ${fmt(num(p.delta) * 100, 2)}%，需要再平衡` })
    }
  })

  rebPositions.forEach(p => {
    if (p.hold_duration_hours > 720) { // 30 days
      alerts.push({ level: 'warning', message: `${p.strategy_type} 仓位 ${(p.position_id ?? '').slice(0, 8)} 持仓超30天` })
    }
  })

  if (alerts.length === 0) {
    alerts.push({ level: 'info', message: '所有系统运行正常，无告警' })
  }

  return (
    <div className="space-y-6">
      {focus === 'wash_trade' && (
        <div>
          <h2 className="text-lg font-semibold">刷交易监控</h2>
          <p className="text-sm text-muted-foreground">
            展示刷量安全窗口、日交易量、活跃仓位和风险告警，后续会接入 wash_trade_logs 时间线。
          </p>
        </div>
      )}
      {focus === 'wash_trade' && (
        <Section title={`刷交易时间线 (${timeline.length})`}>
          <div className="space-y-2">
            {timeline.length === 0 ? (
              <div className="text-sm text-muted-foreground">暂无刷交易记录</div>
            ) : timeline.map(item => (
              <div key={item.id} className="rounded-lg border border-border bg-muted/20 p-3 flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium">
                    {item.exchange || 'unknown'} · {item.strategy_type || 'N/A'} · ${fmt(item.size_usd)}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {new Date(item.ts * 1000).toLocaleString()} · 风险分 {fmt(item.risk_score, 2)} {item.reason ? `· ${item.reason}` : ''}
                  </div>
                </div>
                <span className={cn(
                  'text-xs px-2 py-1 rounded-full',
                  item.is_safe ? 'bg-green-500/10 text-green-600' : 'bg-red-500/10 text-red-600'
                )}>
                  {item.is_safe ? '安全' : '阻止'}
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}
      {/* Alerts */}
      <Section title={`告警 (${alerts.filter(a => a.level !== 'info').length})`}>
        <div className="space-y-2">
          {alerts.map((a, i) => (
            <div key={i} className={cn(
              'rounded-lg border p-3 flex items-center gap-2 text-sm',
              a.level === 'critical' ? 'border-red-500/30 bg-red-500/5' :
              a.level === 'warning' ? 'border-yellow-500/30 bg-yellow-500/5' :
              'border-border bg-muted/10',
            )}>
              {a.level === 'critical' ? <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0" /> :
               a.level === 'warning' ? <AlertCircle className="w-4 h-4 text-yellow-400 flex-shrink-0" /> :
               <Shield className="w-4 h-4 text-green-400 flex-shrink-0" />}
              <span>{a.message}</span>
            </div>
          ))}
        </div>
      </Section>

      {/* Arbitrage Positions Monitor */}
      <Section title={`套利仓位监控 (${arbPositions.length})`}>
        {arbPositions.length === 0 ? (
          <EmptyState message="暂无活跃套利仓位" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b border-border text-xs">
                  <th className="text-left py-2 px-3">币对</th>
                  <th className="text-left py-2 px-3">策略</th>
                  <th className="text-right py-2 px-3">Delta</th>
                  <th className="text-right py-2 px-3">累计Funding</th>
                  <th className="text-left py-2 px-3">状态</th>
                  <th className="text-left py-2 px-3">入场时间</th>
                </tr>
              </thead>
              <tbody>
                {arbPositions.map((p, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 px-3 font-mono font-semibold">{p.symbol}</td>
                    <td className="py-2 px-3 text-xs">{p.strategy}</td>
                    <td className={cn('py-2 px-3 text-right font-mono',
                      Math.abs(p.delta) < 0.01 ? 'text-green-400' :
                      Math.abs(p.delta) < 0.02 ? 'text-yellow-400' : 'text-red-400'
                    )}>
                      {fmt(p.delta, 4)}
                    </td>
                    <td className={cn('py-2 px-3 text-right font-mono',
                      num(p.accumulated_funding) >= 0 ? 'text-green-400' : 'text-red-400'
                    )}>
                      {num(p.accumulated_funding) >= 0 ? '+' : ''}{fmt(p.accumulated_funding, 2)}
                    </td>
                    <td className="py-2 px-3">
                      <span className={cn('text-xs font-medium px-2 py-0.5 rounded-full',
                        p.status === 'active' ? 'bg-blue-500/20 text-blue-400' : 'bg-muted text-muted-foreground'
                      )}>{p.status}</span>
                    </td>
                    <td className="py-2 px-3 text-xs text-muted-foreground">{p.entry_time ?? '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* Rebate Positions Monitor */}
      <Section title={`积分仓位监控 (${rebPositions.length})`}>
        {rebPositions.length === 0 ? (
          <EmptyState message="暂无活跃积分仓位" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b border-border text-xs">
                  <th className="text-left py-2 px-3">策略</th>
                  <th className="text-left py-2 px-3">交易所</th>
                  <th className="text-right py-2 px-3">返利</th>
                  <th className="text-right py-2 px-3">积分</th>
                  <th className="text-right py-2 px-3">PnL</th>
                  <th className="text-right py-2 px-3">持仓时长</th>
                  <th className="text-left py-2 px-3">模式</th>
                </tr>
              </thead>
              <tbody>
                {rebPositions.map((p, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 px-3">
                      <span className="text-xs font-bold bg-blue-500/10 text-blue-400 px-2 py-0.5 rounded">{p.strategy_type}</span>
                    </td>
                    <td className="py-2 px-3 text-xs">{p.source_exchange}{p.target_exchange ? ` → ${p.target_exchange}` : ''}</td>
                    <td className="py-2 px-3 text-right font-mono text-green-400">${fmt(p.accumulated_rebate, 2)}</td>
                    <td className="py-2 px-3 text-right font-mono">{fmt(p.accumulated_points, 1)}</td>
                    <td className={cn('py-2 px-3 text-right font-mono', num(p.current_pnl) >= 0 ? 'text-green-400' : 'text-red-400')}>
                      {num(p.current_pnl) >= 0 ? '+' : ''}{fmt(p.current_pnl, 2)}
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-xs">{fmt(p.hold_duration_hours, 1)}h</td>
                    <td className="py-2 px-3">
                      <span className={cn('text-xs px-2 py-0.5 rounded-full',
                        p.paper_mode ? 'bg-gray-500/20 text-gray-400' : 'bg-green-500/20 text-green-400'
                      )}>{p.paper_mode ? 'Paper' : 'Live'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* Risk Overview */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <RiskCard label="套利引擎" ok={arbStatus.engine_enabled} detail={arbStatus.engine_enabled ? '运行中' : '已停止'} />
        <RiskCard label="积分引擎" ok={rebStatus.engine_enabled} detail={rebStatus.engine_enabled ? `模式: ${rebStatus.mode}` : '已停止'} />
        <RiskCard label="熔断器" ok={!arbStatus.circuit_breaker_active} detail={arbStatus.circuit_breaker_active ? '已触发' : '正常'} />
        <RiskCard label="刷量风控" ok={washStatus.risk_level === 'low'} detail={`等级: ${washStatus.risk_level}`} />
      </div>

      {/* Risk Exposure — 风险敞口 */}
      <Section title="风险敞口">
        {(() => {
          const totalEquity = rebCapital?.total_equity ?? 0
          const totalUsed = rebCapital?.total_used ?? 0
          const totalExposurePct = totalEquity > 0 ? (totalUsed / totalEquity) * 100 : 0

          // Group by exchange
          const byExchange: Record<string, number> = {}
          rebPositions.forEach(p => {
            const ex = p.source_exchange ?? 'unknown'
            byExchange[ex] = (byExchange[ex] || 0) + num(p.position_value)
          })

          // Group by strategy
          const byStrategy: Record<string, number> = {}
          rebPositions.forEach(p => {
            const st = p.strategy_type ?? 'unknown'
            byStrategy[st] = (byStrategy[st] || 0) + num(p.position_value)
          })

          return (
            <div className="space-y-4">
              {/* Total Exposure */}
              <div>
                <div className="flex items-center justify-between text-sm mb-1">
                  <span className="text-muted-foreground">总敞口 (占总权益)</span>
                  <span className="font-mono font-bold">{fmt(totalExposurePct, 1)}%</span>
                </div>
                <div className="w-full h-3 rounded-full bg-muted/30 overflow-hidden">
                  <div
                    className={cn(
                      'h-full rounded-full transition-all',
                      totalExposurePct > 80 ? 'bg-red-500' :
                      totalExposurePct > 50 ? 'bg-yellow-500' : 'bg-green-500',
                    )}
                    style={{ width: `${Math.min(totalExposurePct, 100)}%` }}
                  />
                </div>
                <div className="flex justify-between text-xs text-muted-foreground mt-1">
                  <span>已用: ${fmt(totalUsed, 2)}</span>
                  <span>权益: ${fmt(totalEquity, 2)}</span>
                </div>
              </div>

              {/* Per-Exchange Exposure */}
              {Object.keys(byExchange).length > 0 && (
                <div>
                  <div className="text-sm font-semibold mb-2">按交易所敞口</div>
                  <div className="space-y-2">
                    {Object.entries(byExchange).map(([exchange, value]) => {
                      const pct = totalEquity > 0 ? (value / totalEquity) * 100 : 0
                      return (
                        <div key={exchange}>
                          <div className="flex items-center justify-between text-xs mb-1">
                            <span>{exchange}</span>
                            <span className="font-mono">{fmt(pct, 1)}% (${fmt(value, 2)})</span>
                          </div>
                          <div className="w-full h-2 rounded-full bg-muted/30 overflow-hidden">
                            <div
                              className={cn(
                                'h-full rounded-full transition-all',
                                pct > 50 ? 'bg-orange-500' : 'bg-blue-500',
                              )}
                              style={{ width: `${Math.min(pct, 100)}%` }}
                            />
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* Per-Strategy Exposure */}
              {Object.keys(byStrategy).length > 0 && (
                <div>
                  <div className="text-sm font-semibold mb-2">按策略敞口</div>
                  <div className="space-y-2">
                    {Object.entries(byStrategy).map(([strategy, value]) => {
                      const pct = totalEquity > 0 ? (value / totalEquity) * 100 : 0
                      return (
                        <div key={strategy}>
                          <div className="flex items-center justify-between text-xs mb-1">
                            <span>{strategy}</span>
                            <span className="font-mono">{fmt(pct, 1)}% (${fmt(value, 2)})</span>
                          </div>
                          <div className="w-full h-2 rounded-full bg-muted/30 overflow-hidden">
                            <div
                              className={cn(
                                'h-full rounded-full transition-all',
                                pct > 50 ? 'bg-orange-500' : 'bg-purple-500',
                              )}
                              style={{ width: `${Math.min(pct, 100)}%` }}
                            />
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              {Object.keys(byExchange).length === 0 && Object.keys(byStrategy).length === 0 && (
                <div className="text-center text-muted-foreground text-sm py-4">暂无持仓数据</div>
              )}
            </div>
          )
        })()}
      </Section>
    </div>
  )
}

function RiskCard({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className={cn(
      'rounded-xl border p-4',
      ok ? 'border-green-500/30 bg-green-500/5' : 'border-red-500/30 bg-red-500/5',
    )}>
      <div className="flex items-center gap-2 mb-1">
        <Shield className={cn('w-4 h-4', ok ? 'text-green-400' : 'text-red-400')} />
        <span className="text-xs text-muted-foreground">{label}</span>
      </div>
      <div className="font-bold">{detail}</div>
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
