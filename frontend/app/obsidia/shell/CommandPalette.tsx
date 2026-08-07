/**
 * CommandPalette —— Ctrl/Cmd+P 命令面板
 * 功能：快速切换页面 + 快速打开 vault 笔记（懒加载索引）。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { CornerDownLeft, FileText, LayoutGrid } from 'lucide-react'
import { registry } from '../core/PluginRegistry'
import { services } from '../core/ServiceContainer'
import { VIEW_VAULT_EXPLORER } from '../plugins'

interface Props {
  open: boolean
  onClose: () => void
  onNavigate: (page: string) => void
}

interface Entry {
  kind: 'page' | 'note'
  id: string
  title: string
  hint: string
  run: () => void
}

/** 子序列模糊匹配打分：命中越靠前、越连续得分越高，未命中返回 -1 */
function fuzzyScore(text: string, q: string): number {
  if (!q) return 0
  const t = text.toLowerCase()
  const query = q.toLowerCase()
  if (t.includes(query)) return 100 - t.indexOf(query)
  let ti = 0
  let score = 0
  let streak = 0
  for (let qi = 0; qi < query.length; qi++) {
    const ch = query[qi]
    let found = -1
    for (let k = ti; k < t.length; k++) {
      if (t[k] === ch) { found = k; break }
    }
    if (found === -1) return -1
    streak = found === ti ? streak + 1 : 0
    score += 10 + streak * 2
    ti = found + 1
  }
  return score
}

export default function CommandPalette({ open, onClose, onNavigate }: Props) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [notes, setNotes] = useState<Entry[]>([])
  const inputRef = useRef<HTMLInputElement>(null)
  const notesLoaded = useRef(false)

  // 页面命令（来自注册表）
  const pageEntries = useMemo<Entry[]>(() => {
    return registry.getNavGroups().flatMap((g) =>
      g.items.map((it) => ({
        kind: 'page' as const,
        id: `page:${it.page}`,
        title: it.label,
        hint: g.label,
        run: () => onNavigate(it.page),
      })),
    )
  }, [onNavigate])

  // 打开时聚焦 + 懒加载 vault 索引
  useEffect(() => {
    if (!open) return
    setQuery('')
    setActive(0)
    setTimeout(() => inputRef.current?.focus(), 20)
    if (!notesLoaded.current) {
      notesLoaded.current = true
      services.vault
        .fetchVaultIndex()
        .then((idx) => {
          setNotes(
            idx.notes.map((n) => ({
              kind: 'note' as const,
              id: `note:${n.path}`,
              title: n.name,
              hint: n.folder || 'vault',
              run: () => {
                services.events.emit('vault:open', { path: n.path })
                onNavigate(VIEW_VAULT_EXPLORER)
              },
            })),
          )
        })
        .catch(() => { notesLoaded.current = false })
    }
  }, [open, onNavigate])

  const results = useMemo<Entry[]>(() => {
    const all = [...pageEntries, ...notes]
    if (!query.trim()) return pageEntries.slice(0, 40)
    return all
      .map((e) => ({ e, s: fuzzyScore(e.title, query) }))
      .filter((x) => x.s >= 0)
      .sort((a, b) => b.s - a.s)
      .slice(0, 60)
      .map((x) => x.e)
  }, [query, pageEntries, notes])

  useEffect(() => { setActive(0) }, [query])

  if (!open) return null

  const runEntry = (e?: Entry) => {
    if (!e) return
    e.run()
    onClose()
  }

  return (
    <div className="obs-cmd-overlay" onMouseDown={onClose}>
      <div className="obs-cmd-panel" onMouseDown={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="obs-cmd-input"
          value={query}
          placeholder="跳转页面或打开 vault 笔记…"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)) }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
            else if (e.key === 'Enter') { e.preventDefault(); runEntry(results[active]) }
            else if (e.key === 'Escape') { e.preventDefault(); onClose() }
          }}
        />
        <div className="obs-cmd-list">
          {results.length === 0 && (
            <div style={{ padding: 16, fontSize: 13, color: 'var(--obs-text-faint)' }}>无匹配结果</div>
          )}
          {results.map((e, i) => (
            <div
              key={e.id}
              className={`obs-cmd-item${i === active ? ' active' : ''}`}
              onMouseEnter={() => setActive(i)}
              onClick={() => runEntry(e)}
            >
              {e.kind === 'note' ? <FileText size={15} /> : <LayoutGrid size={15} />}
              <span>{e.title}</span>
              <span className="obs-cmd-hint">{e.hint}</span>
              {i === active && <CornerDownLeft size={13} style={{ marginLeft: 8, color: 'var(--obs-text-faint)' }} />}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
