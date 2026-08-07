/**
 * K线形态标记组件 — 在图表上显示检测到的蜡烛图形态
 */

import { useTranslation } from 'react-i18next'

export interface PatternInfo {
  id: string
  name: string
  pattern_type: 'bullish' | 'bearish' | 'neutral'
  timestamp: number
  confidence: number
  description: string
  trading_hints: string[]
  reliability: string
}

interface PatternBadgeProps {
  pattern: PatternInfo
  compact?: boolean
  onClick?: () => void
}

export default function PatternBadge({ pattern, compact = false, onClick }: PatternBadgeProps) {
  useTranslation()

  const typeStyle = {
    bullish: 'bg-green-500/10 border-green-500/30 text-green-400',
    bearish: 'bg-red-500/10 border-red-500/30 text-red-400',
    neutral: 'bg-yellow-500/10 border-yellow-500/30 text-yellow-400',
  }[pattern.pattern_type]

  const typeIcon = {
    bullish: '\u2191',
    bearish: '\u2193',
    neutral: '\u2194',
  }[pattern.pattern_type]

  const reliabilityColor = {
    high: 'text-green-400',
    medium: 'text-yellow-400',
    low: 'text-muted-foreground',
  }[pattern.reliability] || 'text-muted-foreground'

  if (compact) {
    return (
      <span
        className={`inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] rounded border cursor-pointer transition-colors hover:opacity-80 ${typeStyle}`}
        title={`${pattern.name}\n${pattern.description}`}
        onClick={onClick}
      >
        <span>{typeIcon}</span>
        <span>{pattern.name.split(' (')[0]}</span>
      </span>
    )
  }

  return (
    <div
      className={`p-2 rounded border cursor-pointer transition-colors hover:opacity-80 ${typeStyle}`}
      onClick={onClick}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium">
          {typeIcon} {pattern.name}
        </span>
        <span className={`text-[9px] ${reliabilityColor}`}>
          {(pattern.confidence * 100).toFixed(0)}%
        </span>
      </div>
      <p className="text-[10px] text-muted-foreground mt-1 leading-tight">
        {pattern.description}
      </p>
      {pattern.trading_hints.length > 0 && (
        <div className="flex gap-1 mt-1 flex-wrap">
          {pattern.trading_hints.slice(0, 2).map((hint, i) => (
            <span key={i} className="text-[9px] text-muted-foreground/70">
              {hint}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
