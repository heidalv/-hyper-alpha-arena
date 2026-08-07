/**
 * BinanceDashboard - 币安数据看板（增强版）
 *
 * 功能对标 Hyperliquid 数据看板：
 * - 多账户摘要卡片
 * - 详细持仓表格
 * - Tab 切换视图
 */
import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  RefreshCw,
  Wallet,
  TrendingUp,
  TrendingDown,
  AlertCircle,
  Eye,
  DollarSign,
  Percent,
  BarChart3,
  List,
  Activity,
  Clock,
  Zap,
} from 'lucide-react'
import { getAccounts } from '@/lib/api'
import { getBinanceBalance, getBinancePositions, getBinanceConfig, getAccountStrategyStatus, getAIDecisionHistory, type StrategyStatus, type AIDecisionEntry } from '@/lib/binanceApi'
import type { BinanceBalance, BinancePosition, BinanceConfig } from '@/lib/types/binance'
import { getModelLogo } from './logoAssets'
import AnimatedNumber from '@/components/ui/animated-number'
import { getRefreshInterval, getRefreshDisplayText } from '@/config/refresh'
import AIOrderPanel from './AIOrderPanel'
import AITradingPanel from './AITradingPanel'
import { formatPrice, formatSize } from '@/lib/priceFormat'

interface BinanceDashboardProps {
  onPageChange?: (page: string) => void
}

interface AccountWithBinance {
  id: number
  name: string
  binanceConfig: BinanceConfig | null
  balance: BinanceBalance | null
  positions: BinancePosition[]
  loading: boolean
  error: string | null
}

type ViewTab = 'overview' | 'positions' | 'ai-trading' | 'ai-strategy'

