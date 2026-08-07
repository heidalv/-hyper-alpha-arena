/**
 * 多时间框架趋势面板
 */

import React from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
// Progress bar component - using simple div implementation
import { TrendingUp, TrendingDown, Minus, BarChart3 } from 'lucide-react'

interface TrendInfo {
  direction: string
  strength: string
  confidence: number
}

interface TrendAnalysis {
  macro: TrendInfo
  meso: TrendInfo
  micro: TrendInfo
  alignment_score: number
  recommended_action: string
}

interface TrendPanelProps {
  trend: TrendAnalysis | null
  symbol: string
}

// 趋势方向图标和颜色
const directionConfig: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
  long: {
    icon: <TrendingUp className="w-4 h-4" />,
    color: 'text-green-500',
    label: '看多',
  },
  short: {
    icon: <TrendingDown className="w-4 h-4" />,
    color: 'text-red-500',
    label: '看空',
  },
  neutral: {
    icon: <Minus className="w-4 h-4" />,
    color: 'text-gray-500',
    label: '中性',
  },
}

// 强度标签
const strengthLabels: Record<string, string> = {
  very_strong: '非常强',
  strong: '强',
  medium: '中',
  weak: '弱',
  very_weak: '非常弱',
}

// 操作建议翻译
const actionLabels: Record<string, { label: string; color: string }> = {
  strong_buy: { label: '强烈买入', color: 'bg-green-600' },
  buy: { label: '买入', color: 'bg-green-500' },
  weak_buy: { label: '弱买入', color: 'bg-green-400' },
  hold: { label: '持有/观望', color: 'bg-gray-500' },
  weak_sell: { label: '弱卖出', color: 'bg-red-400' },
  sell: { label: '卖出', color: 'bg-red-500' },
  strong_sell: { label: '强烈卖出', color: 'bg-red-600' },
}

export function TrendPanel({ trend, symbol }: TrendPanelProps) {
  if (!trend) {
    return (
      <Card className="flex-1">
        <CardHeader className="pb-2">
          <CardTitle className="text-lg flex items-center gap-2">
            <BarChart3 className="w-5 h-5" />
            多时间框架趋势
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-center text-muted-foreground py-8">
            加载中...
          </div>
        </CardContent>
      </Card>
    )
  }

  const renderTrendRow = (label: string, timeframe: string, info: TrendInfo) => {
    const config = directionConfig[info.direction] || directionConfig.neutral
    return (
      <div className="flex items-center justify-between py-2 border-b border-border last:border-0">
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground w-20">{label}</span>
          <span className="text-xs text-muted-foreground">({timeframe})</span>
        </div>
        <div className="flex items-center gap-3">
          <div className={`flex items-center gap-1 ${config.color}`}>
            {config.icon}
            <span className="font-medium">{config.label}</span>
          </div>
          <Badge variant="outline" className="text-xs">
            {strengthLabels[info.strength] || info.strength}
          </Badge>
          <span className="text-sm text-muted-foreground">
            {(info.confidence * 100).toFixed(0)}%
          </span>
        </div>
      </div>
    )
  }

  const alignmentScore = trend.alignment_score * 100
  const action = actionLabels[trend.recommended_action] || actionLabels.hold

  return (
    <Card className="flex-1">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg flex items-center gap-2">
          <BarChart3 className="w-5 h-5" />
          多时间框架趋势
          <span className="text-sm font-normal text-muted-foreground">
            ({symbol})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* 趋势层级 */}
        <div className="space-y-1">
          {renderTrendRow('宏观层', '日线', trend.macro)}
          {renderTrendRow('中期层', '4H', trend.meso)}
          {renderTrendRow('微观层', '15m', trend.micro)}
        </div>

        {/* 共振评分 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">趋势共振</span>
            <span className="font-bold">{alignmentScore.toFixed(0)}%</span>
          </div>
          <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${alignmentScore}%` }}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            {alignmentScore >= 70
              ? '多周期高度共振，信号可靠度高'
              : alignmentScore >= 50
              ? '多周期基本共振，可以考虑入场'
              : alignmentScore >= 30
              ? '共振一般，建议减少仓位或观望'
              : '共振较差，建议观望'}
          </p>
        </div>

        {/* 建议操作 */}
        <div className="pt-2">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">系统建议</span>
            <Badge className={`${action.color} text-white`}>
              {action.label}
            </Badge>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export default TrendPanel
