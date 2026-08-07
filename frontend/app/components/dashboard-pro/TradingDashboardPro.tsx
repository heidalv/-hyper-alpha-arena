/**
 * TradingDashboardPro — 交易矩阵仪表盘（可配置网格版）
 *
 * 替换旧版 comprehensive 页（HyperliquidView / BinanceView / UnifiedDashboardView）。
 * 顶部：账户多选对比 + 布局管理 + 编辑模式切换 + 添加组件；
 * 主体：react-grid-layout 自由拖拽/缩放网格，widget 数据来自 useDashboardData（多账户聚合）。
 */
import { useState } from 'react'
import { motion } from 'framer-motion'
import {
  LayoutGrid,
  Pencil,
  Check,
  Plus,
  Save,
  ChevronDown,
  RefreshCw,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

import AccountModeSelector from './AccountModeSelector'
import GridCanvas from './GridCanvas'
import { WIDGET_REGISTRY } from './WidgetRegistry'
import { useDashboardData } from './useDashboardData'
import { useDashboardLayout } from './useDashboardLayout'

interface TradingDashboardProProps {
  onNavigate?: (page: string) => void
}

export default function TradingDashboardPro({ onNavigate: _onNavigate }: TradingDashboardProProps) {
  const [editing, setEditing] = useState(false)
  const [savingName, setSavingName] = useState('')
  const [showSaveInput, setShowSaveInput] = useState(false)

  const {
    ready,
    widgets,
    selectedAccounts,
    setSelectedAccounts,
    layoutName,
    layouts,
    activeLayoutId,
    saveAsNewLayout,
    switchToLayout,
    addWidget,
    removeWidget,
    updateWidgetConfig,
    setWidgets,
  } = useDashboardLayout()

  const { overviews, wsStatusByAccount, loading, refresh } = useDashboardData(selectedAccounts)

  const handleSaveAsNew = async () => {
    const name = savingName.trim() || `布局 ${layouts.length + 1}`
    await saveAsNewLayout(name)
    setSavingName('')
    setShowSaveInput(false)
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-y-auto">
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
        className="shrink-0 sticky top-0 z-20 bg-background/95 backdrop-blur border-b px-4 py-3 flex flex-wrap items-center gap-2.5"
      >
        <div className="flex items-center gap-2 mr-2">
          <LayoutGrid className="h-5 w-5 text-primary" />
          <span className="text-base font-bold tracking-tight">交易矩阵</span>
        </div>

        <AccountModeSelector value={selectedAccounts} onChange={setSelectedAccounts} />

        <div className="flex-1" />

        <Button variant="ghost" size="sm" className="h-8 gap-1.5" onClick={refresh} disabled={loading}>
          <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          刷新
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-1.5">
              {layoutName}
              <ChevronDown className="h-3 w-3 opacity-60" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel>已保存布局</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {layouts.length === 0 && (
              <div className="px-2 py-2 text-xs text-muted-foreground">暂无已保存布局</div>
            )}
            {layouts.map((l) => (
              <DropdownMenuItem key={l.id} onClick={() => switchToLayout(l.id)} className="justify-between">
                <span className="truncate">{l.name}</span>
                {l.id === activeLayoutId && <Check className="h-3.5 w-3.5 text-emerald-400" />}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {editing && (
          <>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="h-8 gap-1.5">
                  <Plus className="h-3.5 w-3.5" />
                  添加组件
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56 max-h-96 overflow-y-auto">
                <DropdownMenuLabel>组件库</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {WIDGET_REGISTRY.map((def) => {
                  const Icon = def.icon
                  return (
                    <DropdownMenuItem key={def.type} onClick={() => addWidget(def.type)} className="gap-2">
                      <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                      <div className="flex flex-col">
                        <span>{def.label}</span>
                        <span className="text-[10px] text-muted-foreground">{def.description}</span>
                      </div>
                    </DropdownMenuItem>
                  )
                })}
              </DropdownMenuContent>
            </DropdownMenu>

            {showSaveInput ? (
              <div className="flex items-center gap-1">
                <input
                  autoFocus
                  value={savingName}
                  onChange={(e) => setSavingName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSaveAsNew()}
                  placeholder="布局名称"
                  className="h-8 rounded-md border border-border bg-background px-2 text-xs w-32"
                />
                <Button size="sm" className="h-8" onClick={handleSaveAsNew}>
                  确定
                </Button>
              </div>
            ) : (
              <Button variant="outline" size="sm" className="h-8 gap-1.5" onClick={() => setShowSaveInput(true)}>
                <Save className="h-3.5 w-3.5" />
                另存为
              </Button>
            )}
          </>
        )}

        <Button
          variant={editing ? 'default' : 'outline'}
          size="sm"
          className="h-8 gap-1.5"
          onClick={() => setEditing((v) => !v)}
        >
          {editing ? <Check className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}
          {editing ? '完成编辑' : '编辑布局'}
        </Button>
      </motion.div>

      <div className="flex-1 p-4 flex flex-col gap-3">
        {!ready ? (
          <div className="h-64 flex items-center justify-center text-sm text-muted-foreground">加载布局中…</div>
        ) : (
          <>
            {selectedAccounts.length === 0 && (
              <motion.div
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                className="shrink-0 flex items-center gap-2 rounded-lg border border-dashed border-amber-500/40 bg-amber-500/5 px-3 py-2"
              >
                <span className="text-xs text-muted-foreground">
                  请先在顶部"选择账户"添加要监控的账户，组件会在选择后显示实时数据
                </span>
                <Badge variant="outline" className="text-[10px] shrink-0">支持同时勾选实盘 / 模拟 / 多个交易所账户</Badge>
              </motion.div>
            )}
            <div className="flex-1">
              <GridCanvas
                widgets={widgets}
                overviews={overviews}
                selections={selectedAccounts}
                wsStatusByAccount={wsStatusByAccount}
                editable={editing}
                onLayoutChange={setWidgets}
                onRemoveWidget={removeWidget}
                onWidgetConfigChange={updateWidgetConfig}
              />
            </div>
          </>
        )}
      </div>
    </div>
  )
}
