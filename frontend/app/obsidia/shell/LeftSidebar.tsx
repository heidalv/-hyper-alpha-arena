/**
 * LeftSidebar —— 可折叠左侧栏
 * 两种模式：导航（注册表分组）/ 文件（真实 vault 目录树）。
 */

import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Compass, FolderTree, Search } from 'lucide-react'
import { registry } from '../core/PluginRegistry'
import VaultFileTree from './VaultFileTree'

interface Props {
  currentPage: string
  onNavigate: (page: string) => void
  activeVaultPath?: string
}

type Mode = 'nav' | 'files'

export default function LeftSidebar({ currentPage, onNavigate, activeVaultPath }: Props) {
  const [mode, setMode] = useState<Mode>('nav')
  const [query, setQuery] = useState('')
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({})

  const groups = useMemo(() => registry.getNavGroups(), [])

  const toggleGroup = (id: string) =>
    setCollapsedGroups((prev) => ({ ...prev, [id]: !prev[id] }))

  const q = query.trim().toLowerCase()

  return (
    <aside
      style={{
        width: 240,
        flexShrink: 0,
        background: 'var(--obs-sidebar)',
        borderRight: '1px solid var(--obs-border)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* 顶部：品牌 + 模式切换 */}
      <div style={{ padding: '10px 12px 6px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 14, color: 'var(--obs-purple)' }}>Obsidia</span>
        <span style={{ fontSize: 11, color: 'var(--obs-text-faint)' }}>Alpha Arena</span>
      </div>

      <div style={{ display: 'flex', gap: 4, padding: '0 10px 8px' }}>
        <button
          onClick={() => setMode('nav')}
          style={tabBtnStyle(mode === 'nav')}
        >
          <Compass size={14} /> 导航
        </button>
        <button
          onClick={() => setMode('files')}
          style={tabBtnStyle(mode === 'files')}
        >
          <FolderTree size={14} /> 文件
        </button>
      </div>

      {mode === 'nav' && (
        <div style={{ padding: '0 10px 8px' }}>
          <div style={{ position: 'relative' }}>
            <Search size={13} style={{ position: 'absolute', left: 8, top: 8, color: 'var(--obs-text-faint)' }} />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索功能…"
              style={{
                width: '100%',
                padding: '5px 8px 5px 26px',
                fontSize: 12,
                background: 'var(--obs-bg)',
                border: '1px solid var(--obs-border)',
                borderRadius: 6,
                color: 'var(--obs-text)',
                outline: 'none',
              }}
            />
          </div>
        </div>
      )}

      <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
        {mode === 'nav' ? (
          groups.map((g) => {
            const items = q
              ? g.items.filter((it) => it.label.toLowerCase().includes(q) || it.page.includes(q))
              : g.items
            if (items.length === 0) return null
            const collapsed = collapsedGroups[g.id] ?? (g.defaultOpen === false)
            return (
              <div key={g.id} style={{ marginBottom: 4 }}>
                <div className="obs-nav-group-label" onClick={() => toggleGroup(g.id)} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                  {g.label}
                </div>
                {!collapsed && items.map((it) => {
                  const Icon = it.icon
                  return (
                    <div
                      key={it.page}
                      className={`obs-nav-item${currentPage === it.page ? ' active' : ''}`}
                      onClick={() => onNavigate(it.page)}
                    >
                      <Icon size={15} />
                      <span>{it.label}</span>
                    </div>
                  )
                })}
              </div>
            )
          })
        ) : (
          <VaultFileTree onNavigate={onNavigate} activePath={activeVaultPath} />
        )}
      </div>
    </aside>
  )
}

function tabBtnStyle(active: boolean): React.CSSProperties {
  return {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 5,
    padding: '5px 0',
    fontSize: 12,
    borderRadius: 6,
    border: '1px solid ' + (active ? 'var(--obs-border-strong)' : 'transparent'),
    background: active ? 'var(--obs-active)' : 'transparent',
    color: active ? 'var(--obs-text)' : 'var(--obs-text-muted)',
    cursor: 'pointer',
  }
}
