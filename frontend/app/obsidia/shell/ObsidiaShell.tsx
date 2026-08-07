/**
 * ObsidiaShell —— Obsidian 式工作台外壳
 * -------------------------------------
 * 结构：Ribbon(48) | LeftSidebar(240, 可折叠) | [TabBar / 内容区(children) / StatusBar]
 * 内容区渲染由 main.tsx 传入的 children（保留其 keepAlive/WS/全局状态），
 * 外壳只负责导航、标签、命令面板与主题（Tokyo Night 暗色）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import '../styles/obsidia.css'
import '@xyflow/react/dist/style.css'

import Ribbon from './Ribbon'
import LeftSidebar from './LeftSidebar'
import TabBar from './TabBar'
import CommandPalette from './CommandPalette'
import { registry } from '../core/PluginRegistry'
import { services } from '../core/ServiceContainer'

interface Props {
  currentPage: string
  onNavigate: (page: string) => void
  /** 完整 page→标题映射（含 legacy PAGE_TITLES + Obsidia 视图） */
  titles: Record<string, string>
  /** 底部状态栏右侧内容（连接状态等） */
  statusRight?: React.ReactNode
  children: React.ReactNode
}

export default function ObsidiaShell({ currentPage, onNavigate, titles, statusRight, children }: Props) {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [tabs, setTabs] = useState<string[]>([currentPage])
  const [cmdOpen, setCmdOpen] = useState(false)
  const [activeVaultPath, setActiveVaultPath] = useState<string | undefined>()

  const titleMap = useMemo(
    () => ({ ...registry.getPageTitles(), ...titles }),
    [titles],
  )

  // 当前页并入标签
  useEffect(() => {
    setTabs((prev) => (prev.includes(currentPage) ? prev : [...prev, currentPage]))
  }, [currentPage])

  // 记录当前打开的 vault 文件（供文件树高亮）
  useEffect(() => {
    const off = services.events.on('vault:open', ({ path }) => setActiveVaultPath(path))
    return off
  }, [])

  // 全局快捷键：Ctrl/Cmd+P 命令面板
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === 'p' || e.key === 'P')) {
        e.preventDefault()
        setCmdOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    const off = services.events.on('command-palette:toggle', () => setCmdOpen((o) => !o))
    return () => {
      window.removeEventListener('keydown', onKey)
      off()
    }
  }, [])

  const closeTab = useCallback(
    (page: string) => {
      setTabs((prev) => {
        if (prev.length <= 1) return prev
        const idx = prev.indexOf(page)
        const next = prev.filter((p) => p !== page)
        if (page === currentPage && next.length) {
          onNavigate(next[Math.max(0, idx - 1)])
        }
        return next
      })
    },
    [currentPage, onNavigate],
  )

  return (
    <div className="obsidia dark obsidia-root">
      <Ribbon
        currentPage={currentPage}
        onNavigate={onNavigate}
        onToggleSidebar={() => setSidebarOpen((o) => !o)}
        onOpenCommand={() => setCmdOpen(true)}
      />

      {sidebarOpen && (
        <LeftSidebar
          currentPage={currentPage}
          onNavigate={onNavigate}
          activeVaultPath={activeVaultPath}
        />
      )}

      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0, minHeight: 0 }}>
        <TabBar
          tabs={tabs}
          activePage={currentPage}
          titles={titleMap}
          onSelect={onNavigate}
          onClose={closeTab}
        />

        {/* 内容区：main.tsx 的 renderMainContent()（keepAlive 保持全部页面挂载） */}
        <div style={{ flex: 1, minHeight: 0, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--obs-bg)' }}>
          {children}
        </div>

        {/* 状态栏 */}
        <div
          style={{
            height: 24,
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            padding: '0 12px',
            fontSize: 11,
            color: 'var(--obs-text-muted)',
            background: 'var(--obs-statusbar)',
            borderTop: '1px solid var(--obs-border)',
          }}
        >
          <span style={{ color: 'var(--obs-purple)', fontWeight: 600 }}>Obsidia</span>
          <span>{titleMap[currentPage] || currentPage}</span>
          <div style={{ flex: 1 }} />
          {statusRight}
        </div>
      </div>

      <CommandPalette open={cmdOpen} onClose={() => setCmdOpen(false)} onNavigate={onNavigate} />
    </div>
  )
}
