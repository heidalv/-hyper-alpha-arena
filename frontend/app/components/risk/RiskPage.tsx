/**
 * 风控监控页面 — Phase 4
 * 对应设计方案§Phase4 "风控监控 Risk"
 * 展示：风险指标、爆仓预警、杠杆状态、熔断记录
 */

import React, { useEffect, useState, useCallback, useRef } from 'react'
import { AlertTriangle, ShieldAlert, Activity, TrendingDown, RefreshCw, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { usePageActive } from '@/hooks/usePageActive'

interface LiquidationRisk {
  account_id: number
  symbol: string
  side: string
  mark_price: number
  liquidation_price: number
  distance_to_liq_pct: number
  risk_level: 'warning' | 'danger' | 'critical' | 'safe'
  position_value: number
  leverage: number
  triggered_at?: string
  action?: string
}

interface RiskSummary {
  account_id: number
  total_equity: number
  daily_loss_ratio: number
  margin_usage_percent: number
  is_circuit_breaker_active: boolean
  circuit_breaker_end_time?: string
  daily_trades: number
  consecutive_losses: number
  max_single_symbol_ratio: number
}

interface CircuitBreakerEvent {
  account_id: number
  event_time: string
  details: string
}

const RISK_LEVEL_CONFIG = {
  critical: { label: '极危', color: 'text-red-400', bg: 'bg-red-500/10 border-red-500/30', icon: '🔴' },
  danger:   { label: '危险', color: 'text-orange-400', bg: 'bg-orange-500/10 border-orange-500/30', icon: '🟠' },
  warning:  { label: '预警', color: 'text-yellow-400', bg: 'bg-yellow-500/10 border-yellow-500/30', icon: '🟡' },
  safe:     { label: '安全', color: 'text-green-400', bg: 'bg-green-500/10 border-green-500/30', icon: '🟢' },
}

export default function RiskPage() {
  const pageActive = usePageActive()
  const [risks, setRisks] = useState<LiquidationRisk[]>([])
  const [alerts, setAlerts] = useState<LiquidationRisk[]>([])
  const [summary, setSummary] = useState<RiskSummary[]>([])
  const [circuitEvents, setCircuitEvents] = useState<CircuitBreakerEvent[]>([])
  const [loading, setLoading] = useState(false)
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date())
  const [todayAlertCount, setTodayAlertCount] = useState(0)

  const fetchRiskData = useCallback(async () => {
    setLoading(true)
    try {
      const [riskRes, alertRes] = await Promise.allSettled([
        fetch('/api/risk/liquidation-risks'),
        fetch('/api/risk/alert-history?limit=20'),
      ])

      if (riskRes.status === 'fulfilled' && riskRes.value.ok) {
        const data = await riskRes.value.json()
        setRisks(data.risks || [])
        setSummary(data.summaries || [])
      }
      if (alertRes.status === 'fulfilled' && alertRes.value.ok) {
        const data = await alertRes.value.json()
        setAlerts(data.alerts || [])
        setTodayAlertCount(data.today_count ?? 0)
      }
      setLastRefresh(new Date())
    } catch (e) {
      console.error('[RiskPage] fetch error:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  const riskInitDone = useRef(false)
  useEffect(() => {
    if (!riskInitDone.current) {
      riskInitDone.current = true
      fetchRiskData()
    }
    if (!pageActive) return
    const interval = setInterval(fetchRiskData, 30_000)
    return () => clearInterval(interval)
  }, [fetchRiskData, pageActive])

  const criticalCount = risks.filter(r => r.risk_level === 'critical').length
  const dangerCount = risks.filter(r => r.risk_level === 'danger').length
  const warningCount = risks.filter(r => r.risk_level === 'warning').length

  return (
    <div className="min-h-screen bg-background text-foreground p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <ShieldAlert className="w-7 h-7 text-red-500" />
            风控监控
          </h1>
          <p className="text-muted-foreground text-sm mt-1">
            实时爆仓预警 · 多级风控 · 熔断保护
            <span className="ml-3 text-muted-foreground/60">上次刷新: {lastRefresh.toLocaleTimeString()}</span>
          </p>
        </div>
        <button
          onClick={fetchRiskData}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-secondary hover:bg-secondary/80 text-secondary-foreground rounded-lg text-sm transition-colors"
        >
          <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
          刷新
        </button>
      </div>

      <div className="mb-6 rounded-xl border border-purple-500/25 bg-purple-500/5 px-4 py-3 flex flex-wrap items-center justify-between gap-3 text-sm">
        <span className="text-muted-foreground">
          亏多了被锁仓？可在系统设置里分别调节<strong className="text-foreground">模拟盘</strong>和<strong className="text-foreground">实盘</strong>的锁仓强度。
        </span>
        <a
          href="#settings"
          onClick={(e) => {
            e.preventDefault()
            sessionStorage.setItem('settings_tab', 'lock')
            window.location.hash = 'settings'
          }}
          className="text-purple-600 dark:text-purple-400 font-medium hover:underline whitespace-nowrap"
        >
          打开锁仓强度设置 →
        </a>
      </div>

      {/* Risk Overview Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatCard
          label="极危仓位"
          value={criticalCount}
          icon={<AlertTriangle className="w-5 h-5 text-red-400" />}
          color="border-red-500/30 bg-red-500/5"
          highlight={criticalCount > 0}
        />
        <StatCard
          label="危险仓位"
          value={dangerCount}
          icon={<AlertTriangle className="w-5 h-5 text-orange-400" />}
          color="border-orange-500/30 bg-orange-500/5"
        />
        <StatCard
          label="预警仓位"
          value={warningCount}
          icon={<Activity className="w-5 h-5 text-yellow-400" />}
          color="border-yellow-500/30 bg-yellow-500/5"
        />
        <StatCard
          label="今日预警次数"
          value={todayAlertCount}
          icon={<TrendingDown className="w-5 h-5 text-muted-foreground" />}
          color="border-border bg-muted/30"
        />
      </div>

      {/* Account Risk Summary */}
      {summary.length > 0 && (
        <Section title="账户风控状态" className="mb-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {summary.map(s => (
              <AccountRiskCard key={s.account_id} summary={s} />
            ))}
          </div>
        </Section>
      )}

      {/* Active Position Risks */}
      <Section title="持仓风险（非安全级别）" className="mb-6">
        {risks.length === 0 ? (
          <EmptyState icon="✅" message="暂无高风险仓位，系统安全" />
        ) : (
          <div className="space-y-3">
            {risks.map((r, i) => (
              <PositionRiskRow key={i} risk={r} />
            ))}
          </div>
        )}
      </Section>

      {/* Alert History */}
      <Section title="最近预警记录（最新20条）">
        {alerts.length === 0 ? (
          <EmptyState icon="📋" message="暂无预警记录" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="text-left py-2 px-3">时间</th>
                  <th className="text-left py-2 px-3">账户</th>
                  <th className="text-left py-2 px-3">交易对</th>
                  <th className="text-left py-2 px-3">级别</th>
                  <th className="text-right py-2 px-3">距爆仓</th>
                  <th className="text-right py-2 px-3">动作</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((a, i) => {
                  const cfg = RISK_LEVEL_CONFIG[a.risk_level] || RISK_LEVEL_CONFIG.warning
                  return (
                    <tr key={i} className="border-b border-border/50 hover:bg-muted/20">
                      <td className="py-2 px-3 text-muted-foreground text-xs">
                        {a.triggered_at ? new Date(a.triggered_at).toLocaleTimeString() : '-'}
                      </td>
                      <td className="py-2 px-3 text-foreground/80">#{a.account_id}</td>
                      <td className="py-2 px-3 font-mono font-semibold">{a.symbol}</td>
                      <td className="py-2 px-3">
                        <span className={cn('text-xs font-medium', cfg.color)}>
                          {cfg.icon} {cfg.label}
                        </span>
                      </td>
                      <td className="py-2 px-3 text-right font-mono">
                        <span className={cfg.color}>{a.distance_to_liq_pct?.toFixed(1)}%</span>
                      </td>
                      <td className="py-2 px-3 text-right text-muted-foreground text-xs">
                        {a.action === 'emergency_close' ? (
                          <span className="text-red-400 font-medium">紧急平仓</span>
                        ) : a.action === 'alert' ? (
                          <span className="text-yellow-400">已预警</span>
                        ) : '-'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  )
}

// ── 子组件 ──

function StatCard({ label, value, icon, color, highlight }: {
  label: string; value: number; icon: React.ReactNode
  color: string; highlight?: boolean
}) {
  return (
    <div className={cn(
      'rounded-xl border p-4 flex items-center gap-3',
      color,
      highlight && 'animate-pulse'
    )}>
      {icon}
      <div>
        <div className="text-2xl font-bold">{value}</div>
        <div className="text-xs text-stat-label">{label}</div>
      </div>
    </div>
  )
}

function AccountRiskCard({ summary }: { summary: RiskSummary }) {
  const breakerActive = summary.is_circuit_breaker_active
  return (
    <div className={cn(
      'rounded-xl border p-4 space-y-3',
      breakerActive ? 'border-red-500/50 bg-red-500/5' : 'border-border bg-muted/30'
    )}>
      <div className="flex items-center justify-between">
        <span className="font-medium">账户 #{summary.account_id}</span>
        {breakerActive && (
          <span className="text-xs bg-red-500/20 text-red-400 px-2 py-0.5 rounded-full font-medium">
            熔断中
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <MetricRow label="日亏损" value={`${(summary.daily_loss_ratio * 100).toFixed(2)}%`}
          alert={summary.daily_loss_ratio > 0.03} />
        <MetricRow label="保证金使用率" value={`${summary.margin_usage_percent?.toFixed(1)}%`}
          alert={summary.margin_usage_percent > 60} />
        <MetricRow label="今日交易次数" value={`${summary.daily_trades} 笔`}
          alert={summary.daily_trades >= 30} />
        <MetricRow label="连续亏损" value={`${summary.consecutive_losses} 次`}
          alert={summary.consecutive_losses >= 3} />
      </div>
      {breakerActive && summary.circuit_breaker_end_time && (
        <div className="text-xs text-red-400 mt-1">
          熔断结束时间: {new Date(summary.circuit_breaker_end_time).toLocaleString()}
        </div>
      )}
    </div>
  )
}

function MetricRow({ label, value, alert }: { label: string; value: string; alert?: boolean }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn('font-medium', alert ? 'text-red-500' : 'text-foreground')}>{value}</span>
    </div>
  )
}

function PositionRiskRow({ risk }: { risk: LiquidationRisk }) {
  const cfg = RISK_LEVEL_CONFIG[risk.risk_level] || RISK_LEVEL_CONFIG.warning
  return (
    <div className={cn('rounded-xl border p-4 flex items-center justify-between', cfg.bg)}>
      <div className="flex items-center gap-3">
        <span className="text-lg">{cfg.icon}</span>
        <div>
          <div className="font-mono font-bold">{risk.symbol}</div>
          <div className="text-xs text-muted-foreground">
            {risk.side === 'long' ? '做多' : '做空'} · {risk.leverage}x · ${risk.position_value?.toLocaleString()}
          </div>
        </div>
      </div>
      <div className="text-right">
        <div className={cn('text-xl font-bold font-mono', cfg.color)}>
          {risk.distance_to_liq_pct?.toFixed(1)}%
        </div>
        <div className="text-xs text-muted-foreground">距爆仓</div>
        <div className="text-xs text-muted-foreground/60 font-mono mt-0.5">
          爆仓价 {risk.liquidation_price?.toFixed(2)}
        </div>
      </div>
    </div>
  )
}

function Section({ title, children, className }: {
  title: string; children: React.ReactNode; className?: string
}) {
  return (
    <div className={cn('rounded-2xl border border-border bg-card overflow-hidden', className)}>
      <div className="px-4 py-3 border-b border-border flex items-center gap-2">
        <ChevronRight className="w-4 h-4 text-purple-500" />
        <h2 className="font-semibold text-sm">{title}</h2>
      </div>
      <div className="p-4">{children}</div>
    </div>
  )
}

function EmptyState({ icon, message }: { icon: string; message: string }) {
  return (
    <div className="py-8 text-center text-muted-foreground">
      <div className="text-3xl mb-2">{icon}</div>
      <div className="text-sm">{message}</div>
    </div>
  )
}
