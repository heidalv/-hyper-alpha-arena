/**
 * useDashboardLayout — 布局持久化（对接 /api/dashboard/layouts*）
 *
 * 首次加载：拉取激活布局；若从未保存过，使用内置默认布局（纯前端常量，不落库）。
 * 拖拽/缩放/增删组件/切换账户选择变更后 debounce 保存，换设备/账号也能同步。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  activateLayout as apiActivateLayout,
  createLayout as apiCreateLayout,
  deleteLayout as apiDeleteLayout,
  getActiveLayout,
  listLayouts as apiListLayouts,
  updateLayout as apiUpdateLayout,
} from '@/lib/dashboardApi'
import { WIDGET_REGISTRY } from './WidgetRegistry'
import type { AccountSelection, DashboardLayoutDTO, WidgetInstance } from './types'

const SAVE_DEBOUNCE_MS = 1200

function buildDefaultWidgets(): WidgetInstance[] {
  const row1 = ['equity_card', 'pnl_card', 'winrate_card', 'positions_card']
  const widgets: WidgetInstance[] = row1.map((type, idx) => ({
    id: `w_${type}_${idx}`,
    type,
    x: idx * 3,
    y: 0,
    w: 3,
    h: 3,
  }))

  let y = 3
  widgets.push({ id: 'w_account_compare_0', type: 'account_compare', x: 0, y, w: 12, h: 5 })
  y += 5
  widgets.push({ id: 'w_asset_curve_0', type: 'asset_curve', x: 0, y, w: 8, h: 7 })
  widgets.push({ id: 'w_positions_table_0', type: 'positions_table', x: 8, y, w: 4, h: 7 })
  y += 7
  widgets.push({ id: 'w_strategy_overview_0', type: 'strategy_overview', x: 0, y, w: 6, h: 6 })
  widgets.push({ id: 'w_recent_decisions_0', type: 'recent_decisions', x: 6, y, w: 6, h: 6 })
  // 注：指标图表（indicator_chart）默认不放入布局 —— 它会触发全量因子重计算
  // （380+ 因子，单次可达数秒到数十秒，是后端负载的主要来源之一）。改为按需
  // 从"添加组件"手动加入，避免每个用户打开仪表盘就自动跑一遍重计算。
  return widgets
}

export function useDashboardLayout() {
  const [widgets, setWidgets] = useState<WidgetInstance[]>([])
  const [selectedAccounts, setSelectedAccounts] = useState<AccountSelection[]>([])
  const [activeLayoutId, setActiveLayoutId] = useState<number | null>(null)
  const [layoutName, setLayoutName] = useState('默认布局')
  const [layouts, setLayouts] = useState<DashboardLayoutDTO[]>([])
  const [ready, setReady] = useState(false)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const skipNextSave = useRef(true)

  const refreshLayoutList = useCallback(async () => {
    try {
      const rows = await apiListLayouts()
      setLayouts(rows)
    } catch (err) {
      console.warn('[useDashboardLayout] 布局列表加载失败:', err)
    }
  }, [])

  useEffect(() => {
    (async () => {
      try {
        const active = await getActiveLayout()
        if (active) {
          skipNextSave.current = true
          setActiveLayoutId(active.id)
          setLayoutName(active.name)
          setWidgets(active.widgets || [])
          setSelectedAccounts(active.selected_accounts || [])
        } else {
          skipNextSave.current = true
          setWidgets(buildDefaultWidgets())
        }
      } catch (err) {
        console.warn('[useDashboardLayout] 激活布局加载失败，使用默认布局:', err)
        setWidgets(buildDefaultWidgets())
      } finally {
        setReady(true)
      }
      refreshLayoutList()
    })()
  }, [refreshLayoutList])

  // debounce 保存：仅当已存在激活布局时自动回写；首次（无激活布局）不自动建表，
  // 避免用户还没做任何操作就在后端产生一条空布局记录。
  useEffect(() => {
    if (!ready) return
    if (skipNextSave.current) {
      skipNextSave.current = false
      return
    }
    if (activeLayoutId == null) return

    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(async () => {
      try {
        await apiUpdateLayout(activeLayoutId, { widgets, selected_accounts: selectedAccounts })
      } catch (err) {
        console.warn('[useDashboardLayout] 自动保存失败:', err)
      }
    }, SAVE_DEBOUNCE_MS)

    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widgets, selectedAccounts, activeLayoutId, ready])

  const saveAsNewLayout = useCallback(
    async (name: string) => {
      const created = await apiCreateLayout({
        name,
        widgets,
        selected_accounts: selectedAccounts,
        activate: true,
      })
      setActiveLayoutId(created.id)
      setLayoutName(created.name)
      await refreshLayoutList()
      return created
    },
    [widgets, selectedAccounts, refreshLayoutList],
  )

  const switchToLayout = useCallback(async (id: number) => {
    const activated = await apiActivateLayout(id)
    skipNextSave.current = true
    setActiveLayoutId(activated.id)
    setLayoutName(activated.name)
    setWidgets(activated.widgets || [])
    setSelectedAccounts(activated.selected_accounts || [])
  }, [])

  const removeLayout = useCallback(
    async (id: number) => {
      await apiDeleteLayout(id)
      if (id === activeLayoutId) {
        setActiveLayoutId(null)
        setWidgets(buildDefaultWidgets())
        setSelectedAccounts([])
      }
      await refreshLayoutList()
    },
    [activeLayoutId, refreshLayoutList],
  )

  const addWidget = useCallback((type: string) => {
    const def = WIDGET_REGISTRY.find((w) => w.type === type)
    if (!def) return
    setWidgets((prev) => {
      const maxY = prev.length === 0 ? 0 : Math.max(...prev.map((w) => w.y + w.h))
      const id = `w_${type}_${Date.now()}`
      return [...prev, { id, type, x: 0, y: maxY, w: def.defaultSize.w, h: def.defaultSize.h }]
    })
  }, [])

  const removeWidget = useCallback((id: string) => {
    setWidgets((prev) => prev.filter((w) => w.id !== id))
  }, [])

  const updateWidgetConfig = useCallback((id: string, config: Record<string, unknown>) => {
    setWidgets((prev) => prev.map((w) => (w.id === id ? { ...w, config } : w)))
  }, [])

  return {
    ready,
    widgets,
    setWidgets,
    selectedAccounts,
    setSelectedAccounts,
    activeLayoutId,
    layoutName,
    setLayoutName,
    layouts,
    saveAsNewLayout,
    switchToLayout,
    removeLayout,
    addWidget,
    removeWidget,
    updateWidgetConfig,
  }
}
