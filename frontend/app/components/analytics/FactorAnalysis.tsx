import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  Legend,
  Cell,
} from 'recharts'
import { Activity, RefreshCw } from 'lucide-react'
import {
  AdaptiveParameters,
  FactorValue,
  FactorCategory,
} from '@/lib/types/analytics'
import {
  getAdaptiveParameters,
  getFactorValues,
  getAllAdaptiveParameters,
} from '@/lib/api'
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs'

const CATEGORY_COLORS: Record<FactorCategory, string> = {
  momentum: '#3b82f6',
  mean_reversion: '#8b5cf6',
  volatility: '#f97316',
  volume: '#06b6d4',
  trend: '#10b981',
  market_flow: '#ec4899',
  strength: '#f59e0b',
  pattern: '#6366f1',
}

const REGIME_COLORS: Record<string, string> = {
  breakout: '#22c55e',
  continuation: '#3b82f6',
  reversal: '#8b5cf6',
  absorption: '#f97316',
  exhaustion: '#ef4444',
  noise: '#6b7280',
}

interface FactorAnalysisProps {
  accountId?: number
  tradingMode?: string
}

export default function FactorAnalysis({
  accountId,
  tradingMode = 'mainnet',
}: FactorAnalysisProps) {
  useTranslation()
  const { symbols: configuredPairs } = useTradingPairs()
  const [symbol, setSymbol] = useState('BTC')
  const [symbols, setSymbols] = useState<string[]>(
    configuredPairs.length > 0 ? configuredPairs.slice(0, 3) : FALLBACK_TRADING_PAIRS.slice(0, 3)
  )
  const [adaptiveParams, setAdaptiveParams] = useState<AdaptiveParameters | null>(null)
  const [factorValues, setFactorValues] = useState<FactorValue[]>([])
  const [allAdaptiveParams, setAllAdaptiveParams] = useState<Record<string, AdaptiveParameters>>({})
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('factors')

  useEffect(() => {
    loadData()
  }, [symbol, accountId, tradingMode])

  const loadData = async () => {
    setLoading(true)
    try {
      const [paramsData, factorsData, allParamsData] = await Promise.all([
        getAdaptiveParameters(symbol, { account_id: accountId, trading_mode: tradingMode }),
        getFactorValues(symbol, { account_id: accountId, trading_mode: tradingMode }),
        getAllAdaptiveParameters({ account_id: accountId, trading_mode: tradingMode }),
      ])
      setAdaptiveParams(paramsData)
      setFactorValues(factorsData.factors || [])
      setAllAdaptiveParams(allParamsData)
      
      if (Object.keys(allParamsData).length > 0) {
        setSymbols(Object.keys(allParamsData))
      }
    } catch (error) {
      console.error('Failed to load factor data:', error)
    } finally {
      setLoading(false)
    }
  }

  const getCategoryData = () => {
    const categoryMap: Record<string, { total: number; count: number; color: string }> = {}
    
    factorValues.forEach((factor) => {
      if (!categoryMap[factor.category]) {
        categoryMap[factor.category] = {
          total: 0,
          count: 0,
          color: CATEGORY_COLORS[factor.category] || '#6b7280',
        }
      }
      categoryMap[factor.category].total += factor.value
      categoryMap[factor.category].count += 1
    })

    return Object.entries(categoryMap).map(([category, data]) => ({
      category: category.replace('_', ' '),
      average: data.count > 0 ? data.total / data.count : 0,
      count: data.count,
      color: data.color,
    }))
  }

  const getWeightData = () => {
    if (!adaptiveParams) return []
    return Object.entries(adaptiveParams.factor_weights || {})
      .map(([factor, weight]) => ({
        factor,
        weight: weight * 100,
      }))
      .sort((a, b) => b.weight - a.weight)
  }

  const getRegimeData = () => {
    return Object.entries(allAdaptiveParams).map(([sym, params]) => ({
      symbol: sym,
      confidence: params.regime_confidence * 100,
      regime: params.market_regime,
    }))
  }

  const formatValue = (value: number): string => {
    if (Math.abs(value) >= 1) {
      return value.toFixed(2)
    }
    if (Math.abs(value) >= 0.01) {
      return value.toFixed(4)
    }
    return value.toFixed(6)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <RefreshCw className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const categoryData = getCategoryData()
  const weightData = getWeightData()
  const regimeData = getRegimeData()

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">因子分析</h2>
          <p className="text-muted-foreground">
            分析因子权重和市场状态适配
          </p>
        </div>
        <div className="flex items-center gap-4">
          <Select value={symbol} onValueChange={setSymbol}>
            <SelectTrigger className="w-32">
              <SelectValue placeholder="币种" />
            </SelectTrigger>
            <SelectContent>
              {symbols.map((sym) => (
                <SelectItem key={sym} value={sym}>
                  {sym}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button onClick={loadData} variant="outline" size="icon">
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Market Regime Banner */}
      {adaptiveParams && (
        <Card
          className="border-2"
          style={{ borderColor: REGIME_COLORS[adaptiveParams.market_regime] || '#6b7280' }}
        >
          <CardContent className="pt-6">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-4">
                <div
                  className="p-3 rounded-full"
                  style={{
                    backgroundColor: `${REGIME_COLORS[adaptiveParams.market_regime]}20`,
                  }}
                >
                  <Activity
                    className="h-6 w-6"
                    style={{ color: REGIME_COLORS[adaptiveParams.market_regime] }}
                  />
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">当前市场状态</p>
                  <p
                    className="text-2xl font-bold capitalize"
                    style={{ color: REGIME_COLORS[adaptiveParams.market_regime] }}
                  >
                    {adaptiveParams.market_regime}
                  </p>
                </div>
              </div>
              <div className="text-right">
                <p className="text-sm text-muted-foreground">置信度</p>
                <p className="text-xl font-bold">
                  {(adaptiveParams.regime_confidence * 100).toFixed(0)}%
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Execution Parameters */}
      {adaptiveParams && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card>
            <CardContent className="pt-6">
              <p className="text-xs text-muted-foreground">仓位比例</p>
              <p className="text-xl font-bold text-blue-500">
                {(adaptiveParams.execution_parameters.position_size_pct * 100).toFixed(0)}%
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <p className="text-xs text-muted-foreground">止损</p>
              <p className="text-xl font-bold text-red-500">
                {(adaptiveParams.execution_parameters.stop_loss_pct * 100).toFixed(1)}%
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <p className="text-xs text-muted-foreground">止盈</p>
              <p className="text-xl font-bold text-green-500">
                {(adaptiveParams.execution_parameters.take_profit_pct * 100).toFixed(1)}%
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <p className="text-xs text-muted-foreground">风险收益比</p>
              <p className="text-xl font-bold">
                1:{adaptiveParams.execution_parameters.risk_reward_ratio.toFixed(1)}
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="factors">因子值</TabsTrigger>
          <TabsTrigger value="weights">权重</TabsTrigger>
          <TabsTrigger value="regimes">市场状态</TabsTrigger>
          <TabsTrigger value="radar">雷达图</TabsTrigger>
        </TabsList>

        <TabsContent value="factors" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>当前因子值</CardTitle>
              <CardDescription>{symbol} 的实时因子读数</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                {factorValues.map((factor) => (
                  <div
                    key={factor.name}
                    className="p-4 rounded-lg bg-muted hover:bg-muted/80 transition-colors"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium">{factor.name}</span>
                      <span
                        className="text-xs px-2 py-1 rounded-full"
                        style={{
                          backgroundColor: `${CATEGORY_COLORS[factor.category]}20`,
                          color: CATEGORY_COLORS[factor.category],
                        }}
                      >
                        {factor.category.replace('_', ' ')}
                      </span>
                    </div>
                    <p className="text-2xl font-bold">{formatValue(factor.value)}</p>
                    <div className="mt-2 h-1 bg-gray-200 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.min(Math.abs(factor.normalized) * 100, 100)}%`,
                          backgroundColor: factor.value >= 0 ? '#22c55e' : '#ef4444',
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>因子概览</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={categoryData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="category" />
                  <YAxis />
                  <Tooltip />
                  <Bar dataKey="average" fill="#8884d8" name="Average Value">
                    {categoryData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.color} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="weights" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>自适应因子权重</CardTitle>
              <CardDescription>
                基于当前检测市场状态的权重
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={400}>
                <BarChart data={weightData} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis type="number" domain={[0, 'dataMax']} />
                  <YAxis dataKey="factor" type="category" width={100} />
                  <Tooltip formatter={(value: number) => `${value.toFixed(1)}%`} />
                  <Bar dataKey="weight" fill="#3b82f6" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="regimes" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>各币种市场状态</CardTitle>
              <CardDescription>
                所有跟踪币种的检测市场状态
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {regimeData.map((item) => (
                  <div
                    key={item.symbol}
                    className="flex items-center justify-between p-4 rounded-lg bg-muted"
                  >
                    <div className="flex items-center gap-4">
                      <span className="font-bold text-lg w-16">{item.symbol}</span>
                      <span
                        className="px-3 py-1 rounded-full text-sm font-medium capitalize"
                        style={{
                          backgroundColor: `${REGIME_COLORS[item.regime]}20`,
                          color: REGIME_COLORS[item.regime],
                        }}
                      >
                        {item.regime}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-muted-foreground">置信度:</span>
                      <div className="w-32 h-2 bg-gray-200 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${item.confidence}%`,
                            backgroundColor: REGIME_COLORS[item.regime],
                          }}
                        />
                      </div>
                      <span className="text-sm font-medium">{item.confidence.toFixed(0)}%</span>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="radar" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>因子雷达图</CardTitle>
              <CardDescription>因子重要性可视化对比</CardDescription>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={500}>
                <RadarChart data={weightData.slice(0, 8)}>
                  <PolarGrid />
                  <PolarAngleAxis dataKey="factor" />
                  <PolarRadiusAxis angle={30} domain={[0, 'dataMax']} />
                  <Radar
                    name="Weight"
                    dataKey="weight"
                    stroke="#3b82f6"
                    fill="#3b82f6"
                    fillOpacity={0.5}
                  />
                  <Legend />
                </RadarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
