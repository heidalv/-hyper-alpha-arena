import { useCallback, useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  getHyperliquidAvailableSymbols,
  getHyperliquidWatchlist,
  updateHyperliquidWatchlist,
  updateBinanceWatchlist,
} from '@/lib/api'
import type { HyperliquidSymbolMeta } from '@/lib/api'
import { formatDateTime } from '@/lib/dateTime'
import { useTranslation } from 'react-i18next'
import { useExchange } from '@/contexts/ExchangeContext'

interface StrategyConfig {
  price_threshold: number
  interval_seconds: number
  enabled: boolean
  last_trigger_at?: string | null
  signal_pool_id?: number | null
  signal_pool_name?: string | null
}

interface SignalPool {
  id: number
  pool_name: string
  signal_ids: number[]
  symbols: string[]
  enabled: boolean
  logic?: string
}

interface GlobalSamplingConfig {
  sampling_interval: number
}

interface StrategyPanelProps {
  accountId: number
  accountName: string
  refreshKey?: number
  accounts?: Array<{ id: number; name: string; model?: string | null }>
  onAccountChange?: (accountId: number) => void
  accountsLoading?: boolean
}

// Use formatDateTime from @/lib/dateTime
function formatTimestamp(value?: string | null): string {
  if (!value) return 'No executions yet'
  return formatDateTime(value, { style: 'short' })
}

