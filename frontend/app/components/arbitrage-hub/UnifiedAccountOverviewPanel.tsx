/**
 * UnifiedAccountOverviewPanel — 统一账户概览（阶段 5 前端整合）
 *
 * 跨系统（AI 交易员 + 套利中心）paper 账户统一视图。
 * 复用 unifiedAccountApi（双表共存归一化），展示:
 *   - 合并敞口 KPI（总权益 / 总冻结 / 总 uPnL，AI + 套利分项）
 *   - 所有 paper 账户列表（AI 树 + 套利树）
 *   - 费率表（6 交易所 maker/taker/mmr）
 *
 * 数据源: /api/unified-account/* （unified_account_routes.py）
 * 自轮询 20s，遵循 ArbitragePaperAccountPanel 的 self-fetching 模式。
 */
import { useState, useEffect, useCallback } from 'react'
import {
  Wallet, RefreshCw, TrendingUp, Snowflake, DollarSign,
  Layers, Coins, Building2, AlertCircle,
} from 'lucide-react'
import { MetricCard } from '@/components/ui/metric-card'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import {
  listPaperAccounts, getCombinedExposure, getFeeSchedule,
  fmtUsd, fmtPct,
  type UnifiedPaperAccount, type CombinedExposure, type FeeScheduleEntry,
} from '@/lib/unifiedAccountApi'

const POLL_INTERVAL = 20_000

