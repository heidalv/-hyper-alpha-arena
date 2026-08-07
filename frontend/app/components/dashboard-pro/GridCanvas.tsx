/**
 * GridCanvas — react-grid-layout 封装
 *
 * 每个 widget 外层统一附加一条极窄的拖拽/删除把手条（.widget-drag-handle），
 * 不侵入 widget 自身样式；缩放/拖拽结束后把最新布局回写给上层（用于 debounce 持久化）。
 */
import { useMemo } from 'react'
import { Responsive, WidthProvider } from 'react-grid-layout/legacy'
import type { Layout } from 'react-grid-layout'
import { GripHorizontal, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getWidgetDef } from './WidgetRegistry'
import type { AccountOverview, AccountSelection, WidgetInstance, WsConnStatus } from './types'

import 'react-grid-layout/css/styles.css'

const ResponsiveGridLayout = WidthProvider(Responsive)

const COLS = { lg: 12, md: 12, sm: 6, xs: 4 }
const BREAKPOINTS = { lg: 1024, md: 768, sm: 480, xs: 0 }
const ROW_HEIGHT = 42

interface GridCanvasProps {
  widgets: WidgetInstance[]
  overviews: AccountOverview[]
  selections: AccountSelection[]
  wsStatusByAccount: Record<number, WsConnStatus>
  editable: boolean
  onLayoutChange: (widgets: WidgetInstance[]) => void
  onRemoveWidget: (id: string) => void
  onWidgetConfigChange: (id: string, config: Record<string, unknown>) => void
}

export default function GridCanvas({
  widgets,
  overviews,
  selections,
  wsStatusByAccount,
  editable,
  onLayoutChange,
  onRemoveWidget,
  onWidgetConfigChange,
}: GridCanvasProps) {
  const layout: Layout = useMemo(
    () => widgets.map((w) => ({ i: w.id, x: w.x, y: w.y, w: w.w, h: w.h })),
    [widgets],
  )

  const handleLayoutChange = (newLayout: Layout) => {
    const byId = new Map(newLayout.map((l) => [l.i, l]))
    const next = widgets.map((w) => {
      const l = byId.get(w.id)
      if (!l) return w
      return { ...w, x: l.x, y: l.y, w: l.w, h: l.h }
    })
    onLayoutChange(next)
  }

  if (widgets.length === 0) {
    return (
      <div className="h-64 flex items-center justify-center rounded-lg border border-dashed border-border/70 text-sm text-muted-foreground">
        当前布局为空，点击右上角"添加组件"开始搭建仪表盘
      </div>
    )
  }

  return (
    <ResponsiveGridLayout
      className="layout"
      layouts={{ lg: layout, md: layout, sm: layout, xs: layout }}
      breakpoints={BREAKPOINTS}
      cols={COLS}
      rowHeight={ROW_HEIGHT}
      margin={[10, 10]}
      isDraggable={editable}
      isResizable={editable}
      draggableHandle=".widget-drag-handle"
      onLayoutChange={handleLayoutChange}
      measureBeforeMount={false}
      useCSSTransforms
    >
      {widgets.map((w) => {
        const def = getWidgetDef(w.type)
        if (!def) {
          return (
            <div key={w.id} className="rounded-lg border border-dashed border-red-500/40 flex items-center justify-center text-xs text-red-400">
              未知组件类型: {w.type}
            </div>
          )
        }
        const Comp = def.component
        return (
          <div key={w.id} className="group relative h-full w-full flex flex-col rounded-lg overflow-hidden">
            <div
              className={cn(
                'widget-drag-handle h-4 shrink-0 flex items-center justify-between px-1.5 rounded-t-lg',
                'bg-muted/50 text-muted-foreground/60',
                editable ? 'cursor-grab active:cursor-grabbing' : 'cursor-default',
              )}
            >
              <GripHorizontal className="h-2.5 w-2.5" />
              {editable && (
                <button
                  className="opacity-0 group-hover:opacity-100 transition-opacity hover:text-red-400"
                  onClick={(e) => {
                    e.stopPropagation()
                    onRemoveWidget(w.id)
                  }}
                  title="移除组件"
                >
                  <X className="h-2.5 w-2.5" />
                </button>
              )}
            </div>
            <div className="flex-1 min-h-0">
              <Comp
                overviews={overviews}
                selections={selections}
                wsStatusByAccount={wsStatusByAccount}
                config={w.config}
                onConfigChange={(cfg) => onWidgetConfigChange(w.id, cfg)}
              />
            </div>
          </div>
        )
      })}
    </ResponsiveGridLayout>
  )
}
