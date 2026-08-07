'use client'

import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'react-hot-toast'
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  Sparkles,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  Minus,
  Target,
  Zap,
  LineChart,
  Activity,
  AlertTriangle,
  CheckCircle2,
  Info,
  Copy,
  Download,
  Plus,
  Loader2,
  ArrowUpRight,
  ArrowDownRight,
  Settings,
  Eye,
  Brain,
  Bot,
} from 'lucide-react'

// ==================== Types ====================

interface MarketRegime {
  type: string
  direction: string
  confidence: number
  indicators?: {
    cvd_ratio: number
    oi_delta: number
    taker_ratio: number
    price_atr: number
    rsi: number
  }
  debug?: {
    taker_buy: number
    taker_sell: number
    total_notional: number
  }
}

interface AdaptiveParameters {
  position_size_modifier: number
  stop_loss_atr_multiple: number
  take_profit_ratio: number
  entry_confirmation_count: number
  max_position_percent: number
  trailing_stop_enabled: boolean
  regime_type: string
  regime_direction: string
  regime_confidence: number
}

interface MarketAnalysis {
  symbol: string
  regime: MarketRegime
  adaptive_params: AdaptiveParameters
  multi_timeframe?: {
    consensus: any
    timeframes: Record<string, any>
    recommendation: string
  }
}

interface DetectedPattern {
  symbol: string
  pattern_name: string
  direction: string
  confidence: number
  historical_win_rate: number
  triggered_conditions: any[]
}

interface GeneratedSignal {
  signal_name: string
  symbol: string
  description: string
  direction: string
  trigger_conditions: any[]
  backtest_metrics: {
    win_rate: number
    avg_return: number
    sharpe_ratio: number
    total_trades: number
    max_drawdown: number
    effectiveness_score: number
  }
  recommended_position_size: number
  recommended_stop_loss: number
  recommended_take_profit: number
  ai_prompt_template?: string  // AI提示词模板
}

interface StrategyStyle {
  id: string
  name: string
  description: string
  best_regimes: string[]
  risk_profile: string
}

interface AITrader {
  id: number
  name: string
  model: string
  account_type: string
}

// ==================== Constants ====================

const RISK_LEVELS = [
  { value: 'conservative', label: '保守', labelEn: 'Conservative', color: 'text-blue-500' },
  { value: 'moderate', label: '稳健', labelEn: 'Moderate', color: 'text-green-500' },
  { value: 'aggressive', label: '激进', labelEn: 'Aggressive', color: 'text-orange-500' },
]

const REGIME_COLORS: Record<string, string> = {
  breakout: 'bg-green-500/20 text-green-500 border-green-500/30',
  continuation: 'bg-emerald-500/20 text-emerald-500 border-emerald-500/30',
  absorption: 'bg-yellow-500/20 text-yellow-500 border-yellow-500/30',
  exhaustion: 'bg-orange-500/20 text-orange-500 border-orange-500/30',
  trap: 'bg-red-500/20 text-red-500 border-red-500/30',
  stop_hunt: 'bg-purple-500/20 text-purple-500 border-purple-500/30',
  noise: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
}

const REGIME_LABELS: Record<string, { zh: string; en: string }> = {
  breakout: { zh: '突破', en: 'Breakout' },
  continuation: { zh: '延续', en: 'Continuation' },
  absorption: { zh: '吸收', en: 'Absorption' },
  exhaustion: { zh: '衰竭', en: 'Exhaustion' },
  trap: { zh: '陷阱', en: 'Trap' },
  stop_hunt: { zh: '扫损', en: 'Stop Hunt' },
  noise: { zh: '噪音', en: 'Noise' },
}

// ==================== API Functions ====================

async function fetchMarketAnalysis(symbol: string, period: string = '5m'): Promise<any> {
  const res = await fetch(`/api/smart-signals/market-analysis/${symbol}?period=${period}`)
  if (!res.ok) throw new Error('Failed to fetch market analysis')
  return res.json()
}

async function fetchRegimeInfo(symbol: string): Promise<any> {
  const res = await fetch(`/api/smart-signals/regime/${symbol}`)
  if (!res.ok) throw new Error('Failed to fetch regime info')
  return res.json()
}

async function fetchAdaptiveParameters(symbol: string): Promise<any> {
  const res = await fetch(`/api/prompts/adaptive-parameters/${symbol}`)
  if (!res.ok) throw new Error('Failed to fetch adaptive parameters')
  return res.json()
}

async function fetchStrategyStyles(): Promise<StrategyStyle[]> {
  const res = await fetch('/api/prompts/strategy-styles')
  if (!res.ok) throw new Error('Failed to fetch strategy styles')
  const data = await res.json()
  return data.styles || []
}

async function generateOptimalSignal(params: {
  symbol: string
  direction: string
  risk_level: string
  time_window: string
  strategy_type?: string
  lookback_days?: number  // 新增参数：历史数据天数
}): Promise<GeneratedSignal> {
  const res = await fetch('/api/smart-signals/generate-optimal-signal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}))
    throw new Error(errorData?.detail || errorData?.message || 'Failed to generate signal')
  }
  const data = await res.json()
  const signal = data.signal || data
  
  // Safely extract trigger conditions with multiple fallbacks
  const extractConditions = () => {
    const tc = signal.trigger_condition
    if (!tc) return []
    // If conditions array exists, use it
    if (Array.isArray(tc.conditions)) return tc.conditions
    // If single condition object, wrap in array
    if (tc.metric) return [tc]
    return []
  }
  
  const conditions = extractConditions()
  
  // Build trigger_condition for backtest API (use first condition)
  const triggerCondition = signal.trigger_condition || {}
  
  // Call the signal system's backtest API for real performance metrics
  let backtestMetrics = {
    win_rate: signal.backtest_metrics?.win_rate ?? 0,
    avg_return: signal.backtest_metrics?.avg_return_percent ?? signal.backtest_metrics?.avg_return ?? 0,
    sharpe_ratio: signal.backtest_metrics?.sharpe_ratio ?? 0,
    total_trades: signal.backtest_metrics?.total_triggers ?? signal.backtest_metrics?.total_trades ?? 0,
    max_drawdown: signal.backtest_metrics?.max_drawdown_percent ?? signal.backtest_metrics?.max_drawdown ?? 0,
    effectiveness_score: signal.effectiveness_score ?? 0,
  }
  
  try {
    // Use signal system's backtest-performance-preview API for real backtest
    const backtestRes = await fetch('/api/signals/backtest-performance-preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: params.symbol,
        triggerCondition: triggerCondition,
        days: params.lookback_days || 14,  // 使用相同的天数
        config: {
          position_size_usd: 1000,
          take_profit_percent: signal.recommended_take_profit_percent ?? 3.0,
          stop_loss_percent: signal.recommended_stop_loss_percent ?? 1.5,
          max_hold_bars: 20,
        }
      }),
    })
    
    if (backtestRes.ok) {
      const backtestData = await backtestRes.json()
      const summary = backtestData.summary
      if (summary) {
        backtestMetrics = {
          win_rate: summary.win_rate ?? 0,
          avg_return: summary.avg_pnl_percent ?? 0,
          sharpe_ratio: summary.sharpe_ratio ?? 0,
          total_trades: backtestData.trigger_count ?? backtestData.trade_count ?? 0,
          max_drawdown: summary.max_drawdown_percent ?? 0,
          effectiveness_score: summary.profit_factor ? Math.min(100, summary.profit_factor * 30) : 0,
        }
      }
    }
  } catch (e) {
    console.warn('Backtest API call failed, using default metrics:', e)
  }
  
  // Map backend fields to frontend interface with comprehensive null protection
  return {
    signal_name: signal.signal_name || `${params.symbol}_signal`,
    symbol: signal.symbol || params.symbol,
    description: signal.description || '',
    direction: signal.direction || params.direction || 'long',
    trigger_conditions: conditions,
    backtest_metrics: backtestMetrics,
    recommended_position_size: signal.recommended_position_size ?? 0.1,
    recommended_stop_loss: signal.recommended_stop_loss_percent ?? 2.0,
    recommended_take_profit: signal.recommended_take_profit_percent ?? 4.0,
    ai_prompt_template: signal.ai_prompt_template || undefined,
  }
}

async function scanPatterns(symbol: string): Promise<DetectedPattern[]> {
  const res = await fetch(`/api/smart-signals/pattern-scan/${symbol}`)
  if (!res.ok) throw new Error('Failed to scan patterns')
  const data = await res.json()
  return data.patterns || []
}

// 新增：生成量化提示词的API函数
async function generateQuantifiedPromptFromSignal(params: {
  symbol: string
  direction: string
  risk_level: string
  time_window: string
  strategy_type?: string
  lookback_days?: number
}): Promise<any> {
  const res = await fetch('/api/signal-prompt-integration/generate-quantified-prompt', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      symbol: params.symbol,
      direction: params.direction,
      risk_level: params.risk_level,
      time_window: params.time_window,
      strategy_type: params.strategy_type || 'adaptive',
      lookback_days: params.lookback_days || 14,
    })
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}))
    throw new Error(errorData?.detail || errorData?.message || 'Failed to generate quantified prompt from signal')
  }
  return res.json()
}

async function createSignal(signalConfig: any): Promise<any> {
  const res = await fetch('/api/signals/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signalConfig),
  })
  if (!res.ok) throw new Error('Failed to create signal')
  return res.json()
}

async function callAIAnalysis(symbol: string, accountId: number, marketData: any): Promise<any> {
  const res = await fetch('/api/smart-signals/ai-analysis', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      symbol,
      accountId,
      marketData,
    }),
  })
  if (!res.ok) throw new Error('Failed to call AI analysis')
  return res.json()
}

