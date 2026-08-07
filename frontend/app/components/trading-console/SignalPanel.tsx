/**
 * 信号面板
 */

import React from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Zap, TrendingUp, TrendingDown, Minus, AlertCircle } from 'lucide-react'

interface SignalInfo {
  name: string
  direction: string
  quality_score: number
  confidence: number
  trend_alignment: number
}

interface SignalPanelProps {
  signals: SignalInfo[]
  symbol: string
}

// 方向配置
const directionConfig: Record<string, { icon: React.ReactNode; color: string }> = {
  long: {
    icon: <TrendingUp className="w-4 h-4" />,
    color: 'text-green-500',
  },
  short: {
    icon: <TrendingDown className="w-4 h-4" />,
    color: 'text-red-500',
  },
  neutral: {
    icon: <Minus className="w-4 h-4" />,
    color: 'text-gray-500',
  },
}

// 质量徽章
const getQualityBadge = (score: number) => {
  if (score >= 0.8) {
    return <Badge className="bg-green-500 text-white">高质量</Badge>
  } else if (score >= 0.6) {
    return <Badge className="bg-yellow-500 text-white">中等</Badge>
  } else {
    return <Badge variant="secondary">待确认</Badge>
  }
}

export function SignalPanel({ signals, symbol }: SignalPanelProps) {
  return (
    <Card className="flex-1">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg flex items-center gap-2">
          <Zap className="w-5 h-5 text-yellow-500" />
          活跃信号
          <span className="text-sm font-normal text-muted-foreground">
            ({symbol})
          </span>
          {signals.length > 0 && (
            <Badge variant="outline" className="ml-auto">
              {signals.length} 个
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {signals.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
            <AlertCircle className="w-12 h-12 mb-2 opacity-50" />
            <p>暂无触发信号</p>
            <p className="text-sm">等待市场条件满足</p>
          </div>
        ) : (
          <div className="space-y-3">
            {signals.map((signal, index) => {
              const config = directionConfig[signal.direction] || directionConfig.neutral
              return (
                <div
                  key={index}
                  className="p-3 rounded-lg border border-border bg-card hover:bg-accent/50 transition-colors"
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className={config.color}>{config.icon}</span>
                      <span className="font-medium">{signal.name}</span>
                    </div>
                    {getQualityBadge(signal.quality_score)}
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-sm">
                    <div>
                      <span className="text-muted-foreground">质量</span>
                      <p className="font-mono">{(signal.quality_score * 100).toFixed(0)}%</p>
                    </div>
                    <div>
                      <span className="text-muted-foreground">置信度</span>
                      <p className="font-mono">{(signal.confidence * 100).toFixed(0)}%</p>
                    </div>
                    <div>
                      <span className="text-muted-foreground">共振</span>
                      <p className="font-mono">{(signal.trend_alignment * 100).toFixed(0)}%</p>
                    </div>
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

export default SignalPanel
