import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { Button } from '../ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select'
import { Textarea } from '../ui/textarea'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../ui/dialog'
import ReactMarkdown from 'react-markdown'
import PacmanLoader from '../ui/pacman-loader'
import { Badge } from '../ui/badge'
import { getAccounts } from '@/lib/api'
import { getBinanceConfig, getBinancePositions } from '@/lib/binanceApi'

interface AITrader {
  id: number
  name: string
  model: string
  is_active: boolean | string
}

interface BinanceAccountOption {
  account_id: number
  account_name: string
  market_type: 'spot' | 'futures'
  testnet: boolean
}

interface PositionItem {
  symbol?: string
  size?: number
  entry_price?: number
  mark_price?: number
  position_value?: number
  liquidation_price?: number
  side?: string
  leverage?: number
  unrealized_pnl?: number
  pnl_percentage?: number
}

interface AIAnalysisPanelProps {
  symbol: string
  period: string
  klines: any[]
  indicators: Record<string, any>
  marketData: any
  selectedIndicators?: string[]
  selectedFlowIndicators?: string[]
  onAnalysisComplete?: () => void
  // 允许上层传入账户列表，暂无使用，预留扩展
  accounts?: AITrader[]
}

// Flow indicator key to display label mapping
const FLOW_INDICATOR_LABELS: Record<string, string> = {
  cvd: 'CVD',
  taker_volume: 'Taker Vol',
  oi: 'OI',
  oi_delta: 'OI Delta',
  funding: 'Funding',
  depth_ratio: 'Depth',
  order_imbalance: 'Imbalance',
}

interface AnalysisResult {
  success: boolean
  analysis_id?: number
  symbol?: string
  period?: string
  model?: string
  trader_name?: string
  analysis?: string
  created_at?: string
  prompt?: string
  error?: string
}