export default function BinanceDashboard({ onPageChange }: BinanceDashboardProps) {
  useTranslation()
  const [accounts, setAccounts] = useState<AccountWithBinance[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [activeTab, setActiveTab] = useState<ViewTab>('overview')
  const [selectedAccount, setSelectedAccount] = useState<number | 'all'>('all')
  const [refreshKey, setRefreshKey] = useState(0)

  // ⚡ 性能优化 #1: 并发控制 - 防止多个刷新同时运行
  const isRefreshingRef = useRef(false)

  // 初始加载（显示加载状态）
  const loadData = async () => {
    try {
      setLoading(true)
      const accountList = await getAccounts()
  
      // 初次加载：完整构建账户数据
      const accountsWithData = await Promise.all(
        accountList.map(async (account: any) => {
          try {
            const config = await getBinanceConfig(account.id)
              
            if (!config.configured) {
              return {
                id: account.id,
                name: account.name,
                binanceConfig: config,  // 保留 config 对象，而不是设为 null
                balance: null,
                positions: [],
                loading: false,
                error: null,
              }
            }
  
            // 并行获取余额和持仓（持仓使用 forceRefresh=true 获取实时数据）
            const [balance, positionsResponse] = await Promise.all([
              getBinanceBalance(account.id).catch(() => null),
              getBinancePositions(account.id, true).catch(() => ({ positions: [] })),
            ])
  
            return {
              id: account.id,
              name: account.name,
              binanceConfig: config,
              balance,
              positions: positionsResponse?.positions || [],
              loading: false,
              error: null,
            }
          } catch (err) {
            return {
              id: account.id,
              name: account.name,
              binanceConfig: null,
              balance: null,
              positions: [],
              loading: false,
              error: err instanceof Error ? err.message : '加载失败',
            }
          }
        })
      )
  
      console.log('[BinanceDashboard] Loaded accounts:', accountsWithData)
      setAccounts(accountsWithData)
  
      // 🔥 在数据加载完成后，如果没有已配置的账户，默认选择“全部账户”
      // 如果有已配置的账户且当前选择的是“全部账户”，保持“全部账户”选中
      // 不再自动切换到第一个账户
    } catch (error) {
      console.error('[BinanceDashboard] Failed to load dashboard:', error)
    } finally {
      setLoading(false)
    }
  }

  // ⚡ 数据刷新（应用所有性能优化）
  const refreshData = useCallback(async () => {
    // 性能优化 #1: 并发控制 - 如果已经在刷新，直接返回
    if (isRefreshingRef.current) {
      console.log('[BinanceDashboard] 跳过本次刷新，上一次刷新仍在进行')
      return
    }

    isRefreshingRef.current = true
    console.log('[BinanceDashboard] 开始刷新数据')

    try {
      const accountList = await getAccounts()

      // ⚡ 性能优化 #2: 并行处理所有账户（而不是串行for循环）
      // 性能优化 #3: 移除人工延迟
      const accountUpdates = await Promise.all(
        accountList.map(async (account) => {
          try {
            const config = await getBinanceConfig(account.id)

            if (!config.configured) {
              return null
            }

            // ⚡ 性能优化 #2: 并行获取余额和持仓
            const [balance, positionsResponse] = await Promise.all([
              getBinanceBalance(account.id).catch(() => null),
              getBinancePositions(account.id, true).catch(() => ({ positions: [] })),
            ])

            return {
              accountId: account.id,
              balance,
              positions: positionsResponse?.positions || [],
            }
          } catch (err) {
            console.error(`Failed to refresh account ${account.id}:`, err)
            return null
          }
        })
      )

      // ⚡ 性能优化 #4: 批量更新状态（只触发一次re-render）
      const updatesMap = new Map(
        accountUpdates.filter(Boolean).map(u => [u!.accountId, { balance: u!.balance, positions: u!.positions }])
      )

      setAccounts(prev =>
        prev.map(acc => {
          const update = updatesMap.get(acc.id)
          if (!update) return acc

          return {
            ...acc,
            balance: update.balance || acc.balance,
            positions: update.positions,
          }
        })
      )

      console.log('[BinanceDashboard] 数据刷新完成')
    } catch (error) {
      console.error('[BinanceDashboard] Failed to refresh data:', error)
    } finally {
      // 性能优化 #1: 重置标志，允许下次刷新
      isRefreshingRef.current = false
    }
  }, [])

  // 手动刷新（显示刷新图标）
  const handleRefresh = async () => {
    setRefreshing(true)
    await refreshData()
    setRefreshing(false)
  }

  useEffect(() => {
    loadData()
  }, []) // ✅ 只在组件加载时执行一次

  // ⚡ 性能优化 #5: 稳定的定时器（使用useRef避免依赖变化）
  const refreshDataRef = useRef(refreshData)
  refreshDataRef.current = refreshData

  useEffect(() => {
    const interval = setInterval(() => {
      refreshDataRef.current()
      setRefreshKey(prev => prev + 1)
    }, getRefreshInterval('binance_balance')) // 30000ms

    return () => clearInterval(interval)
  }, []) // ✅ 空依赖数组，定时器不会重新创建

  const configuredAccounts = useMemo(
    () => {
      // 过滤出已配置币安的账户
      const filtered = accounts.filter((a) => a.binanceConfig?.configured === true)
      console.log('[BinanceDashboard] configuredAccounts:', filtered.length, 'of', accounts.length)
      return filtered
    },
    [accounts]
  )

  const filteredAccounts = useMemo(() => {
    // “全部账户”模式：返回所有已配置的账户
    if (selectedAccount === 'all') {
      console.log('[BinanceDashboard] filteredAccounts (all):', configuredAccounts.length)
      return configuredAccounts
    }
    // 单个账户模式：返回指定账户
    const filtered = configuredAccounts.filter((a) => a.id === selectedAccount)
    console.log('[BinanceDashboard] filteredAccounts (single):', filtered.length, 'selectedAccount:', selectedAccount)
    return filtered
  }, [configuredAccounts, selectedAccount])

  // 按 api_key_fingerprint 去重，避免同一币安账户被多个交易员重复统计
  const uniqueAccounts = useMemo(() => {
    const seen = new Set<string>()
    return configuredAccounts.filter((a) => {
      const fp = a.binanceConfig?.api_key_fingerprint
      if (!fp) return true // 无指纹的账户不去重
      if (seen.has(fp)) return false // 已见过的指纹跳过
      seen.add(fp)
      return true
    })
  }, [configuredAccounts])

  const totalEquity = useMemo(
    () => {
      const total = uniqueAccounts.reduce(
        (sum, a) => sum + (a.balance?.total_balance || a.balance?.total_equity || 0),
        0
      )
      return total
    },
    [uniqueAccounts]
  )
  const totalAvailable = useMemo(
    () =>
      uniqueAccounts.reduce(
        (sum, a) => sum + (a.balance?.available_balance || 0),
        0
      ),
    [uniqueAccounts]
  )
  const totalPnL = useMemo(
    () =>
      uniqueAccounts.reduce(
        (sum, a) =>
          sum + a.positions.reduce((psum, p) => psum + (p.unrealized_pnl || 0), 0),
        0
      ),
    [uniqueAccounts]
  )
  const totalPositions = useMemo(
    () =>
      uniqueAccounts.reduce(
        (sum, a) => sum + a.positions.length,
        0
      ),
    [uniqueAccounts]
  )

  const allPositions = useMemo(() => {
    return filteredAccounts.flatMap((acc) =>
      acc.positions.map((pos) => ({
        ...pos,
        accountId: acc.id,
        accountName: acc.name,
      }))
    )
  }, [filteredAccounts])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <RefreshCw className="h-8 w-8 animate-spin mx-auto mb-2 text-muted-foreground" />
          <p className="text-muted-foreground">加载币安数据...</p>
        </div>
      </div>
    )
  }

  if (!loading && configuredAccounts.length === 0) {
    return (
      <div className="flex items-center justify-center h-96">
        <Card className="max-w-md">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Wallet className="h-5 w-5" />
              币安账户未配置
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-muted-foreground">
              请先在 AI 交易员管理中配置币安钱包。
            </p>
            <Button onClick={() => onPageChange?.('trader-management')}>
              去配置
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">币安数据看板</h2>
          <p className="text-muted-foreground">账户余额和持仓概览</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Account Selector */}
          <select
            value={selectedAccount}
            onChange={(e) =>
              setSelectedAccount(
                e.target.value === 'all' ? 'all' : parseInt(e.target.value)
              )
            }
            className="h-9 px-3 rounded-md border border-input bg-background text-sm"
          >
            <option value="all">全部账户</option>
            {configuredAccounts.map((acc) => (
              <option key={acc.id} value={acc.id}>
                {acc.name}
              </option>
            ))}
          </select>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefresh}
            disabled={refreshing}
          >
            <RefreshCw
              className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`}
            />
            刷新
          </Button>
          {/* 显示上次刷新时间 */}
          <span className="text-xs text-muted-foreground">
            {getRefreshDisplayText('binance_balance')}
          </span>
        </div>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-muted-foreground text-sm mb-1">
              <DollarSign className="h-4 w-4" />
              总权益
            </div>
            <div className="text-2xl font-bold">
              $<AnimatedNumber value={totalEquity} decimals={2} />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-muted-foreground text-sm mb-1">
              <Wallet className="h-4 w-4" />
              可用余额
            </div>
            <div className="text-2xl font-bold">
              $<AnimatedNumber value={totalAvailable} decimals={2} />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-muted-foreground text-sm mb-1">
              {totalPnL >= 0 ? (
                <TrendingUp className="h-4 w-4 text-green-600" />
              ) : (
                <TrendingDown className="h-4 w-4 text-red-600" />
              )}
              未实现盈亏
            </div>
            <div className="text-2xl font-bold">
              <AnimatedNumber 
                value={totalPnL} 
                decimals={2} 
                prefix={totalPnL >= 0 ? '+' : ''}
                suffix=" USDT"
                colorize={true}
              />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-muted-foreground text-sm mb-1">
              <BarChart3 className="h-4 w-4" />
              持仓数量
            </div>
            <div className="text-2xl font-bold">{totalPositions}</div>
          </CardContent>
        </Card>
      </div>

      {/* Tabs */}
      <Tabs
        value={activeTab}
        onValueChange={(v) => setActiveTab(v as ViewTab)}
        className="space-y-4"
      >
        <TabsList>
          <TabsTrigger value="overview" className="flex items-center gap-2">
            <Eye className="h-4 w-4" />
            账户概览
          </TabsTrigger>
          <TabsTrigger value="positions" className="flex items-center gap-2">
            <List className="h-4 w-4" />
            持仓明细
          </TabsTrigger>
          <TabsTrigger value="ai-trading" className="flex items-center gap-2">
            <Activity className="h-4 w-4" />
            AI自动开单
          </TabsTrigger>
          <TabsTrigger value="ai-strategy" className="flex items-center gap-2">
            <Zap className="h-4 w-4" />
            AI策略开单
          </TabsTrigger>
        </TabsList>

        {/* Overview Tab */}
        <TabsContent value="overview" className="space-y-4">
          <div
            className={`grid gap-4 ${
              filteredAccounts.length === 1
                ? 'grid-cols-1'
                : filteredAccounts.length === 2
                ? 'grid-cols-1 md:grid-cols-2'
                : 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3'
            }`}
          >
            {filteredAccounts.map((account) => {
              const logo = getModelLogo(account.name)
              const accountPnL = account.positions.reduce(
                (sum, p) => sum + (p.unrealized_pnl || 0),
                0
              )
              const totalBalance = account.balance?.total_balance || account.balance?.total_equity || 0
              const marginUsed = account.balance
                ? totalBalance - account.balance.available_balance
                : 0
              const marginPercent = totalBalance
                ? (marginUsed / totalBalance) * 100
                : 0

              return (
                <Card
                  key={account.id}
                  className="hover:shadow-md transition-shadow"
                >
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        {logo && (
                          <img
                            src={logo.src}
                            alt={logo.alt}
                            className="h-6 w-6 rounded-full object-contain"
                          />
                        )}
                        <CardTitle className="text-lg">{account.name}</CardTitle>
                      </div>
                      <div className="flex gap-2">
                        <Badge
                          variant={
                            account.binanceConfig?.testnet
                              ? 'default'
                              : 'destructive'
                          }
                        >
                          {account.binanceConfig?.testnet ? 'TESTNET' : 'MAINNET'}
                        </Badge>
                        <Badge variant="outline">
                          {(account.binanceConfig?.market_type || account.binanceConfig?.marketType) === 'spot'
                            ? '现货'
                            : '合约'}
                        </Badge>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {account.error ? (
                      <div className="flex items-center gap-2 text-sm text-red-600">
                        <AlertCircle className="h-4 w-4" />
                        {account.error}
                      </div>
                    ) : (
                      <>
                        {/* Balance Grid */}
                        {account.balance && (
                          <div className="grid grid-cols-2 gap-4">
                            <div>
                              <div className="text-xs text-muted-foreground">
                                权益
                              </div>
                              <div className="text-lg font-bold">
                                ${(account.balance.total_balance || account.balance.total_equity || 0).toFixed(2)}
                              </div>
                            </div>
                            <div>
                              <div className="text-xs text-muted-foreground">
                                可用
                              </div>
                              <div className="text-lg font-medium">
                                ${account.balance.available_balance.toFixed(2)}
                              </div>
                            </div>
                            <div>
                              <div className="text-xs text-muted-foreground">
                                保证金
                              </div>
                              <div
                                className={`text-sm font-medium ${
                                  marginPercent > 70
                                    ? 'text-red-600'
                                    : marginPercent > 50
                                    ? 'text-yellow-600'
                                    : 'text-green-600'
                                }`}
                              >
                                {marginPercent.toFixed(1)}%
                              </div>
                            </div>
                            <div>
                              <div className="text-xs text-muted-foreground">
                                盈亏
                              </div>
                              <div
                                className={`text-sm font-medium ${
                                  accountPnL >= 0
                                    ? 'text-green-600'
                                    : 'text-red-600'
                                }`}
                              >
                                {accountPnL >= 0 ? '+' : ''}
                                {accountPnL.toFixed(2)}
                              </div>
                            </div>
                          </div>
                        )}

                        {/* Positions Preview */}
                        <div className="pt-2 border-t">
                          <div className="text-xs text-muted-foreground mb-2">
                            持仓 ({account.positions.length})
                          </div>
                          {account.positions.length > 0 ? (
                            <div className="flex flex-wrap gap-1.5">
                              {account.positions.map((pos, idx) => {
                                const isLong = pos.side === 'long'
                                const pnlColor =
                                  (pos.unrealized_pnl || 0) >= 0
                                    ? 'text-green-600'
                                    : 'text-red-600'
                                return (
                                  <div
                                    key={idx}
                                    className={`text-xs px-2 py-1 rounded border ${
                                      isLong
                                        ? 'bg-green-500/10 border-green-500/20'
                                        : 'bg-red-500/10 border-red-500/20'
                                    }`}
                                  >
                                    <div className="flex items-center gap-1">
                                      <span
                                        className={`font-medium ${
                                          isLong
                                            ? 'text-green-600'
                                            : 'text-red-600'
                                        }`}
                                      >
                                        {pos.symbol} {isLong ? 'L' : 'S'}
                                      </span>
                                      <span className="text-muted-foreground">
                                        {pos.leverage || 1}x
                                      </span>
                                    </div>
                                    <div className={`font-medium ${pnlColor}`}>
                                      {(pos.unrealized_pnl || 0) >= 0 ? '+' : ''}
                                      ${(pos.unrealized_pnl || 0).toFixed(2)}
                                    </div>
                                  </div>
                                )
                              })}
                            </div>
                          ) : (
                            <div className="text-xs text-muted-foreground">
                              暂无持仓
                            </div>
                          )}
                        </div>

                        {/* AI Strategy Status */}
                        <AIStrategyStatusIndicator accountId={account.id} />
                      </>
                    )}
                  </CardContent>
                </Card>
              )
            })}
          </div>
        </TabsContent>

        {/* Positions Tab */}
        <TabsContent value="positions">
          <Card>
            <CardHeader>
              <CardTitle>持仓明细</CardTitle>
            </CardHeader>
            <CardContent>
              {allPositions.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-muted-foreground">
                        <th className="text-left py-3 px-2">账户</th>
                        <th className="text-left py-3 px-2">交易对</th>
                        <th className="text-left py-3 px-2">方向</th>
                        <th className="text-right py-3 px-2">数量</th>
                        <th className="text-right py-3 px-2">开仓价</th>
                        <th className="text-right py-3 px-2">标记价</th>
                        <th className="text-right py-3 px-2">杠杆</th>
                        <th className="text-right py-3 px-2">未实现盈亏</th>
                        <th className="text-right py-3 px-2">ROE</th>
                      </tr>
                    </thead>
                    <tbody>
                      {allPositions.map((pos, idx) => {
                        const isLong = pos.side === 'long'
                        const pnl = pos.unrealized_pnl || 0
                        const roe = pos.roe_percent || 0
                        // Extract base symbol for price formatting (e.g., "VIRTUAL/USDT:USDT" -> "VIRTUAL")
                        const baseSymbol = pos.symbol.split('/')[0].toUpperCase()
                        return (
                          <tr
                            key={idx}
                            className="border-b hover:bg-muted/50 transition-colors"
                          >
                            <td className="py-3 px-2 font-medium">
                              {pos.accountName}
                            </td>
                            <td className="py-3 px-2 font-semibold">
                              {pos.symbol}
                            </td>
                            <td className="py-3 px-2">
                              <Badge
                                variant={isLong ? 'default' : 'destructive'}
                                className="text-xs"
                              >
                                {isLong ? '多' : '空'}
                              </Badge>
                            </td>
                            <td className="py-3 px-2 text-right">
                              {formatSize(pos.size, baseSymbol)}
                            </td>
                            <td className="py-3 px-2 text-right">
                              {formatPrice(pos.entry_price, baseSymbol)}
                            </td>
                            <td className="py-3 px-2 text-right">
                              {formatPrice(pos.mark_price, baseSymbol)}
                            </td>
                            <td className="py-3 px-2 text-right">
                              {pos.leverage || 1}x
                            </td>
                            <td
                              className={`py-3 px-2 text-right font-medium ${
                                pnl >= 0 ? 'text-green-600' : 'text-red-600'
                              }`}
                            >
                              {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                            </td>
                            <td
                              className={`py-3 px-2 text-right font-medium ${
                                roe >= 0 ? 'text-green-600' : 'text-red-600'
                              }`}
                            >
                              {roe >= 0 ? '+' : ''}{roe.toFixed(2)}%
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-center py-12 text-muted-foreground">
                  <List className="h-12 w-12 mx-auto mb-4 opacity-50" />
                  <p>暂无持仓</p>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* AI自动开单 Tab */}
        <TabsContent value="ai-trading">
          {selectedAccount !== 'all' && typeof selectedAccount === 'number' ? (
            <AITradingPanel accountId={selectedAccount} refreshKey={refreshKey} />
          ) : (
            // 当选择"全部"时，显示第一个账户的AI交易（或显示所有账户的汇总）
            accounts.length > 0 ? (
              <AITradingPanel accountId={accounts[0].id} refreshKey={refreshKey} />
            ) : (
              <Card>
                <CardContent className="flex items-center justify-center py-12">
                  <div className="text-center text-muted-foreground">
                    <Activity className="h-12 w-12 mx-auto mb-4 opacity-50" />
                    <p className="mb-2">暂无账户</p>
                    <p className="text-sm">请先添加并配置一个账户</p>
                  </div>
                </CardContent>
              </Card>
            )
          )}
        </TabsContent>

        {/* AI策略开单 Tab */}
        <TabsContent value="ai-strategy">
          {selectedAccount !== 'all' && typeof selectedAccount === 'number' ? (
            <AIOrderPanel accountId={selectedAccount} refreshKey={refreshKey} />
          ) : (
            // 当选择"全部"时，显示第一个账户的AI策略（或显示所有账户的汇总）
            accounts.length > 0 ? (
              <AIOrderPanel accountId={accounts[0].id} refreshKey={refreshKey} />
            ) : (
              <Card>
                <CardContent className="flex items-center justify-center py-12">
                  <div className="text-center text-muted-foreground">
                    <Zap className="h-12 w-12 mx-auto mb-4 opacity-50" />
                    <p className="mb-2">暂无账户</p>
                    <p className="text-sm">请先添加并配置一个账户</p>
                  </div>
                </CardContent>
              </Card>
            )
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}

/**
 * AI策略状态指示器组件
 */
function AIStrategyStatusIndicator({ accountId }: { accountId: number }) {
  const [status, setStatus] = useState<StrategyStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [countdown, setCountdown] = useState<number | null>(null)
  const hasStrategy = useRef<boolean | null>(null)

  // 获取策略状态
  const fetchStatus = useCallback(async () => {
    // 如果已确认无策略，不再轮询
    if (hasStrategy.current === false) return
    try {
      const data = await getAccountStrategyStatus(accountId)
      if (data) {
        hasStrategy.current = true
        setStatus(data)
      } else {
        // null = 404/无策略，标记后不再轮询
        hasStrategy.current = false
        setStatus(null)
      }
    } catch {
      // Silently handle - account may not have strategy configured
      hasStrategy.current = false
    } finally {
      setLoading(false)
    }
  }, [accountId])

  useEffect(() => {
    // 账户切换时重置
    hasStrategy.current = null
    setLoading(true)
    fetchStatus()
    // 只有有策略的账户才持续轮询
    const interval = setInterval(() => {
      if (hasStrategy.current === true) fetchStatus()
    }, 30000)
    return () => clearInterval(interval)
  }, [fetchStatus])

  // 倒计时更新
  useEffect(() => {
    if (!status?.next_trigger_in) {
      setCountdown(null)
      return
    }

    setCountdown(status.next_trigger_in)
    const interval = setInterval(() => {
      setCountdown((prev) => (prev && prev > 0 ? prev - 1 : 0))
    }, 1000)

    return () => clearInterval(interval)
  }, [status?.next_trigger_in])

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground py-2 border-t">
        <Activity className="h-3 w-3 animate-pulse" />
        加载AI状态...
      </div>
    )
  }

  if (!status) return null

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const formatLastTrigger = (timestamp: string | null) => {
    if (!timestamp) return '从未触发'
    const date = new Date(timestamp)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffMins = Math.floor(diffMs / 60000)
    
    if (diffMins < 1) return '刚刚'
    if (diffMins < 60) return `${diffMins}分钟前`
    const diffHours = Math.floor(diffMins / 60)
    if (diffHours < 24) return `${diffHours}小时前`
    return `${Math.floor(diffHours / 24)}天前`
  }

  return (
    <div className="pt-2 border-t space-y-2">
      {/* 状态行 */}
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center gap-1.5">
          <Activity 
            className={`h-3 w-3 ${
              status.running 
                ? 'text-green-500 animate-pulse' 
                : status.enabled 
                ? 'text-blue-500'
                : 'text-muted-foreground'
            }`} 
          />
          <span className="text-muted-foreground">
            AI策略
          </span>
        </div>
        <div className="flex items-center gap-1">
          {status.running && (
            <span className="text-xs bg-green-500/20 text-green-600 px-1.5 py-0.5 rounded">
              运行中
            </span>
          )}
          {!status.running && status.enabled && (
            <span className="text-xs bg-blue-500/20 text-blue-600 px-1.5 py-0.5 rounded">
              就绪
            </span>
          )}
          {!status.enabled && (
            <span className="text-xs bg-muted text-muted-foreground px-1.5 py-0.5 rounded">
              已禁用
            </span>
          )}
        </div>
      </div>

      {/* 详细信息 */}
      {status.enabled && (
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="flex items-center gap-1 text-muted-foreground">
            <Clock className="h-3 w-3" />
            <span>最后触发: {formatLastTrigger(status.last_trigger_at)}</span>
          </div>
          {countdown !== null && countdown > 0 && (
            <div className="flex items-center gap-1 text-muted-foreground justify-end">
              <Zap className="h-3 w-3" />
              <span>下次: {formatTime(countdown)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
