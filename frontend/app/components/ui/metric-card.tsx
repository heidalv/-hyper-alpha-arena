import { type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import AnimatedNumber from './animated-number'

interface MetricCardProps {
  icon: LucideIcon
  title: string
  value: number
  prefix?: string
  suffix?: string
  decimals?: number
  colorBySign?: boolean
  subtitle?: string
  className?: string
}

export function MetricCard({
  icon: Icon,
  title,
  value,
  prefix = '',
  suffix = '',
  decimals = 2,
  colorBySign = false,
  subtitle,
  className,
}: MetricCardProps) {
  const valueColor = colorBySign
    ? value > 0
      ? 'text-emerald-400'
      : value < 0
        ? 'text-red-400'
        : 'text-foreground'
    : 'text-foreground'

  return (
    <div
      className={cn(
        'bg-card border border-border rounded-lg p-4 flex flex-col gap-1',
        className
      )}
    >
      <div className="flex items-center gap-2 text-muted-foreground">
        <Icon className="h-4 w-4" />
        <span className="text-xs font-medium uppercase tracking-wide">{title}</span>
      </div>
      <div className={cn('text-xl font-bold tabular-nums', valueColor)}>
        <AnimatedNumber
          value={value}
          decimals={decimals}
          prefix={prefix}
          suffix={suffix}
          className={valueColor}
        />
      </div>
      {subtitle && (
        <span className="text-xs text-muted-foreground">{subtitle}</span>
      )}
    </div>
  )
}
