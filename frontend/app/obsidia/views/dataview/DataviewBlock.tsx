/**
 * DataviewBlock —— 渲染一个 ```dataview 代码块为真实表格
 * 数据来自全库索引（由父组件通过 props 注入，避免每块都拉一次）。
 */

import { useMemo } from 'react'
import type { VaultNote } from '../../lib/vaultApi'
import { parseDql, runDql } from './dql'
import { services } from '../../core/ServiceContainer'
import { VIEW_VAULT_EXPLORER } from '../../plugins'

interface Props {
  source: string
  notes: VaultNote[]
  onNavigate?: (page: string) => void
}

function formatCell(v: any): string {
  if (v == null || v === '') return '—'
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'boolean') return v ? '✓' : '✗'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(4).replace(/\.?0+$/, '')
  return String(v)
}

export default function DataviewBlock({ source, notes, onNavigate }: Props) {
  const { query, rows } = useMemo(() => {
    const q = parseDql(source)
    return { query: q, rows: runDql(q, notes) }
  }, [source, notes])

  if (!query.valid) {
    return (
      <div style={{ padding: 10, border: '1px solid var(--obs-border)', borderRadius: 8, fontSize: 12, color: 'var(--obs-red)' }}>
        Dataview 解析失败：{query.error || '未知'}
      </div>
    )
  }

  const openNote = (n: VaultNote) => {
    services.events.emit('vault:open', { path: n.path })
    onNavigate?.(VIEW_VAULT_EXPLORER)
  }

  return (
    <div style={{ overflowX: 'auto', border: '1px solid var(--obs-border)', borderRadius: 8, margin: '10px 0' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
        <thead>
          <tr>
            {query.columns.map((c, i) => (
              <th
                key={i}
                style={{
                  textAlign: 'left',
                  padding: '7px 10px',
                  color: 'var(--obs-accent)',
                  borderBottom: '1px solid var(--obs-border-strong)',
                  whiteSpace: 'nowrap',
                  background: 'var(--obs-panel)',
                  position: 'sticky',
                  top: 0,
                }}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={query.columns.length} style={{ padding: 12, color: 'var(--obs-text-faint)' }}>
                无匹配笔记（可能数据尚未导出，或后端 /api/vault 未启用）
              </td>
            </tr>
          )}
          {rows.map((row, ri) => (
            <tr key={ri} style={{ borderBottom: '1px solid var(--obs-border)' }}>
              {row.cells.map((cell, ci) => {
                const isLink = ci === 0 || query.columns[ci].expr.startsWith('file.')
                return (
                  <td key={ci} style={{ padding: '6px 10px', color: 'var(--obs-text)', whiteSpace: 'nowrap' }}>
                    {isLink ? (
                      <span className="obs-wikilink" onClick={() => openNote(row.note)}>
                        {formatCell(cell) === '—' ? row.note.name : formatCell(cell)}
                      </span>
                    ) : (
                      formatCell(cell)
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