export default function StrategyPanel({
  accountId,
  accountName,
  refreshKey,
  accounts,
  onAccountChange,
  accountsLoading = false,
}: StrategyPanelProps) {
  const { t } = useTranslation()
  const { currentExchange } = useExchange()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  // Trader-specific settings
  const [priceThreshold, setPriceThreshold] = useState<string>('1.0')
  const [triggerInterval, setTriggerInterval] = useState<string>('150')
  const [lastTriggerAt, setLastTriggerAt] = useState<string | null>(null)
  const [signalPoolId, setSignalPoolId] = useState<number | null>(null)
  const [signalPools, setSignalPools] = useState<SignalPool[]>([])

  // Global settings
  const [samplingInterval, setSamplingInterval] = useState<string>('18')
  const [availableWatchlistSymbols, setAvailableWatchlistSymbols] = useState<HyperliquidSymbolMeta[]>([])
  const [watchlistSymbols, setWatchlistSymbols] = useState<string[]>([])
  const [watchlistLoading, setWatchlistLoading] = useState(true)
  const [watchlistSaving, setWatchlistSaving] = useState(false)
  const [watchlistError, setWatchlistError] = useState<string | null>(null)
  const [watchlistSuccess, setWatchlistSuccess] = useState<string | null>(null)
  const [maxWatchlistSymbols, setMaxWatchlistSymbols] = useState<number>(10)
  const [customSymbolInput, setCustomSymbolInput] = useState<string>('')

  const resetMessages = useCallback(() => {
    setError(null)
    setSuccess(null)
  }, [])

  const resetWatchlistMessages = useCallback(() => {
    setWatchlistError(null)
    setWatchlistSuccess(null)
  }, [])

  const fetchStrategy = useCallback(async () => {
    setLoading(true)
    resetMessages()
    try {
      // Fetch trader-specific config and signal pools in parallel
      const [strategyResponse, signalsResponse, globalResponse] = await Promise.all([
        fetch(`/api/account/${accountId}/strategy`),
        fetch('/api/signals'),
        fetch('/api/config/global-sampling'),
      ])

      if (strategyResponse.ok) {
        const strategy: StrategyConfig = await strategyResponse.json()
        setPriceThreshold((strategy.price_threshold ?? 1.0).toString())
        setTriggerInterval((strategy.interval_seconds ?? 150).toString())
        setLastTriggerAt(strategy.last_trigger_at ?? null)
        setSignalPoolId(strategy.signal_pool_id ?? null)
      }

      if (signalsResponse.ok) {
        const data = await signalsResponse.json()
        const pools: SignalPool[] = data.pools || []
        // Show all pools: enabled ones first, then disabled (for visibility of bound but disabled pools)
        setSignalPools(pools.sort((a, b) => (b.enabled ? 1 : 0) - (a.enabled ? 1 : 0)))
      }

      if (globalResponse.ok) {
        const globalConfig: GlobalSamplingConfig = await globalResponse.json()
        setSamplingInterval((globalConfig.sampling_interval ?? 18).toString())
      }
    } catch (err) {
      console.error('Failed to load strategy config', err)
      setError(err instanceof Error ? err.message : 'Unable to load strategy configuration.')
    } finally {
      setLoading(false)
    }
  }, [accountId, resetMessages])

  const fetchWatchlistConfig = useCallback(async () => {
    resetWatchlistMessages()
    setWatchlistLoading(true)
    try {
      // Load symbols based on current exchange
      // 注：/api/binance/* 后端路由当前不存在，404 时降级 Hyperliquid，避免面板报错
      if (currentExchange === 'binance') {
        const [availRes, wlRes] = await Promise.all([
          fetch('/api/binance/symbols/available'),
          fetch('/api/binance/symbols/watchlist'),
        ])
        if (!availRes.ok || !wlRes.ok) {
          const [available, watchlist] = await Promise.all([
            getHyperliquidAvailableSymbols(),
            getHyperliquidWatchlist(),
          ])
          setAvailableWatchlistSymbols(available.symbols || [])
          setMaxWatchlistSymbols(watchlist.max_symbols ?? available.max_symbols ?? 10)
          setWatchlistSymbols(watchlist.symbols || [])
          return
        }
        const [available, watchlist] = await Promise.all([availRes.json(), wlRes.json()])
        setAvailableWatchlistSymbols(available.symbols || [])
        setMaxWatchlistSymbols(watchlist.max_symbols ?? available.max_symbols ?? 20)
        setWatchlistSymbols(watchlist.symbols || [])
      } else {
        // Default to Hyperliquid
        const [available, watchlist] = await Promise.all([
          getHyperliquidAvailableSymbols(),
          getHyperliquidWatchlist(),
        ])
        setAvailableWatchlistSymbols(available.symbols || [])
        setMaxWatchlistSymbols(watchlist.max_symbols ?? available.max_symbols ?? 10)
        setWatchlistSymbols(watchlist.symbols || [])
      }
    } catch (err) {
      console.error('Failed to load watchlist', err)
      setWatchlistError(err instanceof Error ? err.message : 'Unable to load watchlist.')
    } finally {
      setWatchlistLoading(false)
    }
  }, [resetWatchlistMessages, currentExchange])
  useEffect(() => {
    fetchStrategy()
  }, [fetchStrategy, refreshKey])

  useEffect(() => {
    fetchWatchlistConfig()
  }, [fetchWatchlistConfig, refreshKey])

  const accountOptions = useMemo(() => {
    if (!accounts || accounts.length === 0) return []
    return accounts.map((account) => ({
      value: account.id.toString(),
      label: `${account.name}${account.model ? ` (${account.model})` : ''}`,
    }))
  }, [accounts])

  const selectedAccountLabel = useMemo(() => {
    const match = accountOptions.find((option) => option.value === accountId.toString())
    return match?.label ?? accountName
  }, [accountOptions, accountId, accountName])

  const watchlistCount = watchlistSymbols.length

  useEffect(() => {
    resetMessages()
  }, [accountId, resetMessages])

  const toggleWatchlistSymbol = useCallback(
    (symbol: string) => {
      const symbolUpper = symbol.toUpperCase()
      resetWatchlistMessages()
      setWatchlistSymbols((prev) => {
        if (prev.includes(symbolUpper)) {
          return prev.filter((entry) => entry !== symbolUpper)
        }
        if (prev.length >= maxWatchlistSymbols) {
          setWatchlistError(`You can monitor up to ${maxWatchlistSymbols} symbols.`)
          return prev
        }
        return [...prev, symbolUpper]
      })
    },
    [maxWatchlistSymbols, resetWatchlistMessages]
  )

  const handleAddCustomSymbol = useCallback(async () => {
    const symbolUpper = customSymbolInput.trim().toUpperCase()
    resetWatchlistMessages()
    
    if (!symbolUpper) {
      setWatchlistError('请输入交易对名称')
      return
    }
    
    if (watchlistSymbols.includes(symbolUpper)) {
      setWatchlistError(`${symbolUpper} 已在监控列表中`)
      return
    }
    
    if (watchlistSymbols.length >= maxWatchlistSymbols) {
      setWatchlistError(`最多可监控 ${maxWatchlistSymbols} 个交易对`)
      return
    }
    
    // 添加到列表
    const updatedSymbols = [...watchlistSymbols, symbolUpper]
    setWatchlistSymbols(updatedSymbols)
    setCustomSymbolInput('')
    
    // 自动保存到后端
    try {
      setWatchlistSaving(true)
      
      let response
      if (currentExchange === 'binance') {
        response = await updateBinanceWatchlist(updatedSymbols)
      } else {
        response = await updateHyperliquidWatchlist(updatedSymbols)
      }
      
      setWatchlistSymbols(response.symbols || [])
      setMaxWatchlistSymbols(response.max_symbols ?? maxWatchlistSymbols)
      setWatchlistSuccess(`已添加 ${symbolUpper} 并保存到监控列表`)
    } catch (err) {
      console.error('Failed to save watchlist', err)
      // 保存失败时回滚
      setWatchlistSymbols(watchlistSymbols)
      setWatchlistError(err instanceof Error ? err.message : '保存失败，请重试')
    } finally {
      setWatchlistSaving(false)
    }
  }, [customSymbolInput, watchlistSymbols, maxWatchlistSymbols, resetWatchlistMessages, currentExchange])

  const handleRemoveSymbol = useCallback(
    (symbol: string) => {
      resetWatchlistMessages()
      setWatchlistSymbols((prev) => prev.filter((s) => s !== symbol))
    },
    [resetWatchlistMessages]
  )

  const handleSaveWatchlist = useCallback(async () => {
    resetWatchlistMessages()
    if (watchlistSymbols.length < 1) {
      setWatchlistError('At least one symbol must be selected.')
      return
    }
    try {
      setWatchlistSaving(true)
      
      // Save watchlist based on current exchange
      let response
      if (currentExchange === 'binance') {
        response = await updateBinanceWatchlist(watchlistSymbols)
      } else {
        // Default to Hyperliquid
        response = await updateHyperliquidWatchlist(watchlistSymbols)
      }
      
      setWatchlistSymbols(response.symbols || [])
      setMaxWatchlistSymbols(response.max_symbols ?? maxWatchlistSymbols)
      setWatchlistSuccess('Watchlist updated successfully.')
    } catch (err) {
      console.error('Failed to update watchlist', err)
      setWatchlistError(err instanceof Error ? err.message : 'Failed to update watchlist.')
    } finally {
      setWatchlistSaving(false)
    }
  }, [watchlistSymbols, maxWatchlistSymbols, resetWatchlistMessages, currentExchange])

  const handleSaveTrader = useCallback(async () => {
    resetMessages()

    const threshold = parseFloat(priceThreshold)
    const interval = parseInt(triggerInterval)

    if (!Number.isFinite(threshold) || threshold <= 0) {
      setError('Price threshold must be a positive number.')
      return
    }

    if (!Number.isInteger(interval) || interval <= 0) {
      setError('Trigger interval must be a positive integer.')
      return
    }

    try {
      setSaving(true)
      const payload = {
        price_threshold: threshold,
        interval_seconds: interval,
        enabled: true,
        trigger_mode: "unified",
        tick_batch_size: 1,
        signal_pool_id: signalPoolId,
      }
      console.log('Frontend saving payload:', payload)
      const response = await fetch(`/api/account/${accountId}/strategy`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })

      if (!response.ok) {
        throw new Error('Failed to save trader configuration')
      }

      const result: StrategyConfig = await response.json()
      setPriceThreshold((result.price_threshold ?? 1.0).toString())
      setTriggerInterval((result.interval_seconds ?? 150).toString())
      setLastTriggerAt(result.last_trigger_at ?? null)
      setSignalPoolId(result.signal_pool_id ?? null)

      setSuccess('Trader configuration saved successfully.')
    } catch (err) {
      console.error('Failed to update trader config', err)
      setError(err instanceof Error ? err.message : 'Failed to save trader configuration.')
    } finally {
      setSaving(false)
    }
  }, [accountId, priceThreshold, triggerInterval, signalPoolId, resetMessages])

  const handleSaveGlobal = useCallback(async () => {
    resetMessages()

    const interval = parseInt(samplingInterval)

    if (!Number.isInteger(interval) || interval <= 0) {
      setError('Sampling interval must be a positive integer.')
      return
    }

    try {
      setSaving(true)
      const response = await fetch('/api/config/global-sampling', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sampling_interval: interval,
        }),
      })

      if (!response.ok) {
        throw new Error('Failed to save global configuration')
      }

      const result: GlobalSamplingConfig = await response.json()
      setSamplingInterval((result.sampling_interval ?? 18).toString())

      setSuccess('Global configuration saved successfully.')
    } catch (err) {
      console.error('Failed to update global config', err)
      setError(err instanceof Error ? err.message : 'Failed to save global configuration.')
    } finally {
      setSaving(false)
    }
  }, [samplingInterval, resetMessages])

  return (
    <Card className="h-full flex flex-col">
      <CardHeader>
        <CardTitle>{t('strategy.title', 'Strategy Configuration')}</CardTitle>
        <CardDescription>
          {currentExchange === 'binance' 
            ? '配置触发参数和币安监控列表'
            : t('strategy.description', '配置触发参数和Hyperliquid监控列表')}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex-1 overflow-hidden">
        <Tabs defaultValue="strategy" className="flex flex-col h-full">
          <TabsList className="grid grid-cols-3 max-w-2xl mb-4">
            <TabsTrigger value="strategy">{t('strategy.aiStrategy', 'AI Strategy')}</TabsTrigger>
            <TabsTrigger value="watchlist">{t('strategy.symbolWatchlist', 'Symbol Watchlist')}</TabsTrigger>
            <TabsTrigger value="global">{t('strategy.globalConfig', 'Global Configuration')}</TabsTrigger>
          </TabsList>
          <TabsContent value="strategy" className="flex-1 overflow-y-auto space-y-6">
            {loading ? (
              <div className="text-sm text-muted-foreground">{t('strategy.loadingStrategy', 'Loading strategy…')}</div>
            ) : (
              <>
            {/* Trader Configuration */}
            <Card className="border-muted">
              <CardHeader className="pb-3">
                <div className="flex justify-between items-start">
                  <div className="flex flex-col space-y-1.5">
                    <CardTitle className="text-base">AI 决策触发配置</CardTitle>
                    <CardDescription className="text-xs">控制 AI 多久分析一次市场并做出决策</CardDescription>
                  </div>
                  <div className="flex flex-col space-y-1">
                    {error && <div className="text-sm text-destructive">{error}</div>}
                    {success && <div className="text-sm text-green-500">{success}</div>}
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <section className="space-y-2">
                  <div className="text-xs text-muted-foreground uppercase tracking-wide">AI 分析间隔（秒）</div>
                  <Input
                    type="number"
                    min={30}
                    step={30}
                    value={triggerInterval}
                    onChange={(event) => {
                      setTriggerInterval(event.target.value)
                      resetMessages()
                    }}
                  />
                  <p className="text-xs text-muted-foreground">AI 每隔多少秒分析一次市场数据并决策（建议 60~300 秒）</p>
                </section>

                <section className="space-y-1 text-sm">
                  <div className="text-xs text-muted-foreground uppercase tracking-wide">上次执行</div>
                  <div className="text-xs">{formatTimestamp(lastTriggerAt)}</div>
                </section>

                {signalPoolId && (
                  <section className="space-y-1">
                    <div className="text-xs text-muted-foreground uppercase tracking-wide">关联信号池</div>
                    <div className="text-xs px-3 py-2 bg-muted/50 rounded-lg border border-border">
                      {signalPools.find(p => p.id === signalPoolId)?.pool_name || `信号池 #${signalPoolId}`}
                      <span className="text-muted-foreground ml-2">（由策略管理模块自动绑定）</span>
                    </div>
                  </section>
                )}

                <Button onClick={handleSaveTrader} disabled={saving} className="w-full">
                  {saving ? '保存中…' : '保存配置'}
                </Button>
              </CardContent>
            </Card>

              </>
            )}
          </TabsContent>
          <TabsContent value="watchlist" className="flex-1 overflow-y-auto space-y-4">
            {watchlistLoading ? (
              <div className="text-sm text-muted-foreground">{t('strategy.loadingWatchlist', 'Loading watchlist…')}</div>
            ) : (
              <div className="flex flex-col gap-4">
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground uppercase tracking-wide">
                  <span>
                    {currentExchange === 'binance' 
                      ? '配置币安监控交易对' 
                      : t('strategy.configureSymbols', '配置Hyperliquid监控交易对')}
                  </span>
                  <span className="text-foreground font-semibold">
                    {watchlistCount} / {maxWatchlistSymbols}
                  </span>
                </div>
                {watchlistError && <div className="text-sm text-destructive">{watchlistError}</div>}
                {watchlistSuccess && <div className="text-sm text-emerald-600">{watchlistSuccess}</div>}
                
                {/* Custom Symbol Input */}
                <div className="border rounded-lg p-4 bg-muted/30">
                  <div className="text-sm font-medium mb-3">自定义添加交易对</div>
                  <div className="flex gap-2">
                    <Input
                      type="text"
                      placeholder={currentExchange === 'binance' ? "输入交易对名称（如 BTCUSDT、ETHUSDT）" : "输入交易对名称（如 BTC、ETH、SOL）"}
                      value={customSymbolInput}
                      onChange={(e) => setCustomSymbolInput(e.target.value)}
                      onKeyPress={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault()
                          handleAddCustomSymbol()
                        }
                      }}
                      className="flex-1"
                    />
                    <Button onClick={handleAddCustomSymbol} variant="outline" disabled={watchlistSaving}>
                      {watchlistSaving ? '保存中...' : '添加'}
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground mt-2">
                    {currentExchange === 'binance' 
                      ? '提示：币安请输入完整格式（如 BTCUSDT、ETHUSDT、SOLUSDT），名称将自动转换为大写。' 
                      : '提示：交易对名称将自动转换为大写。支持添加任意交易对，但请确保名称正确。'
                    }
                  </p>
                </div>

                {/* Current Watchlist */}
                {watchlistSymbols.length > 0 && (
                  <div className="border rounded-lg p-4">
                    <div className="text-sm font-medium mb-3">当前监控列表</div>
                    <div className="flex flex-wrap gap-2">
                      {watchlistSymbols.map((symbol) => (
                        <div
                          key={symbol}
                          className="flex items-center gap-2 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/30 border-2 border-blue-500 rounded-md text-sm shadow-sm"
                        >
                          <span className="font-semibold text-blue-700 dark:text-blue-300">{symbol}</span>
                          <button
                            type="button"
                            onClick={() => handleRemoveSymbol(symbol)}
                            className="text-blue-600 hover:text-red-600 dark:text-blue-400 dark:hover:text-red-400 transition-colors font-bold"
                            title="移除"
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                
                {/* Preset Symbols */}
                {availableWatchlistSymbols.length === 0 ? (
                  <div className="text-sm text-muted-foreground">{t('strategy.noSymbols', 'No tradable symbols available.')}</div>
                ) : (
                  <div>
                    <div className="text-sm font-medium mb-3">预设交易对（点击快速添加）</div>
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                      {availableWatchlistSymbols.map((symbol) => {
                        const active = watchlistSymbols.includes(symbol.symbol)
                        return (
                          <button
                            type="button"
                            key={symbol.symbol}
                            onClick={() => toggleWatchlistSymbol(symbol.symbol)}
                            className={`border-2 rounded-md p-3 text-left transition-all ${
                              active 
                                ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/30 text-foreground shadow-md' 
                                : 'border-border text-foreground hover:bg-accent hover:border-blue-300'
                            }`}
                          >
                            <div className={`text-base ${active ? 'font-bold text-blue-700 dark:text-blue-300' : 'font-semibold'}`}>
                              {symbol.symbol}
                            </div>
                            <div className="text-[11px] text-muted-foreground">
                              {symbol.name || t('strategy.untitled', 'Untitled')}
                            </div>
                            {symbol.type && (
                              <div className="text-[10px] uppercase tracking-wide text-muted-foreground mt-1">{symbol.type}</div>
                            )}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )}
                <Button
                  onClick={handleSaveWatchlist}
                  disabled={watchlistSaving || watchlistLoading}
                  className="self-start"
                >
                  {watchlistSaving ? t('strategy.saving', 'Saving…') : t('strategy.saveWatchlist', 'Save Watchlist')}
                </Button>
              </div>
            )}
          </TabsContent>
          <TabsContent value="global" className="flex-1 overflow-y-auto space-y-4">
            {loading ? (
              <div className="text-sm text-muted-foreground">{t('strategy.loadingConfig', 'Loading configuration…')}</div>
            ) : (
              <Card className="border-muted">
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">{t('strategy.globalConfig', 'Global Configuration')}</CardTitle>
                  <CardDescription className="text-xs">{t('strategy.globalDesc', 'Settings that affect all traders')}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <section className="space-y-2">
                    <div className="text-xs text-muted-foreground uppercase tracking-wide">{t('strategy.samplingInterval', 'Sampling Interval (seconds)')}</div>
                    <Input
                      type="number"
                      min={5}
                      max={60}
                      step={1}
                      value={samplingInterval}
                      onChange={(event) => {
                        setSamplingInterval(event.target.value)
                        resetMessages()
                      }}
                    />
                    <p className="text-xs text-muted-foreground">{t('strategy.samplingHint', 'How often to collect price samples (default: 18s)')}</p>
                  </section>

                  {error && <div className="text-sm text-destructive">{error}</div>}
                  {success && <div className="text-sm text-green-500">{success}</div>}

                  <Button onClick={handleSaveGlobal} disabled={saving} className="w-full">
                    {saving ? t('strategy.saving', 'Saving…') : t('strategy.saveGlobalSettings', 'Save Global Settings')}
                  </Button>
                </CardContent>
              </Card>
            )}
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}