export default function UnifiedAccountOverviewPanel() {
  const [accounts, setAccounts] = useState<UnifiedPaperAccount[]>([])
  const [exposure, setExposure] = useState<CombinedExposure | null>(null)
  const [fees, setFees] = useState<FeeScheduleEntry[]>([])
  const [defaultExchange, setDefaultExchange] = useState('asterdex')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setError(null)
      const [listRes, exposureRes, feeRes] = await Promise.all([
        listPaperAccounts(),
        // 取第一个 AI + 第一个套利账户做合并敞口
        listPaperAccounts().then(lst => {
          const ai = lst.accounts.find(a => a.scope === 'ai')
          const arb = lst.accounts.find(a => a.scope === 'arbitrage')
          return getCombinedExposure(ai?.id, arb?.id)
        }).catch(() => null),
        getFeeSchedule(),
      ])
      setAccounts(listRes.accounts)
      setExposure(exposureRes)
      setFees(feeRes.exchanges)
      setDefaultExchange(feeRes.default_exchange)
    } catch (e: any) {
      setError(e?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, POLL_INTERVAL)
    return () => clearInterval(id)
  }, [refresh])

  const aiAccounts = accounts.filter(a => a.scope === 'ai')
  const arbAccounts = accounts.filter(a => a.scope === 'arbitrage')

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Layers className="w-5 h-5 text-purple-500" />
            统一账户概览
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            AI 交易员 + 套利中心 paper 账户合并视图 · 双表共存归一化
          </p>
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-md border border-border hover:bg-accent disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      {error && (
        <Card className="border-red-500/50">
          <CardContent className="pt-4 flex items-center gap-2 text-red-500">
            <AlertCircle className="w-4 h-4" />
            <span className="text-sm">{error}</span>
          </CardContent>
        </Card>
      )}

      {/* 合并敞口 KPI */}
      {exposure && (
        <div>
          <h3 className="text-sm font-medium text-muted-foreground mb-3">合并敞口（AI + 套利）</h3>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            <MetricCard
              icon={DollarSign}
              title="总权益"
              value={exposure.total_equity}
              prefix="$"
              subtitle={`AI ${fmtUsd(exposure.ai_equity, 0)} + 套利 ${fmtUsd(exposure.arbitrage_equity, 0)}`}
            />
            <MetricCard
              icon={Snowflake}
              title="总冻结"
              value={exposure.total_frozen}
              prefix="$"
              subtitle={`AI ${fmtUsd(exposure.ai_frozen, 0)} + 套利 ${fmtUsd(exposure.arbitrage_frozen, 0)}`}
            />
            <MetricCard
              icon={TrendingUp}
              title="总未实现盈亏"
              value={exposure.total_upnl}
              prefix="$"
              colorBySign
              subtitle={`AI ${fmtUsd(exposure.ai_upnl, 0)} + 套利 ${fmtUsd(exposure.arbitrage_upnl, 0)}`}
            />
            <MetricCard
              icon={Wallet}
              title="可用余额"
              value={(exposure.total_equity - exposure.total_frozen)}
              prefix="$"
              subtitle="总权益 - 总冻结"
            />
          </div>
        </div>
      )}

      {/* 账户列表 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* AI 交易员 Paper 账户 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Building2 className="w-4 h-4 text-blue-500" />
              AI 交易员 Paper 账户
              <Badge variant="secondary" className="text-xs">{aiAccounts.length}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {aiAccounts.length === 0 && (
              <p className="text-xs text-muted-foreground py-4 text-center">暂无 AI paper 账户</p>
            )}
            {aiAccounts.map(a => (
              <AccountRow key={`ai-${a.id}`} account={a} />
            ))}
          </CardContent>
        </Card>

        {/* 套利 Paper 账户 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Coins className="w-4 h-4 text-amber-500" />
              套利中心 Paper 账户
              <Badge variant="secondary" className="text-xs">{arbAccounts.length}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {arbAccounts.length === 0 && (
              <p className="text-xs text-muted-foreground py-4 text-center">暂无套利 paper 账户</p>
            )}
            {arbAccounts.map(a => (
              <AccountRow key={`arb-${a.id}`} account={a} />
            ))}
          </CardContent>
        </Card>
      </div>

      {/* 费率表 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Coins className="w-4 h-4 text-emerald-500" />
            交易所费率表
            <Badge variant="outline" className="text-xs">默认: {defaultExchange}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="text-left py-2 px-2 font-medium">交易所</th>
                  <th className="text-right py-2 px-2 font-medium">Maker</th>
                  <th className="text-right py-2 px-2 font-medium">Taker</th>
                  <th className="text-right py-2 px-2 font-medium">维持保证金率</th>
                  <th className="text-right py-2 px-2 font-medium">最小名义</th>
                </tr>
              </thead>
              <tbody>
                {fees.map(f => (
                  <tr key={f.exchange} className="border-b border-border/50 hover:bg-accent/30">
                    <td className="py-2 px-2 font-medium">
                      {f.exchange}
                      {f.exchange === defaultExchange && (
                        <Badge variant="default" className="ml-2 text-[10px] py-0">默认</Badge>
                      )}
                    </td>
                    <td className="text-right py-2 px-2 tabular-nums">{fmtPct(f.maker_fee_rate)}</td>
                    <td className="text-right py-2 px-2 tabular-nums">{fmtPct(f.taker_fee_rate)}</td>
                    <td className="text-right py-2 px-2 tabular-nums">{fmtPct(f.maintenance_margin_rate, 3)}</td>
                    <td className="text-right py-2 px-2 tabular-nums">${f.min_notional_usd}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-muted-foreground mt-2">
            费率中心化（fee_schedule_service）· 单一真相源 · {fees.length} 个交易所
          </p>
        </CardContent>
      </Card>
    </div>
  )
}

// ── 账户行组件 ──────────────────────────────────────────────

function AccountRow({ account }: { account: UnifiedPaperAccount }) {
  const scopeColor = account.scope === 'ai' ? 'bg-blue-500/10 text-blue-500' : 'bg-amber-500/10 text-amber-600'
  return (
    <div className="flex items-center justify-between p-2.5 rounded-md border border-border/50 hover:bg-accent/30 transition-colors">
      <div className="flex items-center gap-2 min-w-0">
        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${scopeColor}`}>
          {account.scope === 'ai' ? 'AI' : '套利'}
        </span>
        <div className="min-w-0">
          <div className="text-sm font-medium truncate">{account.name || `#${account.id}`}</div>
          <div className="text-[11px] text-muted-foreground">
            ID: {account.id} · {account.exchange || '—'}
            {account.risk_profile && ` · ${account.risk_profile}`}
          </div>
        </div>
      </div>
      <div className="text-right shrink-0 ml-2">
        <div className="text-sm font-semibold tabular-nums">{fmtUsd(account.total_equity, 0)}</div>
        <div className="text-[11px] text-muted-foreground tabular-nums">
          可用 {fmtUsd(account.available_balance, 0)} · 冻结 {fmtUsd(account.frozen_balance, 0)}
        </div>
      </div>
    </div>
  )
}
