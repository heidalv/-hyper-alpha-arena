/**
 * 交易所枢纽 & 跨交易所套利页面 — Phase 6 前端同步
 *
 * 功能:
 *  - 交易所状态监控 (多交易所连接状态、延迟)
 *  - 统一持仓视图 (跨交易所)
 *  - 跨交易所套利 (价差扫描、活跃套利、风险敞口)
 *  - 单腿风控状态
 */
import React, { useEffect, useState, useCallback, useRef } from 'react'
import { cn } from '@/lib/utils'
import { usePageActive } from '@/hooks/usePageActive'
import {
  RefreshCw, ArrowRightLeft, Shield, AlertTriangle,
  ArrowUpRight, ArrowDownRight, Link2, Activity,
} from 'lucide-react'
import {
  type ExchangeStatus, type ExchangePosition, type CrossExchangeSpread,
  type CrossExchangeTrade, type CrossExchangeExposure, type LegRiskStatus,
  getExchangeStatuses, getAllPositions, scanCrossExchangeSpreads,
  getCrossExchangeTrades, getCrossExchangeExposure, getLegRiskStatuses,
  type RebateStatus, type RebateOpportunity, type RebatePosition,
  type RebateCapital, type RebateAnalytics,
  getRebateStatus, getRebateOpportunities, getRebatePositions,
  getRebateCapital, getRebateAnalytics, triggerRebateScan,
  closeRebatePosition, emergencyCloseAllRebate,
} from '@/lib/exchangeApi'

// ── Main Component ──

