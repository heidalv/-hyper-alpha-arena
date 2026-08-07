/**
 * AI决策面板
 */

import React from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Brain, TrendingUp, TrendingDown, Minus, Play, Eye } from 'lucide-react'

interface DecisionInfo {
  action: string
  symbol: string
  direction: string
  size: number
  entry_price: number
  stop_loss: number
  take_profit: number
  confidence: number
  reasoning: Record<string, any>
  timestamp: string | null
}

interface FactorInfo {
  direction: string
  strength: number
  confidence: number
}

interface AIDecisionPanelProps {
  decision: DecisionInfo | null
  factors: FactorInfo | null
  onExecute: () => void
}

// 操作配置
const actionConfig: Record<string, { label: string; color: string; bgColor: string }> = {
  strong_buy: { label: '强烈买入', color: 'text-green-500', bgColor: 'bg-green-500' },
  buy: { label: '买入', color: 'text-green-500', bgColor: 'bg-green-500' },
  weak_buy: { label: '弱买入', color: 'text-green-400', bgColor: 'bg-green-400' },
  hold: { label: '持有/观望', color: 'text-gray-500', bgColor: 'bg-gray-500' },
  weak_sell: { label: '弱卖出', color: 'text-red-400', bgColor: 'bg-red-400' },
  sell: { label: '卖出', color: 'text-red-500', bgColor: 'bg-red-500' },
  strong_sell: { label: '强烈卖出', color: 'text-red-600', bgColor: 'bg-red-600' },
}

// 方向图标
const directionIcons: Record<string, React.ReactNode> = {
  long: <TrendingUp className="w-5 h-5 text-green-500" />,
  short: <TrendingDown className="w-5 h-5 text-red-500" />,
  neutral: <Minus className="w-5 h-5 text-gray-500" />,
}

export function AIDecisionPanel({ decision, factors, onExecute }: AIDecisionPanelProps) {
  const config = decision ? (actionConfig[decision.action] || actionConfig.hold) : actionConfig.hold

  const formatPrice = (price: number) => {
    if (!price) return '-'
    return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }

  const calculateRiskReward = () => {
    if (!decision || !decision.entry_price || !decision.stop_loss || !decision.take_profit) {
      return '-'
    }
    const risk = Math.abs(decision.entry_price - decision.stop_loss)
    const reward = Math.abs(decision.take_profit - decision.entry_price)
    return (reward / risk).toFixed(2)
  }

  const calculateRiskPct = () => {
    if (!decision || !decision.entry_price || !decision.stop_loss) {
      return '-'
    }
    const pct = Math.abs(decision.entry_price - decision.stop_loss) / decision.entry_price * 100
    return pct.toFixed(1) + '%'
  }

  return (
    <Card className="flex-1">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg flex items-center gap-2">
          <Brain className="w-5 h-5 text-purple-500" />
          AI决策
        </CardTitle>
      </CardHeader>
      <CardContent>
        {!decision || decision.action === 'hold' ? (
          <div className="flex flex-col items-center justify-center py-6 text-muted-foreground">
            <Brain className="w-12 h-12 mb-2 opacity-50" />
            <p>暂无交易建议</p>
            <p className="text-sm">等待信号触发</p>
          </div>
        ) : (
          <div className="space-y-4">
            {/* 决策头部 */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {directionIcons[decision.direction]}
                <span className="text-lg font-bold">{decision.symbol}</span>
              </div>
              <Badge className={`${config.bgColor} text-white`}>
                {config.label}
              </Badge>
            </div>

            {/* 价格信息 */}
            <div className="grid grid-cols-3 gap-3 p-3 bg-muted/50 rounded-lg">
              <div className="text-center">
                <p className="text-xs text-muted-foreground">入场</p>
                <p className="font-mono font-bold">${formatPrice(decision.entry_price)}</p>
              </div>
              <div className="text-center">
                <p className="text-xs text-muted-foreground">止损 ({calculateRiskPct()})</p>
                <p className="font-mono text-red-500">${formatPrice(decision.stop_loss)}</p>
              </div>
              <div className="text-center">
                <p className="text-xs text-muted-foreground">止盈</p>
                <p className="font-mono text-green-500">${formatPrice(decision.take_profit)}</p>
              </div>
            </div>

            {/* 置信度和风险收益比 */}
            <div className="flex items-center justify-between text-sm">
              <div>
                <span className="text-muted-foreground">置信度: </span>
                <span className="font-bold">{(decision.confidence * 100).toFixed(0)}%</span>
              </div>
              <div>
                <span className="text-muted-foreground">风险收益比: </span>
                <span className="font-bold">{calculateRiskReward()}</span>
              </div>
            </div>

            {/* 因子评分 */}
            {factors && (
              <div className="flex items-center justify-between text-sm p-2 bg-muted/30 rounded">
                <span className="text-muted-foreground">因子方向:</span>
                <div className="flex items-center gap-2">
                  {directionIcons[factors.direction]}
                  <span>强度 {(factors.strength * 100).toFixed(0)}%</span>
                </div>
              </div>
            )}

            {/* 操作按钮 */}
            <div className="flex gap-2 pt-2">
              <Button
                variant="outline"
                size="sm"
                className="flex-1"
                onClick={() => {
                  // 查看完整推理
                  console.log('Reasoning:', decision.reasoning)
                }}
              >
                <Eye className="w-4 h-4 mr-1" />
                查看推理
              </Button>
              <Button
                size="sm"
                className="flex-1"
                onClick={onExecute}
              >
                <Play className="w-4 h-4 mr-1" />
                手动执行
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export default AIDecisionPanel
