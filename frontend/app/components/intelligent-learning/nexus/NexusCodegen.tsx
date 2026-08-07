/**
 * NexusCodegen — opencode 治理 codegen（受控管道）
 *
 * 生成的因子/策略 .py 仅落隔离沙箱，审批也不自动合并；展示提案列表 + 安全体检 + 审批。
 */

import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { SectionCard, RefreshButton, InfoBanner, EmptyState } from '../IlcUi'
import { apiRequest } from '@/lib/api'

interface Proposal {
  proposal_id: string; name: string; kind: string; status: string
  sandbox_path?: string; safety?: { clean?: boolean; banned_hits?: string[]; syntax_ok?: boolean }
  created_at?: string; code?: string
}

const STATUS_TONE: Record<string, string> = {
  pending_review: 'bg-yellow-500/20 text-yellow-300',
  approved: 'bg-green-500/20 text-green-300',
  rejected: 'bg-red-500/20 text-red-300',
}

export function NexusCodegen() {
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [loading, setLoading] = useState(false)
  const [name, setName] = useState('')
  const [spec, setSpec] = useState('')
  const [kind, setKind] = useState('factor')
  const [busy, setBusy] = useState(false)
  const [detail, setDetail] = useState<Proposal | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiRequest('/learning/codegen/proposals')
      const d = await res.json()
      setProposals(d.proposals || [])
    } catch { /* ignore */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const propose = async () => {
    if (!name.trim() || !spec.trim()) { alert('请填写名称与需求'); return }
    setBusy(true)
    try {
      const res = await apiRequest('/learning/codegen/propose', {
        method: 'POST', body: JSON.stringify({ name, spec, kind }),
      })
      const r = await res.json()
      if (!r.ok) alert(`生成失败：${r.error}`)
      else { setName(''); setSpec(''); load() }
    } catch (e: any) { alert(e.message || '生成失败') } finally { setBusy(false) }
  }

  const act = async (id: string, action: 'approve' | 'reject') => {
    if (!confirm(action === 'approve' ? '审批通过？（仅标记可合入，不自动合并）' : '拒绝该提案？')) return
    try {
      await apiRequest(`/learning/codegen/proposals/${id}/${action}`, { method: 'POST' })
      load()
    } catch (e: any) { alert(e.message || '操作失败') }
  }

  const openDetail = async (id: string) => {
    try {
      const res = await apiRequest(`/learning/codegen/proposals/${id}`)
      setDetail(await res.json())
    } catch { /* ignore */ }
  }

  return (
    <div className="space-y-4">
      <InfoBanner title="受控管道" variant="warn">
        codegen 生成的 <code>.py</code> 仅写入隔离沙箱 <code>data/codegen_shadow/</code>，
        <strong>不会被自动导入或执行</strong>；审批通过也仅标记可合入，须人工在隔离 worktree + paper 验证后合并。
        需先开启 <code>OPENCODE_CODEGEN_ENABLED</code>。
      </InfoBanner>

      <SectionCard title="新建 codegen 提案" description="用 opencode LLM 生成因子/策略代码">
        <div className="space-y-2">
          <div className="flex gap-2">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="名称，如 MyMomentumFactor"
              className="flex-1 rounded-md border bg-transparent px-2 py-1 text-sm" />
            <select value={kind} onChange={(e) => setKind(e.target.value)}
              className="rounded-md border bg-transparent px-2 py-1 text-sm">
              <option value="factor">因子</option>
              <option value="strategy">策略</option>
            </select>
          </div>
          <textarea value={spec} onChange={(e) => setSpec(e.target.value)} rows={3}
            placeholder="用自然语言描述需求，例如：基于 RSI 与成交量背离的多空因子…"
            className="w-full rounded-md border bg-transparent px-2 py-1 text-sm" />
          <Button size="sm" onClick={propose} disabled={busy}>{busy ? '生成中…' : '生成提案'}</Button>
        </div>
      </SectionCard>

      <SectionCard title="提案列表" description="待审 / 已批 / 已拒"
        action={<RefreshButton onClick={load} loading={loading} />}>
        {proposals.length === 0 ? (
          <EmptyState message="暂无 codegen 提案" />
        ) : (
          <div className="space-y-2 max-h-[420px] overflow-y-auto">
            {proposals.map((p) => (
              <div key={p.proposal_id} className="rounded-md border p-3 text-sm">
                <div className="flex items-center gap-2 mb-1">
                  <Badge variant="outline">{p.kind}</Badge>
                  <button className="font-medium hover:underline" onClick={() => openDetail(p.proposal_id)}>{p.name}</button>
                  {p.safety && (
                    <Badge variant={p.safety.clean ? 'secondary' : 'destructive'}>
                      {p.safety.clean ? '安全体检通过' : '需人工复核'}
                    </Badge>
                  )}
                  <span className={`ml-auto text-xs px-1.5 py-0.5 rounded ${STATUS_TONE[p.status] || ''}`}>{p.status}</span>
                </div>
                {p.safety?.banned_hits && p.safety.banned_hits.length > 0 && (
                  <div className="text-xs text-red-400">命中危险调用：{p.safety.banned_hits.join(', ')}</div>
                )}
                <div className="text-xs text-muted-foreground font-mono truncate">{p.sandbox_path}</div>
                {p.status === 'pending_review' && (
                  <div className="flex gap-1 mt-1.5">
                    <button onClick={() => act(p.proposal_id, 'approve')}
                      className="px-2 py-0.5 text-xs bg-green-500/20 text-green-300 rounded hover:bg-green-500/30">审批通过</button>
                    <button onClick={() => act(p.proposal_id, 'reject')}
                      className="px-2 py-0.5 text-xs bg-red-500/20 text-red-300 rounded hover:bg-red-500/30">拒绝</button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      {detail && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div className="absolute inset-0 bg-black/40" onClick={() => setDetail(null)} />
          <div className="relative w-full max-w-2xl bg-[#1a1b26] shadow-xl overflow-y-auto p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold">{detail.name} · {detail.kind}</h3>
              <button onClick={() => setDetail(null)} className="text-muted-foreground text-lg">✕</button>
            </div>
            <pre className="text-xs bg-black/30 p-3 rounded border overflow-auto max-h-[70vh]">{detail.code || '（无代码）'}</pre>
          </div>
        </div>
      )}
    </div>
  )
}

export default NexusCodegen
