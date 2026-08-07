/**
 * NexusBacktestLoop — 回测驱动优化闭环
 *
 * 展示"回测→血缘账本→参数优化→RL replay"闭环：回测事件流 + 指标 + replay 注入量。
 */

import { useCallback, useEffect, useState } from 'react'
import { SectionCard, StatCard, RefreshButton, EmptyState, InfoBanner } from '../IlcUi'
import { getRecentEvents, type EvolutionEnvelope } from '@/lib/learningCoreApi'
import { apiRequest } from '@/lib/api'

export function NexusBacktestLoop() {
  const [events, setEvents] = useState<EvolutionEnvelope[]>([])
  const [replay, setReplay] = useState<{ total?: number; avg_reward?: number; by_source?: Record<string, number> }>({})
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [evolve, rl] = await Promise.all([
        getRecentEvents(50, 'evolve'),
        apiRequest('/learning/replay/stats').then((r) => r.json()).catch(() => ({})),
      ])
      setEvents(evolve)
      setReplay(rl)
    } catch { /* ignore */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t) }, [load])

  return (
    <div className="space-y-4">
      <InfoBanner title="闭环说明">
        回测结果统一进血缘账本（evolve 阶段）→ 达标结果经 Envelope 驱动参数优化 → 逐笔折算为 RL 转移样本写入 replay buffer，
        形成"回测→优化→RL"闭环。信号回测已并入统一账本。
      </InfoBanner>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="回测事件" value={events.length} hint="evolve 阶段" />
        <StatCard label="replay 样本" value={replay?.total ?? 0} />
        <StatCard label="平均奖励" value={(replay?.avg_reward ?? 0).toFixed(5)} tone={(replay?.avg_reward ?? 0) >= 0 ? 'good' : 'bad'} />
        <StatCard label="回测来源" value={Object.keys(replay?.by_source || {}).length} hint="数据源种类" />
      </div>

      <SectionCard title="回测事件流" description="来自策略进化 / 信号回测的统一结果"
        action={<RefreshButton onClick={load} loading={loading} />}>
        {events.length === 0 ? (
          <EmptyState message="暂无回测事件（触发回测后出现）" />
        ) : (
          <div className="rounded-md border overflow-hidden">
            <div className="grid grid-cols-12 gap-2 text-xs font-medium text-muted-foreground px-3 py-2 bg-muted/50 border-b">
              <div className="col-span-3">来源</div>
              <div className="col-span-2">标的</div>
              <div className="col-span-2">Sharpe</div>
              <div className="col-span-2">胜率</div>
              <div className="col-span-1">状态</div>
              <div className="col-span-2">时间</div>
            </div>
            <div className="max-h-[360px] overflow-y-auto">
              {events.map((e) => (
                <div key={e.envelope_id} className="grid grid-cols-12 gap-2 items-center px-3 py-2 border-b last:border-0 text-xs">
                  <div className="col-span-3 font-mono">{e.source}</div>
                  <div className="col-span-2">{e.symbol || '—'}</div>
                  <div className="col-span-2">{fmt(e.metrics?.sharpe ?? e.metrics?.sharpe_ratio)}</div>
                  <div className="col-span-2">{fmtPct(e.metrics?.win_rate)}</div>
                  <div className="col-span-1">{e.status === 'passed' ? '✅' : '⚠️'}</div>
                  <div className="col-span-2 text-muted-foreground">{new Date(e.created_at).toLocaleTimeString()}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </SectionCard>
    </div>
  )
}

function fmt(v: unknown): string {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '—'
}
function fmtPct(v: unknown): string {
  const n = Number(v)
  return Number.isFinite(n) ? `${(n * 100).toFixed(0)}%` : '—'
}

export default NexusBacktestLoop