// 新增：应用AI分析结果到信号参数的API函数
async function applyAIAnalysisToSignal(symbol: string, accountId: number, marketData: any): Promise<any> {
  const res = await fetch('/api/smart-signals/apply-ai-analysis-to-signal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      symbol,
      accountId,
      marketData,
    }),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}))
    throw new Error(errorData?.detail || errorData?.message || 'Failed to apply AI analysis to signal')
  }
  return res.json()
}

// 新增：应用参数调整的API函数
async function applyParamAdjustments(params: {
  symbol: string
  adjustments: Record<string, any>
  accountId?: number
}): Promise<any> {
  const res = await fetch('/api/smart-signals/apply-param-adjustments', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}))
    throw new Error(errorData?.detail || errorData?.message || 'Failed to apply parameter adjustments')
  }
  return res.json()
}

async function fetchAITraders(): Promise<AITrader[]> {
  const res = await fetch('/api/account/list')
  if (!res.ok) throw new Error('Failed to fetch AI traders')
  const data = await res.json()
  // API may return array directly or { value: [...] } or { accounts: [...] }
  const accounts = Array.isArray(data) ? data : (data.value || data.accounts || [])
  return accounts.filter((acc: any) => acc.account_type === 'AI')
}

// ==================== Components ====================

// Market Analysis Card - Enhanced version with more data
function MarketAnalysisCard({ 
  symbol, 
  analysis, 
  loading,
  isZh,
  onAIAnalysis,
}: { 
  symbol: string
  analysis: MarketAnalysis | null
  loading: boolean
  isZh: boolean
  onAIAnalysis?: () => void
}) {
  if (loading) {
    return (
      <Card className="h-full">
        <CardContent className="p-4 flex items-center justify-center h-48">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    )
  }

  if (!analysis) {
    return (
      <Card className="h-full opacity-50">
        <CardContent className="p-4 flex items-center justify-center h-48">
          <span className="text-muted-foreground text-sm">
            {isZh ? '暂无数据' : 'No data'}
          </span>
        </CardContent>
      </Card>
    )
  }

  const regime = analysis.regime
  const params = analysis.adaptive_params
  const regimeColor = REGIME_COLORS[regime.type] || REGIME_COLORS.noise
  const regimeLabel = REGIME_LABELS[regime.type] || { zh: regime.type, en: regime.type }
  
  // Direction icon and color
  const directionConfig = {
    bullish: { icon: <TrendingUp className="w-4 h-4" />, color: 'text-green-500', bg: 'bg-green-500/10', label: isZh ? '看涨' : 'Bullish' },
    bearish: { icon: <TrendingDown className="w-4 h-4" />, color: 'text-red-500', bg: 'bg-red-500/10', label: isZh ? '看跌' : 'Bearish' },
    neutral: { icon: <Minus className="w-4 h-4" />, color: 'text-gray-400', bg: 'bg-gray-500/10', label: isZh ? '中性' : 'Neutral' },
  }
  const dir = directionConfig[regime.direction as keyof typeof directionConfig] || directionConfig.neutral

  // Risk level color
  const riskColors: Record<string, string> = {
    low: 'text-green-500',
    medium: 'text-yellow-500',
    high: 'text-red-500',
  }

  return (
    <Card className="h-full hover:shadow-lg transition-shadow border-t-2 border-t-purple-500">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CardTitle className="text-xl font-bold">{symbol}</CardTitle>
            <span className="text-xs text-muted-foreground">USDT</span>
          </div>
          <Badge variant="outline" className={`${regimeColor} border font-semibold`}>
            {isZh ? regimeLabel.zh : regimeLabel.en}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Direction and Confidence Row */}
        <div className="flex items-center justify-between p-2 rounded-lg bg-muted/30">
          <div className={`flex items-center gap-2 px-3 py-1 rounded-full ${dir.bg}`}>
            <span className={dir.color}>{dir.icon}</span>
            <span className={`text-sm font-semibold ${dir.color}`}>{dir.label}</span>
          </div>
          <div className="text-right">
            <div className="text-lg font-bold">{(regime.confidence * 100).toFixed(0)}%</div>
            <div className="text-xs text-muted-foreground">{isZh ? '置信度' : 'Confidence'}</div>
          </div>
        </div>

        {/* Confidence Bar */}
        <div className="space-y-1">
          <div className="h-2 bg-muted rounded-full overflow-hidden">
            <div 
              className={`h-full transition-all duration-500 ${
                regime.confidence > 0.7 ? 'bg-green-500' : 
                regime.confidence > 0.4 ? 'bg-yellow-500' : 'bg-red-500'
              }`}
              style={{ width: `${regime.confidence * 100}%` }} 
            />
          </div>
        </div>

        {/* Trading Parameters Grid */}
        {params && (
          <div className="grid grid-cols-2 gap-2">
            <div className="p-2 rounded-lg bg-muted/20 space-y-1">
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <Target className="w-3 h-3" />
                {isZh ? '仓位系数' : 'Position Size'}
              </div>
              <div className="text-lg font-bold text-purple-500">
                {params.position_size_modifier?.toFixed(2) || '1.00'}x
              </div>
            </div>
            <div className="p-2 rounded-lg bg-muted/20 space-y-1">
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <AlertTriangle className="w-3 h-3" />
                {isZh ? '止损距离' : 'Stop Loss'}
              </div>
              <div className="text-lg font-bold text-orange-500">
                {params.stop_loss_atr_multiple?.toFixed(1) || '1.5'} ATR
              </div>
            </div>
            <div className="p-2 rounded-lg bg-muted/20 space-y-1">
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <Zap className="w-3 h-3" />
                {isZh ? '盈亏比' : 'TP Ratio'}
              </div>
              <div className="text-lg font-bold text-green-500">
                1:{params.take_profit_ratio?.toFixed(1) || '2.0'}
              </div>
            </div>
            <div className="p-2 rounded-lg bg-muted/20 space-y-1">
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <CheckCircle2 className="w-3 h-3" />
                {isZh ? '确认数' : 'Confirms'}
              </div>
              <div className="text-lg font-bold text-blue-500">
                {params.entry_confirmation_count || 2}
              </div>
            </div>
          </div>
        )}

        {/* Raw Market Indicators - Real Data Proof */}
        {regime.indicators && (
          <div className="p-3 rounded-lg bg-slate-100 dark:bg-slate-800/50 space-y-2">
            <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground mb-2">
              <Activity className="w-3 h-3" />
              {isZh ? '实时市场指标 (5分钟)' : 'Live Market Indicators (5m)'}
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="text-center p-1.5 bg-white dark:bg-slate-700 rounded cursor-help">
                      <div className={`font-bold font-mono ${regime.indicators.taker_ratio > 1.5 ? 'text-green-500' : regime.indicators.taker_ratio < 0.67 ? 'text-red-500' : 'text-gray-500'}`}>
                        {regime.indicators.taker_ratio?.toFixed(2) || '-'}
                      </div>
                      <div className="text-muted-foreground">{isZh ? '买卖比' : 'Taker'}</div>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="text-xs max-w-48">
                      {isZh 
                        ? '买卖比 = 主买量/主卖量。>1.5看涨，<0.67看跌' 
                        : 'Taker Ratio = Buy/Sell. >1.5 bullish, <0.67 bearish'}
                    </p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="text-center p-1.5 bg-white dark:bg-slate-700 rounded cursor-help">
                      <div className={`font-bold font-mono ${regime.indicators.cvd_ratio > 0.3 ? 'text-green-500' : regime.indicators.cvd_ratio < -0.3 ? 'text-red-500' : 'text-gray-500'}`}>
                        {regime.indicators.cvd_ratio?.toFixed(3) || '-'}
                      </div>
                      <div className="text-muted-foreground">CVD</div>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="text-xs max-w-48">
                      {isZh 
                        ? '累计成交量差异比率。>0.3买盘强势，<-0.3卖盘强势' 
                        : 'Cumulative Volume Delta ratio. >0.3 buying pressure, <-0.3 selling pressure'}
                    </p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="text-center p-1.5 bg-white dark:bg-slate-700 rounded cursor-help">
                      <div className={`font-bold font-mono ${regime.indicators.rsi > 70 ? 'text-red-500' : regime.indicators.rsi < 30 ? 'text-green-500' : 'text-gray-500'}`}>
                        {regime.indicators.rsi?.toFixed(1) || '-'}
                      </div>
                      <div className="text-muted-foreground">RSI</div>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="text-xs max-w-48">
                      {isZh 
                        ? '相对强弱指数。>70超买区间，<30超卖区间' 
                        : 'Relative Strength Index. >70 overbought, <30 oversold'}
                    </p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="text-center p-1.5 bg-white dark:bg-slate-700 rounded cursor-help">
                      <div className={`font-bold font-mono ${regime.indicators.oi_delta > 2 ? 'text-green-500' : regime.indicators.oi_delta < -2 ? 'text-red-500' : 'text-gray-500'}`}>
                        {regime.indicators.oi_delta?.toFixed(1) || '0'}%
                      </div>
                      <div className="text-muted-foreground">{isZh ? 'OI变化' : 'OI Δ'}</div>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="text-xs max-w-48">
                      {isZh 
                        ? '持仓量变化百分比。>2%新资金入场，<-2%资金离场' 
                        : 'Open Interest change %. >2% new positions, <-2% closing positions'}
                    </p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="text-center p-1.5 bg-white dark:bg-slate-700 rounded cursor-help">
                      <div className="font-bold font-mono text-purple-500">
                        {regime.indicators.price_atr?.toFixed(2) || '-'}%
                      </div>
                      <div className="text-muted-foreground">ATR</div>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="text-xs max-w-48">
                      {isZh 
                        ? '平均真实波幅百分比。用于计算止损距离和仓位大小' 
                        : 'Average True Range %. Used for stop loss distance and position sizing'}
                    </p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              
              {regime.debug && (
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="text-center p-1.5 bg-white dark:bg-slate-700 rounded cursor-help">
                        <div className="font-bold font-mono text-blue-500">
                          ${((regime.debug.total_notional || 0) / 1000000).toFixed(2)}M
                        </div>
                        <div className="text-muted-foreground">{isZh ? '成交额' : 'Volume'}</div>
                      </div>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p className="text-xs max-w-48">
                        {isZh 
                          ? '5分钟成交额（美元）。高成交额表示市场活跃' 
                          : '5-minute trading volume in USD. High volume indicates active market'}
                      </p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              )}
            </div>
            {regime.debug && (
              <div className="flex justify-between text-xs text-muted-foreground pt-1 border-t border-dashed">
                <span>{isZh ? '买入' : 'Buy'}: <span className="text-green-500 font-mono">${(regime.debug.taker_buy/1000).toFixed(0)}K</span></span>
                <span>{isZh ? '卖出' : 'Sell'}: <span className="text-red-500 font-mono">${(regime.debug.taker_sell/1000).toFixed(0)}K</span></span>
              </div>
            )}
          </div>
        )}

        {/* Strategy Recommendation */}
        {params && (
          <div className="flex items-center justify-between p-2 rounded-lg border border-dashed border-muted-foreground/30">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-purple-500" />
              <span className="text-sm">{isZh ? '推荐策略' : 'Strategy'}</span>
            </div>
            <Badge variant="secondary" className="font-mono text-xs">
              {params.regime_type === 'breakout' ? (isZh ? '趋势跟踪' : 'Trend Follow') :
               params.regime_type === 'absorption' ? (isZh ? '均值回归' : 'Mean Revert') :
               params.regime_type === 'exhaustion' ? (isZh ? '反转交易' : 'Reversal') :
               (isZh ? '观望/小仓' : 'Wait/Small')}
            </Badge>
          </div>
        )}

        {/* Risk Level Indicator */}
        {params && (
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">{isZh ? '风险等级' : 'Risk Level'}:</span>
            <span className={`font-semibold ${riskColors[params.regime_type === 'noise' ? 'low' : params.regime_type === 'trap' ? 'high' : 'medium']}`}>
              {params.regime_type === 'noise' ? (isZh ? '低' : 'Low') :
               params.regime_type === 'trap' || params.regime_type === 'exhaustion' ? (isZh ? '高' : 'High') :
               (isZh ? '中' : 'Medium')}
            </span>
            <span className="text-muted-foreground">|</span>
            <span className="text-muted-foreground">{isZh ? '移动止损' : 'Trailing'}:</span>
            <span className={params.trailing_stop_enabled ? 'text-green-500' : 'text-gray-400'}>
              {params.trailing_stop_enabled ? (isZh ? '启用' : 'ON') : (isZh ? '关闭' : 'OFF')}
            </span>
          </div>
        )}
        
        {/* AI Deep Analysis Button */}
        {onAIAnalysis && (
          <Button
            variant="outline"
            size="sm"
            className="w-full mt-2 bg-gradient-to-r from-blue-500/10 to-purple-500/10 hover:from-blue-500/20 hover:to-purple-500/20 border-blue-500/30"
            onClick={onAIAnalysis}
          >
            <Brain className="w-4 h-4 mr-2 text-blue-500" />
            {isZh ? 'AI 深度分析' : 'AI Deep Analysis'}
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

// Generated Signal Card
function GeneratedSignalCard({
  signal,
  onViewDetails,
  onCreate,
  isCreating,
  isZh,
}: {
  signal: GeneratedSignal
  onViewDetails: () => void
  onCreate: () => void
  isCreating: boolean
  isZh: boolean
}) {
  const metrics = signal.backtest_metrics
  
  return (
    <Card className="hover:shadow-lg transition-shadow border-l-4 border-l-purple-500">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">{signal.signal_name}</CardTitle>
          <Badge variant={signal.direction === 'long' ? 'default' : 'destructive'}>
            {signal.direction === 'long' ? (isZh ? '做多' : 'Long') : (isZh ? '做空' : 'Short')}
          </Badge>
        </div>
        <CardDescription className="text-xs">{signal.description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Trigger Conditions */}
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">{isZh ? '触发条件' : 'Trigger Conditions'}</Label>
          <div className="bg-muted/50 rounded-md p-2 space-y-1">
            {signal.trigger_conditions.slice(0, 3).map((cond, idx) => (
              <div key={idx} className="text-xs font-mono flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3 text-green-500" />
                <span>{cond.metric} {cond.operator} {cond.threshold}</span>
              </div>
            ))}
            {signal.trigger_conditions.length > 3 && (
              <div className="text-xs text-muted-foreground">
                +{signal.trigger_conditions.length - 3} {isZh ? '更多条件' : 'more conditions'}
              </div>
            )}
          </div>
        </div>

        {/* Backtest Metrics */}
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="bg-muted/30 rounded-md p-2">
            <div className="text-lg font-bold text-green-500">
              {metrics.win_rate.toFixed(1)}%
            </div>
            <div className="text-xs text-muted-foreground">{isZh ? '胜率' : 'Win Rate'}</div>
          </div>
          <div className="bg-muted/30 rounded-md p-2">
            <div className="text-lg font-bold text-blue-500">
              {metrics.avg_return > 0 ? '+' : ''}{metrics.avg_return.toFixed(2)}%
            </div>
            <div className="text-xs text-muted-foreground">{isZh ? '平均收益' : 'Avg Return'}</div>
          </div>
          <div className="bg-muted/30 rounded-md p-2">
            <div className="text-lg font-bold text-purple-500">
              {metrics.sharpe_ratio.toFixed(2)}
            </div>
            <div className="text-xs text-muted-foreground">{isZh ? '夏普比率' : 'Sharpe'}</div>
          </div>
        </div>

        {/* Effectiveness Score */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-purple-500" />
            <span className="text-sm">{isZh ? '有效性评分' : 'Effectiveness'}</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-20 h-2 bg-muted rounded-full overflow-hidden">
              <div 
                className="h-full bg-purple-500 transition-all duration-300" 
                style={{ width: `${metrics.effectiveness_score}%` }} 
              />
            </div>
            <span className="text-sm font-bold">{metrics.effectiveness_score.toFixed(0)}</span>
          </div>
        </div>

        {/* Recommendations */}
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div className="text-center p-2 bg-green-500/10 rounded">
            <div className="font-bold">{(signal.recommended_position_size * 100).toFixed(0)}%</div>
            <div className="text-muted-foreground">{isZh ? '建议仓位' : 'Position'}</div>
          </div>
          <div className="text-center p-2 bg-red-500/10 rounded">
            <div className="font-bold">{signal.recommended_stop_loss.toFixed(1)}%</div>
            <div className="text-muted-foreground">{isZh ? '止损' : 'Stop Loss'}</div>
          </div>
          <div className="text-center p-2 bg-blue-500/10 rounded">
            <div className="font-bold">{signal.recommended_take_profit.toFixed(1)}%</div>
            <div className="text-muted-foreground">{isZh ? '止盈' : 'Take Profit'}</div>
          </div>
        </div>

        {/* Actions */}
        <div className="flex gap-2">
          <Button variant="outline" size="sm" className="flex-1" onClick={onViewDetails}>
            <Eye className="w-4 h-4 mr-1" />
            {isZh ? '查看回测' : 'View Backtest'}
          </Button>
          <Button size="sm" className="flex-1" onClick={onCreate} disabled={isCreating}>
            {isCreating ? (
              <Loader2 className="w-4 h-4 mr-1 animate-spin" />
            ) : (
              <Plus className="w-4 h-4 mr-1" />
            )}
            {isZh ? '创建信号' : 'Create Signal'}
          </Button>
        </div>
        
        {/* AI Prompt Indicator */}
        {signal.ai_prompt_template && (
          <div className="flex items-center gap-2 pt-2 border-t">
            <Brain className="w-4 h-4 text-blue-500" />
            <span className="text-xs text-blue-500 font-medium">{isZh ? '已生成AI提示词' : 'AI Prompt Generated'}</span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// Pattern Card
function PatternCard({ pattern, isZh }: { pattern: DetectedPattern; isZh: boolean }) {
  return (
    <div className="flex items-center justify-between p-3 bg-muted/30 rounded-lg">
      <div className="flex items-center gap-3">
        <div className={`p-2 rounded-full ${
          pattern.direction === 'long' ? 'bg-green-500/20' : 'bg-red-500/20'
        }`}>
          {pattern.direction === 'long' ? (
            <ArrowUpRight className="w-4 h-4 text-green-500" />
          ) : (
            <ArrowDownRight className="w-4 h-4 text-red-500" />
          )}
        </div>
        <div>
          <div className="font-medium text-sm">{pattern.pattern_name}</div>
          <div className="text-xs text-muted-foreground">
            {pattern.symbol} · {isZh ? '历史胜率' : 'Win Rate'}: {pattern.historical_win_rate.toFixed(1)}%
          </div>
        </div>
      </div>
      <div className="text-right">
        <div className="text-sm font-mono font-bold">
          {(pattern.confidence * 100).toFixed(0)}%
        </div>
        <div className="text-xs text-muted-foreground">{isZh ? '置信度' : 'Confidence'}</div>
      </div>
    </div>
  )
}

// ==================== Main Component ====================

export default function SmartSignalGenerator() {
  const { i18n } = useTranslation()
  const isZh = i18n.language?.startsWith('zh')
  const { symbols: configuredPairs } = useTradingPairs()
  const availableSymbols = configuredPairs.length > 0 ? configuredPairs : FALLBACK_TRADING_PAIRS

  // Selection state
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([])  // 默认不选，让用户自己选择
  const [direction, setDirection] = useState<string>('auto')
  const [strategyType, setStrategyType] = useState<string>('adaptive')
  const [riskLevel, setRiskLevel] = useState<string>('moderate')
  const [timeWindow, setTimeWindow] = useState<string>('5m')
  const [lookbackDays, setLookbackDays] = useState<number>(14)  // 新增：历史数据天数

  // Data state
  const [marketAnalyses, setMarketAnalyses] = useState<Record<string, MarketAnalysis>>({})
  const [detectedPatterns, setDetectedPatterns] = useState<DetectedPattern[]>([])
  const [generatedSignals, setGeneratedSignals] = useState<GeneratedSignal[]>([])
  const [strategyStyles, setStrategyStyles] = useState<StrategyStyle[]>([])

  // Loading state
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [patternScanning, setPatternScanning] = useState(false)
  const [creatingSignals, setCreatingSignals] = useState<Set<string>>(new Set())
  
  // Auto-refresh and timestamp state
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [refreshInterval, setRefreshInterval] = useState<number>(60)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  
  // Error state for detailed error handling
  const [analysisError, setAnalysisError] = useState<string | null>(null)

  // Dialog state
  const [signalDetailOpen, setSignalDetailOpen] = useState(false)
  const [selectedSignalDetail, setSelectedSignalDetail] = useState<GeneratedSignal | null>(null)
  
  // AI Analysis state
  const [aiTraders, setAiTraders] = useState<AITrader[]>([])
  const [selectedAiTrader, setSelectedAiTrader] = useState<number | null>(null)
  const [aiAnalysisOpen, setAiAnalysisOpen] = useState(false)
  const [aiAnalysisSymbol, setAiAnalysisSymbol] = useState<string>('')
  const [aiAnalysisLoading, setAiAnalysisLoading] = useState(false)
  const [aiAnalysisResult, setAiAnalysisResult] = useState<string>('')
  const [aiSuggestedParams, setAiSuggestedParams] = useState<{
    direction: string
    strategy: string
    risk_level: string
    time_window: string
    confidence: number
    stop_loss_percent: number | null
    take_profit_percent: number | null
    // Additional parameters from feedback loop
    rsi_oversold_threshold?: number
    rsi_overbought_threshold?: number
    macd_sensitivity?: number
    bollinger_band_width_multiplier?: number
    atr_stop_loss_multiplier?: number
    take_profit_ratio?: number
    min_volume_threshold?: number
    min_price_change_threshold?: number
    strategy_recommendation?: string
    risk_assessment?: number
  } | null>(null)
  
  // Saved AI stop loss / take profit for signal creation
  const [aiStopLoss, setAiStopLoss] = useState<number | null>(null)
  const [aiTakeProfit, setAiTakeProfit] = useState<number | null>(null)

  // Load strategy styles on mount
  useEffect(() => {
    fetchStrategyStyles()
      .then(setStrategyStyles)
      .catch(err => console.error('Failed to load strategy styles:', err))
  }, [])
  
  // Load AI Traders on mount
  useEffect(() => {
    fetchAITraders()
      .then(traders => {
        setAiTraders(traders)
        if (traders.length > 0 && !selectedAiTrader) {
          setSelectedAiTrader(traders[0].id)
        }
      })
      .catch(err => console.error('Failed to load AI traders:', err))
  }, [])

  // Load market analyses for selected symbols
  const loadMarketAnalyses = useCallback(async () => {
    if (selectedSymbols.length === 0) return

    setAnalysisLoading(true)
    setAnalysisError(null)
    try {
      const analyses: Record<string, MarketAnalysis> = {}
      
      await Promise.all(
        selectedSymbols.map(async (symbol) => {
          try {
            const [regimeResponse, adaptiveResponse] = await Promise.all([
              fetchRegimeInfo(symbol),
              fetchAdaptiveParameters(symbol),
            ])
            
            // Extract data from nested response structure
            const regimeData = regimeResponse?.regime || regimeResponse || {}
            const adaptiveParams = adaptiveResponse?.parameters || adaptiveResponse || {}
            
            analyses[symbol] = {
              symbol,
              regime: {
                type: regimeData.regime || adaptiveParams.regime_type || 'noise',
                direction: regimeData.direction || adaptiveParams.regime_direction || 'neutral',
                confidence: regimeData.confidence || adaptiveParams.regime_confidence || 0.5,
                indicators: regimeData.indicators || null,
                debug: regimeData.debug || null,
              },
              adaptive_params: {
                position_size_modifier: adaptiveParams.position_size_modifier || 1.0,
                stop_loss_atr_multiple: adaptiveParams.stop_loss_atr_multiple || 1.5,
                take_profit_ratio: adaptiveParams.take_profit_ratio || 2.0,
                entry_confirmation_count: adaptiveParams.entry_confirmation_count || 2,
                max_position_percent: adaptiveParams.max_position_percent || 0.1,
                trailing_stop_enabled: adaptiveParams.trailing_stop_enabled || false,
                regime_type: adaptiveParams.regime_type || 'noise',
                regime_direction: adaptiveParams.regime_direction || 'neutral',
                regime_confidence: adaptiveParams.regime_confidence || 0.5,
              },
              multi_timeframe: regimeData.multi_timeframe,
            }
          } catch (err) {
            console.error(`Failed to load analysis for ${symbol}:`, err)
          }
        })
      )
      
      setMarketAnalyses(analyses)
      setLastUpdated(new Date())
    } catch (err: any) {
      const errorMsg = err?.message || (isZh ? '加载市场分析失败' : 'Failed to load market analysis')
      setAnalysisError(errorMsg)
      toast.error(errorMsg)
    } finally {
      setAnalysisLoading(false)
    }
  }, [selectedSymbols, isZh])

  // Load analyses when symbols change
  useEffect(() => {
    loadMarketAnalyses()
  }, [loadMarketAnalyses])
  
  // Auto-refresh effect
  useEffect(() => {
    if (!autoRefresh || selectedSymbols.length === 0) return
    
    const intervalId = setInterval(() => {
      loadMarketAnalyses()
    }, refreshInterval * 1000)
    
    return () => clearInterval(intervalId)
  }, [autoRefresh, refreshInterval, selectedSymbols, loadMarketAnalyses])

  // Scan patterns for all selected symbols
  const handleScanPatterns = async () => {
    setPatternScanning(true)
    setDetectedPatterns([])
    
    try {
      const allPatterns: DetectedPattern[] = []
      
      await Promise.all(
        selectedSymbols.map(async (symbol) => {
          try {
            const patterns = await scanPatterns(symbol)
            allPatterns.push(...patterns)
          } catch (err) {
            console.error(`Failed to scan patterns for ${symbol}:`, err)
          }
        })
      )
      
      // Sort by confidence
      allPatterns.sort((a, b) => b.confidence - a.confidence)
      setDetectedPatterns(allPatterns)
      
      if (allPatterns.length === 0) {
        toast(isZh ? '未检测到活跃模式' : 'No active patterns detected', { icon: '📊' })
      } else {
        toast.success(isZh ? `检测到 ${allPatterns.length} 个模式` : `Found ${allPatterns.length} patterns`)
      }
    } catch (err) {
      toast.error(isZh ? '模式扫描失败' : 'Pattern scan failed')
    } finally {
      setPatternScanning(false)
    }
  }

  // Generate optimal signals
  const handleGenerateSignals = async () => {
    if (selectedSymbols.length === 0) {
      toast.error(isZh ? '请选择至少一个交易品种' : 'Please select at least one symbol')
      return
    }

    setGenerating(true)
    setGeneratedSignals([])

    try {
      const signals: GeneratedSignal[] = []
      
      for (const symbol of selectedSymbols) {
        try {
          const signal = await generateOptimalSignal({
            symbol,
            direction,
            risk_level: riskLevel,
            time_window: timeWindow,
            strategy_type: strategyType,
            lookback_days: lookbackDays  // 添加历史数据天数参数
          })
          signals.push(signal)
        } catch (err) {
          console.error(`Failed to generate signal for ${symbol}:`, err)
        }
      }
      
      setGeneratedSignals(signals)
      
      if (signals.length > 0) {
        toast.success(
          isZh 
            ? `成功生成 ${signals.length} 个信号 (${lookbackDays} 天数据)`
            : `Generated ${signals.length} signals (${lookbackDays} days data)`
        )
      } else {
        toast.error(isZh ? '信号生成失败' : 'Failed to generate signals')
      }
    } catch (err) {
      toast.error(isZh ? '信号生成失败' : 'Signal generation failed')
    } finally {
      setGenerating(false)
    }
  }

  // Generate quantified prompt from signal - Deep integration feature
  const handleGenerateQuantifiedPrompt = async () => {
    if (selectedSymbols.length === 0) {
      toast.error(isZh ? '请选择至少一个交易品种' : 'Please select at least one symbol')
      return
    }

    setGenerating(true)
    setGeneratedSignals([])

    try {
      const signals: GeneratedSignal[] = []
      
      for (const symbol of selectedSymbols) {
        try {
          // Use the new integration API that generates both signal and executable prompt
          const response = await generateQuantifiedPromptFromSignal({
            symbol,
            direction,
            risk_level: riskLevel,
            time_window: timeWindow,
            strategy_type: strategyType,
            lookback_days: lookbackDays  // 添加历史数据天数参数
          })
          
          // Extract both signal and prompt template from response
          if (response.signal_and_prompt) {
            const { signal, prompt_template } = response.signal_and_prompt
            // Map to our GeneratedSignal interface
            const mappedSignal: GeneratedSignal = {
              signal_name: signal.signal_name || `${symbol}_integrated_signal`,
              symbol: signal.symbol || symbol,
              description: signal.description || '',
              direction: signal.direction || direction,
              trigger_conditions: signal.trigger_condition?.conditions || [],
              backtest_metrics: {
                win_rate: signal.backtest_metrics?.win_rate || 0,
                avg_return: signal.backtest_metrics?.avg_return_percent || signal.backtest_metrics?.avg_return || 0,
                sharpe_ratio: signal.backtest_metrics?.sharpe_ratio || 0,
                total_trades: signal.backtest_metrics?.total_triggers || signal.backtest_metrics?.total_trades || 0,
                max_drawdown: signal.backtest_metrics?.max_drawdown_percent || signal.backtest_metrics?.max_drawdown || 0,
                effectiveness_score: signal.effectiveness_score || 0,
              },
              recommended_position_size: signal.recommended_position_size || 0.1,
              recommended_stop_loss: signal.recommended_stop_loss_percent || 2.0,
              recommended_take_profit: signal.recommended_take_profit_percent || 4.0,
              ai_prompt_template: prompt_template?.template_text, // Include the generated prompt template
            }
            signals.push(mappedSignal)
          } else if (response.signal) {
            // Fallback to original signal-only response
            const signal = response.signal
            const mappedSignal: GeneratedSignal = {
              signal_name: signal.signal_name || `${symbol}_signal`,
              symbol: signal.symbol || symbol,
              description: signal.description || '',
              direction: signal.direction || direction,
              trigger_conditions: signal.trigger_condition?.conditions || [],
              backtest_metrics: {
                win_rate: signal.backtest_metrics?.win_rate || 0,
                avg_return: signal.backtest_metrics?.avg_return_percent || signal.backtest_metrics?.avg_return || 0,
                sharpe_ratio: signal.backtest_metrics?.sharpe_ratio || 0,
                total_trades: signal.backtest_metrics?.total_triggers || signal.backtest_metrics?.total_trades || 0,
                max_drawdown: signal.backtest_metrics?.max_drawdown_percent || signal.backtest_metrics?.max_drawdown || 0,
                effectiveness_score: signal.effectiveness_score || 0,
              },
              recommended_position_size: signal.recommended_position_size || 0.1,
              recommended_stop_loss: signal.recommended_stop_loss_percent || 2.0,
              recommended_take_profit: signal.recommended_take_profit_percent || 4.0,
              ai_prompt_template: signal.ai_prompt_template || undefined,
            }
            signals.push(mappedSignal)
          }
        } catch (err) {
          console.error(`Failed to generate integrated signal for ${symbol}:`, err)
        }
      }
      
      setGeneratedSignals(signals)
      
      if (signals.length > 0) {
        toast.success(
          isZh 
            ? `成功生成 ${signals.length} 个集成信号 (${lookbackDays} 天数据)，包含可执行提示词`
            : `Generated ${signals.length} integrated signals (${lookbackDays} days data) with executable prompts`
        )
      } else {
        toast.error(isZh ? '集成信号生成失败' : 'Failed to generate integrated signals')
      }
    } catch (err) {
      toast.error(isZh ? '集成信号生成失败' : 'Integrated signal generation failed')
    } finally {
      setGenerating(false)
    }
  }

  // Create signal from generated config
  const handleCreateSignal = async (signal: GeneratedSignal) => {
    const signalKey = signal.signal_name
    setCreatingSignals(prev => new Set(prev).add(signalKey))

    try {
      // Use AI suggested stop loss / take profit if available, otherwise use signal recommendations
      const stopLossPercent = aiStopLoss ?? signal.recommended_stop_loss ?? 2.0
      const takeProfitPercent = aiTakeProfit ?? signal.recommended_take_profit ?? 4.0
      
      // Convert to API format with complete configuration
      const signalConfig = {
        signal_name: signal.signal_name,
        description: signal.description,
        metric: signal.trigger_conditions[0]?.metric || 'oi_delta',
        operator: signal.trigger_conditions[0]?.operator || 'greater_than',
        threshold: signal.trigger_conditions[0]?.threshold || 1.0,
        time_window: timeWindow,
        enabled: true,
        // Include AI-suggested or recommended risk parameters
        stop_loss_percent: stopLossPercent,
        take_profit_percent: takeProfitPercent,
        position_size: signal.recommended_position_size ?? 0.1,
        direction: signal.direction,
      }

      await createSignal(signalConfig)
      toast.success(
        isZh 
          ? `信号创建成功 (止损: ${stopLossPercent}%, 止盈: ${takeProfitPercent}%)`
          : `Signal created (SL: ${stopLossPercent}%, TP: ${takeProfitPercent}%)`
      )
    } catch (err: any) {
      const message = err?.message || (isZh ? '信号创建失败' : 'Failed to create signal')
      toast.error(message)
    } finally {
      setCreatingSignals(prev => {
        const next = new Set(prev)
        next.delete(signalKey)
        return next
      })
    }
  }

  // Toggle symbol selection
  const toggleSymbol = (symbol: string) => {
    setSelectedSymbols(prev => 
      prev.includes(symbol)
        ? prev.filter(s => s !== symbol)
        : [...prev, symbol]
    )
  }
  
  // Handle AI deep analysis with feedback loop
  const handleAIAnalysis = async (symbol: string) => {
    // Auto-select first AI trader if none selected
    let traderId = selectedAiTrader
    if (!traderId && aiTraders.length > 0) {
      traderId = aiTraders[0].id
      setSelectedAiTrader(traderId)
    }
    
    if (!traderId) {
      toast.error(isZh ? '没有可用的 AI Trader' : 'No AI Trader available')
      return
    }
    
    const analysis = marketAnalyses[symbol]
    if (!analysis) {
      toast.error(isZh ? '市场数据不可用' : 'Market data not available')
      return
    }
    
    setAiAnalysisSymbol(symbol)
    setAiAnalysisOpen(true)
    setAiAnalysisLoading(true)
    setAiAnalysisResult('')
    setAiSuggestedParams(null)
    
    try {
      // First, get the initial AI analysis
      const result = await callAIAnalysis(symbol, traderId, {
        regime: analysis.regime.type,
        direction: analysis.regime.direction,
        confidence: analysis.regime.confidence,
        indicators: analysis.regime.indicators,
        debug: analysis.regime.debug,
      })
      
      if (result.success) {
        setAiAnalysisResult(result.analysis)
        // Save suggested parameters if available
        if (result.suggested_params) {
          setAiSuggestedParams(result.suggested_params)
        }
      } else {
        throw new Error(result.error || 'AI analysis failed')
      }
      
      // Then, apply the AI analysis to signal parameters (feedback loop)
      const feedbackResult = await applyAIAnalysisToSignal(symbol, traderId, {
        regime: analysis.regime.type,
        direction: analysis.regime.direction,
        confidence: analysis.regime.confidence,
        indicators: analysis.regime.indicators,
        debug: analysis.regime.debug,
      })
      
      if (feedbackResult.success && feedbackResult.param_adjustments) {
        // Show notification about parameter adjustments
        toast.success(
          isZh 
            ? `AI分析完成，检测到信号参数调整建议`
            : `AI analysis complete, signal parameter adjustment suggestions detected`
        )
        
        // Update the suggested parameters with the feedback loop results
        if (feedbackResult.param_adjustments && Object.keys(feedbackResult.param_adjustments).length > 0) {
          setAiSuggestedParams(prev => ({
            ...prev,
            ...feedbackResult.param_adjustments,
            strategy_recommendation: feedbackResult.strategy_recommendation,
            risk_assessment: feedbackResult.risk_assessment,
          }))
        }
      }
    } catch (err) {
      toast.error(isZh ? 'AI分析失败' : 'AI analysis failed')
      setAiAnalysisResult(isZh ? '分析失败，请稍后重试' : 'Analysis failed, please try again later')
    } finally {
      setAiAnalysisLoading(false)
    }
  }
  
  // Apply AI suggested parameters with feedback loop
  const handleApplyAIParams = async () => {
    if (!aiSuggestedParams) return
    
    try {
      // Apply parameters
      if (aiSuggestedParams.direction && aiSuggestedParams.direction !== 'auto') {
        setDirection(aiSuggestedParams.direction)
      }
      if (aiSuggestedParams.strategy) {
        setStrategyType(aiSuggestedParams.strategy)
      }
      if (aiSuggestedParams.risk_level) {
        setRiskLevel(aiSuggestedParams.risk_level)
      }
      if (aiSuggestedParams.time_window) {
        setTimeWindow(aiSuggestedParams.time_window)
      }
      
      // Apply additional parameter adjustments from the feedback loop
      const paramAdjustments = {
        rsi_oversold_threshold: aiSuggestedParams.rsi_oversold_threshold,
        rsi_overbought_threshold: aiSuggestedParams.rsi_overbought_threshold,
        macd_sensitivity: aiSuggestedParams.macd_sensitivity,
        bollinger_band_width_multiplier: aiSuggestedParams.bollinger_band_width_multiplier,
        atr_stop_loss_multiplier: aiSuggestedParams.atr_stop_loss_multiplier,
        take_profit_ratio: aiSuggestedParams.take_profit_ratio,
        min_volume_threshold: aiSuggestedParams.min_volume_threshold,
        min_price_change_threshold: aiSuggestedParams.min_price_change_threshold,
      }
      
      // Filter out undefined/null values
      const validAdjustments = Object.fromEntries(
        Object.entries(paramAdjustments).filter(([_, value]) => value !== undefined && value !== null)
      )
      
      // If we have parameter adjustments, apply them via the API
      if (Object.keys(validAdjustments).length > 0 && aiAnalysisSymbol) {
        await applyParamAdjustments({
          symbol: aiAnalysisSymbol,
          adjustments: validAdjustments,
          accountId: selectedAiTrader || undefined,
        })
      }
      
      // Save stop loss / take profit for signal creation
      if (aiSuggestedParams.stop_loss_percent) {
        setAiStopLoss(aiSuggestedParams.stop_loss_percent)
      }
      if (aiSuggestedParams.take_profit_percent) {
        setAiTakeProfit(aiSuggestedParams.take_profit_percent)
      }
      
      toast.success(isZh ? 'AI 建议参数已应用（含闭环优化）' : 'AI suggested parameters applied (with feedback loop)')
      setAiAnalysisOpen(false)
    } catch (error) {
      console.error('Failed to apply AI parameter adjustments:', error)
      toast.error(isZh ? '应用参数调整失败' : 'Failed to apply parameter adjustments')
    }
  }

  return (
    <div className="w-full min-h-full bg-gradient-to-br from-slate-50 via-white to-slate-100 dark:from-slate-950 dark:via-slate-900 dark:to-slate-950 p-4 md:p-6 lg:p-8">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-3xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent flex items-center gap-2">
              <Sparkles className="w-8 h-8 text-purple-500" />
              {isZh ? '智能信号生成器' : 'Smart Signal Generator'}
            </h1>
            <p className="text-slate-500 dark:text-slate-400 mt-2">
              {isZh ? '基于市场数据分析，自动生成最优交易信号' : 'Auto-generate optimal trading signals based on market data analysis'}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {/* Last Updated Timestamp */}
            {lastUpdated && (
              <span className="text-xs text-muted-foreground">
                {isZh ? '更新于' : 'Updated'}: {lastUpdated.toLocaleTimeString()}
              </span>
            )}
            
            {/* Auto Refresh Toggle */}
            <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-muted/50">
              <label className="text-xs text-muted-foreground cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={(e) => setAutoRefresh(e.target.checked)}
                  className="mr-1"
                />
                {isZh ? '自动刷新' : 'Auto'}
              </label>
              {autoRefresh && (
                <Select value={refreshInterval.toString()} onValueChange={(v) => setRefreshInterval(parseInt(v))}>
                  <SelectTrigger className="h-6 w-16 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="30">30s</SelectItem>
                    <SelectItem value="60">60s</SelectItem>
                    <SelectItem value="120">2m</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </div>
            
            <Button
              variant="outline"
              size="sm"
              onClick={loadMarketAnalyses}
              disabled={analysisLoading}
              className="bg-white dark:bg-slate-800/50"
            >
              <RefreshCw className={`w-4 h-4 mr-2 ${analysisLoading ? 'animate-spin' : ''}`} />
              {isZh ? '刷新' : 'Refresh'}
            </Button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Panel - Configuration */}
        <div className="space-y-6">
          {/* Step 1: Symbol Selection */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <span className="flex items-center justify-center w-6 h-6 rounded-full bg-purple-500 text-white text-xs font-bold">1</span>
                {isZh ? '选择交易品种' : 'Select Symbols'}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-2">
                {availableSymbols.map(symbol => (
                  <Button
                    key={symbol}
                    variant={selectedSymbols.includes(symbol) ? 'selected' : 'outline'}
                    size="sm"
                    onClick={() => toggleSymbol(symbol)}
                  >
                    {symbol}
                  </Button>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Step 2: Strategy Configuration */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <span className="flex items-center justify-center w-6 h-6 rounded-full bg-purple-500 text-white text-xs font-bold">2</span>
                {isZh ? '策略配置' : 'Strategy Config'}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Direction */}
              <div className="space-y-2">
                <Label>{isZh ? '交易方向' : 'Direction'}</Label>
                <div className="flex gap-2">
                  {[
                    { value: 'auto', label: isZh ? '自动' : 'Auto', icon: <Target className="w-4 h-4" /> },
                    { value: 'long', label: isZh ? '做多' : 'Long', icon: <TrendingUp className="w-4 h-4 text-green-500" /> },
                    { value: 'short', label: isZh ? '做空' : 'Short', icon: <TrendingDown className="w-4 h-4 text-red-500" /> },
                  ].map(opt => (
                    <Button
                      key={opt.value}
                      variant={direction === opt.value ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setDirection(opt.value)}
                      className={direction === opt.value ? 'bg-purple-500 hover:bg-purple-600' : ''}
                    >
                      {opt.icon}
                      <span className="ml-1">{opt.label}</span>
                    </Button>
                  ))}
                </div>
              </div>

              {/* Strategy Type */}
              <div className="space-y-2">
                <Label>{isZh ? '策略类型' : 'Strategy Type'}</Label>
                <Select value={strategyType} onValueChange={setStrategyType}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {strategyStyles.length > 0 ? (
                      strategyStyles.map(style => (
                        <SelectItem key={style.id} value={style.id}>
                          <div className="flex flex-col">
                            <span>{isZh ? style.name : style.id}</span>
                          </div>
                        </SelectItem>
                      ))
                    ) : (
                      <>
                        <SelectItem value="adaptive">{isZh ? '自适应' : 'Adaptive'}</SelectItem>
                        <SelectItem value="trend_following">{isZh ? '趋势跟踪' : 'Trend Following'}</SelectItem>
                        <SelectItem value="mean_reversion">{isZh ? '均值回归' : 'Mean Reversion'}</SelectItem>
                        <SelectItem value="breakout">{isZh ? '突破策略' : 'Breakout'}</SelectItem>
                        <SelectItem value="scalping">{isZh ? '剥头皮' : 'Scalping'}</SelectItem>
                      </>
                    )}
                  </SelectContent>
                </Select>
              </div>

              {/* Risk Level */}
              <div className="space-y-2">
                <Label>{isZh ? '风险等级' : 'Risk Level'}</Label>
                <div className="flex gap-2">
                  {RISK_LEVELS.map(level => (
                    <Button
                      key={level.value}
                      variant={riskLevel === level.value ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setRiskLevel(level.value)}
                      className={riskLevel === level.value ? 'bg-purple-500 hover:bg-purple-600' : ''}
                    >
                      {isZh ? level.label : level.labelEn}
                    </Button>
                  ))}
                </div>
              </div>

              {/* Time Window */}
              <div className="space-y-2">
                <Label>{isZh ? '时间窗口' : 'Time Window'}</Label>
                <Select value={timeWindow} onValueChange={setTimeWindow}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="1m">1m</SelectItem>
                    <SelectItem value="5m">5m</SelectItem>
                    <SelectItem value="15m">15m</SelectItem>
                    <SelectItem value="1h">1h</SelectItem>
                    <SelectItem value="4h">4h</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              
              {/* Lookback Days */}
              <div className="space-y-2">
                <Label className="flex items-center gap-2">
                  <Activity className="w-4 h-4 text-purple-500" />
                  {isZh ? '历史数据天数' : 'Historical Data Days'}
                </Label>
                <div className="flex items-center gap-2">
                  <input
                    type="range"
                    min="1"
                    max="90"
                    value={lookbackDays}
                    onChange={(e) => setLookbackDays(Number(e.target.value))}
                    className="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer dark:bg-gray-700"
                  />
                  <span className="w-12 text-center text-sm font-medium bg-muted rounded px-2 py-1">
                    {lookbackDays}
                  </span>
                </div>
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span>1天</span>
                  <span>90天</span>
                </div>
              </div>
              
              {/* AI Trader Selection */}
              {aiTraders.length > 0 && (
                <div className="space-y-2">
                  <Label className="flex items-center gap-2">
                    <Brain className="w-4 h-4 text-blue-500" />
                    {isZh ? 'AI 分析模型' : 'AI Analysis Model'}
                  </Label>
                  <Select 
                    value={selectedAiTrader?.toString() || ''} 
                    onValueChange={(v) => setSelectedAiTrader(parseInt(v))}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={isZh ? '选择 AI' : 'Select AI'} />
                    </SelectTrigger>
                    <SelectContent>
                      {aiTraders.map(trader => (
                        <SelectItem key={trader.id} value={trader.id.toString()}>
                          <div className="flex items-center gap-2">
                            <Bot className="w-4 h-4 text-blue-500" />
                            <span>{trader.name}</span>
                            <span className="text-xs text-muted-foreground">({trader.model})</span>
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Generate Buttons - Standard and Integrated */}
          <div className="grid grid-cols-2 gap-2 w-full">
            <Button
              className="h-12 text-sm bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600"
              onClick={handleGenerateSignals}
              disabled={generating || selectedSymbols.length === 0}
            >
              {generating ? (
                <>
                  <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4 mr-1" />
                  {isZh ? '标准信号' : 'Standard'}
                </>
              )}
            </Button>
            <Button
              className="h-12 text-sm bg-gradient-to-r from-blue-500 to-cyan-500 hover:from-blue-600 hover:to-cyan-600"
              onClick={handleGenerateQuantifiedPrompt}
              disabled={generating || selectedSymbols.length === 0}
            >
              {generating ? (
                <>
                  <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                </>
              ) : (
                <>
                  <Brain className="w-4 h-4 mr-1" />
                  {isZh ? '集成提示词' : 'With Prompts'}
                </>
              )}
            </Button>
          </div>
        </div>

        {/* Middle Panel - Market Analysis */}
        <div className="space-y-6">
          <Card className="h-full">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base flex items-center gap-2">
                  <LineChart className="w-5 h-5 text-purple-500" />
                  {isZh ? '当前市场分析' : 'Current Market Analysis'}
                </CardTitle>
                <Button variant="ghost" size="sm" onClick={loadMarketAnalyses} disabled={analysisLoading}>
                  <RefreshCw className={`w-4 h-4 ${analysisLoading ? 'animate-spin' : ''}`} />
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {analysisError ? (
                <div className="flex flex-col items-center justify-center py-8 text-center">
                  <AlertTriangle className="w-10 h-10 text-orange-500 mb-3" />
                  <p className="text-sm text-muted-foreground mb-3">{analysisError}</p>
                  <Button variant="outline" size="sm" onClick={loadMarketAnalyses}>
                    <RefreshCw className="w-4 h-4 mr-2" />
                    {isZh ? '重试' : 'Retry'}
                  </Button>
                </div>
              ) : (
                <div className="grid gap-4">
                  {selectedSymbols.map(symbol => (
                    <MarketAnalysisCard
                      key={symbol}
                      symbol={symbol}
                      analysis={marketAnalyses[symbol] || null}
                      loading={analysisLoading}
                      isZh={isZh}
                      onAIAnalysis={() => handleAIAnalysis(symbol)}
                    />
                  ))}
                  {selectedSymbols.length === 0 && (
                    <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                      <Target className="w-12 h-12 mb-4 opacity-30" />
                      <p className="text-sm font-medium">{isZh ? '请选择交易品种' : 'Please select symbols'}</p>
                      <p className="text-xs mt-1 opacity-70">
                        {isZh ? '从左侧面板选择您感兴趣的品种' : 'Select symbols from the left panel'}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Pattern Detection */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base flex items-center gap-2">
                  <Zap className="w-5 h-5 text-yellow-500" />
                  {isZh ? '检测到的模式' : 'Detected Patterns'}
                </CardTitle>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleScanPatterns}
                  disabled={patternScanning || selectedSymbols.length === 0}
                >
                  {patternScanning ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <>{isZh ? '扫描模式' : 'Scan Patterns'}</>
                  )}
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-48">
                {detectedPatterns.length > 0 ? (
                  <div className="space-y-2">
                    {detectedPatterns.map((pattern, idx) => (
                      <PatternCard key={idx} pattern={pattern} isZh={isZh} />
                    ))}
                  </div>
                ) : (
                  <div className="text-center py-8 text-muted-foreground text-sm">
                    {isZh ? '点击扫描按钮检测当前市场模式' : 'Click scan to detect current market patterns'}
                  </div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </div>

        {/* Right Panel - Generated Signals */}
        <div className="space-y-6">
          <Card className="h-full">
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <Activity className="w-5 h-5 text-green-500" />
                {isZh ? '生成结果' : 'Generated Signals'}
              </CardTitle>
              <CardDescription>
                {generatedSignals.length > 0
                  ? (isZh ? `${generatedSignals.length} 个信号已生成` : `${generatedSignals.length} signals generated`)
                  : (isZh ? '点击生成按钮创建智能信号' : 'Click generate to create smart signals')}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[calc(100vh-400px)]">
                {generatedSignals.length > 0 ? (
                  <div className="space-y-4">
                    {generatedSignals.map((signal, idx) => (
                      <GeneratedSignalCard
                        key={idx}
                        signal={signal}
                        onViewDetails={() => {
                          setSelectedSignalDetail(signal)
                          setSignalDetailOpen(true)
                        }}
                        onCreate={() => handleCreateSignal(signal)}
                        isCreating={creatingSignals.has(signal.signal_name)}
                        isZh={isZh}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
                    <Sparkles className="w-12 h-12 mb-4 opacity-30" />
                    <p className="text-sm">{isZh ? '尚未生成信号' : 'No signals generated yet'}</p>
                    <p className="text-xs mt-2">{isZh ? '配置参数后点击生成按钮' : 'Configure and click generate'}</p>
                  </div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Signal Detail Dialog */}
      <Dialog open={signalDetailOpen} onOpenChange={setSignalDetailOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{selectedSignalDetail?.signal_name}</DialogTitle>
            <DialogDescription>{selectedSignalDetail?.description}</DialogDescription>
          </DialogHeader>
          {selectedSignalDetail && (
            <div className="space-y-4">
              {/* Full Backtest Metrics */}
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label className="text-muted-foreground">{isZh ? '回测指标' : 'Backtest Metrics'}</Label>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div className="flex justify-between">
                      <span>{isZh ? '胜率' : 'Win Rate'}:</span>
                      <span className="font-mono font-bold">{selectedSignalDetail.backtest_metrics.win_rate.toFixed(1)}%</span>
                    </div>
                    <div className="flex justify-between">
                      <span>{isZh ? '平均收益' : 'Avg Return'}:</span>
                      <span className="font-mono font-bold">{selectedSignalDetail.backtest_metrics.avg_return.toFixed(2)}%</span>
                    </div>
                    <div className="flex justify-between">
                      <span>{isZh ? '夏普比率' : 'Sharpe'}:</span>
                      <span className="font-mono font-bold">{selectedSignalDetail.backtest_metrics.sharpe_ratio.toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>{isZh ? '总交易数' : 'Total Trades'}:</span>
                      <span className="font-mono font-bold">{selectedSignalDetail.backtest_metrics.total_trades}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>{isZh ? '最大回撤' : 'Max DD'}:</span>
                      <span className="font-mono font-bold text-red-500">-{selectedSignalDetail.backtest_metrics.max_drawdown.toFixed(1)}%</span>
                    </div>
                    <div className="flex justify-between">
                      <span>{isZh ? '有效性评分' : 'Effectiveness'}:</span>
                      <span className="font-mono font-bold">{selectedSignalDetail.backtest_metrics.effectiveness_score.toFixed(0)}/100</span>
                    </div>
                  </div>
                </div>
                <div className="space-y-2">
                  <Label className="text-muted-foreground">{isZh ? '建议参数' : 'Recommended Parameters'}</Label>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span>{isZh ? '建议仓位' : 'Position Size'}:</span>
                      <span className="font-mono font-bold">{(selectedSignalDetail.recommended_position_size * 100).toFixed(0)}%</span>
                    </div>
                    <div className="flex justify-between">
                      <span>{isZh ? '止损' : 'Stop Loss'}:</span>
                      <span className="font-mono font-bold text-red-500">{selectedSignalDetail.recommended_stop_loss.toFixed(1)}%</span>
                    </div>
                    <div className="flex justify-between">
                      <span>{isZh ? '止盈' : 'Take Profit'}:</span>
                      <span className="font-mono font-bold text-green-500">{selectedSignalDetail.recommended_take_profit.toFixed(1)}%</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* All Trigger Conditions */}
              <div className="space-y-2">
                <Label className="text-muted-foreground">{isZh ? '全部触发条件' : 'All Trigger Conditions'}</Label>
                <div className="bg-muted/50 rounded-md p-3 space-y-2">
                  {selectedSignalDetail.trigger_conditions.map((cond, idx) => (
                    <div key={idx} className="flex items-center gap-2 text-sm">
                      <CheckCircle2 className="w-4 h-4 text-green-500" />
                      <span className="font-mono">{cond.metric}</span>
                      <span className="text-muted-foreground">{cond.operator}</span>
                      <span className="font-bold">{cond.threshold}</span>
                      {cond.time_window && (
                        <Badge variant="outline" className="text-xs">{cond.time_window}</Badge>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* AI Prompt Template */}
              {selectedSignalDetail.ai_prompt_template && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-muted-foreground flex items-center gap-2">
                      <Sparkles className="w-4 h-4 text-purple-500" />
                      {isZh ? 'AI 提示词模板' : 'AI Prompt Template'}
                    </Label>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        navigator.clipboard.writeText(selectedSignalDetail.ai_prompt_template || '')
                        toast.success(isZh ? '已复制到剪贴板' : 'Copied to clipboard')
                      }}
                    >
                      <Copy className="w-4 h-4 mr-1" />
                      {isZh ? '复制' : 'Copy'}
                    </Button>
                  </div>
                  <ScrollArea className="h-48 bg-slate-900 rounded-md">
                    <pre className="text-xs p-3 text-green-400 whitespace-pre-wrap font-mono">
                      {selectedSignalDetail.ai_prompt_template}
                    </pre>
                  </ScrollArea>
                  <p className="text-xs text-muted-foreground">
                    {isZh 
                      ? '此提示词模板用于 AI 实时判断交易信号。将 {market_data} 替换为实时市场数据后发送给 AI。'
                      : 'This prompt template is used for AI real-time signal judgment. Replace {market_data} with live market data before sending to AI.'
                    }
                  </p>
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setSignalDetailOpen(false)}>
              {isZh ? '关闭' : 'Close'}
            </Button>
            <Button
              onClick={() => {
                if (selectedSignalDetail) {
                  handleCreateSignal(selectedSignalDetail)
                  setSignalDetailOpen(false)
                }
              }}
              disabled={!selectedSignalDetail || creatingSignals.has(selectedSignalDetail?.signal_name || '')}
            >
              <Plus className="w-4 h-4 mr-1" />
              {isZh ? '创建信号' : 'Create Signal'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      
      {/* AI Analysis Dialog */}
      <Dialog open={aiAnalysisOpen} onOpenChange={setAiAnalysisOpen}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-hidden flex flex-col">
          <DialogHeader className="shrink-0">
            <DialogTitle className="flex items-center gap-2">
              <Brain className="w-5 h-5 text-blue-500" />
              {isZh ? `${aiAnalysisSymbol} AI 深度分析` : `${aiAnalysisSymbol} AI Deep Analysis`}
            </DialogTitle>
            <DialogDescription className="flex items-center gap-2">
              {selectedAiTrader && aiTraders.find(t => t.id === selectedAiTrader) && (
                <>
                  <Bot className="w-4 h-4" />
                  {aiTraders.find(t => t.id === selectedAiTrader)?.name} 
                  <span className="text-xs opacity-70">
                    ({aiTraders.find(t => t.id === selectedAiTrader)?.model})
                  </span>
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          
          {/* AI Trader Selector */}
          {aiTraders.length > 1 && (
            <div className="flex items-center gap-2 mb-2">
              <Label className="text-sm">{isZh ? '选择 AI' : 'Select AI'}:</Label>
              <Select 
                value={selectedAiTrader?.toString() || ''} 
                onValueChange={(v) => setSelectedAiTrader(parseInt(v))}
              >
                <SelectTrigger className="w-48">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {aiTraders.map(trader => (
                    <SelectItem key={trader.id} value={trader.id.toString()}>
                      {trader.name} ({trader.model})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          
          {/* AI Analysis Content */}
          <div className="flex-1 overflow-y-auto">
            {aiAnalysisLoading ? (
              <div className="flex flex-col items-center justify-center h-64">
                <Loader2 className="w-8 h-8 animate-spin text-blue-500 mb-4" />
                <p className="text-muted-foreground">
                  {isZh ? 'AI 正在分析市场数据...' : 'AI is analyzing market data...'}
                </p>
              </div>
            ) : aiAnalysisResult ? (
              <div className="prose prose-sm dark:prose-invert max-w-none p-4">
                <div
                  className="text-sm leading-relaxed whitespace-pre-wrap"
                  dangerouslySetInnerHTML={{
                    __html: aiAnalysisResult
                      .replace(/```json[\s\S]*?```/g, '') // Hide JSON block in display
                      .replace(/### /g, '<h3 class="text-base font-semibold mt-4 mb-2">')
                      .replace(/## /g, '<h2 class="text-lg font-bold mt-4 mb-2">')
                      .replace(/# /g, '<h1 class="text-xl font-bold mt-4 mb-2">')
                      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                      .replace(/\*(.*?)\*/g, '<em>$1</em>')
                      .replace(/- /g, '• ')
                      .replace(/\n/g, '<br/>')
                  }}
                />
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
                <Brain className="w-12 h-12 opacity-30 mb-4" />
                <p>{isZh ? '等待 AI 分析结果' : 'Waiting for AI analysis'}</p>
              </div>
            )}
          </div>
          
          {/* AI Suggested Parameters */}
          {aiSuggestedParams && !aiAnalysisLoading && (
            <div className="border rounded-lg p-4 bg-gradient-to-r from-blue-50 to-purple-50 dark:from-blue-950/30 dark:to-purple-950/30">
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-sm font-semibold flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-purple-500" />
                  {isZh ? 'AI 建议参数' : 'AI Suggested Parameters'}
                </h4>
                <Badge variant={aiSuggestedParams.confidence >= 0.7 ? 'default' : 'secondary'}>
                  {isZh ? '置信度' : 'Confidence'}: {(aiSuggestedParams.confidence * 100).toFixed(0)}%
                </Badge>
              </div>
              
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                <div className="bg-white/60 dark:bg-slate-800/60 rounded px-3 py-2">
                  <div className="text-xs text-muted-foreground">{isZh ? '方向' : 'Direction'}</div>
                  <div className="font-medium flex items-center gap-1">
                    {aiSuggestedParams.direction === 'long' ? (
                      <><TrendingUp className="w-3 h-3 text-green-500" /> {isZh ? '做多' : 'Long'}</>
                    ) : aiSuggestedParams.direction === 'short' ? (
                      <><TrendingDown className="w-3 h-3 text-red-500" /> {isZh ? '做空' : 'Short'}</>
                    ) : (
                      <>{isZh ? '观望' : 'Wait'}</>
                    )}
                  </div>
                </div>
                <div className="bg-white/60 dark:bg-slate-800/60 rounded px-3 py-2">
                  <div className="text-xs text-muted-foreground">{isZh ? '策略' : 'Strategy'}</div>
                  <div className="font-medium capitalize">{aiSuggestedParams.strategy}</div>
                </div>
                <div className="bg-white/60 dark:bg-slate-800/60 rounded px-3 py-2">
                  <div className="text-xs text-muted-foreground">{isZh ? '风险等级' : 'Risk Level'}</div>
                  <div className="font-medium capitalize">{aiSuggestedParams.risk_level}</div>
                </div>
                <div className="bg-white/60 dark:bg-slate-800/60 rounded px-3 py-2">
                  <div className="text-xs text-muted-foreground">{isZh ? '时间窗口' : 'Time Window'}</div>
                  <div className="font-medium">{aiSuggestedParams.time_window}</div>
                </div>
              </div>
              
              {(aiSuggestedParams.stop_loss_percent || aiSuggestedParams.take_profit_percent) && (
                <div className="grid grid-cols-2 gap-3 mt-3 text-sm">
                  {aiSuggestedParams.stop_loss_percent && (
                    <div className="bg-red-50 dark:bg-red-950/30 rounded px-3 py-2">
                      <div className="text-xs text-red-600 dark:text-red-400">{isZh ? '建议止损' : 'Stop Loss'}</div>
                      <div className="font-medium text-red-700 dark:text-red-300">{aiSuggestedParams.stop_loss_percent}%</div>
                    </div>
                  )}
                  {aiSuggestedParams.take_profit_percent && (
                    <div className="bg-green-50 dark:bg-green-950/30 rounded px-3 py-2">
                      <div className="text-xs text-green-600 dark:text-green-400">{isZh ? '建议止盈' : 'Take Profit'}</div>
                      <div className="font-medium text-green-700 dark:text-green-300">{aiSuggestedParams.take_profit_percent}%</div>
                    </div>
                  )}
                </div>
              )}
              
              {/* Additional Feedback Loop Parameters */}
              {(aiSuggestedParams.rsi_oversold_threshold || aiSuggestedParams.rsi_overbought_threshold || 
                aiSuggestedParams.macd_sensitivity || aiSuggestedParams.bollinger_band_width_multiplier ||
                aiSuggestedParams.atr_stop_loss_multiplier || aiSuggestedParams.take_profit_ratio) && (
                <div className="mt-3 pt-3 border-t border-muted">
                  <h5 className="text-xs font-semibold text-muted-foreground mb-2 flex items-center gap-1">
                    <Zap className="w-3 h-3 text-yellow-500" />
                    {isZh ? '信号参数调整' : 'Signal Parameter Adjustments'}
                  </h5>
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs">
                    {aiSuggestedParams.rsi_oversold_threshold && (
                      <div className="bg-blue-50 dark:bg-blue-950/30 rounded px-2 py-1">
                        <div className="text-muted-foreground">RSI超卖</div>
                        <div className="font-medium">{aiSuggestedParams.rsi_oversold_threshold}</div>
                      </div>
                    )}
                    {aiSuggestedParams.rsi_overbought_threshold && (
                      <div className="bg-blue-50 dark:bg-blue-950/30 rounded px-2 py-1">
                        <div className="text-muted-foreground">RSI超买</div>
                        <div className="font-medium">{aiSuggestedParams.rsi_overbought_threshold}</div>
                      </div>
                    )}
                    {aiSuggestedParams.macd_sensitivity && (
                      <div className="bg-purple-50 dark:bg-purple-950/30 rounded px-2 py-1">
                        <div className="text-muted-foreground">MACD敏感度</div>
                        <div className="font-medium">{aiSuggestedParams.macd_sensitivity}x</div>
                      </div>
                    )}
                    {aiSuggestedParams.bollinger_band_width_multiplier && (
                      <div className="bg-purple-50 dark:bg-purple-950/30 rounded px-2 py-1">
                        <div className="text-muted-foreground">布林带宽度</div>
                        <div className="font-medium">{aiSuggestedParams.bollinger_band_width_multiplier}x</div>
                      </div>
                    )}
                    {aiSuggestedParams.atr_stop_loss_multiplier && (
                      <div className="bg-orange-50 dark:bg-orange-950/30 rounded px-2 py-1">
                        <div className="text-muted-foreground">ATR止损</div>
                        <div className="font-medium">{aiSuggestedParams.atr_stop_loss_multiplier}x</div>
                      </div>
                    )}
                    {aiSuggestedParams.take_profit_ratio && (
                      <div className="bg-green-50 dark:bg-green-950/30 rounded px-2 py-1">
                        <div className="text-muted-foreground">止盈比例</div>
                        <div className="font-medium">1:{aiSuggestedParams.take_profit_ratio}</div>
                      </div>
                    )}
                  </div>
                </div>
              )}
              
              {/* Risk Assessment */}
              {aiSuggestedParams.risk_assessment !== undefined && (
                <div className="mt-3 pt-3 border-t border-muted">
                  <h5 className="text-xs font-semibold text-muted-foreground mb-2 flex items-center gap-1">
                    <AlertTriangle className="w-3 h-3 text-orange-500" />
                    {isZh ? '风险评估' : 'Risk Assessment'}
                  </h5>
                  <div className="flex items-center gap-2">
                    <div className="w-full bg-muted rounded-full h-2">
                      <div 
                        className={`h-2 rounded-full ${
                          aiSuggestedParams.risk_assessment > 0.7 ? 'bg-red-500' :
                          aiSuggestedParams.risk_assessment > 0.4 ? 'bg-yellow-500' : 'bg-green-500'
                        }`}
                        style={{ width: `${aiSuggestedParams.risk_assessment * 100}%` }}
                      />
                    </div>
                    <span className="text-xs font-medium w-10">
                      {(aiSuggestedParams.risk_assessment * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
              )}
            </div>
          )}
          
          <DialogFooter className="flex-row gap-2 justify-end shrink-0">
            <Button 
              variant="outline" 
              onClick={() => handleAIAnalysis(aiAnalysisSymbol)}
              disabled={aiAnalysisLoading}
            >
              <RefreshCw className={`w-4 h-4 mr-1 ${aiAnalysisLoading ? 'animate-spin' : ''}`} />
              {isZh ? '重新分析' : 'Re-analyze'}
            </Button>
            {aiSuggestedParams && !aiAnalysisLoading && (
              <Button 
                variant="default"
                onClick={handleApplyAIParams}
                className="bg-gradient-to-r from-purple-500 to-blue-500 hover:from-purple-600 hover:to-blue-600"
              >
                <Sparkles className="w-4 h-4 mr-1" />
                {isZh ? '应用建议参数' : 'Apply Suggestions'}
              </Button>
            )}
            <Button variant="ghost" onClick={() => setAiAnalysisOpen(false)}>
              {isZh ? '关闭' : 'Close'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
