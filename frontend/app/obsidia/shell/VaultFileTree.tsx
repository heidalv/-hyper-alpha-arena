/**
 * VaultFileTree —— 左侧栏"文件"模式下的真实 vault 目录树
 * 点击 .md 文件 → EventBus 'vault:open' + 导航到知识库阅读页。
 */

import { useEffect, useState, useCallback } from 'react'
import { ChevronRight, ChevronDown, FileText, Workflow, Folder, FolderOpen, RefreshCw } from 'lucide-react'
import { services } from '../core/ServiceContainer'
import type { VaultTreeNode } from '../lib/vaultApi'
import { VIEW_VAULT_EXPLORER, VIEW_VAULT_CANVAS } from '../plugins'

interface Props {
  onNavigate: (page: string) => void
  activePath?: string
}

function TreeItem({ node, depth, onNavigate, activePath }: {
  node: VaultTreeNode
  depth: number
  onNavigate: (page: string) => void
  activePath?: string
}) {
  const [open, setOpen] = useState(depth < 1)

  if (node.type === 'folder') {
    return (
      <div>
        <div
          className="obs-nav-item"
          style={{ paddingLeft: 8 + depth * 12, color: 'var(--obs-text-muted)' }}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {open ? <FolderOpen size={14} /> : <Folder size={14} />}
          <span>{node.name}</span>
        </div>
        {open && node.children?.map((c) => (
          <TreeItem key={c.path} node={c} depth={depth + 1} onNavigate={onNavigate} activePath={activePath} />
        ))}
      </div>
    )
  }

  const isCanvas = node.type === 'canvas'
  const isActive = activePath === node.path
  return (
    <div
      className={`obs-nav-item${isActive ? ' active' : ''}`}
      style={{ paddingLeft: 26 + depth * 12 }}
      title={node.path}
      onClick={() => {
        if (isCanvas) {
          onNavigate(VIEW_VAULT_CANVAS)
          services.events.emit('vault:open', { path: node.path })
        } else {
          services.events.emit('vault:open', { path: node.path })
          onNavigate(VIEW_VAULT_EXPLORER)
        }
      }}
    >
      {isCanvas ? <Workflow size={14} /> : <FileText size={14} />}
      <span>{node.name}</span>
    </div>
  )
}

export default function VaultFileTree({ onNavigate, activePath }: Props) {
  const [tree, setTree] = useState<VaultTreeNode | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await services.vault.fetchVaultTree()
      setTree(data)
    } catch (e: any) {
      setError(e?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div style={{ paddingBottom: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 12px' }}>
        <span className="obs-nav-group-label" style={{ padding: 0 }}>VAULT 文件</span>
        <RefreshCw
          size={13}
          style={{ cursor: 'pointer', color: 'var(--obs-text-faint)' }}
          className={loading ? 'animate-spin' : ''}
          onClick={load}
        />
      </div>
      {error && (
        <div style={{ padding: '8px 14px', fontSize: 12, color: 'var(--obs-red)' }}>
          {error}（后端 /api/vault 是否已启用？）
        </div>
      )}
      {tree?.children?.map((c) => (
        <TreeItem key={c.path} node={c} depth={0} onNavigate={onNavigate} activePath={activePath} />
      ))}
    </div>
  )
}
