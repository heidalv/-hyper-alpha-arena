/**
 * MarkdownNote —— Obsidian 风格 markdown 渲染
 * - [[wikilink]] → 可点击（解析到真实笔记 / canvas）
 * - ```dataview 代码块 → 真实表格（DataviewBlock）
 * - frontmatter 折叠展示
 * - 代码高亮 + GFM 表格
 */

import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { ChevronDown, ChevronRight } from 'lucide-react'

import type { VaultNote } from '../lib/vaultApi'
import { services } from '../core/ServiceContainer'
import DataviewBlock from './dataview/DataviewBlock'
import { VIEW_VAULT_CANVAS, VIEW_VAULT_EXPLORER } from '../plugins'

interface Props {
  body: string
  frontmatter?: Record<string, any>
  /** 全库索引，用于 dataview 求值 + wikilink 解析 */
  notes?: VaultNote[]
  onNavigate?: (page: string) => void
}

/** 把 [[target|alias]] 预处理成 markdown 链接 [alias](wikilink:target) */
function preprocessWikilinks(md: string): string {
  return md.replace(/\[\[([^\]]+)\]\]/g, (_m, inner: string) => {
    const [rawTarget, alias] = inner.split('|')
    const target = rawTarget.split('#')[0].trim()
    const label = (alias || rawTarget).trim()
    return `[${label}](wikilink:${encodeURIComponent(target)})`
  })
}

export default function MarkdownNote({ body, frontmatter, notes, onNavigate }: Props) {
  const [fmOpen, setFmOpen] = useState(false)
  const processed = useMemo(() => preprocessWikilinks(body || ''), [body])

  const nameToPath = useMemo(() => {
    const m = new Map<string, string>()
    ;(notes || []).forEach((n) => { if (!m.has(n.name)) m.set(n.name, n.path) })
    return m
  }, [notes])

  const openWikilink = (target: string) => {
    if (target.endsWith('.canvas')) {
      services.events.emit('vault:open', { path: target })
      onNavigate?.(VIEW_VAULT_CANVAS)
      return
    }
    const stem = target.replace(/\.md$/, '').split('/').pop() || target
    const path = nameToPath.get(stem) || (target.endsWith('.md') ? target : undefined)
    if (path) {
      services.events.emit('vault:open', { path })
      onNavigate?.(VIEW_VAULT_EXPLORER)
    }
  }

  const fmEntries = frontmatter ? Object.entries(frontmatter) : []

  return (
    <div>
      {fmEntries.length > 0 && (
        <div style={{ border: '1px solid var(--obs-border)', borderRadius: 8, marginBottom: 14, overflow: 'hidden' }}>
          <div
            onClick={() => setFmOpen((o) => !o)}
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', cursor: 'pointer', fontSize: 12, color: 'var(--obs-text-muted)', background: 'var(--obs-panel)' }}
          >
            {fmOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            属性 (frontmatter) · {fmEntries.length}
          </div>
          {fmOpen && (
            <div style={{ padding: '8px 12px', display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 16px', fontSize: 12.5 }}>
              {fmEntries.map(([k, v]) => (
                <div key={k} style={{ display: 'contents' }}>
                  <span style={{ color: 'var(--obs-purple)' }}>{k}</span>
                  <span style={{ color: 'var(--obs-text)' }}>{Array.isArray(v) ? v.join(', ') : String(v)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="obs-markdown prose prose-invert max-w-none" style={{ fontSize: 14 }}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            // 拆掉 pre 包裹，避免自定义块级渲染(表格/高亮)被塞进 <pre> 造成非法嵌套
            pre({ children }) {
              return <>{children}</>
            },
            a({ href, children }) {
              if (href && href.startsWith('wikilink:')) {
                const target = decodeURIComponent(href.slice('wikilink:'.length))
                const stem = target.replace(/\.md$/, '').split('/').pop() || target
                const resolved = target.endsWith('.canvas') || nameToPath.has(stem) || target.endsWith('.md')
                return (
                  <span
                    className={`obs-wikilink${resolved ? '' : ' unresolved'}`}
                    onClick={() => openWikilink(target)}
                  >
                    {children}
                  </span>
                )
              }
              return <a href={href} target="_blank" rel="noreferrer">{children}</a>
            },
            code({ node: _node, className, children, ...props }: any) {
              const lang = /language-(\w+)/.exec(className || '')?.[1]
              if (lang === 'dataview') {
                return <DataviewBlock source={String(children)} notes={notes || []} onNavigate={onNavigate} />
              }
              if (!lang) {
                return (
                  <code style={{ background: 'var(--obs-panel)', padding: '1px 5px', borderRadius: 4, fontSize: 12.5 }} {...props}>
                    {children}
                  </code>
                )
              }
              return (
                <SyntaxHighlighter language={lang} style={vscDarkPlus as any} customStyle={{ background: 'var(--obs-panel)', borderRadius: 8, fontSize: 12.5 }}>
                  {String(children).replace(/\n$/, '')}
                </SyntaxHighlighter>
              )
            },
          }}
        >
          {processed}
        </ReactMarkdown>
      </div>
    </div>
  )
}
