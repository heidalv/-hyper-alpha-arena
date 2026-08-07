'use client'

import { useState, useEffect, useRef } from 'react'
import { createChart, LineSeries, Time, IChartApi } from 'lightweight-charts'
import { formatChartTime } from '../../lib/dateTime'
import { 
  TrendingUp, TrendingDown, BarChart3, Clock, Target, 
  AlertTriangle, DollarSign, Percent, Activity, X,
  ChevronDown, ChevronUp, Play, Settings
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

// Type definitions
interface TradeResult {
  entry_time: number
  exit_time: number
  entry_price: number
  exit_price: number
  direction: string
  pnl_percent: number
  pnl_usd: number
  hold_duration_min: number
  trigger_value: number
  trigger_threshold: number
}

interface BacktestSummary {
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  total_pnl_percent: number
  total_pnl_usd: number
  avg_pnl_percent: number
  avg_win_percent: number
  avg_loss_percent: number
  profit_factor: number
  max_drawdown_percent: number
  sharpe_ratio: number
  avg_hold_duration_min: number
  max_consecutive_wins: number
  max_consecutive_losses: number
  best_trade_pnl: number
  worst_trade_pnl: number
  start_time: number
  end_time: number
  period_days: number
  trades_per_day: number
}

interface EquityCurvePoint {
  time: number
  equity: number
  pnl: number
}

interface TimeAnalysis {
  hourly_distribution: Record<number, number>
  daily_distribution: Record<number, number>
  total_triggers: number
}

interface BacktestConfig {
  position_size_usd: number
  take_profit_percent: number
  stop_loss_percent: number
  max_hold_bars: number
  use_trailing_stop: boolean
  trailing_stop_percent: number
  commission_percent: number
}

interface BacktestResult {
  signal_id?: number
  pool_id?: number
  signal_name?: string
  pool_name?: string
  symbol: string
  period_days: number
  config: BacktestConfig
  trigger_count: number
  trade_count: number
  trades: TradeResult[]
  summary: BacktestSummary | null
  equity_curve: EquityCurvePoint[]
  time_analysis?: TimeAnalysis
  signal_stats?: Array<{
    signal_id: number
    signal_name: string
    total_triggers: number
    pool_contribution: number
    contribution_percent: number
  }>
  message?: string
  error?: string
}

interface BacktestResultPanelProps {
  result: BacktestResult | null
  loading: boolean
  onClose: () => void
  onRunBacktest: (config: BacktestConfig) => void
}

// Format duration
function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes.toFixed(0)}m`
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)}h`
  return `${(minutes / 1440).toFixed(1)}d`
}

