/**
 * ModernTradingDashboard - 现代化AI交易仪表板
 * Glassmorphism + Dark Mode 设计风格
 * 完整的响应式设计和视觉效果
 */

import { useState, useEffect, useLayoutEffect } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import {
  TrendingUp,
  TrendingDown,
  Activity,
  DollarSign,
  Bot,
  AlertCircle,
} from 'lucide-react'

interface Position {
  symbol: string
  side: 'LONG' | 'SHORT'
  quantity: number
  entry_price: number
  current_price: number
  pnl: number
  pnl_percent: number
  leverage: number
}

interface MarketOverview {
  total_balance: number
  available_margin: number
  margin_used: number
  margin_used_percent: number
  unrealized_pnl: number
  daily_pnl: number
  daily_pnl_percent: number
  open_positions: number
}

export default function ModernTradingDashboard() {
  const [marketOverview, setMarketOverview] = useState<MarketOverview>({
    total_balance: 10000,
    available_margin: 8000,
    margin_used: 2000,
    margin_used_percent: 20,
    unrealized_pnl: 150,
    daily_pnl: 320,
    daily_pnl_percent: 3.2,
    open_positions: 3
  })

  const [positions, setPositions] = useState<Position[]>([
    {
      symbol: 'BTCUSDT',
      side: 'LONG',
      quantity: 0.5,
      entry_price: 95000,
      current_price: 96500,
      pnl: 750,
      pnl_percent: 1.58,
      leverage: 5
    },
    {
      symbol: 'ETHUSDT',
      side: 'LONG',
      quantity: 5,
      entry_price: 3300,
      current_price: 3380,
      pnl: 400,
      pnl_percent: 2.42,
      leverage: 3
    },
    {
      symbol: 'BNBUSDT',
      side: 'LONG',
      quantity: 10,
      entry_price: 650,
      current_price: 655,
      pnl: 50,
      pnl_percent: 0.77,
      leverage: 2
    },
  ])

  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const timer = setTimeout(() => setIsLoading(false), 300)
    return () => clearTimeout(timer)
  }, [])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full min-h-[400px]">
        <div className="text-center">
          <div className="animate-spin rounded-full h-16 w-16 border-4 border-purple-500 border-t-transparent mx-auto mb-4"></div>
          <p className="text-slate-400">加载中...</p>
        </div>
      </div>
    )
  }

  // Layout is handled by parent flex container, no manual margin adjustment needed

  return (
   <div className="w-full h-full overflow-auto p-4 md:p-6 lg:p-8 pb-20 md:pb-8">
      {/* 页面标题 */}
      <div className="mb-6 md:mb-8">
        <h1 className="text-2xl md:text-4xl font-bold bg-gradient-to-r from-blue-400 via-purple-400 to-pink-400 bg-clip-text text-transparent mb-2">
          现代化交易仪表板
        </h1>
        <p className="text-sm md:text-base text-slate-400">AI-Powered Trading Dashboard</p>
      </div>

      {/* 账户总览卡片 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 md:gap-6 mb-6 md:mb-8">
        {/* 总余额 */}
        <Card className="group backdrop-blur-xl bg-slate-900/60 border border-slate-700/50 hover:border-emerald-500/50 hover:bg-slate-900/80 transition-all duration-300 shadow-xl hover:shadow-emerald-500/20">
          <CardContent className="p-4 md:p-6">
            <div className="flex items-center justify-between">
              <div className="flex-1 min-w-0">
                <p className="text-xs md:text-sm text-slate-400 mb-1 md:mb-2 font-medium">总余额</p>
                <p className="text-xl md:text-2xl lg:text-3xl font-bold text-white mb-2">
                  ${marketOverview.total_balance.toLocaleString()}
                </p>
                <div className="flex items-center gap-1.5 text-emerald-400 text-xs md:text-sm bg-emerald-500/10 px-2 py-1 rounded-full w-fit">
                  <TrendingUp className="w-3 h-3 md:w-4 md:h-4" />
                  <span className="font-semibold">+{marketOverview.daily_pnl_percent}%</span>
                </div>
              </div>
              <div className="w-12 h-12 md:w-14 md:h-14 rounded-xl bg-gradient-to-br from-emerald-500/30 to-emerald-600/30 flex items-center justify-center flex-shrink-0 ml-3 md:ml-4 border border-emerald-500/30 group-hover:scale-110 transition-transform">
                <DollarSign className="w-6 h-6 md:w-7 md:h-7 text-emerald-400" />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* 已用保证金 */}
        <Card className="group backdrop-blur-xl bg-slate-900/60 border border-slate-700/50 hover:border-blue-500/50 hover:bg-slate-900/80 transition-all duration-300 shadow-xl hover:shadow-blue-500/20">
          <CardContent className="p-4 md:p-6">
            <div className="flex items-center justify-between">
              <div className="flex-1 min-w-0">
                <p className="text-xs md:text-sm text-slate-400 mb-1 md:mb-2 font-medium">已用保证金</p>
                <p className="text-xl md:text-2xl lg:text-3xl font-bold text-white mb-2">
                  ${marketOverview.margin_used.toLocaleString()}
                </p>
                <div className="mt-2">
                  <div className="w-full bg-slate-800 rounded-full h-2 md:h-2.5 overflow-hidden">
                    <div
                      className="bg-gradient-to-r from-blue-500 via-purple-500 to-pink-500 h-full rounded-full transition-all duration-500"
                      style={{ width: `${marketOverview.margin_used_percent}%` }}
                    />
                  </div>
                  <p className="text-xs text-slate-400 mt-1.5 md:mt-2 font-medium">{marketOverview.margin_used_percent}% 使用率</p>
                </div>
              </div>
              <div className="w-12 h-12 md:w-14 md:h-14 rounded-xl bg-gradient-to-br from-blue-500/30 to-purple-600/30 flex items-center justify-center flex-shrink-0 ml-3 md:ml-4 border border-blue-500/30 group-hover:scale-110 transition-transform">
                <Activity className="w-6 h-6 md:w-7 md:h-7 text-blue-400" />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* 未实现盈亏 */}
        <Card className={`group backdrop-blur-xl bg-slate-900/60 border transition-all duration-300 shadow-xl ${marketOverview.unrealized_pnl >= 0 ? 'border-slate-700/50 hover:border-emerald-500/50 hover:bg-slate-900/80 hover:shadow-emerald-500/20' : 'border-slate-700/50 hover:border-red-500/50 hover:bg-slate-900/80 hover:shadow-red-500/20'}`}>
          <CardContent className="p-4 md:p-6">
            <div className="flex items-center justify-between">
              <div className="flex-1 min-w-0">
                <p className="text-xs md:text-sm text-slate-400 mb-1 md:mb-2 font-medium">未实现盈亏</p>
                <p className={`text-xl md:text-2xl lg:text-3xl font-bold mb-2 ${marketOverview.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {marketOverview.unrealized_pnl >= 0 ? '+' : ''}${marketOverview.unrealized_pnl.toLocaleString()}
                </p>
                <p className="text-xs md:text-sm text-slate-400 font-medium">{marketOverview.open_positions} 个持仓</p>
              </div>
              <div className={`w-12 h-12 md:w-14 md:h-14 rounded-xl flex items-center justify-center flex-shrink-0 ml-3 md:ml-4 border group-hover:scale-110 transition-transform ${marketOverview.unrealized_pnl >= 0 ? 'bg-gradient-to-br from-emerald-500/30 to-emerald-600/30 border-emerald-500/30' : 'bg-gradient-to-br from-red-500/30 to-red-600/30 border-red-500/30'}`}>
                {marketOverview.unrealized_pnl >= 0 ? (
                  <TrendingUp className="w-6 h-6 md:w-7 md:h-7 text-emerald-400" />
                ) : (
                  <TrendingDown className="w-6 h-6 md:w-7 md:h-7 text-red-400" />
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        {/* 今日盈亏 */}
        <Card className={`group backdrop-blur-xl bg-slate-900/60 border transition-all duration-300 shadow-xl ${marketOverview.daily_pnl >= 0 ? 'border-slate-700/50 hover:border-emerald-500/50 hover:bg-slate-900/80 hover:shadow-emerald-500/20' : 'border-slate-700/50 hover:border-red-500/50 hover:bg-slate-900/80 hover:shadow-red-500/20'}`}>
          <CardContent className="p-4 md:p-6">
            <div className="flex items-center justify-between">
              <div className="flex-1 min-w-0">
                <p className="text-xs md:text-sm text-slate-400 mb-1 md:mb-2 font-medium">今日盈亏</p>
                <p className={`text-xl md:text-2xl lg:text-3xl font-bold mb-2 ${marketOverview.daily_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {marketOverview.daily_pnl >= 0 ? '+' : ''}${marketOverview.daily_pnl.toLocaleString()}
                </p>
                <p className={`text-xs md:text-sm font-medium ${marketOverview.daily_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {marketOverview.daily_pnl_percent}%
                </p>
              </div>
              <div className={`w-12 h-12 md:w-14 md:h-14 rounded-xl flex items-center justify-center flex-shrink-0 ml-3 md:ml-4 border group-hover:scale-110 transition-transform ${marketOverview.daily_pnl >= 0 ? 'bg-gradient-to-br from-emerald-500/30 to-emerald-600/30 border-emerald-500/30' : 'bg-gradient-to-br from-red-500/30 to-red-600/30 border-red-500/30'}`}>
                <Bot className={`w-6 h-6 md:w-7 md:h-7 ${marketOverview.daily_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`} />
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 持仓列表 */}
      <Card className="backdrop-blur-xl bg-slate-900/60 border border-slate-700/50 hover:border-slate-600/50 transition-all duration-300 shadow-xl">
        <CardContent className="p-4 md:p-6">
          <div className="flex items-center justify-between mb-4 md:mb-6">
            <h2 className="text-lg md:text-xl font-bold text-white">当前持仓</h2>
            <div className="text-xs md:text-sm text-slate-400">
              共 {positions.length} 个持仓
            </div>
          </div>

          {/* 移动端：堆叠布局 */}
          <div className="space-y-3 md:hidden">
            {positions.map((position, index) => (
              <div
                key={index}
                className="p-4 rounded-xl bg-slate-800/60 hover:bg-slate-800/80 border border-slate-700/50 transition-all"
              >
                <div className="flex items-center justify-between mb-3">
                  <div className={`px-3 py-1 rounded-full text-xs font-bold border ${
                    position.side === 'LONG'
                      ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                      : 'bg-red-500/20 text-red-400 border-red-500/30'
                  }`}>
                    {position.side}
                  </div>
                  <div className={`text-lg font-bold ${position.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {position.pnl >= 0 ? '+' : ''}${position.pnl.toLocaleString()}
                  </div>
                </div>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-slate-400">交易对</span>
                    <span className="font-semibold text-white">{position.symbol}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">数量</span>
                    <span className="text-white">{position.quantity} × {position.leverage}x</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">入场价</span>
                    <span className="text-white">${position.entry_price.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">当前价</span>
                    <span className="text-white">${position.current_price.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">盈亏比例</span>
                    <span className={`font-semibold ${position.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {position.pnl >= 0 ? '+' : ''}{position.pnl_percent}%
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* 桌面端：表格布局 */}
          <div className="hidden md:block">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-slate-700/50">
                    <th className="text-left py-3 px-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">交易对</th>
                    <th className="text-left py-3 px-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">方向</th>
                    <th className="text-right py-3 px-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">数量</th>
                    <th className="text-right py-3 px-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">入场价</th>
                    <th className="text-right py-3 px-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">当前价</th>
                    <th className="text-right py-3 px-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">盈亏</th>
                    <th className="text-right py-3 px-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">盈亏%</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((position, index) => (
                    <tr
                      key={index}
                      className="border-b border-slate-700/30 hover:bg-slate-800/30 transition-colors"
                    >
                      <td className="py-4 px-4">
                        <span className="font-semibold text-white">{position.symbol}</span>
                      </td>
                      <td className="py-4 px-4">
                        <span className={`px-3 py-1 rounded-full text-xs font-bold border ${
                          position.side === 'LONG'
                            ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                            : 'bg-red-500/20 text-red-400 border-red-500/30'
                        }`}>
                          {position.side}
                        </span>
                      </td>
                      <td className="py-4 px-4 text-right text-white">
                        {position.quantity} × {position.leverage}x
                      </td>
                      <td className="py-4 px-4 text-right text-slate-300">
                        ${position.entry_price.toLocaleString()}
                      </td>
                      <td className="py-4 px-4 text-right text-slate-300">
                        ${position.current_price.toLocaleString()}
                      </td>
                      <td className={`py-4 px-4 text-right font-semibold ${position.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {position.pnl >= 0 ? '+' : ''}${position.pnl.toLocaleString()}
                      </td>
                      <td className={`py-4 px-4 text-right font-semibold ${position.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {position.pnl >= 0 ? '+' : ''}{position.pnl_percent}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* 空状态 */}
          {positions.length === 0 && (
            <div className="text-center py-12 md:py-16">
              <Bot className="w-16 h-16 mx-auto mb-4 text-slate-600 opacity-50" />
              <p className="text-slate-400 text-lg">暂无持仓</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 底部装饰 */}
      <div className="mt-8 text-center pb-4">
        <p className="text-xs text-slate-500">
          Powered by AI • Real-time Market Data
        </p>
      </div>
    </div>
  )
}