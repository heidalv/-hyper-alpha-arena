import React, { useState } from 'react'
import { ChevronDown, ChevronUp, HelpCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

export default function CollapsibleHelpPanel({
  title,
  summary,
  defaultOpen = false,
  className,
  children,
}: {
  title: string
  summary?: string
  defaultOpen?: boolean
  className?: string
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className={cn('rounded-xl border border-border/60 bg-muted/10 overflow-hidden', className)}>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-start gap-2 px-3 py-2.5 text-left hover:bg-muted/30 transition-colors"
      >
        <HelpCircle className="w-4 h-4 text-muted-foreground mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-foreground">{title}</div>
          {!open && summary && (
            <div className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{summary}</div>
          )}
        </div>
        <span className="text-xs text-muted-foreground shrink-0 pt-0.5 flex items-center gap-1">
          {open ? '收起' : '展开'}
          {open ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </span>
      </button>
      {open && (
        <div className="px-3 pb-3 pt-0 border-t border-border/40 space-y-3">
          {children}
        </div>
      )}
    </div>
  )
}
