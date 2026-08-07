/**
 * 持仓管理面板
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Wallet, TrendingUp, TrendingDown, X, Settings } from 'lucide-react'
import { formatSize } from '@/lib/priceFormat'

interface Position {
  direction: string
  size: number
  entry_price: number
  unrealized_pnl: number
}

interface PositionPanelProps {
  positions: Record<string, Position>
}

export function PositionPanel({ positions }: PositionPanelProps) {
  const positionList = Object.entries(positions)
  
  // 计算总盈亏
  const totalPnl = positionList.reduce((sum, [_, pos]) => sum + (pos.unrealized_pnl || 0), 0)
  const totalPnlColor = totalPnl >= 0 ? 'text-green-500' : 'text-red-500'

  const formatPrice = (price: number) => {
    if (!price) return '-'
    return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }

  const formatPnl = (pnl: number) => {
    const prefix = pnl >= 0 ? '+' : ''
    return prefix + pnl.toFixed(2)
  }

  return (
    <Card className="flex-1">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg flex items-center gap-2">
          <Wallet className="w-5 h-5" />
          持仓管理
          {positionList.length > 0 && (
            <Badge variant="outline" className="ml-auto">
              {positionList.length} 个
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {positionList.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
            <Wallet className="w-12 h-12 mb-2 opacity-50" />
            <p>暂无持仓</p>
          </div>
        ) : (
          <div className="space-y-3">
            {/* 总盈亏 */}
            <div className="flex items-center justify-between p-2 bg-muted/50 rounded">
              <span className="text-sm text-muted-foreground">总未实现盈亏</span>
              <span className={`font-bold font-mono ${totalPnlColor}`}>
                ${formatPnl(totalPnl)}
              </span>
            </div>

            {/* 持仓列表 */}
            {positionList.map(([symbol, position]) => {
              const isLong = position.direction === 'long'
              const pnlColor = (position.unrealized_pnl || 0) >= 0 ? 'text-green-500' : 'text-red-500'
              
              return (
                <div
                  key={symbol}
                  className="p-3 rounded-lg border border-border"
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      {isLong ? (
                        <TrendingUp className="w-4 h-4 text-green-500" />
                      ) : (
                        <TrendingDown className="w-4 h-4 text-red-500" />
                      )}
                      <span className="font-bold">{symbol}</span>
                      <Badge variant={isLong ? 'default' : 'destructive'} className="text-xs">
                        {isLong ? '多' : '空'}
                      </Badge>
                    </div>
                    <span className={`font-mono ${pnlColor}`}>
                      ${formatPnl(position.unrealized_pnl || 0)}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <span className="text-muted-foreground">数量</span>
                      <p className="font-mono">{formatSize(position.size, symbol)}</p>
                    </div>
                    <div>
                      <span className="text-muted-foreground">入场价</span>
                      <p className="font-mono">${formatPrice(position.entry_price)}</p>
                    </div>
                  </div>
                  <div className="flex gap-2 mt-3">
                    <Button variant="outline" size="sm" className="flex-1">
                      <Settings className="w-3 h-3 mr-1" />
                      调整止损
                    </Button>
                    <Button variant="destructive" size="sm" className="flex-1">
                      <X className="w-3 h-3 mr-1" />
                      平仓
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export default PositionPanel
