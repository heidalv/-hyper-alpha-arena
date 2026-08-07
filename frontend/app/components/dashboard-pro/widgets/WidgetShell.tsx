import { type ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface WidgetShellProps {
  title: string
  icon?: ReactNode
  badge?: ReactNode
  footer?: ReactNode
  className?: string
  bodyClassName?: string
  children: ReactNode
}

/**
 * 仪表盘 widget 的统一玻璃拟态外壳（标题 + 内容 + 可选底部）。
 * 拖拽把手由 GridCanvas 在外层统一提供（顶部窄条），此处只负责视觉呈现。
 */
export default function WidgetShell({
  title,
  icon,
  badge,
  footer,
  className,
  bodyClassName,
  children,
}: WidgetShellProps) {
  return (
    <div
      className={cn(
        'h-full w-full flex flex-col rounded-lg border border-border/70 bg-card/80 backdrop-blur-sm',
        'shadow-[0_1px_0_0_rgba(255,255,255,0.03)_inset] overflow-hidden',
        className,
      )}
    >
      <div className="px-3 py-2 border-b border-border/60 flex items-center justify-between gap-2 shrink-0">
        <div className="flex items-center gap-1.5 min-w-0">
          {icon && <span className="text-muted-foreground shrink-0">{icon}</span>}
          <span className="text-xs font-semibold uppercase tracking-wide text-foreground truncate">
            {title}
          </span>
        </div>
        {badge && <span className="shrink-0">{badge}</span>}
      </div>
      <div className={cn('flex-1 min-h-0 overflow-hidden', bodyClassName)}>{children}</div>
      {footer && <div className="shrink-0 border-t border-border/60">{footer}</div>}
    </div>
  )
}
