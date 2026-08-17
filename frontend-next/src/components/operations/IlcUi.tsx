import { ReactNode } from 'react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'

/** 与 Analytics / NetPerformance 对齐的 KPI 卡片（Aurora 玻璃 + 渐变数字） */
export function StatCard({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: 'default' | 'good' | 'warn' | 'bad'
}) {
  const valueClass =
    tone === 'good'
      ? 'grad-text-green'
      : tone === 'warn'
        ? 'text-warning'
        : tone === 'bad'
          ? 'grad-text-red'
          : 'grad-text'

  return (
    <Card className="glass">
      <CardContent className="pt-5">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        <p className={cn('text-2xl font-bold tabular-nums mt-1', valueClass)}>{value}</p>
        {hint != null && hint !== '' && (
          <p className="text-xs text-muted-foreground mt-1">{hint}</p>
        )}
      </CardContent>
    </Card>
  )
}

export function SectionCard({
  title,
  description,
  action,
  children,
  className,
}: {
  title?: string
  description?: string
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <Card className={cn('glass', className)}>
      {(title || description || action) && (
        <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-3">
          <div className="space-y-1">
            {title && (
              <CardTitle className="text-base flex items-center gap-2">
                <span className="w-[3px] h-3.5 rounded-r bg-gradient-to-b from-cyan-400 to-violet-500 shadow-[0_0_6px_rgba(34,211,238,0.5)]" />
                {title}
              </CardTitle>
            )}
            {description && <CardDescription>{description}</CardDescription>}
          </div>
          {action}
        </CardHeader>
      )}
      <CardContent className={title || description ? 'pt-0' : 'pt-6'}>{children}</CardContent>
    </Card>
  )
}

export function StatusBadge({
  ok,
  labelOn,
  labelOff,
}: {
  ok: boolean
  labelOn?: string
  labelOff?: string
}) {
  return (
    <Badge variant={ok ? 'default' : 'destructive'} className="font-normal">
      {ok ? labelOn ?? '正常' : labelOff ?? '异常'}
    </Badge>
  )
}

export function RefreshButton({
  onClick,
  loading,
  label = '刷新',
}: {
  onClick: () => void
  loading?: boolean
  label?: string
}) {
  return (
    <Button variant="outline" size="sm" onClick={onClick} disabled={loading}>
      <RefreshCw className={cn('h-4 w-4 mr-1.5', loading && 'animate-spin')} />
      {label}
    </Button>
  )
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <p className="text-sm text-muted-foreground">{message}</p>
    </div>
  )
}

export function InfoBanner({
  title,
  children,
  variant = 'default',
}: {
  title: string
  children: ReactNode
  variant?: 'default' | 'warn'
}) {
  return (
    <Card
      className={cn(
        variant === 'warn'
          ? 'border-amber-500/40 bg-amber-50/50 dark:bg-amber-950/20'
          : 'border-border',
      )}
    >
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">{children}</CardContent>
    </Card>
  )
}
