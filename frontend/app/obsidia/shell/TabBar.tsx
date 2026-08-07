/**
 * TabBar —— 顶部多标签（Obsidian leaves 风格）
 */

import { X } from 'lucide-react'

interface Props {
  tabs: string[]
  activePage: string
  titles: Record<string, string>
  onSelect: (page: string) => void
  onClose: (page: string) => void
}

export default function TabBar({ tabs, activePage, titles, onSelect, onClose }: Props) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'stretch',
        height: 34,
        background: 'var(--obs-tabbar)',
        borderBottom: '1px solid var(--obs-border)',
        overflowX: 'auto',
        flexShrink: 0,
      }}
    >
      {tabs.map((page) => (
        <div
          key={page}
          className={`obs-tab${page === activePage ? ' active' : ''}`}
          onClick={() => onSelect(page)}
          title={titles[page] || page}
        >
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{titles[page] || page}</span>
          {tabs.length > 1 && (
            <span
              className="obs-tab-close"
              onClick={(e) => {
                e.stopPropagation()
                onClose(page)
              }}
            >
              <X size={12} />
            </span>
          )}
        </div>
      ))}
    </div>
  )
}
