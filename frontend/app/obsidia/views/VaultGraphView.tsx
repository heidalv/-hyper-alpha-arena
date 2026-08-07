/**
 * VaultGraphView —— 关系图谱（真 Obsidian graph 精神）
 * 节点 = 笔记 + 未解析实体([[币种]]/[[策略id]] 幻影节点)；边 = wikilink。
 * 按顶层文件夹着色，可切文件夹 / 限节点数 / 开关实体幻影节点；点真实笔记跳阅读页。
 */

import { useEffect, useMemo, useState } from 'react'
import { ReactFlow, Background, Controls, type Node, type Edge } from '@xyflow/react'
import { Loader2, Network } from 'lucide-react'
import { services } from '../core/ServiceContainer'
import type { VaultNote } from '../lib/vaultApi'
import { computeLayout } from './graph/forceLayout'
import { VIEW_VAULT_EXPLORER } from '../plugins'

interface Props {
  onNavigate?: (page: string) => void
}

const FOLDER_COLORS: Record<string, string> = {
  '01-分析报告': '#7aa2f7',
  '02-交易教训': '#9ece6a',
  '03-Hermes进化': '#bb9af7',
  '04-Agent决策': '#ff9e64',
  entity: '#7dcfff',
  _default: '#787c99',
}

function topFolder(folder: string): string {
  return folder.split('/')[0] || '_default'
}

export default function VaultGraphView({ onNavigate }: Props) {
  const [notes, setNotes] = useState<VaultNote[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [folder, setFolder] = useState<string>('02-交易教训')
  const [maxNodes, setMaxNodes] = useState(250)
  const [showEntities, setShowEntities] = useState(true)

  useEffect(() => {
    services.vault
      .fetchVaultIndex()
      .then((idx) => setNotes(idx.notes))
      .catch((e) => setError(e?.message || '加载失败'))
      .finally(() => setLoading(false))
  }, [])

  const folders = useMemo(() => {
    const set = new Set<string>()
    notes.forEach((n) => set.add(topFolder(n.folder)))
    return ['all', ...Array.from(set).filter((f) => f && f !== '_default').sort()]
  }, [notes])

  const { nodes, edges } = useMemo<{ nodes: Node[]; edges: Edge[] }>(() => {
    if (notes.length === 0) return { nodes: [], edges: [] }

    let selected = folder === 'all' ? notes : notes.filter((n) => topFolder(n.folder) === folder)
    selected = [...selected].sort((a, b) => b.mtime - a.mtime).slice(0, maxNodes)
    const selectedPaths = new Set(selected.map((n) => n.path))

    const rfNodes: Node[] = []
    const rfEdges: Edge[] = []
    const entityIds = new Map<string, string>()

    for (const n of selected) {
      rfNodes.push({
        id: n.path,
        data: { label: n.name, kind: 'note', path: n.path },
        position: { x: 0, y: 0 },
        style: nodeStyle(FOLDER_COLORS[topFolder(n.folder)] || FOLDER_COLORS._default, false),
      })
      // 已解析真实笔记之间的边
      for (const out of n.outlinks) {
        if (selectedPaths.has(out)) {
          rfEdges.push({ id: `${n.path}->${out}`, source: n.path, target: out, style: { stroke: '#3b4261' } })
        }
      }
      // 未解析实体幻影节点
      if (showEntities) {
        const resolvedStems = new Set(n.outlinks.map((p) => p.split('/').pop()?.replace(/\.md$/, '')))
        for (const raw of n.outlinks_raw) {
          const label = raw.split('/').pop()?.replace(/\.(md|canvas)$/, '') || raw
          if (raw.endsWith('.canvas') || resolvedStems.has(label)) continue
          const eid = `entity:${label}`
          if (!entityIds.has(eid)) {
            entityIds.set(eid, label)
            rfNodes.push({
              id: eid,
              data: { label, kind: 'entity' },
              position: { x: 0, y: 0 },
              style: nodeStyle(FOLDER_COLORS.entity, true),
            })
          }
          rfEdges.push({ id: `${n.path}->${eid}`, source: n.path, target: eid, style: { stroke: '#2a3a4f' } })
        }
      }
    }

    // 力导向布局
    const pos = computeLayout(
      rfNodes.map((n) => n.id),
      rfEdges.map((e) => ({ source: e.source, target: e.target })),
      { iterations: rfNodes.length > 200 ? 120 : 180, width: 1400, height: 900 },
    )
    rfNodes.forEach((n) => { n.position = pos.get(n.id) || { x: 0, y: 0 } })

    return { nodes: rfNodes, edges: rfEdges }
  }, [notes, folder, maxNodes, showEntities])

  const onNodeClick = (_: any, node: Node) => {
    if (node.data?.kind === 'note' && node.data?.path) {
      services.events.emit('vault:open', { path: node.data.path as string })
      onNavigate?.(VIEW_VAULT_EXPLORER)
    }
  }

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', background: 'var(--obs-bg)' }}>
      {/* 工具条 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 14px', borderBottom: '1px solid var(--obs-border)', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--obs-text)', fontWeight: 600, fontSize: 14 }}>
          <Network size={16} style={{ color: 'var(--obs-accent)' }} /> 关系图谱
        </div>
        <label style={labelStyle}>文件夹
          <select value={folder} onChange={(e) => setFolder(e.target.value)} style={selectStyle}>
            {folders.map((f) => <option key={f} value={f}>{f === 'all' ? '全部' : f}</option>)}
          </select>
        </label>
        <label style={labelStyle}>最大节点
          <select value={maxNodes} onChange={(e) => setMaxNodes(Number(e.target.value))} style={selectStyle}>
            {[100, 150, 250, 400, 600].map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label style={{ ...labelStyle, cursor: 'pointer' }}>
          <input type="checkbox" checked={showEntities} onChange={(e) => setShowEntities(e.target.checked)} />
          实体节点(币种/策略)
        </label>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--obs-text-faint)' }}>
          {nodes.length} 节点 · {edges.length} 边
        </span>
      </div>

      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {loading && (
          <div style={centerBox}><Loader2 size={18} className="animate-spin" /> 加载图谱…</div>
        )}
        {error && <div style={{ ...centerBox, color: 'var(--obs-red)' }}>{error}（需重启后端加载 /api/vault）</div>}
        {!loading && !error && (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodeClick={onNodeClick}
            fitView
            minZoom={0.1}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#2a2e3f" gap={20} />
            <Controls />
          </ReactFlow>
        )}
      </div>
    </div>
  )
}

function nodeStyle(color: string, entity: boolean): React.CSSProperties {
  return {
    background: entity ? 'transparent' : 'var(--obs-panel)',
    color: 'var(--obs-text)',
    border: `2px solid ${color}`,
    borderRadius: entity ? 999 : 8,
    fontSize: 11,
    padding: entity ? '3px 8px' : '5px 10px',
    width: 'auto',
    maxWidth: 160,
    boxShadow: `0 0 10px ${color}22`,
  }
}

const labelStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--obs-text-muted)' }
const selectStyle: React.CSSProperties = { background: 'var(--obs-panel)', color: 'var(--obs-text)', border: '1px solid var(--obs-border)', borderRadius: 5, padding: '2px 6px', fontSize: 12 }
const centerBox: React.CSSProperties = { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--obs-text-muted)' }
