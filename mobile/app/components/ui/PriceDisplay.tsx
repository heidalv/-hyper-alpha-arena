import React from 'react'

interface PriceDisplayProps {
  value: number
  /** 百分比模式（旧字段 pct 的别名） */
  pct?: boolean
  /** @deprecated 使用 percent，保留兼容 */
  percent?: boolean
  /** 显示正负号；当为字符串时作为前缀 */
  prefix?: boolean | string
  /** @deprecated 保留兼容，与 prefix 协同 */
  showSign?: boolean
  /** 数值后缀，如 "%" */
  suffix?: string
  className?: string
  size?: 'sm' | 'md' | 'lg'
}

function PriceDisplay({ value, pct, percent, prefix = true, showSign, suffix, className = '', size = 'md' }: PriceDisplayProps) {
  const isPercent = percent || pct
  const isPositive = value > 0
  const isZero = value === 0
  const color = isZero ? 'text-terminal-muted' : isPositive ? 'text-terminal-profit' : 'text-terminal-loss'
  // prefix 为字符串时作为前缀字面量；为 true 时用 +/- 号（百分比）或 $ 前缀（金额）
  const wantSign = (typeof prefix === 'boolean' && prefix) || showSign
  const customPrefix = typeof prefix === 'string' ? prefix : ''
  const sign = wantSign ? (isPositive ? '+' : '') : ''
  const formatted = isPercent
    ? `${sign}${value.toFixed(2)}%`
    : `${customPrefix || (sign)}${customPrefix ? '' : '$'}${Math.abs(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  const withSuffix = suffix ? `${formatted.replace(/%$/, '')}${suffix}` : formatted
  const sizeClass = size === 'lg' ? 'text-xl font-bold' : size === 'sm' ? 'text-xs' : 'text-sm'

  return <span className={`${color} ${sizeClass} font-mono tabular-nums ${className}`}>{withSuffix}</span>
}

export default PriceDisplay
// 具名导出别名，兼容 `import { PriceDisplay }` 写法
export { PriceDisplay }