// Format timestamp
function formatTime(timestamp: number): string {
  return new Date(timestamp).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

// Day names
const DAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

export default function BacktestResultPanel({ 
  result, 
  loading, 
  onClose,
  onRunBacktest 
}: BacktestResultPanelProps) {
  const [showConfig, setShowConfig] = useState(false)
  const [showTrades, setShowTrades] = useState(false)
  const [config, setConfig] = useState<BacktestConfig>({
    position_size_usd: 1000,
    take_profit_percent: 2.0,
    stop_loss_percent: 1.0,
    max_hold_bars: 20,
    use_trailing_stop: false,
    trailing_stop_percent: 0.5,
    commission_percent: 0.04
  })
  
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  
  // Draw equity curve
  useEffect(() => {
    if (!chartContainerRef.current || !result?.equity_curve?.length) return
    
    // Clean up previous chart
    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
    }
    
    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: 200,
      layout: {
        background: { color: 'transparent' },
        textColor: '#9ca3af',
      },
      grid: {
        vertLines: { color: '#374151' },
        horzLines: { color: '#374151' },
      },
      rightPriceScale: {
        borderColor: '#374151',
      },
      timeScale: {
        borderColor: '#374151',
        timeVisible: true,
      },
    })
    
    chartRef.current = chart
    
    const lineSeries = chart.addSeries(LineSeries, {
      color: result.summary && result.summary.total_pnl_percent >= 0 ? '#22c55e' : '#ef4444',
      lineWidth: 2,
    })
    
    const chartData = result.equity_curve.map((p, idx) => ({
      time: (idx === 0 ? Date.now() / 1000 - result.period_days * 86400 : p.time / 1000) as Time,
      value: p.equity,
    }))
    
    lineSeries.setData(chartData)
    chart.timeScale().fitContent()
    
    return () => {
      if (chartRef.current) {
        chartRef.current.remove()
        chartRef.current = null
      }
    }
  }, [result])
  
  if (loading) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
        <div className="bg-slate-800 rounded-lg p-8 flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-300">正在运行回测分析...</p>
        </div>
      </div>
    )
  }
  
  if (!result) return null
  
  if (result.error) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
        <div className="bg-slate-800 rounded-lg p-6 max-w-md">
          <div className="flex items-center gap-2 text-red-400 mb-4">
            <AlertTriangle className="w-5 h-5" />
            <span className="font-medium">回测失败</span>
          </div>
          <p className="text-gray-300 mb-4">{result.error}</p>
          <Button onClick={onClose}>关闭</Button>
        </div>
      </div>
    )
  }
  
  const summary = result.summary
  const name = result.signal_name || result.pool_name || '回测结果'
  
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4 overflow-auto">
      <div className="bg-slate-800 rounded-lg w-full max-w-4xl max-h-[90vh] overflow-auto">
        {/* Header */}
        <div className="sticky top-0 bg-slate-800 border-b border-slate-700 p-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <BarChart3 className="w-5 h-5 text-blue-400" />
              回测分析: {name}
            </h2>
            <p className="text-sm text-gray-400">
              {result.symbol} · {result.period_days}天 · {result.trigger_count}次触发 · {result.trade_count}笔交易
            </p>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-slate-700 rounded">
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>
        
        {/* Content */}
        <div className="p-4 space-y-4">
          {/* Message if no trades */}
          {result.message && (
            <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4 text-yellow-300">
              {result.message}
            </div>
          )}
          
          {/* Config Section */}
          <div className="bg-slate-900/50 rounded-lg p-4">
            <button 
              onClick={() => setShowConfig(!showConfig)}
              className="flex items-center justify-between w-full text-left"
            >
              <div className="flex items-center gap-2">
                <Settings className="w-4 h-4 text-gray-400" />
                <span className="text-sm font-medium text-gray-300">回测参数配置</span>
              </div>
              {showConfig ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
            </button>
            
            {showConfig && (
              <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <Label className="text-xs text-gray-400">仓位大小 (USD)</Label>
                  <Input
                    type="number"
                    value={config.position_size_usd}
                    onChange={e => setConfig({...config, position_size_usd: parseFloat(e.target.value) || 1000})}
                    className="mt-1 bg-slate-800 border-slate-600"
                  />
                </div>
                <div>
                  <Label className="text-xs text-gray-400">止盈 (%)</Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={config.take_profit_percent}
                    onChange={e => setConfig({...config, take_profit_percent: parseFloat(e.target.value) || 2})}
                    className="mt-1 bg-slate-800 border-slate-600"
                  />
                </div>
                <div>
                  <Label className="text-xs text-gray-400">止损 (%)</Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={config.stop_loss_percent}
                    onChange={e => setConfig({...config, stop_loss_percent: parseFloat(e.target.value) || 1})}
                    className="mt-1 bg-slate-800 border-slate-600"
                  />
                </div>
                <div>
                  <Label className="text-xs text-gray-400">最大持仓K线数</Label>
                  <Input
                    type="number"
                    value={config.max_hold_bars}
                    onChange={e => setConfig({...config, max_hold_bars: parseInt(e.target.value) || 20})}
                    className="mt-1 bg-slate-800 border-slate-600"
                  />
                </div>
                <div className="col-span-2 md:col-span-4">
                  <Button 
                    onClick={() => onRunBacktest(config)}
                    className="bg-blue-600 hover:bg-blue-700"
                  >
                    <Play className="w-4 h-4 mr-2" />
                    重新运行回测
                  </Button>
                </div>
              </div>
            )}
          </div>
          
          {/* Summary Stats */}
          {summary && (
            <>
              {/* Key Metrics */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {/* Total PnL */}
                <div className={`rounded-lg p-4 ${summary.total_pnl_percent >= 0 ? 'bg-green-500/10 border border-green-500/30' : 'bg-red-500/10 border border-red-500/30'}`}>
                  <div className="flex items-center gap-2 mb-1">
                    <DollarSign className={`w-4 h-4 ${summary.total_pnl_percent >= 0 ? 'text-green-400' : 'text-red-400'}`} />
                    <span className="text-xs text-gray-400">总收益</span>
                  </div>
                  <div className={`text-xl font-bold ${summary.total_pnl_percent >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {summary.total_pnl_percent >= 0 ? '+' : ''}{summary.total_pnl_percent.toFixed(2)}%
                  </div>
                  <div className={`text-sm ${summary.total_pnl_percent >= 0 ? 'text-green-300' : 'text-red-300'}`}>
                    ${summary.total_pnl_usd.toFixed(2)}
                  </div>
                </div>
                
                {/* Win Rate */}
                <div className="bg-slate-900/50 rounded-lg p-4 border border-slate-700">
                  <div className="flex items-center gap-2 mb-1">
                    <Target className="w-4 h-4 text-blue-400" />
                    <span className="text-xs text-gray-400">胜率</span>
                  </div>
                  <div className="text-xl font-bold text-white">
                    {summary.win_rate.toFixed(1)}%
                  </div>
                  <div className="text-sm text-gray-400">
                    {summary.winning_trades}胜 / {summary.losing_trades}败
                  </div>
                </div>
                
                {/* Profit Factor */}
                <div className="bg-slate-900/50 rounded-lg p-4 border border-slate-700">
                  <div className="flex items-center gap-2 mb-1">
                    <Activity className="w-4 h-4 text-purple-400" />
                    <span className="text-xs text-gray-400">盈亏比</span>
                  </div>
                  <div className={`text-xl font-bold ${summary.profit_factor >= 1 ? 'text-green-400' : 'text-red-400'}`}>
                    {summary.profit_factor.toFixed(2)}
                  </div>
                  <div className="text-sm text-gray-400">
                    盈:{summary.avg_win_percent.toFixed(2)}% 亏:{summary.avg_loss_percent.toFixed(2)}%
                  </div>
                </div>
                
                {/* Max Drawdown */}
                <div className="bg-slate-900/50 rounded-lg p-4 border border-slate-700">
                  <div className="flex items-center gap-2 mb-1">
                    <TrendingDown className="w-4 h-4 text-orange-400" />
                    <span className="text-xs text-gray-400">最大回撤</span>
                  </div>
                  <div className="text-xl font-bold text-orange-400">
                    -{summary.max_drawdown_percent.toFixed(2)}%
                  </div>
                  <div className="text-sm text-gray-400">
                    Sharpe: {summary.sharpe_ratio.toFixed(2)}
                  </div>
                </div>
              </div>
              
              {/* Secondary Stats */}
              <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
                <div className="bg-slate-900/50 rounded p-3 text-center">
                  <div className="text-xs text-gray-500">总交易数</div>
                  <div className="text-lg font-semibold text-white">{summary.total_trades}</div>
                </div>
                <div className="bg-slate-900/50 rounded p-3 text-center">
                  <div className="text-xs text-gray-500">日均交易</div>
                  <div className="text-lg font-semibold text-white">{summary.trades_per_day.toFixed(1)}</div>
                </div>
                <div className="bg-slate-900/50 rounded p-3 text-center">
                  <div className="text-xs text-gray-500">平均持仓</div>
                  <div className="text-lg font-semibold text-white">{formatDuration(summary.avg_hold_duration_min)}</div>
                </div>
                <div className="bg-slate-900/50 rounded p-3 text-center">
                  <div className="text-xs text-gray-500">最佳交易</div>
                  <div className="text-lg font-semibold text-green-400">+{summary.best_trade_pnl.toFixed(2)}%</div>
                </div>
                <div className="bg-slate-900/50 rounded p-3 text-center">
                  <div className="text-xs text-gray-500">最差交易</div>
                  <div className="text-lg font-semibold text-red-400">{summary.worst_trade_pnl.toFixed(2)}%</div>
                </div>
                <div className="bg-slate-900/50 rounded p-3 text-center">
                  <div className="text-xs text-gray-500">连胜/连败</div>
                  <div className="text-lg font-semibold text-white">
                    <span className="text-green-400">{summary.max_consecutive_wins}</span>
                    /
                    <span className="text-red-400">{summary.max_consecutive_losses}</span>
                  </div>
                </div>
              </div>
              
              {/* Equity Curve */}
              {result.equity_curve && result.equity_curve.length > 1 && (
                <div className="bg-slate-900/50 rounded-lg p-4">
                  <h3 className="text-sm font-medium text-gray-300 mb-3">资金曲线</h3>
                  <div ref={chartContainerRef} className="w-full h-[200px]" />
                </div>
              )}
              
              {/* Time Distribution */}
              {result.time_analysis && (
                <div className="bg-slate-900/50 rounded-lg p-4">
                  <h3 className="text-sm font-medium text-gray-300 mb-3">触发时间分布</h3>
                  <div className="grid grid-cols-2 gap-4">
                    {/* Daily Distribution */}
                    <div>
                      <div className="text-xs text-gray-500 mb-2">按星期</div>
                      <div className="flex items-end h-16 gap-1">
                        {Object.entries(result.time_analysis.daily_distribution).map(([day, count]) => {
                          const maxCount = Math.max(...Object.values(result.time_analysis!.daily_distribution))
                          const height = maxCount > 0 ? (count / maxCount) * 100 : 0
                          return (
                            <div key={day} className="flex-1 flex flex-col items-center">
                              <div 
                                className="w-full bg-blue-500 rounded-t" 
                                style={{ height: `${height}%`, minHeight: count > 0 ? '4px' : '0' }}
                              />
                              <div className="text-xs text-gray-500 mt-1">{DAY_NAMES[parseInt(day)]}</div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                    
                    {/* Hourly Distribution (simplified) */}
                    <div>
                      <div className="text-xs text-gray-500 mb-2">按小时 (UTC)</div>
                      <div className="flex items-end h-16 gap-px">
                        {Object.entries(result.time_analysis.hourly_distribution).map(([hour, count]) => {
                          const maxCount = Math.max(...Object.values(result.time_analysis!.hourly_distribution))
                          const height = maxCount > 0 ? (count / maxCount) * 100 : 0
                          return (
                            <div 
                              key={hour} 
                              className="flex-1 bg-purple-500 rounded-t" 
                              style={{ height: `${height}%`, minHeight: count > 0 ? '2px' : '0' }}
                              title={`${hour}:00 - ${count}次`}
                            />
                          )
                        })}
                      </div>
                      <div className="flex justify-between text-xs text-gray-500 mt-1">
                        <span>0</span>
                        <span>12</span>
                        <span>23</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}
              
              {/* Signal Stats (for pool backtest) */}
              {result.signal_stats && result.signal_stats.length > 0 && (
                <div className="bg-slate-900/50 rounded-lg p-4">
                  <h3 className="text-sm font-medium text-gray-300 mb-3">信号贡献分析</h3>
                  <div className="space-y-2">
                    {result.signal_stats.map(stat => (
                      <div key={stat.signal_id} className="flex items-center gap-3">
                        <div className="flex-1">
                          <div className="text-sm text-white">{stat.signal_name}</div>
                          <div className="text-xs text-gray-500">{stat.total_triggers}次触发</div>
                        </div>
                        <div className="w-32">
                          <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                            <div 
                              className="h-full bg-blue-500 rounded-full"
                              style={{ width: `${stat.contribution_percent}%` }}
                            />
                          </div>
                        </div>
                        <div className="text-sm text-gray-400 w-16 text-right">
                          {stat.contribution_percent.toFixed(1)}%
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              
              {/* Trade List */}
              {result.trades && result.trades.length > 0 && (
                <div className="bg-slate-900/50 rounded-lg p-4">
                  <button 
                    onClick={() => setShowTrades(!showTrades)}
                    className="flex items-center justify-between w-full text-left"
                  >
                    <span className="text-sm font-medium text-gray-300">交易记录 ({result.trades.length})</span>
                    {showTrades ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
                  </button>
                  
                  {showTrades && (
                    <div className="mt-3 max-h-64 overflow-auto">
                      <table className="w-full text-sm">
                        <thead className="text-gray-500 text-xs">
                          <tr>
                            <th className="text-left pb-2">时间</th>
                            <th className="text-right pb-2">入场</th>
                            <th className="text-right pb-2">出场</th>
                            <th className="text-right pb-2">持仓</th>
                            <th className="text-right pb-2">收益</th>
                          </tr>
                        </thead>
                        <tbody className="text-gray-300">
                          {result.trades.map((trade, idx) => (
                            <tr key={idx} className="border-t border-slate-700">
                              <td className="py-2 text-xs">
                                {formatTime(trade.entry_time)}
                              </td>
                              <td className="py-2 text-right">${trade.entry_price.toFixed(2)}</td>
                              <td className="py-2 text-right">${trade.exit_price.toFixed(2)}</td>
                              <td className="py-2 text-right text-gray-500">{formatDuration(trade.hold_duration_min)}</td>
                              <td className={`py-2 text-right font-medium ${trade.pnl_percent >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                {trade.pnl_percent >= 0 ? '+' : ''}{trade.pnl_percent.toFixed(2)}%
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
