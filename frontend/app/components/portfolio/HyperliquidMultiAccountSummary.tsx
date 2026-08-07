import { useEffect, useState, useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { TrendingUp, AlertTriangle, Eye, Zap } from 'lucide-react'
import { getHyperliquidBalance, getWalletRateLimit, getTradingStats, TradingStats } from '@/lib/hyperliquidApi'
import { getModelLogo } from './logoAssets'
import type { HyperliquidEnvironment } from '@/lib/types/hyperliquid'
import type { HyperliquidBalance } from '@/lib/types/hyperliquid'
import { useTradingMode } from '@/contexts/TradingModeContext'
import { formatDateTime } from '@/lib/dateTime'
import { formatSize } from '@/lib/priceFormat'
import {
  getCachedData,
  setCachedData,
  getApiUsageCacheKey,
  getTradingStatsCacheKey,
  getCacheTimestamp,
} from '@/lib/cacheUtils'
import TraderDetailModal from './TraderDetailModal'

// Position type from parent component
export interface Position {
  symbol: string
  side: string
  size: number
  entry_price: number
  mark_price: number
  unrealized_pnl: number
  leverage: number
  account_id: number
}

interface RateLimitData {
  cumVlm: number
  nRequestsUsed: number
  nRequestsCap: number
  remaining: number
  usagePercent: number
  isOverLimit: boolean
}

interface AccountBalance {
  accountId: number
  accountName: string
  balance: HyperliquidBalance | null
  error: string | null
  loading: boolean
  rateLimit: RateLimitData | null
  rateLimitUpdated: number | null
  tradingStats: TradingStats | null
  tradingStatsUpdated: number | null
}

interface HyperliquidMultiAccountSummaryProps {
  accounts: Array<{ account_id: number; account_name: string }>
  refreshKey?: number
  selectedAccount?: number | 'all'
  positions?: Position[]
}

const getMarginStatus = (percent: number, t: (key: string, fallback?: string) => string) => {
  if (percent < 50) {
    return {
      color: 'bg-green-500',
      text: t('account.marginHealthy', 'Healthy'),
      icon: TrendingUp,
      textColor: 'text-green-600',
      dotColor: 'bg-green-500',
    } as const
  }
  if (percent < 75) {
    return {
      color: 'bg-yellow-500',
      text: t('account.marginModerate', 'Moderate'),
      icon: AlertTriangle,
      textColor: 'text-yellow-600',
      dotColor: 'bg-yellow-500',
    } as const
  }
  return {
    color: 'bg-red-500',
    text: t('account.marginHighRisk', 'High Risk'),
    icon: AlertTriangle,
    textColor: 'text-red-600',
    dotColor: 'bg-red-500',
  } as const
}

export default function HyperliquidMultiAccountSummary({
  accounts,
  refreshKey,
  selectedAccount = 'all',
  positions = [],
}: HyperliquidMultiAccountSummaryProps) {
  const { t } = useTranslation()
  const { tradingMode } = useTradingMode()
  const [accountBalances, setAccountBalances] = useState<AccountBalance[]>([])
  const [globalLastUpdate, setGlobalLastUpdate] = useState<string | null>(null)
  const [selectedTraderForModal, setSelectedTraderForModal] = useState<AccountBalance | null>(null)
  const [isModalOpen, setIsModalOpen] = useState(false)

  // Filter accounts based on selectedAccount - memoized to prevent infinite loops
  const filteredAccounts = useMemo(() => {
    return selectedAccount === 'all'
      ? accounts
      : accounts.filter(acc => acc.account_id === selectedAccount)
  }, [accounts, selectedAccount])

  // Get environment string
  const environment: HyperliquidEnvironment =
    tradingMode === 'testnet' || tradingMode === 'mainnet' ? tradingMode : 'testnet'

  // Load balances first (fast), then async load API Usage and Trading Stats
  const loadAllBalances = useCallback(async () => {
    // Step 1: Load balances quickly with cached data
    const balanceResults = await Promise.allSettled(
      filteredAccounts.map(async (acc) => {
        try {
          const balance = await getHyperliquidBalance(acc.account_id)
          // Read from cache immediately (no API call)
          const apiUsageCacheKey = getApiUsageCacheKey(acc.account_id, environment)
          const statsCacheKey = getTradingStatsCacheKey(acc.account_id, environment)
          return {
            accountId: acc.account_id,
            accountName: acc.account_name,
            balance,
            error: null,
            loading: false,
            rateLimit: getCachedData<RateLimitData>(apiUsageCacheKey),
            rateLimitUpdated: getCacheTimestamp(apiUsageCacheKey),
            tradingStats: getCachedData<TradingStats>(statsCacheKey),
            tradingStatsUpdated: getCacheTimestamp(statsCacheKey),
          }
        } catch (error: any) {
          return {
            accountId: acc.account_id,
            accountName: acc.account_name,
            balance: null,
            error: error.message || 'Failed to load',
            loading: false,
            rateLimit: null,
            rateLimitUpdated: null,
            tradingStats: null,
            tradingStatsUpdated: null,
          }
        }
      })
    )

    const initialBalances: AccountBalance[] = balanceResults.map((result, index) => {
      if (result.status === 'fulfilled') return result.value
      return {
        accountId: filteredAccounts[index].account_id,
        accountName: filteredAccounts[index].account_name,
        balance: null,
        error: 'Failed to load',
        loading: false,
        rateLimit: null,
        rateLimitUpdated: null,
        tradingStats: null,
        tradingStatsUpdated: null,
      }
    })

    setAccountBalances(initialBalances)

    // Update timestamp
    const latestUpdate = initialBalances
      .map((acc) => acc.balance?.lastUpdated)
      .filter((ts): ts is string => ts !== undefined)
      .sort()
      .reverse()[0]
    if (latestUpdate) setGlobalLastUpdate(formatDateTime(latestUpdate))

    // Step 2: Async load API Usage and Trading Stats for accounts missing cache
    filteredAccounts.forEach(async (acc) => {
      const apiUsageCacheKey = getApiUsageCacheKey(acc.account_id, environment)
      const statsCacheKey = getTradingStatsCacheKey(acc.account_id, environment)
      let needsUpdate = false
      let newRateLimit = getCachedData<RateLimitData>(apiUsageCacheKey)
      let newRateLimitUpdated = getCacheTimestamp(apiUsageCacheKey)
      let newTradingStats = getCachedData<TradingStats>(statsCacheKey)
      let newTradingStatsUpdated = getCacheTimestamp(statsCacheKey)

      // Fetch API Usage if not cached
      if (!newRateLimit) {
        try {
          const res = await getWalletRateLimit(acc.account_id, environment)
          if (res.success && res.rateLimit) {
            newRateLimit = res.rateLimit
            setCachedData(apiUsageCacheKey, newRateLimit)
            newRateLimitUpdated = Date.now()
            needsUpdate = true
          }
        } catch (e) { /* ignore */ }
      }

      // Fetch Trading Stats if not cached
      if (!newTradingStats) {
        try {
          const res = await getTradingStats(acc.account_id, environment)
          if (res.success && res.stats) {
            newTradingStats = res.stats
            setCachedData(statsCacheKey, newTradingStats)
            newTradingStatsUpdated = Date.now()
            needsUpdate = true
          }
        } catch (e) { /* ignore */ }
      }

      // Update state if new data fetched
      if (needsUpdate) {
        setAccountBalances(prev => prev.map(a =>
          a.accountId === acc.account_id
            ? { ...a, rateLimit: newRateLimit, rateLimitUpdated: newRateLimitUpdated, tradingStats: newTradingStats, tradingStatsUpdated: newTradingStatsUpdated }
            : a
        ))
      }
    })
  }, [filteredAccounts, environment])

  useEffect(() => {
    if (filteredAccounts.length === 0) {
      setAccountBalances([])
      return
    }

    // Only initialize with loading state on first load (when accountBalances is empty)
    const isFirstLoad = accountBalances.length === 0
    if (isFirstLoad) {
      setAccountBalances(
        filteredAccounts.map((acc) => ({
          accountId: acc.account_id,
          accountName: acc.account_name,
          balance: null,
          error: null,
          loading: true,
          rateLimit: null,
          rateLimitUpdated: null,
          tradingStats: null,
          tradingStatsUpdated: null,
        }))
      )
    }

    loadAllBalances()
  }, [filteredAccounts, tradingMode, refreshKey])

  // Get positions for a specific account
  const getAccountPositions = (accountId: number) => {
    return positions.filter(p => p.account_id === accountId)
  }

  // Handle opening modal
  const handleViewDetails = (account: AccountBalance) => {
    setSelectedTraderForModal(account)
    setIsModalOpen(true)
  }

  if (tradingMode !== 'testnet' && tradingMode !== 'mainnet') {
    return null
  }

  if (filteredAccounts.length === 0) {
    return (
      <Card className="p-6">
        <div className="text-sm text-muted-foreground">
          {t('account.noAccountsConfigured', 'No Hyperliquid accounts configured')}
        </div>
      </Card>
    )
  }

  const isLoading = accountBalances.some((acc) => acc.loading)

  // Helper to get API usage color
  const getApiUsageColor = (usagePercent: number) => {
    if (usagePercent >= 90) return 'text-red-600'
    if (usagePercent >= 70) return 'text-yellow-600'
    return 'text-green-600'
  }

  // Dynamic grid columns based on number of accounts
  const accountCount = accountBalances.length
  const gridColsClass = accountCount === 1
    ? 'grid-cols-1'
    : accountCount === 2
    ? 'grid-cols-1 md:grid-cols-2'
    : accountCount === 3
    ? 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3'
    : 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4'

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">{t('account.accountStatus', 'Hyperliquid Account Status')}</h2>
        <Badge
          variant={environment === 'testnet' ? 'default' : 'destructive'}
          className="uppercase text-xs"
        >
          {environment}
        </Badge>
      </div>

      {globalLastUpdate && (
        <div className="text-xs text-muted-foreground -mt-2">
          {t('common.lastUpdate', 'Last update')}: {globalLastUpdate}
        </div>
      )}

      {/* Loading state - only show when no data yet */}
      {isLoading && accountBalances.every(a => !a.balance) && (
        <div className="text-sm text-muted-foreground">{t('account.loadingData', 'Loading account data...')}</div>
      )}

      {/* Account cards grid */}
      <div className={`grid ${gridColsClass} gap-4`}>
        {accountBalances.map((account) => {
          const logo = getModelLogo(account.accountName)
          const marginStatus = account.balance
            ? getMarginStatus(account.balance.marginUsagePercent, t)
            : null
          const accountPositions = getAccountPositions(account.accountId)

          return (
            <Card
              key={account.accountId}
              className="p-4 space-y-3 hover:shadow-md transition-shadow"
            >
              {/* Account header with logo and View Details button */}
              <div className="flex items-center justify-between pb-2 border-b border-border">
                <div className="flex items-center gap-2">
                  {logo && (
                    <img
                      src={logo.src}
                      alt={logo.alt}
                      className="h-6 w-6 rounded-full object-contain"
                    />
                  )}
                  <span className="font-semibold text-sm truncate">
                    {account.accountName}
                  </span>
                </div>
                {account.balance && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-[10px] h-6 px-2"
                    onClick={() => handleViewDetails(account)}
                  >
                    <Eye className="w-3 h-3 mr-1" />
                    {t('common.details', 'Details')}
                  </Button>
                )}
              </div>

              {/* Error state */}
              {account.error && (
                <div className="text-xs text-red-600">{account.error}</div>
              )}

              {/* Main metrics grid */}
              {account.balance && (
                <div className="grid grid-cols-2 gap-3">
                  {/* Equity */}
                  <div>
                    <div className="text-[10px] text-muted-foreground">{t('account.equity', 'Equity')}</div>
                    <div className="text-sm font-bold">
                      ${account.balance.totalEquity.toLocaleString('en-US', {
                        minimumFractionDigits: 0,
                        maximumFractionDigits: 0,
                      })}
                    </div>
                  </div>

                  {/* Margin */}
                  <div>
                    <div className="text-[10px] text-muted-foreground">{t('account.margin', 'Margin')}</div>
                    <div className={`text-sm font-medium ${marginStatus?.textColor || ''}`}>
                      {account.balance.marginUsagePercent.toFixed(1)}%
                    </div>
                  </div>

                  {/* API Usage */}
                  <div>
                    <div className="text-[10px] text-muted-foreground">API</div>
                    {account.rateLimit ? (
                      <div className={`text-sm font-medium ${getApiUsageColor(account.rateLimit.usagePercent)}`}>
                        {(100 - account.rateLimit.usagePercent).toFixed(0)}%
                        <span className="text-[10px] text-muted-foreground ml-1">{t('account.left', 'left')}</span>
                      </div>
                    ) : (
                      <div className="text-sm text-muted-foreground">--</div>
                    )}
                  </div>

                  {/* Win Rate */}
                  <div>
                    <div className="text-[10px] text-muted-foreground">{t('account.winRate', 'Win Rate')}</div>
                    {account.tradingStats && account.tradingStats.total_trades > 0 ? (
                      <div className="text-sm font-medium">
                        {account.tradingStats.win_rate.toFixed(0)}%
                        <span className="text-[10px] text-muted-foreground ml-1">
                          ({account.tradingStats.wins}W/{account.tradingStats.losses}L)
                        </span>
                      </div>
                    ) : (
                      <div className="text-sm text-muted-foreground">--</div>
                    )}
                  </div>
                </div>
              )}

              {/* Positions section - always show */}
              <div className="pt-2 border-t border-border">
                <div className="text-[10px] text-muted-foreground mb-1">
                  {t('account.positions', 'Positions')} {accountPositions.length > 0 && `(${accountPositions.length})`}
                </div>
                {accountPositions.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {accountPositions.slice(0, 4).map((pos, idx) => {
                      const isLong = pos.side.toLowerCase() === 'long'
                      const pnlColor = pos.unrealized_pnl >= 0 ? 'text-green-600' : 'text-red-600'
                      return (
                        <div
                          key={idx}
                          className={`text-[10px] px-1.5 py-1 rounded border ${
                            isLong
                              ? 'bg-green-500/10 border-green-500/20'
                              : 'bg-red-500/10 border-red-500/20'
                          }`}
                        >
                          <div className="flex items-center gap-1">
                            <span className={`font-medium ${isLong ? 'text-green-600' : 'text-red-600'}`}>
                              {pos.symbol} {isLong ? 'L' : 'S'}
                            </span>
                            <span className="text-muted-foreground">{pos.leverage}x</span>
                          </div>
                          <div className="flex items-center gap-1 mt-0.5">
                            <span className="text-muted-foreground">{formatSize(pos.size, pos.symbol)}</span>
                            <span className={`font-medium ${pnlColor}`}>
                              {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl.toFixed(2)}
                            </span>
                          </div>
                        </div>
                      )
                    })}
                    {accountPositions.length > 4 && (
                      <div className="text-[10px] text-muted-foreground self-center">
                        +{accountPositions.length - 4} {t('common.more', 'more')}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-[10px] text-muted-foreground">{t('account.noOpenPositions', 'No open positions')}</div>
                )}
              </div>
            </Card>
          )
        })}
      </div>

      {/* Trader Detail Modal */}
      {selectedTraderForModal && (
        <TraderDetailModal
          isOpen={isModalOpen}
          onClose={() => setIsModalOpen(false)}
          account={selectedTraderForModal}
          positions={getAccountPositions(selectedTraderForModal.accountId)}
          environment={environment}
        />
      )}
    </div>
  )
}