/**
 * HyperliquidView - Hyperliquid Trading Mode Main Dashboard
 *
 * Layout:
 *   Row 1: 4 metric cards (total equity, total PnL, win rate, active positions)
 *   Row 2: Asset chart (2/3) + Positions table (1/3)
 *   Row 3: Strategy overview + Account status + Recent AI decisions
 *
 * Data flow: All data fetched here and passed down via props.
 */
import { useState, useEffect, useCallback, useRef }from 'react'
import { useTranslation } from 'react-i18next'
import { Wallet, TrendingUp, Target, BarChart3 } from 'lucide-react'
import { useTradingMode } from '@/contexts/TradingModeContext'
import { usePageActive } from '@/hooks/usePageActive'
import { getArenaPositions, getArenaTrades, ArenaTrade } from '@/lib/api'
import { getHyperliquidBalance, getTradingStats, type TradingStats } from '@/lib/hyperliquidApi'

import { MetricCard } from '@/components/ui/metric-card'
import HyperliquidAssetChart, { TradeMarker } from './HyperliquidAssetChart'
import PositionsTable, { type DashboardPosition } from '@/components/dashboard/PositionsTable'
import StrategyOverview from '@/components/dashboard/StrategyOverview'
import AccountStatusCard from '@/components/dashboard/AccountStatusCard'
import RecentDecisions from '@/components/dashboard/RecentDecisions'

interface HyperliquidViewProps {
  wsRef?: React.MutableRefObject<WebSocket | null>
  refreshKey?: number
  onPageChange?: (page: string) => void
}

