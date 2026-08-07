/**
 * VaultExplorerView —— 知识库阅读页
 * 监听 EventBus 'vault:open' 打开真实 vault 笔记，渲染 markdown + 反链面板。
 */

import { useEffect, useMemo, useState } from 'react'
import { Link2, Loader2, FileText } from 'lucide-react'
import { services } from '../core/ServiceContainer'
import type { VaultFile, VaultNote } from '../lib/vaultApi'
import MarkdownNote from './MarkdownNote'

interface Props {
  onNavigate?: (page: string) => void
}

const DEFAULT_NOTE = 'Agent进化中心.md'

export default function VaultExplorerView({ onNavigate }: Props) {
  const [notes, setNotes] = useState<VaultNote[]>([])
  const [path, setPath] = useState<string>(DEFAULT_NOTE)
  const [file, setFile] = useState<VaultFile | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 全库索引（一次）
  useEffect(() => {
    services.vault.fetchVaultIndex().then((idx) => setNotes(idx.notes)).catch(() => {})
  }, [])

  // 监听打开事件
  useEffect(() => {
    return services.events.on('vault:open', ({ path: p }) => {
      if (p && p.endsWith('.md')) setPath(p)
    })
  }, [])

  // 加载当前文件
  useEffect(() => {
    if (!path) return
    let cancelled = false
    setLoading(true)
    setError(null)
    services.vault
      .fetchVaultFile(path)
      .then((f) => { if (!cancelled) setFile(f) })
      .catch((e) => { if (!cancelled) setError(e?.message || '加载失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [path])

  const backlinks = useMemo(() => {
    const n = notes.find((x) => x.path === path)
    if (!n) return []
    return n.backlinks
      .map((bp) => notes.find((x) => x.path === bp))
      .filter(Boolean) as VaultNote[]
  }, [notes, path])

  return (
    <div style={{ display: 'flex', flex: 1, minHeight: 0, background: 'var(--obs-bg)' }}>
      {/* 阅读区 */}
      <div style={{ flex: 1, minWidth: 0, overflowY: 'auto', padding: '20px 32px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, fontSize: 12, color: 'var(--obs-text-faint)' }}>
          <FileText size={13} /> {path}
        </div>
        <h1 style={{ fontSize: 24, fontWeight: 700, margin: '0 0 16px', color: 'var(--obs-text)' }}>
          {file?.name || '—'}
        </h1>
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--obs-text-muted)' }}>
            <Loader2 size={16} className="animate-spin" /> 加载中…
          </div>
        )}
        {error && (
          <div style={{ color: 'var(--obs-red)', fontSize: 13 }}>
            {error}（后端 /api/vault 是否已启用？需重启后端加载 vault 路由）
          </div>
        )}
        {!loading && !error && file && (
          <MarkdownNote body={file.body} frontmatter={file.frontmatter} notes={notes} onNavigate={onNavigate} />
        )}
      </div>

      {/* 反链面板 */}
      <div style={{ width: 240, flexShrink: 0, borderLeft: '1px solid var(--obs-border)', padding: '16px 12px', overflowY: 'auto', background: 'var(--obs-sidebar)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600, color: 'var(--obs-text-muted)', marginBottom: 10 }}>
          <Link2 size={14} /> 反链 · {backlinks.length}
        </div>
        {backlinks.length === 0 && (
          <div style={{ fontSize: 12, color: 'var(--obs-text-faint)' }}>暂无反链</div>
        )}
        {backlinks.map((b) => (
          <div
            key={b.path}
            className="obs-nav-item"
            style={{ margin: '2px 0' }}
            onClick={() => setPath(b.path)}
            title={b.path}
          >
            <FileText size={13} />
            <span>{b.name}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
