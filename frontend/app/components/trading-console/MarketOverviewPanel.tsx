/**
 * 市场概览面板
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { TrendingUp, TrendingDown, Minus, Activity } from 'lucide-react'

interface MarketData {
  [symbol: string]: {
    price: number
    regime: string
    regime_confidence: number
    trend_direction: string
    trend_alignment: number
    volatility: number
    error?: string
  }
}

interface MarketOverviewPanelProps {
  data: MarketData
  selectedSymbol: string
  onSelectSymbol: (symbol: string) => void
}

// 市场状态翻译
const regimeLabels: Record<string, string> = {
  breakout: '突破',
  continuation: '延续',
  reversal: '反转',
  absorption: '吸筹',
  exhaustion: '衰竭',
  stop_hunt: '猎杀',
  trap: '陷阱',
  noise: '噪音',
}

// 状态颜色
const regimeColors: Record<string, string> = {
  breakout: 'bg-green-500',
  continuation: 'bg-blue-500',
  reversal: 'bg-yellow-500',
  absorption: 'bg-purple-500',
  exhaustion: 'bg-orange-500',
  stop_hunt: 'bg-red-500',
  trap: 'bg-red-600',
  noise: 'bg-gray-500',
}

export function MarketOverviewPanel({
  data,
  selectedSymbol,
  onSelectSymbol,
}: MarketOverviewPanelProps) {
  const symbols = Object.keys(data)

  const getTrendIcon = (direction: string) => {
    switch (direction) {
      case 'long':
        return <TrendingUp className="w-4 h-4 text-green-500" />
      case 'short':
        return <TrendingDown className="w-4 h-4 text-red-500" />
      default:
        return <Minus className="w-4 h-4 text-gray-500" />
    }
  }

  const formatPrice = (price: number) => {
    if (price >= 1000) {
      return price.toLocaleString('en-US', { maximumFractionDigits: 0 })
    }
    return price.toFixed(2)
  }

  return (
    <Card className="flex-1">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg flex items-center gap-2">
          <Activity className="w-5 h-5" />
          市场概览
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {symbols.length === 0 ? (
          <div className="text-center text-muted-foreground py-4">
            暂无数据
          </div>
        ) : (
          symbols.map((symbol) => {
            const market = data[symbol]
            if (market.error) {
              return (
                <div
                  key={symbol}
                  className="p-3 rounded-lg border border-destructive/50 bg-destructive/10"
                >
                  <span className="font-medium">{symbol}</span>
                  <span className="text-sm text-destructive ml-2">加载失败</span>
                </div>
              )
            }

            const isSelected = symbol === selectedSymbol
            return (
              <div
                key={symbol}
                className={`p-3 rounded-lg border cursor-pointer transition-all ${
                  isSelected
                    ? 'border-primary bg-primary/10'
                    : 'border-border hover:border-primary/50'
                }`}
                onClick={() => onSelectSymbol(symbol)}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="font-bold">{symbol.replace('USDT', '')}</span>
                    {getTrendIcon(market.trend_direction)}
                  </div>
                  <span className="font-mono text-lg">
                    ${formatPrice(market.price)}
                  </span>
                </div>
                <div className="flex items-center justify-between mt-2 text-sm">
                  <div className="flex items-center gap-2">
                    <Badge
                      variant="secondary"
                      className={`${regimeColors[market.regime] || 'bg-gray-500'} text-white`}
                    >
                      {regimeLabels[market.regime] || market.regime}
                    </Badge>
                    <span className="text-muted-foreground">
                      {(market.regime_confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                  <div className="text-muted-foreground">
                    共振: {(market.trend_alignment * 100).toFixed(0)}%
                  </div>
                </div>
              </div>
            )
          })
        )}
      </CardContent>
    </Card>
  )
}

export default MarketOverviewPanel