export default function HyperliquidView({ refreshKey = 0, onPageChange }: HyperliquidViewProps) {
  const { t } = useTranslation()
  const { tradingMode } = useTradingMode()
  const pageActive = usePageActive()

  const [loading, setLoading] = useState(true)
  const [positionsData, setPositionsData] = useState<any>(null)
  const [chartRefreshKey, setChartRefreshKey] = useState(0)
  const [tradeMarkers, setTradeMarkers] = useState<TradeMarker[]>([])

  // Aggregated metrics for top cards
  const [totalEquity, setTotalEquity] = useState(0)
  const [totalPnl, setTotalPnl] = useState(0)
  const [winRate, setWinRate] = useState(0)

  const environment = tradingMode === 'testnet' || tradingMode === 'mainnet' ? tradingMode : undefined

  // --- Data loading ---
  const loadPositionsAndTrades = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true)
      const [positions, tradesRes] = await Promise.all([
        getArenaPositions({ trading_mode: tradingMode }),
        getArenaTrades({ trading_mode: tradingMode, limit: 200 }),
      ])
      setPositionsData(positions)
      const markers: TradeMarker[] = (tradesRes.trades || []).map((tr: ArenaTrade) => ({
        trade_id: tr.trade_id,
        trade_time: tr.trade_time || '',
        side: tr.side,
        symbol: tr.symbol,
        account_id: tr.account_id,
        price: tr.price,
      }))
      setTradeMarkers(markers)
    } catch (error) {
      console.error('Failed to load Hyperliquid data:', error)
    } finally {
      if (!silent) {
        setChartRefreshKey(prev => prev + 1)
        setLoading(false)
      }
    }
  }, [tradingMode])

  // Load balance + stats for metric cards
  const loadMetrics = useCallback(async () => {
    if (!positionsData?.accounts?.length) return
    try {
      const accountIds: number[] = positionsData.accounts.map((a: any) => a.account_id)

      const balanceResults = await Promise.allSettled(
        accountIds.map(id => getHyperliquidBalance(id, environment))
      )
      let equity = 0
      balanceResults.forEach(r => {
        if (r.status === 'fulfilled' && r.value) {
          equity += r.value.totalEquity ?? 0
        }
      })
      setTotalEquity(equity)

      const statsResults = await Promise.allSettled(
        accountIds.map(id => getTradingStats(id, environment))
      )
      let totalWins = 0
      let totalTrades = 0
      let pnl = 0
      statsResults.forEach(r => {
        if (r.status === 'fulfilled' && r.value?.stats) {
          const s: TradingStats = r.value.stats
          totalWins += s.wins ?? 0
          totalTrades += s.total_trades ?? 0
          pnl += s.total_pnl ?? 0
        }
      })
      setTotalPnl(pnl)
      setWinRate(totalTrades > 0 ? (totalWins / totalTrades) * 100 : 0)
    } catch {
      // metric loading failure is non-critical
    }
  }, [positionsData, environment])

  // Initial load
  useEffect(() => {
    loadPositionsAndTrades()
  }, [loadPositionsAndTrades, refreshKey])

  // Load metrics when positionsData becomes available
  useEffect(() => {
    loadMetrics()
  }, [loadMetrics])

  // Periodic refresh
  const loadRef = useRef(loadPositionsAndTrades)
  loadRef.current = loadPositionsAndTrades
  const metricsRef = useRef(loadMetrics)
  metricsRef.current = loadMetrics

  useEffect(() => {
    if (!pageActive) return
    const timer = setInterval(() => {
      loadRef.current(true)
      metricsRef.current()
    }, 8000)
    return () => clearInterval(timer)
  }, [pageActive])

  // --- Derived data ---
  const accounts = positionsData?.accounts?.map((acc: any) => ({
    account_id: acc.account_id,
    account_name: acc.account_name,
  })) || []

  const allPositions: DashboardPosition[] = positionsData?.accounts?.flatMap((acc: any) =>
    (acc.positions || []).map((pos: any) => ({
      symbol: pos.symbol,
      side: pos.side,
      size: pos.quantity,
      entry_price: pos.avg_cost,
      mark_price: pos.current_price,
      unrealized_pnl: pos.unrealized_pnl,
      leverage: pos.leverage || null,
      account_id: acc.account_id,
      pnl_pct: pos.return_on_equity ?? null,
    }))
  ) || []

  const firstAccountId = accounts[0]?.account_id
  const totalUnrealizedPnl = allPositions.reduce((sum, p) => sum + p.unrealized_pnl, 0)

  // --- Loading skeleton ---
  if (loading && !positionsData) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-muted-foreground">{t('dashboard.loadingData', 'Loading Hyperliquid data...')}</div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4 h-full min-h-0">
      {/* Row 1: Metric Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard
          icon={Wallet}
          title="总资产"
          value={totalEquity}
          prefix="$"
          decimals={2}
        />
        <MetricCard
          icon={TrendingUp}
          title="总盈亏"
          value={totalPnl + totalUnrealizedPnl}
          prefix="$"
          decimals={2}
          colorBySign
          subtitle={`未实现: $${totalUnrealizedPnl.toFixed(2)}`}
        />
        <MetricCard
          icon={Target}
          title="胜率"
          value={winRate}
          suffix="%"
          decimals={1}
          subtitle={`基于交易统计`}
        />
        <MetricCard
          icon={BarChart3}
          title="活跃持仓"
          value={allPositions.length}
          decimals={0}
          subtitle={accounts.length > 0 ? `${accounts.length} 个账户` : undefined}
        />
      </div>

      {/* Row 2: Chart + Positions Table */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 flex-1 min-h-0" style={{ minHeight: '320px' }}>
        <div className="lg:col-span-2 min-h-[300px]">
          {positionsData?.accounts?.length > 0 ? (
            <HyperliquidAssetChart
              accountId={firstAccountId}
              refreshTrigger={chartRefreshKey}
              environment={environment}
              selectedAccount="all"
              trades={tradeMarkers}
            />
          ) : (
            <div className="bg-card border border-border rounded-lg h-full flex items-center justify-center">
              <div className="text-muted-foreground">
                {t('dashboard.noAccountConfigured', 'No Hyperliquid account configured')}
              </div>
            </div>
          )}
        </div>
        <div className="lg:col-span-1 min-h-[300px]">
          <PositionsTable positions={allPositions} className="h-full" />
        </div>
      </div>

      {/* Row 3: Strategy + Accounts + AI Decisions */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4" style={{ minHeight: '200px' }}>
        <StrategyOverview
          onNavigate={onPageChange}
          className="min-h-[200px]"
        />
        <AccountStatusCard
          accounts={accounts}
          className="min-h-[200px]"
        />
        <RecentDecisions
          onNavigate={onPageChange}
          className="min-h-[200px]"
        />
      </div>
    </div>
  )
}
