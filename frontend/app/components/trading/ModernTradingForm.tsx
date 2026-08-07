/**
 * ModernTradingForm - 现代化交易表单组件
 * Glassmorphism + Dark Mode 设计风格
 */

import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  TrendingUp,
  TrendingDown,
  Target,
  DollarSign,
  Percent,
  Settings,
  Sliders,
  Info,
  CheckCircle2,
  AlertTriangle
} from 'lucide-react'

type OrderSide = 'LONG' | 'SHORT'
type OrderType = 'MARKET' | 'LIMIT'

interface TradingFormProps {
  symbol?: string
  currentPrice?: number
  onSubmit?: (order: any) => void
}

export default function ModernTradingForm({
  symbol = 'BTCUSDT',
  currentPrice = 96500
}: TradingFormProps) {
  const [side, setSide] = useState<OrderSide>('LONG')
  const [orderType, setOrderType] = useState<OrderType>('MARKET')
  const [quantity, setQuantity] = useState('')
  const [price, setPrice] = useState(currentPrice.toString())
  const [leverage, setLeverage] = useState(5)
  const [stopLoss, setStopLoss] = useState('')
  const [takeProfit, setTakeProfit] = useState('')

  const estimatedCost = parseFloat(quantity) * currentPrice / leverage
  const maxAffordable = 10000 * leverage / currentPrice

  return (
    <div className="space-y-6">
      {/* 交易方向选择 */}
      <Card className="backdrop-blur-xl bg-slate-900/50 border-slate-800/50">
        <CardContent className="p-4">
          <div className="flex gap-2">
            <Button
              onClick={() => setSide('LONG')}
              className={`flex-1 h-14 text-lg font-bold transition-all ${
                side === 'LONG'
                  ? 'bg-gradient-to-r from-emerald-500 to-emerald-600 text-white shadow-lg shadow-emerald-500/50'
                  : 'bg-slate-800/50 hover:bg-slate-800/70 text-slate-400'
              }`}
            >
              <TrendingUp className="w-5 h-5 mr-2" />
              做多
            </Button>
            <Button
              onClick={() => setSide('SHORT')}
              className={`flex-1 h-14 text-lg font-bold transition-all ${
                side === 'SHORT'
                  ? 'bg-gradient-to-r from-red-500 to-red-600 text-white shadow-lg shadow-red-500/50'
                  : 'bg-slate-800/50 hover:bg-slate-800/70 text-slate-400'
              }`}
            >
              <TrendingDown className="w-5 h-5 mr-2" />
              做空
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 订单类型 */}
      <Card className="backdrop-blur-xl bg-slate-900/50 border-slate-800/50">
        <CardHeader className="border-b border-slate-800/50 pb-4">
          <CardTitle className="text-lg flex items-center gap-2">
            <Target className="w-5 h-5 text-purple-400" />
            订单类型
          </CardTitle>
        </CardHeader>
        <CardContent className="p-6">
          <div className="grid grid-cols-2 gap-4">
            <button
              onClick={() => setOrderType('MARKET')}
              className={`p-4 rounded-xl border-2 transition-all ${
                orderType === 'MARKET'
                  ? 'border-purple-500 bg-purple-500/20'
                  : 'border-slate-700 bg-slate-800/50 hover:bg-slate-800/70'
              }`}
            >
              <div className="text-center">
                <p className="font-bold mb-1">市价单</p>
                <p className="text-xs text-slate-400">立即成交</p>
              </div>
            </button>
            <button
              onClick={() => setOrderType('LIMIT')}
              className={`p-4 rounded-xl border-2 transition-all ${
                orderType === 'LIMIT'
                  ? 'border-purple-500 bg-purple-500/20'
                  : 'border-slate-700 bg-slate-800/50 hover:bg-slate-800/70'
              }`}
            >
              <div className="text-center">
                <p className="font-bold mb-1">限价单</p>
                <p className="text-xs text-slate-400">指定价格</p>
              </div>
            </button>
          </div>

          {orderType === 'LIMIT' && (
            <div className="mt-4 space-y-4">
              <div>
                <Label className="text-slate-300">价格 (USDT)</Label>
                <div className="relative mt-2">
                  <DollarSign className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
                  <Input
                    type="number"
                    value={price}
                    onChange={(e) => setPrice(e.target.value)}
                    className="pl-10 bg-slate-800/50 border-slate-700/50 text-white focus:border-purple-500/50"
                    placeholder="0.00"
                  />
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 数量和杠杆 */}
      <Card className="backdrop-blur-xl bg-slate-900/50 border-slate-800/50">
        <CardHeader className="border-b border-slate-800/50 pb-4">
          <CardTitle className="text-lg flex items-center gap-2">
            <Sliders className="w-5 h-5 text-purple-400" />
            交易参数
          </CardTitle>
        </CardHeader>
        <CardContent className="p-6 space-y-6">
          {/* 数量 */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <Label className="text-slate-300">数量</Label>
              <span className="text-sm text-slate-400">{symbol.replace('USDT', '')}</span>
            </div>
            <div className="relative">
              <Input
                type="number"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className="bg-slate-800/50 border-slate-700/50 text-white text-lg focus:border-purple-500/50"
                placeholder="0.00"
              />
            </div>
            <div className="flex items-center gap-2 mt-2">
              <Button
                variant="outline"
                size="sm"
                className="bg-slate-800/50 hover:bg-slate-800/70 border-slate-700/50 text-xs"
                onClick={() => setQuantity('0.1')}
              >
                10%
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="bg-slate-800/50 hover:bg-slate-800/70 border-slate-700/50 text-xs"
                onClick={() => setQuantity('0.5')}
              >
                50%
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="bg-slate-800/50 hover:bg-slate-800/70 border-slate-700/50 text-xs"
                onClick={() => setQuantity(maxAffordable.toString())}
              >
                Max
              </Button>
            </div>
          </div>

          {/* 杠杆 */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <Label className="text-slate-300">杠杆</Label>
              <Badge className="bg-purple-500/20 text-purple-400 border-purple-500/30">
                {leverage}x
              </Badge>
            </div>
            <input
              type="range"
              min="1"
              max="20"
              value={leverage}
              onChange={(e) => setLeverage(parseInt(e.target.value))}
              className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-purple-500"
            />
            <div className="flex justify-between mt-2 text-xs text-slate-500">
              <span>1x</span>
              <span>5x</span>
              <span>10x</span>
              <span>20x</span>
            </div>
          </div>

          {/* 估算成本 */}
          <div className="p-4 rounded-xl bg-slate-800/30 border border-slate-700/50">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-400">估算保证金</span>
              <span className="text-lg font-bold text-white">
                {quantity ? `$${estimatedCost.toFixed(2)}` : '$0.00'}
              </span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 止盈止损 */}
      <Card className="backdrop-blur-xl bg-slate-900/50 border-slate-800/50">
        <CardHeader className="border-b border-slate-800/50 pb-4">
          <CardTitle className="text-lg flex items-center gap-2">
            <Settings className="w-5 h-5 text-purple-400" />
            止盈止损
          </CardTitle>
        </CardHeader>
        <CardContent className="p-6 space-y-4">
          <div>
            <Label className="text-slate-300 mb-2 block">止盈价格 (可选)</Label>
            <div className="relative">
              <DollarSign className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
              <Input
                type="number"
                value={takeProfit}
                onChange={(e) => setTakeProfit(e.target.value)}
                className="pl-10 bg-slate-800/50 border-slate-700/50 text-white focus:border-emerald-500/50"
                placeholder="设置止盈价格"
              />
            </div>
          </div>
          <div>
            <Label className="text-slate-300 mb-2 block">止损价格 (可选)</Label>
            <div className="relative">
              <DollarSign className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
              <Input
                type="number"
                value={stopLoss}
                onChange={(e) => setStopLoss(e.target.value)}
                className="pl-10 bg-slate-800/50 border-slate-700/50 text-white focus:border-red-500/50"
                placeholder="设置止损价格"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 风险提示 */}
      <Card className={`backdrop-blur-xl border ${
        side === 'LONG' ? 'bg-emerald-500/10 border-emerald-500/30' : 'bg-red-500/10 border-red-500/30'
      }`}>
        <CardContent className="p-4">
          <div className="flex items-start gap-3">
            <Info className={`w-5 h-5 mt-0.5 ${side === 'LONG' ? 'text-emerald-400' : 'text-red-400'}`} />
            <div className="flex-1">
              <p className="text-sm font-medium mb-1">
                {side === 'LONG' ? '做多风险提示' : '做空风险提示'}
              </p>
              <p className="text-xs text-slate-400">
                {side === 'LONG'
                  ? '做多盈利方向：价格上涨。如果价格下跌，您将亏损。'
                  : '做空盈利方向：价格下跌。如果价格上涨，您将亏损。'}
              </p>
              <p className="text-xs text-slate-500 mt-2">
                请确保您了解杠杆交易的风险，并只投入您能承受损失的资金。
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 提交按钮 */}
      <Button
        className={`w-full h-14 text-lg font-bold shadow-lg ${
          side === 'LONG'
            ? 'bg-gradient-to-r from-emerald-500 to-emerald-600 hover:from-emerald-600 hover:to-emerald-700 shadow-emerald-500/50'
            : 'bg-gradient-to-r from-red-500 to-red-600 hover:from-red-600 hover:to-red-700 shadow-red-500/50'
        } text-white`}
      >
        {side === 'LONG' ? (
          <>
            <TrendingUp className="w-5 h-5 mr-2" />
            开多仓位
          </>
        ) : (
          <>
            <TrendingDown className="w-5 h-5 mr-2" />
            开空仓位
          </>
        )}
      </Button>

      {/* 账户信息 */}
      <div className="grid grid-cols-2 gap-4">
        <Card className="backdrop-blur-xl bg-slate-900/50 border-slate-800/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-2 text-sm text-slate-400 mb-1">
              <DollarSign className="w-4 h-4" />
              可用余额
            </div>
            <p className="text-xl font-bold">$8,000.00</p>
          </CardContent>
        </Card>
        <Card className="backdrop-blur-xl bg-slate-900/50 border-slate-800/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-2 text-sm text-slate-400 mb-1">
              <Percent className="w-4 h-4" />
              已用保证金
            </div>
            <p className="text-xl font-bold">20%</p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
