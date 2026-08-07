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
  PieChart,
  Pie,
  Cell,
  Legend,
} from 'recharts'
import {
  TrendingUp,
  TrendingDown,
  Activity,
  Target,
  Shield,
  RefreshCw,
  Download,
  DollarSign,
} from 'lucide-react'
import { formatCurrency } from '@/lib/priceFormat'
import {
  PerformanceMetrics,
  PerformanceSummary,
  SymbolPerformance,
} from '@/lib/types/analytics'
import {
  getPerformanceMetrics,
  getPerformanceSummary,
} from '@/lib/api'

interface MetricCardProps {
  title: string
  value: string | number
  subValue?: string
  trend?: 'up' | 'down' | 'neutral'
  icon: React.ReactNode
  color: string
  description?: string
}

function MetricCard({
  title,
  value,
  subValue,
  trend,
  icon,
  color,
  description,
}: MetricCardProps) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <p className="text-sm font-medium text-muted-foreground">{title}</p>
            <p className="text-2xl font-bold" style={{ color }}>
              {value}
            </p>
            {subValue && (
              <p className="text-xs text-muted-foreground">{subValue}</p>
            )}
            {trend && (
              <div className="flex items-center gap-1">
                {trend === 'up' && (
                  <TrendingUp className="h-3 w-3 text-green-500" />
                )}
                {trend === 'down' && (
                  <TrendingDown className="h-3 w-3 text-red-500" />
                )}
              </div>
            )}
          </div>
          <div
            className="p-3 rounded-full"
            style={{ backgroundColor: `${color}20` }}
          >
            {icon}
          </div>
        </div>
        {description && (
          <p className="text-xs text-muted-foreground mt-2">{description}</p>
        )}
      </CardContent>
    </Card>
  )
}

interface PerformanceDashboardProps {
  accountId?: number
  tradingMode?: string
}

