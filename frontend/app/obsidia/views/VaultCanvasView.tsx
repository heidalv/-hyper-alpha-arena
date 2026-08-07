/**
 * VaultCanvasView —— 还原 Obsidian .canvas
 * 读 /api/vault/canvas，用 @xyflow/react 画出 file/text 节点、带标签的边、Tokyo Night 颜色。
 * 点 file 节点 → 打开对应笔记。默认加载 Hermes四层进化.canvas。
 */

import { useEffect, useMemo, useState } from 'react'
import { ReactFlow, Background, Controls, MarkerType, type Node, type Edge } from '@xyflow/react'
import { Loader2, Workflow, FileText } from 'lucide-react'
import { services } from '../core/ServiceContainer'
import type { VaultCanvas } from '../lib/vaultApi'
import { VIEW_VAULT_EXPLORER } from '../plugins'

interface Props {
  onNavigate?: (page: string) => void
}

const DEFAULT_CANVAS = '_canvas/Hermes四层进化.canvas'

/** Obsidian 预设色 1-6 + 裸 hex 归一 */
const PRESET: Record<string, string> = { '1': '#f7768e', '2': '#ff9e64', '3': '#e0af68', '4': '#9ece6a', '5': '#7dcfff', '6': '#bb9af7' }
function normColor(c?: string): string {
  if (!c) return '#7aa2f7'
  if (PRESET[c]) return PRESET[c]
  if (c.startsWith('#')) return c
  if (/^[0-9a-fA-F]{6}$/.test(c)) return `#${c}`
  return '#7aa2f7'
}

export default function VaultCanvasView({ onNavigate }: Props) {
  const [path, setPath] = useState(DEFAULT_CANVAS)
  const [canvas, setCanvas] = useState<VaultCanvas | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    return services.events.on('vault:open', ({ path: p }) => {
      if (p && p.endsWith('.canvas')) setPath(p)
    })
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    services.vault
      .fetchVaultCanvas(path)
      .then((c) => { if (!cancelled) setCanvas(c) })
      .catch((e) => { if (!cancelled) setError(e?.message || '加载失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [path])

  const { nodes, edges } = useMemo<{ nodes: Node[]; edges: Edge[] }>(() => {
    const data = canvas?.data
    if (!data?.nodes) return { nodes: [], edges: [] }

    const rfNodes: Node[] = data.nodes.map((n) => {
      const color = normColor(n.color)
      const isFile = n.type === 'file'
      return {
        id: String(n.id),
        position: { x: n.x ?? 0, y: n.y ?? 0 },
        data: {
          label: isFile ? (n.file as string) : (n.text as string) || '',
          kind: n.type,
          file: n.file,
        },
        style: {
          width: n.width ?? 260,
          height: n.height ?? 140,
          background: 'var(--obs-panel)',
          color: 'var(--obs-text)',
          border: `2px solid ${color}`,
          borderLeft: `6px solid ${color}`,
          borderRadius: 10,
          fontSize: 12,
          padding: 10,
          textAlign: 'left',
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          cursor: isFile ? 'pointer' : 'default',
        },
        draggable: true,
      }
    })

    const rfEdges: Edge[] = (data.edges || []).map((e, i) => ({
      id: String(e.id ?? i),
      source: String(e.fromNode),
      target: String(e.toNode),
      label: e.label,
      animated: true,
      labelStyle: { fill: 'var(--obs-text-muted)', fontSize: 11 },
      labelBgStyle: { fill: '#16161e' },
      style: { stroke: normColor(e.color) || '#565f89' },
      markerEnd: { type: MarkerType.ArrowClosed, color: normColor(e.color) || '#565f89' },
    }))

    return { nodes: rfNodes, edges: rfEdges }
  }, [canvas])

  const onNodeClick = (_: any, node: Node) => {
    if (node.data?.kind === 'file' && node.data?.file) {
      const file = String(node.data.file)
      if (file.endsWith('.canvas')) { setPath(file); return }
      services.events.emit('vault:open', { path: file })
      onNavigate?.(VIEW_VAULT_EXPLORER)
    }
  }

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', background: 'var(--obs-bg)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', borderBottom: '1px solid var(--obs-border)' }}>
        <Workflow size={16} style={{ color: 'var(--obs-accent)' }} />
        <span style={{ fontWeight: 600, fontSize: 14, color: 'var(--obs-text)' }}>{canvas?.name || 'Canvas'}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--obs-text-faint)' }}>
          <FileText size={12} /> {path}
        </span>
      </div>
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {loading && <div style={centerBox}><Loader2 size={18} className="animate-spin" /> 加载 Canvas…</div>}
        {error && <div style={{ ...centerBox, color: 'var(--obs-red)' }}>{error}（需重启后端加载 /api/vault）</div>}
        {!loading && !error && (
          <ReactFlow nodes={nodes} edges={edges} onNodeClick={onNodeClick} fitView minZoom={0.1} proOptions={{ hideAttribution: true }}>
            <Background color="#2a2e3f" gap={24} />
            <Controls />
          </ReactFlow>
        )}
      </div>
    </div>
  )
}

const centerBox: React.CSSProperties = { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--obs-text-muted)' }
