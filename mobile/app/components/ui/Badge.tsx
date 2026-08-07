import React from 'react'

interface BadgeProps {
  // 同时兼容设计文档语义（success/danger）与页面现有变体
  variant: 'profit' | 'loss' | 'neutral' | 'active' | 'paused' | 'defensive' | 'stopped' | 'warning' | 'success' | 'danger'
  children: React.ReactNode
  className?: string
}

function Badge({ variant, children, className = '' }: BadgeProps) {
  const cls: Record<string, string> = {
    profit: 'badge-profit',
    success: 'badge-profit',
    loss: 'badge-loss',
    danger: 'badge-loss',
    neutral: 'badge-neutral',
    active: 'badge-active',
    paused: 'badge-paused',
    defensive: 'badge-defensive',
    stopped: 'badge-stopped',
    warning: 'badge-warning',
  }
  return <span className={`${cls[variant] || 'badge-neutral'} ${className}`}>{children}</span>
}

export default Badge
// 具名导出别名，兼容 `import { Badge }` 写法
export { Badge }

