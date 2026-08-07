/**
 * Ribbon —— 最左侧 48px 图标条（Obsidian 风格）
 * 上：知识库视图；中：交易快捷入口；下：命令面板 / 侧栏折叠 / 设置。
 */

import { PanelLeft, Command, Settings } from 'lucide-react'
import { registry } from '../core/PluginRegistry'

interface Props {
  currentPage: string
  onNavigate: (page: string) => void
  onToggleSidebar: () => void
  onOpenCommand: () => void
}

export default function Ribbon({ currentPage, onNavigate, onToggleSidebar, onOpenCommand }: Props) {
  const items = registry.getRibbonItems()
  const obsidian = items.filter((i) => i.zone === 'obsidian')
  const trading = items.filter((i) => i.zone === 'trading')

  const renderItem = (i: typeof items[number]) => {
    const Icon = i.icon
    return (
      <div
        key={i.id}
        className={`obs-ribbon-btn${currentPage === i.page ? ' active' : ''}`}
        title={i.label}
        onClick={() => onNavigate(i.page)}
      >
        <Icon size={19} />
      </div>
    )
  }

  return (
    <div
      style={{
        width: 48,
        flexShrink: 0,
        background: 'var(--obs-ribbon)',
        borderRight: '1px solid var(--obs-border)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        paddingTop: 6,
      }}
    >
      <div className="obs-ribbon-btn" title="折叠/展开侧栏" onClick={onToggleSidebar}>
        <PanelLeft size={19} />
      </div>
      <div style={{ height: 8 }} />
      {obsidian.map(renderItem)}
      <div style={{ width: 24, height: 1, background: 'var(--obs-border)', margin: '8px 0' }} />
      {trading.map(renderItem)}

      <div style={{ flex: 1 }} />
      <div className="obs-ribbon-btn" title="命令面板 (Ctrl/Cmd+P)" onClick={onOpenCommand}>
        <Command size={19} />
      </div>
      <div
        className={`obs-ribbon-btn${currentPage === 'settings' ? ' active' : ''}`}
        title="设置"
        onClick={() => onNavigate('settings')}
        style={{ marginBottom: 8 }}
      >
        <Settings size={19} />
      </div>
    </div>
  )
}