export default function ExchangeHubPage() {
  const pageActive = usePageActive()
  const [tab, setTab] = useState<'overview' | 'arb' | 'rebate'>('overview')

  // Overview
  const [statuses, setStatuses] = useState<ExchangeStatus[]>([])
  const [positions, setPositions] = useState<ExchangePosition[]>([])
  // Arbitrage
  const [spreads, setSpreads] = useState<CrossExchangeSpread[]>([])
  const [trades, setTrades] = useState<CrossExchangeTrade[]>([])
  const [exposure, setExposure] = useState<CrossExchangeExposure | null>(null)
  const [legRisks, setLegRisks] = useState<LegRiskStatus[]>([])
  // Rebate
  const [rebateStatus, setRebateStatus] = useState<RebateStatus | null>(null)
  const [rebateOpps, setRebateOpps] = useState<RebateOpportunity[]>([])
  const [rebatePositions, setRebatePositions] = useState<RebatePosition[]>([])
  const [rebateCapital, setRebateCapital] = useState<RebateCapital | null>(null)
  const [rebateAnalytics, setRebateAnalytics] = useState<RebateAnalytics | null>(null)

  const [loading, setLoading] = useState(false)
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date())

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [st, pos, sp, tr, exp, legs, rs, ro, rp, rc, ra] = await Promise.allSettled([
        getExchangeStatuses(),
        getAllPositions(),
        scanCrossExchangeSpreads(),
        getCrossExchangeTrades(),
        getCrossExchangeExposure(),
        getLegRiskStatuses(),
        getRebateStatus(),
        getRebateOpportunities(),
        getRebatePositions(),
        getRebateCapital(),
        getRebateAnalytics(),
      ])
      if (st.status === 'fulfilled') setStatuses(st.value)
      if (pos.status === 'fulfilled') setPositions(pos.value)
      if (sp.status === 'fulfilled') setSpreads(sp.value)
      if (tr.status === 'fulfilled') setTrades(tr.value)
      if (exp.status === 'fulfilled') setExposure(exp.value)
      if (legs.status === 'fulfilled') setLegRisks(legs.value)
      if (rs.status === 'fulfilled') setRebateStatus(rs.value)
      if (ro.status === 'fulfilled') setRebateOpps(ro.value)
      if (rp.status === 'fulfilled') setRebatePositions(rp.value)
      if (rc.status === 'fulfilled') setRebateCapital(rc.value)
      if (ra.status === 'fulfilled') setRebateAnalytics(ra.value)
      setLastRefresh(new Date())
    } catch (e) {
      console.error('[ExchangeHub] fetch error:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  const initDone = useRef(false)
  useEffect(() => {
    if (!initDone.current) {
      initDone.current = true
      fetchAll()
    }
    if (!pageActive) return
    const interval = setInterval(fetchAll, 30_000)
    return () => clearInterval(interval)
  }, [fetchAll, pageActive])

  return (
    <div className="min-h-screen bg-background text-foreground p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <ArrowRightLeft className="w-7 h-7 text-blue-500" />
            交易所枢纽
          </h1>
          <p className="text-muted-foreground text-sm mt-1">
            多交易所统一管理 · 跨所套利 · 风险监控
            <span className="ml-3 text-muted-foreground/60">
              上次刷新: {lastRefresh.toLocaleTimeString()}
            </span>
          </p>
        </div>
        <button
          onClick={fetchAll}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-secondary hover:bg-secondary/80 text-secondary-foreground rounded-lg text-sm transition-colors"
        >
          <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
          刷新
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 mb-6 border-b border-border pb-2">
        {[
          { key: 'overview' as const, label: '交易所总览', icon: <Link2 className="w-4 h-4" /> },
          { key: 'arb' as const, label: '跨交易所套利', icon: <ArrowRightLeft className="w-4 h-4" /> },
        ].map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              'flex items-center gap-1.5 px-4 py-2 rounded-t-lg text-sm font-medium transition-colors',
              tab === t.key
                ? 'bg-card text-foreground border border-border border-b-card -mb-[1px]'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'overview' && <OverviewTab statuses={statuses} positions={positions} />}
      {tab === 'arb' && <ArbitrageTab spreads={spreads} trades={trades} exposure={exposure} legRisks={legRisks} />}
    </div>
  )
}

// ── Overview Tab ──

function OverviewTab({ statuses, positions }: { statuses: ExchangeStatus[]; positions: ExchangePosition[] }) {
  return (
    <div className="space-y-6">
      {/* Exchange Status Cards */}
      <Section title="交易所连接状态">
        {statuses.length === 0 ? (
          <EmptyState message="暂无交易所连接" />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {statuses.map(s => (
              <div key={s.exchange} className={cn(
                'rounded-xl border p-4',
                s.connected ? 'border-green-500/30 bg-green-500/5' : 'border-red-500/30 bg-red-500/5',
              )}>
                <div className="flex items-center justify-between mb-2">
                  <span className="font-semibold">{s.exchange.toUpperCase()}</span>
                  <span className={cn(
                    'text-xs font-medium px-2 py-0.5 rounded-full',
                    s.connected ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400',
                  )}>
                    {s.connected ? '已连接' : '断开'}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                  <div>现货: <span className="text-foreground">{s.supports_spot ? '支持' : '不支持'}</span></div>
                  <div>合约: <span className="text-foreground">{s.supports_futures ? '支持' : '不支持'}</span></div>
                  {s.total_equity != null && <div>权益: <span className="text-foreground font-mono">${Number(s.total_equity).toFixed(2)}</span></div>}
                  {s.error && <div className="col-span-2 text-red-400 truncate">{s.error}</div>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Unified Positions */}
      <Section title={`统一持仓视图 (${positions.length})`}>
        {positions.length === 0 ? (
          <EmptyState message="暂无持仓" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b border-border text-xs">
                  <th className="text-left py-2 px-3">交易所</th>
                  <th className="text-left py-2 px-3">交易对</th>
                  <th className="text-left py-2 px-3">方向</th>
                  <th className="text-right py-2 px-3">数量</th>
                  <th className="text-right py-2 px-3">入场价</th>
                  <th className="text-right py-2 px-3">未实现盈亏</th>
                  <th className="text-right py-2 px-3">杠杆</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 px-3">
                      <span className={cn(
                        'text-xs font-medium px-2 py-0.5 rounded',
                        p.exchange === 'hyperliquid' ? 'bg-purple-500/10 text-purple-400' : 'bg-yellow-500/10 text-yellow-400',
                      )}>
                        {p.exchange.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-2 px-3 font-mono font-semibold">{p.symbol}</td>
                    <td className="py-2 px-3">
                      <span className={cn('flex items-center gap-1 text-xs font-medium', p.side === 'long' ? 'text-green-400' : 'text-red-400')}>
                        {p.side === 'long' ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                        {p.side === 'long' ? '多' : '空'}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-right font-mono">{p.size}</td>
                    <td className="py-2 px-3 text-right font-mono">${p.entry_price?.toFixed(2)}</td>
                    <td className={cn('py-2 px-3 text-right font-mono', p.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                      {p.unrealized_pnl >= 0 ? '+' : ''}{p.unrealized_pnl?.toFixed(2)}
                    </td>
                    <td className="py-2 px-3 text-right font-mono">{p.leverage}x</td>
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

// ── Arbitrage Tab ──

function ArbitrageTab({ spreads, trades, exposure, legRisks }: {
  spreads: CrossExchangeSpread[]
  trades: CrossExchangeTrade[]
  exposure: CrossExchangeExposure | null
  legRisks: LegRiskStatus[]
}) {
  return (
    <div className="space-y-6">
      {/* Exposure Summary */}
      {exposure && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <ExpCard label="总权益" value={`$${exposure.total_equity?.toFixed(2) ?? '0'}`} />
          <ExpCard label="持仓名义值" value={`$${exposure.total_positions_notional?.toFixed(2) ?? '0'}`} />
          <ExpCard label="敞口比率" value={`${(exposure.exposure_pct ?? 0).toFixed(1)}%`} alert={(exposure.exposure_pct ?? 0) > 200} />
          <ExpCard
            label="风控状态"
            value={exposure.is_safe ? '安全' : '告警'}
            icon={exposure.is_safe ? <Shield className="w-4 h-4 text-green-400" /> : <AlertTriangle className="w-4 h-4 text-red-400" />}
          />
        </div>
      )}

      {/* Spread Scanner */}
      <Section title={`价差扫描 (${spreads.length})`}>
        {spreads.length === 0 ? (
          <EmptyState message="暂无显著价差" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b border-border text-xs">
                  <th className="text-left py-2 px-3">交易对</th>
                  <th className="text-left py-2 px-3">方向</th>
                  <th className="text-right py-2 px-3">价差%</th>
                  <th className="text-right py-2 px-3">方向</th>
                  <th className="text-left py-2 px-3">交易所A</th>
                  <th className="text-left py-2 px-3">交易所B</th>
                </tr>
              </thead>
              <tbody>
                {spreads.map((s, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 px-3 font-mono font-semibold">{s.symbol}</td>
                    <td className="py-2 px-3">
                      <span className="text-xs text-blue-400">{s.direction === 'buy_a_sell_b' ? 'A买 B卖' : 'A卖 B买'}</span>
                    </td>
                    <td className="py-2 px-3 text-right font-mono">{(s.spread_pct).toFixed(4)}%</td>
                    <td className="py-2 px-3 text-right">
                      <span className={cn('font-mono font-bold', Math.abs(s.spread_pct) > 0.1 ? 'text-green-400' : Math.abs(s.spread_pct) > 0.05 ? 'text-yellow-400' : 'text-muted-foreground')}>
                        {s.direction}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-xs">{s.exchange_a} @ ${s.price_a?.toFixed(2)}</td>
                    <td className="py-2 px-3 text-xs">{s.exchange_b} @ ${s.price_b?.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* Active Trades */}
      <Section title={`套利交易 (${trades.length})`}>
        {trades.length === 0 ? (
          <EmptyState message="暂无活跃套利交易" />
        ) : (
          <div className="space-y-2">
            {trades.map(t => (
              <div key={t.id} className={cn(
                'rounded-lg border p-4 flex items-center justify-between',
                t.status === 'open' ? 'border-blue-500/30 bg-blue-500/5' : 'border-border bg-muted/30',
              )}>
                <div>
                  <div className="font-mono font-bold">{t.symbol}</div>
                  <div className="text-xs text-muted-foreground">
                    {t.strategy || '跨所套利'}
                  </div>
                </div>
                <div className="text-right">
                  <div className={cn('font-mono font-bold', t.pnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                    {t.pnl >= 0 ? '+' : ''}{t.pnl?.toFixed(2)}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {t.status}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Leg Risks */}
      {legRisks.length > 0 && (
        <Section title="单腿风控状态">
          <div className="space-y-2">
            {legRisks.map(l => (
              <div key={l.trade_id} className={cn(
                'rounded-lg border p-3 flex items-center justify-between text-sm',
                l.status === 'emergency_close' ? 'border-red-500/30 bg-red-500/5' :
                l.status === 'retrying' ? 'border-yellow-500/30 bg-yellow-500/5' : 'border-border',
              )}>
                <span className="font-mono">{l.trade_id} · {l.leg_exchange}</span>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">
                    重试 {l.retries}/{l.max_retries}
                  </span>
                  <span className={cn(
                    'text-xs font-medium px-2 py-0.5 rounded-full',
                    l.status === 'healthy' ? 'bg-green-500/20 text-green-400' :
                    l.status === 'retrying' ? 'bg-yellow-500/20 text-yellow-400' :
                    'bg-red-500/20 text-red-400',
                  )}>
                    {l.status === 'healthy' ? '正常' : l.status === 'retrying' ? '重试中' : '紧急平仓'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  )
}

// ── Sub-components ──

function ExpCard({ label, value, alert, icon }: {
  label: string; value: string; alert?: boolean; icon?: React.ReactNode
}) {
  return (
    <div className={cn(
      'rounded-xl border p-4',
      alert ? 'border-yellow-500/30 bg-yellow-500/5' : 'border-border bg-muted/30',
    )}>
      <div className="flex items-center gap-2">
        {icon}
        <div>
          <div className="text-lg font-bold">{value}</div>
          <div className="text-xs text-muted-foreground">{label}</div>
        </div>
      </div>
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
  return (
    <div className="py-8 text-center text-muted-foreground">
      <div className="text-sm">{message}</div>
    </div>
  )
}
