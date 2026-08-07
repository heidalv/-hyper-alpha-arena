/**
 * AgentEvolutionView —— Agent 进化中心 (MOC)
 * 直接渲染真实 vault 的 `Agent进化中心.md`，其中的 ```dataview 表全部实时求值。
 * 这是"网页里跑真 Obsidian 知识库"的门面页。
 */

import { useEffect, useState } from 'react'
import { Loader2, RefreshCw, Sparkles } from 'lucide-react'
import { services } from '../core/ServiceContainer'
import type { VaultFile, VaultNote } from '../lib/vaultApi'
import MarkdownNote from './MarkdownNote'

interface Props {
  onNavigate?: (page: string) => void
}

const MOC_PATH = 'Agent进化中心.md'

export default function AgentEvolutionView({ onNavigate }: Props) {
  const [file, setFile] = useState<VaultFile | null>(null)
  const [notes, setNotes] = useState<VaultNote[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    Promise.all([services.vault.fetchVaultFile(MOC_PATH), services.vault.fetchVaultIndex()])
      .then(([f, idx]) => { setFile(f); setNotes(idx.notes) })
      .catch((e) => setError(e?.message || '加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', background: 'var(--obs-bg)' }}>
      <div style={{ maxWidth: 1040, margin: '0 auto', padding: '20px 28px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
          <Sparkles size={20} style={{ color: 'var(--obs-purple)' }} />
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0, color: 'var(--obs-text)' }}>Agent 进化中心</h1>
          <button
            onClick={load}
            style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 5, padding: '4px 10px', fontSize: 12, background: 'var(--obs-panel)', border: '1px solid var(--obs-border)', borderRadius: 6, color: 'var(--obs-text-muted)', cursor: 'pointer' }}
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> 刷新
          </button>
        </div>

        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--obs-text-muted)', padding: 20 }}>
            <Loader2 size={16} className="animate-spin" /> 正在从真实 vault 加载知识库…
          </div>
        )}
        {error && (
          <div style={{ color: 'var(--obs-red)', fontSize: 13, padding: 16, border: '1px solid var(--obs-border)', borderRadius: 8 }}>
            {error}
            <div style={{ marginTop: 6, color: 'var(--obs-text-faint)' }}>
              需重启后端以加载 /api/vault 路由；或先运行 `python tools/export_to_obsidian.py` 生成 vault。
            </div>
          </div>
        )}
        {!loading && !error && file && (
          <MarkdownNote body={file.body} frontmatter={file.frontmatter} notes={notes} onNavigate={onNavigate} />
        )}
      </div>
    </div>
  )
}