export default function AIAnalysisPanel({
  symbol,
  period,
  klines,
  indicators,
  marketData,
  selectedIndicators = [],
  selectedFlowIndicators = [],
  onAnalysisComplete
}: AIAnalysisPanelProps) {
  const { t } = useTranslation()
  const [selectedTrader, setSelectedTrader] = useState<string>('')
  const [userMessage, setUserMessage] = useState<string>('')
  const [traders, setTraders] = useState<AITrader[]>([])
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [showFullAnalysis, setShowFullAnalysis] = useState(false)
  const [tradersLoaded, setTradersLoaded] = useState(false)
  const [tradersLoading, setTradersLoading] = useState(false)
  const [klineLimit, setKlineLimit] = useState<number>(100)
  const [selectedAccount, setSelectedAccount] = useState<BinanceAccountOption | null>(null)
  const [binanceAccounts, setBinanceAccounts] = useState<BinanceAccountOption[]>([])
  const [accountsLoading, setAccountsLoading] = useState(false)
  const [positions, setPositions] = useState<PositionItem[]>([])
  const [positionsLoading, setPositionsLoading] = useState(false)
  const [indicatorLoading, setIndicatorLoading] = useState(false)
  const [showPrompt, setShowPrompt] = useState(false)

  // Fetch AI Traders list
  const fetchTraders = async () => {
    if (tradersLoaded) return

    try {
      setTradersLoading(true)
      // Use public account list endpoint (no auth cookie required)
      const response = await fetch('/api/account/list')
      const data = await response.json()
      const accounts: any[] = Array.isArray(data)
        ? data
        : Array.isArray((data as any)?.accounts)
          ? (data as any).accounts
          : []

      const aiTraders = accounts.filter((acc: any) => {
        const isActive = acc.is_active === true || acc.is_active === 'true'
        return acc.account_type === 'AI' && isActive
      }) || []
      setTraders(aiTraders)
      setTradersLoaded(true)
    } catch (error) {
      console.error('Failed to fetch AI traders:', error)
    } finally {
      setTradersLoading(false)
    }
  }

  // 预加载 trader 列表，避免首次打开等待
  useEffect(() => {
    fetchTraders()
  }, [])

  // 加载币安账户列表
  useEffect(() => {
    const loadBinanceAccounts = async () => {
      try {
        setAccountsLoading(true)
        const accountList = await getAccounts()
        
        // 获取所有配置了币安的账户
        const accountsWithBinance = await Promise.all(
          accountList.map(async (account: any) => {
            try {
              const config = await getBinanceConfig(account.id)
              if (config.configured) {
                return {
                  account_id: account.id,
                  account_name: account.name,
                  market_type: config.market_type || config.marketType,
                  testnet: config.testnet
                }
              }
            } catch (e) {
              return null
            }
            return null
          })
        )
        
        const validAccounts = accountsWithBinance.filter(Boolean) as BinanceAccountOption[]
        setBinanceAccounts(validAccounts)
      } catch (error) {
        console.error('Failed to load Binance accounts:', error)
      } finally {
        setAccountsLoading(false)
      }
    }
    
    loadBinanceAccounts()
  }, [])

  // 加载选中账户的持仓
  useEffect(() => {
    const loadPositions = async () => {
      if (!selectedAccount) {
        setPositions([])
        return
      }
      try {
        setPositionsLoading(true)
        const data = await getBinancePositions(selectedAccount.account_id)
        const mapped = (data.positions || []).map((p: any) => ({
          symbol: p.symbol || '',
          size: Math.abs(p.size || 0),
          entry_price: p.entry_price || null,
          mark_price: p.mark_price || null,
          position_value: p.notional || null,
          liquidation_price: p.liquidation_price || null,
          side: p.side || '',
          leverage: p.leverage ?? null,
          unrealized_pnl: p.unrealized_pnl ?? null,
          pnl_percentage: p.unrealized_pnl && p.notional
            ? (p.unrealized_pnl / (p.notional - p.unrealized_pnl)) * 100
            : null,
        }))
        setPositions(mapped)
      } catch (err) {
        console.error('Failed to load positions:', err)
        setPositions([])
      } finally {
        setPositionsLoading(false)
      }
    }
    loadPositions()
  }, [selectedAccount])

  // Execute AI Analysis
  const handleAnalyze = async () => {
    if (!selectedTrader || !symbol || !klines.length || indicatorLoading) return

    setLoading(true)
    setResult(null)

    // Record request start time for later retrieval if connection drops
    const requestStartTime = new Date()

    try {
      const slicedKlines = klines.slice(-klineLimit)

      // 前端计算MA，避免后端未返回时为空
      const computeMA = (data: any[], period: number) => {
        if (!data || data.length < period) return []
        const closes = data.map((k) => Number(k.close || k.c))
        const ma: number[] = []
        for (let i = period - 1; i < closes.length; i++) {
          const slice = closes.slice(i - period + 1, i + 1)
          const avg = slice.reduce((a, b) => a + b, 0) / period
          ma.push(Number.isFinite(avg) ? Number(avg.toFixed(4)) : 0)
        }
        // 与对应的时间对齐：前 period-1 为空，后续有值
        const padded = Array(period - 1).fill(null).concat(ma)
        return padded
      }

      const ma5 = computeMA(slicedKlines, 5)
      const ma10 = computeMA(slicedKlines, 10)
      const ma20 = computeMA(slicedKlines, 20)

      const positionPayload = positions.map((p) => ({
        symbol: p.symbol,
        size: p.size,
        entry_price: p.entry_price,
        mark_price: p.mark_price,
        position_value: p.position_value,
        liquidation_price: p.liquidation_price,
        side: p.side,
        leverage: p.leverage,
        unrealized_pnl: p.unrealized_pnl,
        pnl_percentage: p.pnl_percentage,
      }))

      const marketDataPayload = {
        price: marketData?.price || 0,
        oracle_price: marketData?.oracle_price || 0,
        change24h: marketData?.change24h || 0,
        volume24h: marketData?.volume24h || 0,
        percentage24h: marketData?.percentage24h || 0,
        open_interest: marketData?.open_interest || 0,
        funding_rate: marketData?.funding_rate || 0
      }

      const requestData = {
        account_id: parseInt(selectedTrader),
        symbol,
        period,
        kline_limit: klineLimit,
        klines: slicedKlines.map(k => ({
          time: k.time,
          open: k.open,
          high: k.high,
          low: k.low,
          close: k.close,
          volume: k.volume || 0
        })),
        indicators: {
          // 直接携带现有指标
          ...indicators,
          // 补充前端计算的MA（如果后端未返回）
          ...(indicators?.MA5 && indicators.MA5.length ? {} : { MA5: ma5 }),
          ...(indicators?.MA10 && indicators.MA10.length ? {} : { MA10: ma10 }),
          ...(indicators?.MA20 && indicators.MA20.length ? {} : { MA20: ma20 }),
        },
        market_data: marketDataPayload,
        positions: positionPayload,
        selected_flow_indicators: selectedFlowIndicators,
        user_message: userMessage.trim() || null,
        prompt_snapshot: JSON.stringify({
          symbol,
          period,
          kline_limit: klineLimit,
          indicators: Object.keys(indicators || {}),
          flow_indicators: selectedFlowIndicators,
          positions: positionPayload,
          market_data: marketDataPayload,
          user_message: userMessage.trim() || null
        }, null, 2)
      }

      // Create AbortController with 10-minute timeout for AI analysis
      // Reasoning models (like deepseek-reasoner) can be very slow
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 600000) // 10 minutes

      try {
        const response = await fetch('/api/klines/ai-analysis', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestData),
          signal: controller.signal
        })

        clearTimeout(timeoutId)
        const data = await response.json()
        setResult(data)

        if (data.success && onAnalysisComplete) {
          onAnalysisComplete()
        }
      } catch (fetchError: any) {
        clearTimeout(timeoutId)
        console.error('Analysis failed:', fetchError)

        // If connection was closed but backend might have saved the result, try to retrieve it
        if (fetchError instanceof TypeError && fetchError.message.includes('fetch')) {
          console.log('Connection interrupted, attempting to retrieve saved result...')

          try {
            // Wait 2 seconds for backend to finish saving to database
            await new Promise(resolve => setTimeout(resolve, 2000))

            // Fetch recent analysis history for this symbol (get last 5 to be safe)
            const historyResponse = await fetch(
              `/api/klines/ai-analysis/history?symbol=${symbol}&limit=5`
            )

            if (historyResponse.ok) {
              const historyData = await historyResponse.json()

              // Find the analysis that matches this request by time range, symbol, and period
              if (historyData.history && historyData.history.length > 0) {
                const matchedAnalysis = historyData.history.find((item: any) => {
                  const analysisTime = new Date(item.created_at)
                  const timeDiffMs = analysisTime.getTime() - requestStartTime.getTime()

                  // Check if analysis was created within 3 minutes after request started
                  // and matches symbol + period
                  return (
                    timeDiffMs >= 0 &&
                    timeDiffMs < 180000 && // 3 minutes
                    item.symbol === symbol &&
                    item.period === period
                  )
                })

                if (matchedAnalysis) {
                  // Found the result! Display it
                  console.log('Successfully retrieved saved analysis result:', matchedAnalysis.id)
                  setResult({
                    success: true,
                    analysis_id: matchedAnalysis.id,
                    symbol: matchedAnalysis.symbol,
                    period: matchedAnalysis.period,
                    model: matchedAnalysis.model_used,
                    analysis: matchedAnalysis.analysis,
                    created_at: matchedAnalysis.created_at
                  })
                  return
                }

                console.log('No matching analysis found in history within expected time range')
              }
            }
          } catch (retrieveError) {
            console.error('Failed to retrieve saved result:', retrieveError)
          }
        }

        // Provide more specific error messages
        let errorMessage = 'Network error occurred'
        if (fetchError.name === 'AbortError') {
          errorMessage = 'Analysis timeout (10 minutes). Please try again with fewer K-lines or a simpler question.'
        }

        setResult({
          success: false,
          error: errorMessage
        })
      }
    } catch (error) {
      console.error('Analysis failed:', error)
      setResult({
        success: false,
        error: 'Failed to prepare analysis request'
      })
    } finally {
      setLoading(false)
    }
  }

  // 获取分析摘要（第一段）
  const getAnalysisSummary = (analysis: string) => {
    if (!analysis) return ''

    // 找到第一个 ## 标题后的内容作为摘要
    const lines = analysis.split('\n')
    let summaryLines = []
    let foundFirstSection = false

    for (const line of lines) {
      if (line.startsWith('## ')) {
        if (foundFirstSection) break
        foundFirstSection = true
        summaryLines.push(line)
      } else if (foundFirstSection && line.trim()) {
        summaryLines.push(line)
        if (summaryLines.length >= 5) break // 限制摘要长度
      }
    }

    return summaryLines.join('\n') || analysis.substring(0, 200) + '...'
  }

  return (
    <div className="space-y-3">
      {/* AI Trader Selection */}
      <div>
        <label className="text-xs text-muted-foreground mb-1 block">{t('kline.analysis.aiTrader', 'AI Trader')}</label>
        <Select
          value={selectedTrader}
          onValueChange={setSelectedTrader}
          onOpenChange={(open) => open && fetchTraders()}
        >
          <SelectTrigger>
            <SelectValue placeholder={t('kline.analysis.selectAiTrader', 'Select AI Trader')} />
          </SelectTrigger>
          <SelectContent>
            {tradersLoading && traders.length === 0 && (
              <SelectItem value="loading" disabled>
                {t('kline.analysis.loadingTraders', 'Loading AI Traders...')}
              </SelectItem>
            )}
            {traders.map(trader => (
              <SelectItem key={trader.id} value={trader.id.toString()}>
                {trader.name} ({trader.model})
              </SelectItem>
            ))}
            {!tradersLoading && traders.length === 0 && (
              <SelectItem value="empty" disabled>
                {t('kline.analysis.noTraders', 'No AI Traders found')}
              </SelectItem>
            )}
          </SelectContent>
        </Select>
      </div>

      {/* K-line data length */}
      <div>
        <label className="text-xs text-muted-foreground mb-1 block">{t('kline.analysis.klineLength', 'K-line Data Length')}</label>
        <Select value={klineLimit.toString()} onValueChange={(v) => setKlineLimit(parseInt(v))}>
          <SelectTrigger>
            <SelectValue placeholder={t('kline.analysis.selectLength', 'Select length')} />
          </SelectTrigger>
          <SelectContent>
            {[50, 100, 200, 500].map(len => (
              <SelectItem key={len} value={len.toString()}>
                {t('kline.analysis.lastCandles', 'Last {{count}} candles').replace('{{count}}', len.toString())}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-[11px] text-muted-foreground mt-1">{t('kline.analysis.candlesHint', 'More candles give AI more context (500 may be slower).')}</p>
      </div>

      {/* Selected Indicators hint */}
      <div className="space-y-1">
        <div className="text-xs text-muted-foreground">{t('kline.analysis.indicatorsIncluded', 'Indicators Included')}</div>
        {selectedIndicators.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {selectedIndicators.map((ind) => (
              <Badge key={ind} variant="secondary" className="text-[11px] px-2 py-1">
                {ind}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-muted-foreground">
            {t('kline.analysis.selectIndicatorsHint', 'Select indicators in "Technical Indicators" to include them in AI analysis.')}
          </p>
        )}
      </div>

      {/* Selected Market Flow Indicators hint */}
      <div className="space-y-1">
        <div className="text-xs text-muted-foreground">{t('kline.analysis.flowIncluded', 'Market Flow Included')}</div>
        {selectedFlowIndicators.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {selectedFlowIndicators.map((key) => (
              <Badge key={key} className="text-[11px] px-2 py-1 bg-cyan-500/20 text-cyan-400 border border-cyan-500/30">
                {FLOW_INDICATOR_LABELS[key] || key}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-muted-foreground">
            {t('kline.analysis.selectFlowHint', 'Select indicators in "Market Flow" to include them in AI analysis.')}
          </p>
        )}
      </div>

      {/* 币安账户 & 持仓 */}
      <div className="space-y-2">
        <label className="text-xs text-muted-foreground block">交易账户（用于持仓上下文）</label>
        <Select
          value={selectedAccount?.account_id.toString() || ''}
          onValueChange={(val) => {
            const account = binanceAccounts.find(a => a.account_id.toString() === val)
            setSelectedAccount(account || null)
          }}
        >
          <SelectTrigger>
            <SelectValue placeholder="选择币安账户" />
          </SelectTrigger>
          <SelectContent>
            {accountsLoading && binanceAccounts.length === 0 && (
              <SelectItem value="loading" disabled>
                加载中...
              </SelectItem>
            )}
            {!accountsLoading && binanceAccounts.length === 0 && (
              <SelectItem value="empty" disabled>
                暂无可用的币安账户
              </SelectItem>
            )}
            {binanceAccounts.map(account => (
              <SelectItem key={account.account_id} value={account.account_id.toString()}>
                {account.account_name} ({account.market_type === 'spot' ? '现货' : '合约'})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {selectedAccount && (
          <div className="rounded-md border p-3 space-y-2 bg-muted/40">
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{t('kline.analysis.wallet', '账户')}</span>
              <span className="font-medium">{selectedAccount.account_name} ({selectedAccount.testnet ? '测试网' : '主网'})</span>
            </div>
            <div className="space-y-1 max-h-40 overflow-y-auto">
              {positionsLoading && (
                <div className="text-xs text-muted-foreground flex items-center gap-2">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 38 38" stroke="currentColor" className="w-4 h-4 text-primary">
                    <g fill="none" fillRule="evenodd">
                      <g transform="translate(1 1)" strokeWidth="2">
                        <circle strokeOpacity=".3" cx="18" cy="18" r="18" />
                        <path d="M36 18c0-9.94-8.06-18-18-18">
                          <animateTransform attributeName="transform" type="rotate" from="0 18 18" to="360 18 18" dur="0.8s" repeatCount="indefinite" />
                        </path>
                      </g>
                    </g>
                  </svg>
                  {t('kline.analysis.loadingPositions', '加载持仓中...')}
                </div>
              )}
              {!positionsLoading && positions.length === 0 && (
                <div className="text-xs text-muted-foreground">{t('kline.analysis.noPositions', '暂无持仓')}</div>
              )}
              {!positionsLoading && positions.length > 0 && positions.map((p, idx) => {
                const fallbackSymbol = symbol || 'N/A'
                const displaySymbol = p.symbol || fallbackSymbol
                const side = (p.side || '').toUpperCase()
                const size = p.size ?? '-'
                const value = p.position_value ?? '-'
                const pnl = p.unrealized_pnl ?? '-'
                const pnlPct = p.pnl_percentage ?? null
                const leverage = p.leverage ?? null
                return (
                  <div key={idx} className="text-[11px] border-b last:border-b-0 py-1">
                    <div className="flex justify-between">
                      <span className="font-medium">{displaySymbol}</span>
                      <span className="text-muted-foreground">{side} {size}</span>
                    </div>
                    <div className="flex justify-between text-muted-foreground">
                      <span>Value: {value}</span>
                      <span>{leverage ? `${leverage}x` : ''}</span>
                    </div>
                    <div className="flex justify-between text-muted-foreground">
                      <span>PnL: {pnl}</span>
                      <span>{pnlPct !== null && pnlPct !== undefined ? `(${pnlPct}%)` : ''}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* Custom Question */}
      <div>
        <label className="text-xs text-muted-foreground mb-1 block">{t('kline.analysis.customQuestion', 'Custom Question (Optional)')}</label>
        <Textarea
          placeholder={t('kline.analysis.questionPlaceholder', 'e.g., Should I go long now? Where are the support levels?')}
          value={userMessage}
          onChange={(e) => setUserMessage(e.target.value)}
          rows={3}
          className="text-sm"
        />
      </div>

      {/* Analysis Button */}
      <Button
        onClick={handleAnalyze}
        disabled={!selectedTrader || loading || !klines.length}
        className="w-full"
        size="sm"
      >
        {loading ? (
          <div className="flex items-center gap-2">
            <PacmanLoader className="w-4 h-4" />
            {t('kline.analysis.analyzing', 'Analyzing...')}
          </div>
        ) : (
          t('kline.analysis.aiAnalysis', 'AI Analysis')
        )}
      </Button>

      {/* Analysis Result */}
      {result && (
        <Card className="mt-3">
          <CardHeader className="py-2">
            <CardTitle className="text-sm flex items-center justify-between">
              <span>
                {result.success ? t('kline.analysis.analysisResult', 'Analysis Result') : t('kline.analysis.analysisFailed', 'Analysis Failed')}
                {result.trader_name && ` - ${result.trader_name}`}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="py-2 space-y-3">
            {result.success && result.analysis ? (
              <>
                <div className="prose prose-sm max-w-none">
                  <ReactMarkdown>
                    {getAnalysisSummary(result.analysis)}
                  </ReactMarkdown>
                </div>
                <div className="flex justify-end">
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => setShowFullAnalysis(true)}
                    className="text-xs"
                  >
                    {t('kline.analysis.viewFull', 'View Full Analysis')}
                  </Button>
                </div>
              </>
            ) : (
              <p className="text-sm text-red-600">
                {result.error || t('kline.analysis.analysisFailed', 'Analysis failed')}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Full Analysis Dialog */}
      <Dialog open={showFullAnalysis} onOpenChange={setShowFullAnalysis}>
        <DialogContent
          className="w-[95vw] max-w-[1200px] max-h-[85vh] overflow-y-auto"
          aria-describedby={undefined}
        >
          <DialogHeader>
            <DialogTitle>
              {symbol} {period} {t('kline.analysis.reportTitle', 'AI Analysis Report')}
              {result?.trader_name && ` - ${result.trader_name}`}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="rounded-md border p-4 bg-background">
              <div className="prose prose-sm md:prose-base max-w-none break-words">
                <ReactMarkdown>
                  {result?.analysis || ''}
                </ReactMarkdown>
              </div>
            </div>
            {result?.prompt && (
              <div className="rounded-md border bg-muted/50 p-3">
                <div className="flex items-center justify-between">
                  <div className="text-xs font-semibold text-muted-foreground">{t('kline.analysis.userPrompt', 'User Prompt')}</div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-xs"
                    onClick={() => setShowPrompt(!showPrompt)}
                  >
                    {showPrompt ? t('kline.analysis.hidePrompt', 'Hide') : t('kline.analysis.showPrompt', 'Show')} {t('kline.analysis.prompt', 'Prompt')}
                  </Button>
                </div>
                {showPrompt && (
                  <div className="mt-2 max-h-60 overflow-auto rounded border bg-background p-2">
                    <pre className="whitespace-pre-wrap text-[11px] text-foreground break-words">{result.prompt}</pre>
                  </div>
                )}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
