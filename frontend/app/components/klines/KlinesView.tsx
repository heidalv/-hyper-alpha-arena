import { useState, useEffect, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { Button } from '../ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select'
import TradingViewChart from './TradingViewChart'
import AIAnalysisPanel from './AIAnalysisPanel'
import SymbolManageDialog from './SymbolManageDialog'
import KlineDataHealthPanel from './KlineDataHealthPanel'
import PacmanLoader from '../ui/pacman-loader'
import ExchangeIcon from '../exchange/ExchangeIcon'
import { Settings, ChevronDown, ChevronUp, ArrowLeftRight } from 'lucide-react'
import { useExchange } from '@/contexts/ExchangeContext'
import { formatPrice, formatPercentage, formatVolume } from '@/lib/priceFormat'
import { usePageActive } from '@/hooks/usePageActive'
import { useKlinesWebSocket } from '@/hooks/useKlinesWebSocket'
import { EXCHANGE_DISPLAY_NAMES, ExchangeId } from '@/lib/types/exchange'

interface KlinesViewProps {
  onAccountUpdated?: () => void
}

interface MarketData {
  symbol: string
  price: number
  oracle_price: number
  change24h: number
  volume24h: number
  percentage24h: number
  open_interest: number
  funding_rate: number
}

interface BackfillTask {
  task_id: number
  symbol: string
  status: string
  progress: number
  total_records: number
  collected_records: number
}

interface ExchangeProfile {
  exchange: string
  records: number
  symbols: number
  status: string
}

interface ExchangeQuote {
  exchange: string
  price: number
  timestamp: number
  spread_abs?: number
  spread_pct?: number
}

const INDICATOR_GROUPS = [
  { label: '趋势', items: ['MA5', 'MA10', 'MA20', 'EMA20', 'EMA50', 'EMA100'] },
  { label: '通道', items: ['BOLL', 'KELTNER', 'ICHIMOKU'] },
  { label: '成交量', items: ['VWAP', 'OBV'] },
  { label: '动量', items: ['RSI14', 'RSI7', 'STOCH', 'MACD'] },
  { label: '强弱', items: ['ADX', 'WILLIAMS_R'] },
] as const

const INDICATOR_PRESETS = [
  { key: 'clean', label: '裸K', indicators: [] },
  { key: 'trend', label: '趋势', indicators: ['EMA20', 'EMA50'] },
  { key: 'swing', label: '波段', indicators: ['MA20', 'EMA50', 'RSI14'] },
  { key: 'volatility', label: '波动', indicators: ['EMA20', 'BOLL'] },
] as const

const CHANNEL_INDICATORS = ['BOLL', 'KELTNER', 'ICHIMOKU']

export default function KlinesView({ onAccountUpdated }: KlinesViewProps) {
  const { t } = useTranslation()
  const { currentExchange } = useExchange()
  const [selectedSymbol, setSelectedSymbol] = useState<string>('BTC')
  const [selectedPeriod, setSelectedPeriod] = useState<string>('1m')
  const [watchlistSymbols, setWatchlistSymbols] = useState<string[]>([])
  const [marketData, setMarketData] = useState<MarketData[]>([])
  const [currentTask, setCurrentTask] = useState<BackfillTask | null>(null)
  const [loading, setLoading] = useState(false)
  const [isBrowserVisible, setIsBrowserVisible] = useState(true)
  const pageActive = usePageActive()
  const isPageVisible = isBrowserVisible && pageActive
  const [chartType, setChartType] = useState<'candlestick' | 'line' | 'area'>('candlestick')
  const [selectedIndicators, setSelectedIndicators] = useState<string[]>(['EMA20', 'EMA50'])
  const [chartLoading, setChartLoading] = useState(false)
  const [klinesData, setKlinesData] = useState<any[]>([])
  const [indicatorsData, setIndicatorsData] = useState<Record<string, any>>({})
  const [indicatorLoading, setIndicatorLoading] = useState(false)
  const [selectedFlowIndicators, setSelectedFlowIndicators] = useState<string[]>([])
  const [showSymbolManage, setShowSymbolManage] = useState(false)
  const [wsRefreshKey, setWsRefreshKey] = useState(0)
  const [resonanceData, setResonanceData] = useState<{
    resonance_score: number
    resonance_level: string
    alignment: number
    summary: string
    timeframes: { period: string; trend: string; trend_strength: number }[]
  } | null>(null)
  const [controlsExpanded, setControlsExpanded] = useState(false)
  const [chartExchange, setChartExchange] = useState<ExchangeId>('hyperliquid')
  const [compareExchange, setCompareExchange] = useState<ExchangeId | null>(null)
  const [availableExchanges, setAvailableExchanges] = useState<ExchangeProfile[]>([])
  const [exchangeQuotes, setExchangeQuotes] = useState<ExchangeQuote[]>([])

  // 加载有数据的交易所列表
  useEffect(() => {
    const loadProfiles = async () => {
      try {
        const res = await fetch('/api/market-data-v2/exchange-profiles')
        if (!res.ok) return
        const data = await res.json()
        const profiles: ExchangeProfile[] = (data.profiles || [])
          .filter((p: ExchangeProfile) => p.records > 0)
          .sort((a: ExchangeProfile, b: ExchangeProfile) => b.records - a.records)
        setAvailableExchanges(profiles)
      } catch {}
    }
    loadProfiles()
  }, [])

  // 全局交易所切换时同步到 K 线主图（若该所有数据）
  useEffect(() => {
    const hasData = availableExchanges.some(p => p.exchange === currentExchange)
    if (hasData || currentExchange === 'hyperliquid') {
      setChartExchange(currentExchange)
    }
  }, [currentExchange, availableExchanges])

  // 跨所最新价 / 价差
  useEffect(() => {
    if (!selectedSymbol) return
    const fetchQuotes = async () => {
      try {
        const res = await fetch(`/api/market/exchange-quotes/${selectedSymbol}`)
        if (!res.ok) return
        const data = await res.json()
        setExchangeQuotes(data.quotes || [])
      } catch {}
    }
    fetchQuotes()
    const timer = setInterval(fetchQuotes, 5000)
    return () => clearInterval(timer)
  }, [selectedSymbol])

  const exchangeOptions = useMemo(() => {
    const ids = new Set<string>(['hyperliquid'])
    availableExchanges.forEach(p => ids.add(p.exchange))
    return Array.from(ids).sort()
  }, [availableExchanges])

  const compareOptions = useMemo(
    () => exchangeOptions.filter(ex => ex !== chartExchange),
    [exchangeOptions, chartExchange]
  )

  const primaryQuote = useMemo(
    () => exchangeQuotes.find(q => q.exchange === chartExchange),
    [exchangeQuotes, chartExchange]
  )

  const compareQuote = useMemo(
    () => (compareExchange ? exchangeQuotes.find(q => q.exchange === compareExchange) : null),
    [exchangeQuotes, compareExchange]
  )

  const getExchangeLabel = (id: string) =>
    EXCHANGE_DISPLAY_NAMES[id as ExchangeId] || id

  const handleChartExchangeChange = (value: string) => {
    const next = value as ExchangeId
    setChartExchange(next)
    if (compareExchange === next) {
      setCompareExchange(null)
    }
  }

  const marketDataIntervalRef = useRef<NodeJS.Timeout | null>(null)

  // WebSocket 实时 K 线订阅（HTTP 轮询作为 fallback）
  const { latestUpdate, connected: wsConnected, resubscribe } = useKlinesWebSocket(
    selectedSymbol,
    selectedPeriod
  )

  // 当 WS 推送新数据时触发图表刷新
  useEffect(() => {
    if (latestUpdate) {
      setWsRefreshKey(k => k + 1)
    }
  }, [latestUpdate])

  // 监听来自 Win95Ticker 的跳转事件，自动切换选中交易对
  useEffect(() => {
    const handleNavigate = (e: Event) => {
      const { symbol } = (e as CustomEvent<{ symbol: string }>).detail
      if (symbol) {
        setSelectedSymbol(symbol)
      }
    }
    window.addEventListener('klines:navigate', handleNavigate)
    return () => window.removeEventListener('klines:navigate', handleNavigate)
  }, [])

  // 页面可见性监听
  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsBrowserVisible(!document.hidden)
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  // 获取 watchlist 和初始任务检查 - 当交易所切换时重新加载
  useEffect(() => {
    fetchWatchlist()
    checkCurrentTask() // 初始检查一次是否有任务
  }, [currentExchange])

  // 获取市场数据 + 任务状态轮询（合并为单一 interval）
  useEffect(() => {
    if (watchlistSymbols.length > 0 && isPageVisible) {
      fetchMarketData()
      marketDataIntervalRef.current = setInterval(() => {
        fetchMarketData()
        if (currentTask) checkCurrentTask()
      }, 15000)
    }

    return () => {
      if (marketDataIntervalRef.current) {
        clearInterval(marketDataIntervalRef.current)
        marketDataIntervalRef.current = null
      }
    }
  }, [watchlistSymbols, isPageVisible, currentTask, chartExchange])

  // 组件卸载时清理所有定时器
  useEffect(() => {
    return () => {
      if (marketDataIntervalRef.current) {
        clearInterval(marketDataIntervalRef.current)
      }
    }
  }, [])

  // 多周期共振分析
  useEffect(() => {
    if (!selectedSymbol) return
    const fetchResonance = async () => {
      try {
        const res = await fetch(`/api/klines/resonance/${selectedSymbol}`)
        if (res.ok) {
          const data = await res.json()
          setResonanceData(data)
        }
      } catch {}
    }
    fetchResonance()
  }, [selectedSymbol])

  const fetchWatchlist = async () => {
    try {
      // Load watchlist based on current exchange
      // 注：/api/binance/* 后端路由当前不存在（binance_routes.py 缺失），
      // 404 时降级到 Hyperliquid watchlist，避免 K 线页面空白
      let response
      if (currentExchange === 'binance') {
        response = await fetch('/api/binance/symbols/watchlist')
        if (!response.ok) {
          response = await fetch('/api/hyperliquid/symbols/watchlist')
        }
      } else {
        // Default to Hyperliquid
        response = await fetch('/api/hyperliquid/symbols/watchlist')
      }
      if (!response.ok) return

      const data = await response.json()
      let symbols = data.symbols || []

      // For Binance, remove USDT suffix for display
      if (currentExchange === 'binance') {
        symbols = symbols.map((s: string) => s.replace('USDT', ''))
      }

      setWatchlistSymbols(symbols)

      // Auto-select first symbol if current selection is not in the list
      if (symbols.length > 0 && !symbols.includes(selectedSymbol)) {
        setSelectedSymbol(symbols[0])
      }
    } catch (error) {
      console.error('Failed to fetch watchlist:', error)
    }
  }

  const fetchMarketData = async () => {
    try {
      const symbolsParam = watchlistSymbols.join(',')
      if (!symbolsParam) return

      // 根据所选交易所传递 market 参数
      const marketParam = chartExchange
      const response = await fetch(`/api/market/prices?symbols=${symbolsParam}&market=${marketParam}`)
      if (!response.ok) return

      const data = await response.json()
      const formattedData = data.map((item: any) => ({
        symbol: item.symbol,
        price: item.price || 0,
        oracle_price: item.oracle_price || 0,
        change24h: item.change24h || 0,
        volume24h: item.volume24h || 0,
        percentage24h: item.percentage24h || 0,
        open_interest: item.open_interest || 0,
        funding_rate: item.funding_rate || 0
      }))
      setMarketData(formattedData)
    } catch (error) {
      console.error('Failed to fetch market data:', error)
    }
  }

  const checkCurrentTask = async () => {
    try {
      const response = await fetch('/api/klines/backfill-tasks')
      const data = await response.json()
      const tasks = data.tasks || []

      // 找到正在运行或等待的任务
      const activeTask = tasks.find((t: BackfillTask) =>
        t.status === 'running' || t.status === 'pending'
      )

      if (activeTask) {
        setCurrentTask(activeTask)
      } else {
        // 检查是否有刚完成的任务需要删除
        const completedTask = tasks.find((t: BackfillTask) => t.status === 'completed')
        if (completedTask && currentTask?.task_id === completedTask.task_id) {
          // 删除已完成的任务
          await fetch(`/api/klines/backfill-tasks/${completedTask.task_id}`, {
            method: 'DELETE'
          }).catch(() => {}) // 忽略删除错误
        }
        setCurrentTask(null)
      }
    } catch (error) {
      console.error('Failed to check task status:', error)
    }
  }

  const handleBackfill = async () => {
    if (!selectedSymbol || loading || currentTask) return

    setLoading(true)
    try {
      const endTime = new Date()
      const startTime = new Date()
      startTime.setDate(startTime.getDate() - 30)

      const response = await fetch('/api/klines/backfill', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbols: [selectedSymbol],
          start_time: startTime.toISOString(),
          end_time: endTime.toISOString(),
          period: '1m'
        })
      })

      if (response.ok) {
        // 立即检查任务状态
        setTimeout(checkCurrentTask, 500)
      }
    } catch (error) {
      console.error('Failed to start backfill:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleMultiPeriodBackfill = async (periods: string[]) => {
    if (!selectedSymbol || loading || currentTask) return
    if (periods.length === 0) return

    setLoading(true)
    try {
      const endTime = new Date()
      const startTime = new Date()
      startTime.setDate(startTime.getDate() - 30)

      const response = await fetch('/api/klines/backfill', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbols: [selectedSymbol],
          start_time: startTime.toISOString(),
          end_time: endTime.toISOString(),
          period: periods[0],
          periods: periods,
        })
      })

      if (response.ok) {
        setTimeout(checkCurrentTask, 500)
      }
    } catch (error) {
      console.error('Failed to start multi-period backfill:', error)
    } finally {
      setLoading(false)
    }
  }

  const getSymbolMarketData = (symbol: string) => {
    return marketData.find(data => data.symbol === symbol)
  }

  const handleSymbolsUpdated = (newSymbols: string[]) => {
    setWatchlistSymbols(newSymbols)
    // 如果当前选择的交易对不在新列表中，选择第一个
    if (newSymbols.length > 0 && !newSymbols.includes(selectedSymbol)) {
      setSelectedSymbol(newSymbols[0])
    }
  }

  const applyIndicatorPreset = (indicators: readonly string[]) => {
    setSelectedIndicators([...indicators])
  }

  const toggleIndicator = (indicator: string) => {
    setSelectedIndicators(prev => {
      if (prev.includes(indicator)) {
        return prev.filter(i => i !== indicator)
      }

      let next = [...prev, indicator]
      if (CHANNEL_INDICATORS.includes(indicator)) {
        next = next.filter(i => !CHANNEL_INDICATORS.includes(i) || i === indicator)
      }
      return next
    })
  }

  const formatCompactNumber = (value: number) => {
    if (!value && value !== 0) return '-'
    const abs = Math.abs(value)
    if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`
    if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
    if (abs >= 1_000) return `${(value / 1_000).toFixed(2)}K`
    return value.toLocaleString()
  }

  // 渲染按钮或进度条
  const renderBackfillButton = () => {
    if (currentTask) {
      const progress = currentTask.progress || 0
      const collected = currentTask.collected_records || 0
      const total = currentTask.total_records || 0

      return (
        <div className="w-full space-y-1">
          <div className="relative w-full h-8 bg-muted rounded-md overflow-hidden">
            {/* 进度条背景 */}
            <div
              className="absolute inset-y-0 left-0 bg-primary/80 transition-all duration-300"
              style={{ width: `${progress}%` }}
            />
            {/* 进度文字 */}
            <div className="absolute inset-0 flex items-center justify-center text-xs font-medium">
              <span className={progress > 50 ? 'text-primary-foreground' : 'text-foreground'}>
                {currentTask.symbol} ({collected}/{total}) {progress}%
              </span>
            </div>
          </div>
          <p className="text-xs text-muted-foreground text-center">
            {t('kline.backfillInProgress', 'Backfilling in progress...')}
          </p>
        </div>
      )
    }

    return (
      <div className="space-y-2">
        <Button
          onClick={handleBackfill}
          disabled={loading}
          className="w-full"
          size="sm"
        >
          {loading ? t('kline.starting', 'Starting...') : t('kline.backfillHistorical', 'Backfill Historical Data')}
        </Button>
        <p className="text-xs text-muted-foreground">
          {t('kline.backfillLast30Days', 'Backfill last 30 days of K-line data')}
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-1 min-h-0 w-full gap-3 overflow-hidden">
      {/* 左侧：紧凑工具栏 + K 线图（图表优先占满剩余高度） */}
      <div className="flex flex-col flex-[7] min-w-0 min-h-0 overflow-hidden">
        {/* 紧凑顶栏：一行完成选币/周期/核心行情 */}
        <Card className="flex-shrink-0">
          <CardContent className="py-2 px-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Select value={selectedSymbol} onValueChange={setSelectedSymbol}>
                <SelectTrigger className="w-[100px] h-8">
                  <SelectValue placeholder={t('kline.selectSymbol', 'Select Symbol')} />
                </SelectTrigger>
                <SelectContent>
                  {watchlistSymbols.map(symbol => (
                    <SelectItem key={symbol} value={symbol}>{symbol}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Button
                variant="outline"
                size="icon"
                className="h-8 w-8"
                onClick={() => setShowSymbolManage(true)}
                title={t('kline.manageWatchlist', 'Manage Watchlist')}
              >
                <Settings className="w-3.5 h-3.5" />
              </Button>

              <Select value={selectedPeriod} onValueChange={setSelectedPeriod}>
                <SelectTrigger className="w-[72px] h-8">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {['1m','3m','5m','15m','30m','1h','2h','4h','8h','12h','1d','3d','1w','1M'].map(p => (
                    <SelectItem key={p} value={p}>{p}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select value={chartExchange} onValueChange={handleChartExchangeChange}>
                <SelectTrigger className="w-[130px] h-8">
                  <SelectValue placeholder="交易所" />
                </SelectTrigger>
                <SelectContent>
                  {exchangeOptions.map(ex => (
                    <SelectItem key={ex} value={ex}>
                      <span className="flex items-center gap-1.5">
                        <ExchangeIcon exchangeId={ex as ExchangeId} size={14} />
                        {getExchangeLabel(ex)}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select
                value={compareExchange || 'none'}
                onValueChange={(v) => setCompareExchange(v === 'none' ? null : v as ExchangeId)}
              >
                <SelectTrigger className="w-[120px] h-8">
                  <span className="flex items-center gap-1 text-xs">
                    <ArrowLeftRight className="w-3 h-3" />
                    <SelectValue placeholder="对比" />
                  </span>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">不对比</SelectItem>
                  {compareOptions.map(ex => (
                    <SelectItem key={ex} value={ex}>
                      <span className="flex items-center gap-1.5">
                        <ExchangeIcon exchangeId={ex as ExchangeId} size={14} />
                        {getExchangeLabel(ex)}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {compareExchange && primaryQuote && compareQuote && (
                <span
                  className={`text-[11px] px-1.5 py-0.5 rounded border ${
                    (compareQuote.price - primaryQuote.price) >= 0
                      ? 'text-green-600 border-green-500/30 bg-green-500/10'
                      : 'text-red-600 border-red-500/30 bg-red-500/10'
                  }`}
                  title={`${getExchangeLabel(chartExchange)} vs ${getExchangeLabel(compareExchange)}`}
                >
                  价差 {((compareQuote.price - primaryQuote.price) / primaryQuote.price * 100).toFixed(3)}%
                </span>
              )}

              <div className="hidden sm:block w-px h-5 bg-border mx-1" />

              {selectedSymbol && (() => {
                const data = getSymbolMarketData(selectedSymbol)
                const displayPrice = primaryQuote?.price ?? data?.price
                if (!displayPrice && !data) {
                  return (
                    <span className="text-xs text-muted-foreground flex items-center gap-1">
                      <PacmanLoader className="w-8 h-4" />
                      {t('common.loading', 'Loading...')}
                    </span>
                  )
                }
                return (
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                    <span className="flex items-center gap-1 text-muted-foreground">
                      <ExchangeIcon exchangeId={chartExchange} size={12} />
                      {getExchangeLabel(chartExchange)}
                    </span>
                    <span>
                      <span className="text-muted-foreground mr-1">{t('kline.markPrice', 'Mark')}</span>
                      <span className="font-semibold">{formatPrice(displayPrice || 0, selectedSymbol)}</span>
                    </span>
                    {data && (
                      <span className={data.change24h >= 0 ? 'text-green-600 font-medium' : 'text-red-600 font-medium'}>
                        {formatPercentage(data.percentage24h)}
                      </span>
                    )}
                    {chartExchange === 'hyperliquid' && data && (
                      <>
                        <span>
                          <span className="text-muted-foreground mr-1">{t('kline.volume24h', 'Vol')}</span>
                          ${formatVolume(data.volume24h)}
                        </span>
                        <span>
                          <span className="text-muted-foreground mr-1">OI</span>
                          ${formatVolume(data.open_interest)}
                        </span>
                        <span>
                          <span className="text-muted-foreground mr-1">{t('kline.fundingRate', 'Fund')}</span>
                          {formatPercentage(data.funding_rate * 100, 4)}
                        </span>
                      </>
                    )}
                  </div>
                )
              })()}

              <div className="flex-1" />

              <Button
                variant="outline"
                size="sm"
                className="h-8 text-xs gap-1"
                onClick={() => setControlsExpanded(v => !v)}
              >
                {controlsExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                指标与工具
                {selectedIndicators.length > 0 && (
                  <span className="text-[10px] bg-primary/15 text-primary px-1.5 rounded">{selectedIndicators.length}</span>
                )}
              </Button>
            </div>

            {exchangeQuotes.length > 1 && (
              <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                <span className="text-[10px] text-muted-foreground mr-1">各所价格</span>
                {exchangeQuotes.map(q => (
                  <button
                    key={q.exchange}
                    type="button"
                    onClick={() => handleChartExchangeChange(q.exchange)}
                    className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border transition-colors ${
                      q.exchange === chartExchange
                        ? 'bg-primary/15 border-primary/40 text-primary'
                        : 'hover:bg-muted border-border'
                    }`}
                  >
                    <ExchangeIcon exchangeId={q.exchange as ExchangeId} size={10} />
                    {getExchangeLabel(q.exchange).split(' ')[0]}
                    <span className="font-medium">{formatPrice(q.price, selectedSymbol)}</span>
                    {q.spread_pct != null && q.exchange !== chartExchange && (
                      <span className={q.spread_pct >= 0 ? 'text-green-600' : 'text-red-600'}>
                        {q.spread_pct >= 0 ? '+' : ''}{q.spread_pct.toFixed(2)}%
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}

            {controlsExpanded && (
              <div className="space-y-2 border-t pt-2">
                <p className="text-[11px] text-amber-600 font-medium flex items-center gap-1">
                  <span>⚠️</span>
                  <span>{t('kline.mainnetWarning', 'K-line analysis is only available for Mainnet environment')}</span>
                </p>

                <div className="flex flex-wrap items-start gap-4">
                  <div className="min-w-[160px]">{selectedSymbol && renderBackfillButton()}</div>

                  <div className="flex-1 min-w-[200px] space-y-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[10px] text-muted-foreground font-medium">{t('kline.flow', 'Flow')}</span>
                      {[
                        { key: 'cvd', label: 'CVD' },
                        { key: 'taker_volume', label: 'Taker Vol' },
                        { key: 'oi', label: 'OI' },
                        { key: 'oi_delta', label: 'OI Δ' },
                        { key: 'funding', label: 'Funding' },
                        { key: 'depth_ratio', label: 'Depth' },
                        { key: 'order_imbalance', label: 'Imbal' },
                      ].map(({ key, label }) => (
                        <button
                          key={key}
                          onClick={() => setSelectedFlowIndicators(prev =>
                            prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
                          )}
                          className={`px-1.5 py-0.5 text-[10px] rounded border transition-colors ${
                            selectedFlowIndicators.includes(key)
                              ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30'
                              : 'hover:bg-muted'
                          }`}
                        >
                          {label}
                        </button>
                      ))}
                    </div>

                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] text-muted-foreground font-medium">
                          {t('kline.technicalIndicators', 'Technical Indicators')}
                        </span>
                        <button
                          onClick={() => applyIndicatorPreset([])}
                          className="text-[10px] text-muted-foreground hover:text-foreground"
                        >
                          清空
                        </button>
                      </div>
                      <div className="flex gap-1 flex-wrap">
                        {INDICATOR_PRESETS.map(preset => {
                          const active = selectedIndicators.length === preset.indicators.length
                            && preset.indicators.every(ind => selectedIndicators.includes(ind))
                          return (
                            <button
                              key={preset.key}
                              onClick={() => applyIndicatorPreset(preset.indicators)}
                              className={`px-2 py-0.5 text-[10px] rounded border ${
                                active ? 'bg-primary text-primary-foreground border-primary' : 'hover:bg-muted'
                              }`}
                            >
                              {preset.label}
                            </button>
                          )
                        })}
                      </div>
                      <div className="flex flex-wrap gap-x-3 gap-y-1">
                        {INDICATOR_GROUPS.map(group => (
                          <div key={group.label} className="flex items-center gap-1 flex-wrap">
                            <span className="text-[9px] text-muted-foreground">{group.label}</span>
                            {group.items.map(indicator => (
                              <button
                                key={indicator}
                                onClick={() => toggleIndicator(indicator)}
                                className={`px-1 py-0.5 text-[9px] rounded border ${
                                  selectedIndicators.includes(indicator)
                                    ? 'bg-primary/15 text-primary border-primary/40'
                                    : 'hover:bg-muted'
                                }`}
                              >
                                {indicator}
                              </button>
                            ))}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* K 线图：flex-1 占满剩余视口，不再用固定 calc 高度 */}
        <Card className="flex-1 min-h-0 mt-2 flex flex-col overflow-hidden">
          <CardHeader className="py-2 px-3 flex-shrink-0">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <CardTitle className="text-sm truncate">
                  {selectedSymbol} · {getExchangeLabel(chartExchange)}
                  {compareExchange ? ` vs ${getExchangeLabel(compareExchange)}` : ''}
                  {' '}{t('kline.chartTitle', 'K-Line Chart')} ({selectedPeriod})
                </CardTitle>
                {resonanceData && (
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0 ${
                    resonanceData.resonance_level === 'strong_bullish' ? 'bg-green-500/20 text-green-400' :
                    resonanceData.resonance_level === 'bullish' ? 'bg-green-500/10 text-green-400' :
                    resonanceData.resonance_level === 'strong_bearish' ? 'bg-red-500/20 text-red-400' :
                    resonanceData.resonance_level === 'bearish' ? 'bg-red-500/10 text-red-400' :
                    'bg-yellow-500/10 text-yellow-400'
                  }`}
                  title={resonanceData.summary}>
                    共振 {resonanceData.resonance_score > 0 ? '+' : ''}{resonanceData.resonance_score.toFixed(0)}
                  </span>
                )}
                {chartLoading && (
                  <PacmanLoader className="w-8 h-4 shrink-0" />
                )}
              </div>
              <div className="flex gap-0.5 bg-muted/50 rounded p-0.5 shrink-0">
                {(['candlestick', 'line', 'area'] as const).map(type => (
                  <button
                    key={type}
                    onClick={() => setChartType(type)}
                    className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                      chartType === type ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                    }`}
                  >
                    {t(`kline.${type}`, type === 'candlestick' ? 'Candlestick' : type === 'line' ? 'Line' : 'Area')}
                  </button>
                ))}
              </div>
            </div>
          </CardHeader>
          {selectedSymbol && (
            <KlineDataHealthPanel
              symbol={selectedSymbol}
              period={selectedPeriod}
              onBackfillMultiPeriod={handleMultiPeriodBackfill}
            />
          )}
          <CardContent className="flex-1 min-h-0 p-0 relative overflow-hidden flex flex-col">
            <div className="relative flex-1 min-h-[280px]">
            <TradingViewChart
              symbol={selectedSymbol}
              period={selectedPeriod}
              chartType={chartType}
              selectedIndicators={selectedIndicators}
              selectedFlowIndicators={selectedFlowIndicators}
              wsRefreshKey={wsRefreshKey}
              onLoadingChange={setChartLoading}
              onIndicatorLoadingChange={setIndicatorLoading}
              market={chartExchange}
              compareMarket={compareExchange ?? undefined}
              onDataUpdate={(klines, indicators) => {
                setKlinesData(klines || [])
                setIndicatorsData(indicators || {})
              }}
            />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 右侧：AI 分析 */}
      <div className="flex flex-col flex-[3] min-w-[280px] max-w-[360px] min-h-0 overflow-hidden">
        <Card className="flex-1 min-h-0 flex flex-col overflow-hidden">
          <CardHeader className="py-2 px-3 flex-shrink-0">
            <CardTitle className="text-sm">{t('kline.aiAnalysis', 'AI Analysis')}</CardTitle>
          </CardHeader>
          <CardContent className="flex-1 min-h-0 pt-0 overflow-y-auto">
            <AIAnalysisPanel
              symbol={selectedSymbol}
              period={selectedPeriod}
              klines={klinesData}
              indicators={indicatorsData}
              marketData={getSymbolMarketData(selectedSymbol)}
              selectedIndicators={selectedIndicators}
              selectedFlowIndicators={selectedFlowIndicators}
              onAnalysisComplete={() => {}}
            />
          </CardContent>
        </Card>
      </div>

      {/* 交易对管理对话框 */}
      <SymbolManageDialog
        open={showSymbolManage}
        onOpenChange={setShowSymbolManage}
        currentSymbols={watchlistSymbols}
        onSymbolsChange={handleSymbolsUpdated}
        exchange={currentExchange === 'binance' ? 'binance' : 'hyperliquid'}
      />
    </div>
  )
}