export default function PerformanceDashboard({
  accountId,
  tradingMode = 'mainnet',
}: PerformanceDashboardProps) {
  useTranslation()
  const [period, setPeriod] = useState('30')
  const [metricsState, setMetrics] = useState<any>(null)
  const [summaryState, setSummary] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('overview')

  // Safe metrics with defaults for all properties
  const metricsData = metricsState || {
    total_pnl: 0,
    net_pnl: 0,
    trade_count: 0,
    win_count: 0,
    loss_count: 0,
    win_rate: 0,
    avg_win: 0,
    avg_loss: 0,
    profit_factor: 0,
    expectancy: 0,
    sharpe_ratio: 0,
    sortino_ratio: 0,
    calmar_ratio: 0,
    max_drawdown: 0,
    max_drawdown_pct: 0,
    current_drawdown: 0,
    volatility: 0,
    var_95: 0,
    recovery_factor: 0,
    risk_reward_ratio: 0,
    expectancy_ratio: 0,
    avg_holding_period: 0,
    longest_holding_period: 0,
    trades_per_day: 0,
    consecutive_wins: 0,
    consecutive_losses: 0,
    avg_time_to_first_profit: 0,
    final_equity: 0,
    initial_equity: 0,
    max_equity: 0,
    min_equity: 0,
    best_trade_pct: 0,
    worst_trade_pct: 0,
  }

  const summaryData = summaryState || {
    status: 'no_data',
    period: { start: 'N/A', end: 'N/A' },
    returns: { total_pnl: 0, total_pnl_pct: 0, avg_trade_pnl: 0, best_trade: 0, worst_trade: 0 },
    risk: { max_drawdown_pct: 0, current_drawdown: 0, volatility: 0, sharpe_ratio: 0, sortino_ratio: 0, var_95: 0 },
    efficiency: { win_rate: 0, profit_factor: 0, expectancy: 0, avg_holding_hours: 0 },
    consistency: { consecutive_wins: 0, consecutive_losses: 0, trades_per_day: 0 },
  }

  // Safe metric extractor - returns 0 for null/undefined values
  const safeMetric = (value: number | null | undefined): number => {
    if (value === null || value === undefined || isNaN(value)) {
      return 0
    }
    return value
  }

  const formatNum = (num: number | null | undefined, decimals = 2): string => {
    const val = safeMetric(num)
    if (val === 0) return '0'
    return val.toFixed(decimals)
  }

  const formatPercent = (num: number | null | undefined): string => {
    const val = num ?? 0
    if (val >= 0) return `+${val.toFixed(2)}%`
    return `${val.toFixed(2)}%`
  }

  useEffect(() => {
    loadData()
  }, [period, accountId, tradingMode])

  const loadData = async () => {
    setLoading(true)
    try {
      const [metricsResponse, summaryResponse] = await Promise.all([
        getPerformanceMetrics({
          account_id: accountId,
          trading_mode: tradingMode,
        }),
        getPerformanceSummary({
          account_id: accountId,
          trading_mode: tradingMode,
        }),
      ])
      setMetrics(metricsResponse)
      setSummary(summaryResponse)
    } catch (error) {
      console.error('Failed to load performance data:', error)
      setMetrics(null)
      setSummary(null)
    } finally {
      setLoading(false)
    }
  }

  const getScoreColor = (score: number): string => {
    if (score >= 8) return 'text-green-500'
    if (score >= 6) return 'text-yellow-500'
    if (score >= 4) return 'text-orange-500'
    return 'text-red-500'
  }

  const getScoreLabel = (score: number): string => {
    if (score >= 8) return '优秀'
    if (score >= 6) return '良好'
    if (score >= 4) return '一般'
    return '较差'
  }

  const formatNumber = (num: number): string => {
    if (Math.abs(num) >= 1000000) {
      return `${(num / 1000000).toFixed(2)}M`
    }
    if (Math.abs(num) >= 1000) {
      return `${(num / 1000).toFixed(2)}K`
    }
    return num.toFixed(2)
  }

  const scoreData = metricsData
    ? [
        { name: 'Excellent', value: summaryData?.score_distribution?.excellent || 0, color: '#22c55e' },
        { name: 'Good', value: summaryData?.score_distribution?.good || 0, color: '#eab308' },
        { name: 'Fair', value: summaryData?.score_distribution?.acceptable || 0, color: '#f97316' },
        { name: 'Poor', value: summaryData?.score_distribution?.poor || 0, color: '#ef4444' },
      ].filter(d => d.value > 0)
    : []

  const symbolData = metricsData?.by_symbol
    ? Object.entries(metricsData.by_symbol)
        .map(([symbol, data]) => ({
          symbol,
          ...(typeof data === 'object' && data !== null ? data : {}),
        } as { symbol: string; pnl: number; pnl_pct: number; trades: number; win_rate: number }))
        .sort((a, b) => b.pnl - a.pnl)
    : []

  const winLossData = metricsData
    ? [
        { name: 'Wins', value: metricsData.winning_trades, color: '#22c55e' },
        { name: 'Losses', value: metricsData.losing_trades, color: '#ef4444' },
      ]
    : []

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <RefreshCw className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!metricsData || !summaryData) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">暂无性能数据</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">性能看板</h2>
          <p className="text-muted-foreground">
            跟踪您的交易性能指标
          </p>
        </div>
        <div className="flex items-center gap-4">
          <Select value={period} onValueChange={setPeriod}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder="时间范围" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7">近7天</SelectItem>
              <SelectItem value="30">近30天</SelectItem>
              <SelectItem value="90">近90天</SelectItem>
              <SelectItem value="365">近1年</SelectItem>
            </SelectContent>
          </Select>
          <Button onClick={loadData} variant="outline" size="icon">
            <RefreshCw className="h-4 w-4" />
          </Button>
          <Button variant="outline">
            <Download className="h-4 w-4 mr-2" />
            导出
          </Button>
        </div>
      </div>

      {/* Key Metrics Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="总盈亏"
          value={formatCurrency(safeMetric(metricsData?.net_pnl))}
          subValue={formatPercent(safeMetric(summaryData?.returns?.total_pnl_pct))}
          trend={safeMetric(metricsData?.net_pnl) >= 0 ? 'up' : 'down'}
          icon={<DollarSign className="h-6 w-6 text-green-500" />}
          color={safeMetric(metricsData?.net_pnl) >= 0 ? '#22c55e' : '#ef4444'}
          description="总利润与亏损"
        />
        <MetricCard
          title="胜率"
          value={`${(safeMetric(metricsData?.win_rate) * 100).toFixed(1)}%`}
          subValue={`${safeMetric(metricsData?.win_count)} / ${safeMetric(metricsData?.trade_count)} 笔交易`}
          trend={safeMetric(metricsData?.win_rate) >= 0.5 ? 'up' : 'down'}
          icon={<Target className="h-6 w-6 text-blue-500" />}
          color={safeMetric(metricsData?.win_rate) >= 0.5 ? '#3b82f6' : '#f97316'}
          description="盈利交易占比"
        />
        <MetricCard
          title="夏普比率"
          value={formatNum(metricsData?.sharpe_ratio, 2)}
          subValue={safeMetric(metricsData?.sharpe_ratio) >= 1 ? '良好' : '待改进'}
          trend={safeMetric(metricsData?.sharpe_ratio) >= 1 ? 'up' : 'down'}
          icon={<Activity className="h-6 w-6 text-purple-500" />}
          color={safeMetric(metricsData?.sharpe_ratio) >= 1 ? '#a855f7' : '#ef4444'}
          description="风险调整后收益"
        />
        <MetricCard
          title="最大回撤"
          value={`-${formatNum(metricsData?.max_drawdown_pct, 2)}%`}
          subValue={formatCurrency(safeMetric(metricsData?.max_drawdown))}
          icon={<Shield className="h-6 w-6 text-red-500" />}
          color="#ef4444"
          description="最大权益下降"
        />
      </div>

      {/* Additional Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
        <Card>
          <CardContent className="pt-6">
            <p className="text-xs text-muted-foreground">盈利因子</p>
            <p className="text-xl font-bold">{formatNum(metricsData?.profit_factor, 2)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <p className="text-xs text-muted-foreground">期望值</p>
            <p className="text-xl font-bold">{formatNum(metricsData?.expectancy, 4)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <p className="text-xs text-muted-foreground">平均盈利</p>
            <p className="text-xl font-bold text-green-500">
              +{formatNum(metricsData?.avg_win, 2)}%
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <p className="text-xs text-muted-foreground">平均亏损</p>
            <p className="text-xl font-bold text-red-500">
              {formatNum(metricsData?.avg_loss, 2)}%
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <p className="text-xs text-muted-foreground">平均持仓时间</p>
            <p className="text-xl font-bold">{formatNum(metricsData?.avg_holding_period, 1)}小时</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <p className="text-xs text-muted-foreground">日均交易</p>
            <p className="text-xl font-bold">{formatNum(metricsData?.trades_per_day, 1)}笔</p>
          </CardContent>
        </Card>
      </div>

      {/* Tabs for different views */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="trades">按交易</TabsTrigger>
          <TabsTrigger value="symbols">按币种</TabsTrigger>
          <TabsTrigger value="risk">风险分析</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Win/Loss Distribution */}
            <Card>
              <CardHeader>
                <CardTitle>胜负分布</CardTitle>
              </CardHeader>
              <CardContent>
                <ResponsiveContainer width="100%" height={200}>
                  <PieChart>
                    <Pie
                      data={winLossData}
                      cx="50%"
                      cy="50%"
                      innerRadius={60}
                      outerRadius={80}
                      paddingAngle={5}
                      dataKey="value"
                    >
                      {winLossData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>

            {/* Score Distribution */}
            <Card>
              <CardHeader>
                <CardTitle>交易质量评分</CardTitle>
                <CardDescription>按复盘评分分布</CardDescription>
              </CardHeader>
              <CardContent>
                {scoreData.length > 0 ? (
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={scoreData}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="name" />
                      <YAxis />
                      <Tooltip />
                      <Bar dataKey="value" fill="#8884d8">
                        {scoreData.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={entry.color} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="text-muted-foreground text-center py-8">
                    暂无复盘数据
                  </p>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="trades" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>交易表现</CardTitle>
              <CardDescription>最佳与最差交易</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <h4 className="font-medium text-green-500 mb-3 flex items-center gap-2">
                    <TrendingUp className="h-4 w-4" /> 最佳交易
                  </h4>
                  <div className="space-y-2">
                    <div className="flex justify-between p-2 bg-green-50 dark:bg-green-950 rounded">
                      <span>单笔最佳</span>
                      <span className="font-bold text-green-600">
                        +{formatNum(metricsData.best_trade_pct)}%
                      </span>
                    </div>
                    <div className="flex justify-between p-2 bg-green-50 dark:bg-green-950 rounded">
                      <span>平均盈利</span>
                      <span className="font-bold text-green-600">
                        +{formatNum(metricsData.avg_win)}%
                      </span>
                    </div>
                    <div className="flex justify-between p-2 bg-green-50 dark:bg-green-950 rounded">
                      <span>连续盈利</span>
                      <span className="font-bold text-green-600">
                        {metricsData.consecutive_wins || 0}次
                      </span>
                    </div>
                  </div>
                </div>
                <div>
                  <h4 className="font-medium text-red-500 mb-3 flex items-center gap-2">
                    <TrendingDown className="h-4 w-4" /> 最差交易
                  </h4>
                  <div className="space-y-2">
                    <div className="flex justify-between p-2 bg-red-50 dark:bg-red-950 rounded">
                      <span>单笔最差</span>
                      <span className="font-bold text-red-600">
                        {formatNum(metricsData.worst_trade_pct)}%
                      </span>
                    </div>
                    <div className="flex justify-between p-2 bg-red-50 dark:bg-red-950 rounded">
                      <span>平均亏损</span>
                      <span className="font-bold text-red-600">
                        -{formatNum(Math.abs(metricsData.avg_loss || 0))}%
                      </span>
                    </div>
                    <div className="flex justify-between p-2 bg-red-50 dark:bg-red-950 rounded">
                      <span>连续亏损</span>
                      <span className="font-bold text-red-600">
                        {metricsData.consecutive_losses || 0}次
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="symbols" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>币种表现</CardTitle>
              <CardDescription>不同交易对的性能明细</CardDescription>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={400}>
                <BarChart data={symbolData} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis type="number" />
                  <YAxis dataKey="symbol" type="category" width={80} />
                  <Tooltip formatter={(value: number) => formatCurrency(value)} />
                  <Bar
                    dataKey="pnl"
                    fill="#8884d8"
                  >
                    {symbolData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.pnl >= 0 ? '#22c55e' : '#ef4444'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          {/* Symbol Table */}
          <Card>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="text-left py-3 px-4">币种</th>
                      <th className="text-right py-3 px-4">交易数</th>
                      <th className="text-right py-3 px-4">胜率</th>
                      <th className="text-right py-3 px-4">盈亏</th>
                      <th className="text-right py-3 px-4">平均收益</th>
                    </tr>
                  </thead>
                  <tbody>
                    {symbolData.map((item) => (
                      <tr key={item.symbol} className="border-b hover:bg-muted/50">
                        <td className="py-3 px-4 font-medium">{item.symbol}</td>
                        <td className="text-right py-3 px-4">{item.trades}</td>
                        <td
                          className={`text-right py-3 px-4 ${
                            item.win_rate >= 0.5 ? 'text-green-500' : 'text-red-500'
                          }`}
                        >
                          {(item.win_rate * 100).toFixed(1)}%
                        </td>
                        <td
                          className={`text-right py-3 px-4 font-medium ${
                            item.pnl >= 0 ? 'text-green-500' : 'text-red-500'
                          }`}
                        >
                          {formatCurrency(item.pnl)}
                        </td>
                        <td
                          className={`text-right py-3 px-4 ${
                            item.pnl_pct >= 0 ? 'text-green-500' : 'text-red-500'
                          }`}
                        >
                          {item.pnl_pct >= 0 ? '+' : ''}
                          {item.pnl_pct.toFixed(2)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="risk" className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <CardHeader>
                <CardTitle>风险指标</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div className="flex justify-between items-center">
                    <span>最大回撤</span>
                    <span className="font-bold text-red-500">
                      -{formatNum(metricsData.max_drawdown_pct)}%
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span>当前回撤</span>
                    <span className="font-bold">
                      -{formatNum(metricsData.current_drawdown)}%
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span>年化波动率</span>
                    <span className="font-bold">{formatNum(metricsData.volatility)}%</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span>夏普比率</span>
                    <span className="font-bold">{formatNum(metricsData.sharpe_ratio)}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span>索提诺比率</span>
                    <span className="font-bold">{formatNum(metricsData.sortino_ratio)}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span>卡尔玛比率</span>
                    <span className="font-bold">{formatNum(metricsData.calmar_ratio)}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span>在险价值 (95%)</span>
                    <span className="font-bold text-red-500">
                      {formatNum(metricsData.var_95)}%
                    </span>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>风险评估</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div className="p-4 rounded-lg bg-muted">
                    <h4 className="font-medium mb-2">综合风险评分</h4>
                    <div className="text-3xl font-bold mb-2">
                      <span className={getScoreColor(safeMetric(metricsData.sharpe_ratio))}>
                        {getScoreLabel(safeMetric(metricsData.sharpe_ratio))}
                      </span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-2">
                      <div
                        className={`h-2 rounded-full ${
                          safeMetric(metricsData.sharpe_ratio) >= 1
                            ? 'bg-green-500'
                            : safeMetric(metricsData.sharpe_ratio) >= 0.5
                            ? 'bg-yellow-500'
                            : 'bg-red-500'
                        }`}
                        style={{
                          width: `${Math.min(safeMetric(metricsData.sharpe_ratio) * 50, 100)}%`,
                        }}
                      />
                    </div>
                  </div>

                  <div className="p-4 rounded-lg bg-muted">
                    <h4 className="font-medium mb-2">恢复因子</h4>
                    <p className="text-2xl font-bold">{formatNum(metricsData.recovery_factor)}</p>
                    <p className="text-xs text-muted-foreground">
                      {safeMetric(metricsData.recovery_factor) >= 2
                        ? '良好：能有效从亏损中恢复'
                        : safeMetric(metricsData.recovery_factor) >= 1
                        ? '一般：需要改进'
                        : '较差：难以从亏损中恢复'}
                    </p>
                  </div>

                  <div className="p-4 rounded-lg bg-muted">
                    <h4 className="font-medium mb-2">风险收益比</h4>
                    <p className="text-2xl font-bold">
                      1:{formatNum(metricsData.risk_reward_ratio)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {safeMetric(metricsData.risk_reward_ratio) >= 2
                        ? '良好的风险收益配置'
                        : '建议寻找更好的机会'}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}
