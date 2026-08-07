/**
 * ArbitrageProgramsPanel — 积分项目生命周期面板（Phase 4）
 *
 * 展示各积分项目的状态（active/ended/staking_only/monitor_only）、费率、积分规则，
 * 并把 status != active 的"死项目"置灰。数据源为后端离线 program_registry
 * (/api/rebate/programs)，从根本上避免"主力还在刷已结束活动"的误导。
 */
import React, { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { CircleCheck, CircleSlash, Lock, Eye, Clock } from 'lucide-react'
import { getPointsPrograms, type PointsProgram } from '@/lib/arbitrageApi'

const STATUS_META: Record<string, { label: string; tone: string; icon: React.ReactNode }> = {
  active: { label: '进行中', tone: 'bg-green-500/15 text-green-700 dark:text-green-300 border-green-500/30', icon: <CircleCheck className="w-3.5 h-3.5" /> },
  ended: { label: '已结束', tone: 'bg-muted text-muted-foreground border-border', icon: <CircleSlash className="w-3.5 h-3.5" /> },
  staking_only: { label: '仅质押', tone: 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30', icon: <Lock className="w-3.5 h-3.5" /> },
  monitor_only: { label: '仅监控', tone: 'bg-blue-500/15 text-blue-700 dark:text-blue-300 border-blue-500/30', icon: <Eye className="w-3.5 h-3.5" /> },
  upcoming: { label: '即将开始', tone: 'bg-purple-500/15 text-purple-700 dark:text-purple-300 border-purple-500/30', icon: <Clock className="w-3.5 h-3.5" /> },
}

const pct = (v: number) => `${(Number(v) * 100).toFixed(3)}%`

export default function ArbitrageProgramsPanel() {
  const [programs, setPrograms] = useState<PointsProgram[]>([])
  const [activeCount, setActiveCount] = useState(0)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    getPointsPrograms()
      .then(res => {
        if (!alive) return
        setPrograms(res.programs || [])
        setActiveCount(res.active_count || 0)
      })
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [])

  return (
    <div className="rounded-xl border border-border bg-card p-4 mb-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold flex items-center gap-2">
          积分项目生命周期
          <span className="text-xs font-normal text-muted-foreground">
            {activeCount} 个进行中 / 共 {programs.length} 个（离线权威源）
          </span>
        </h3>
      </div>

      {loading ? (
        <div className="text-sm text-muted-foreground py-6 text-center">加载中…</div>
      ) : programs.length === 0 ? (
        <div className="text-sm text-muted-foreground py-6 text-center">暂无项目数据</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {programs.map(p => {
            const meta = STATUS_META[p.status] || STATUS_META.ended
            const dead = !p.is_active_now
            return (
              <div
                key={p.program_id}
                className={cn(
                  'rounded-lg border p-3 transition-opacity',
                  dead ? 'opacity-50 border-border bg-muted/30' : 'border-border bg-background',
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-medium truncate">{p.name}</div>
                    <div className="text-xs text-muted-foreground">{p.exchange}{p.strategy_id ? ` · ${p.strategy_id}` : ''}</div>
                  </div>
                  <span className={cn('shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px]', meta.tone)}>
                    {meta.icon}{meta.label}
                  </span>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                  <span>Maker {pct(p.maker_rate)}</span>
                  <span>Taker {pct(p.taker_rate)}</span>
                  {p.start_date && <span>起 {p.start_date}</span>}
                  {p.end_date && <span>止 {p.end_date}</span>}
                </div>
                {p.points_rule && (
                  <div className="mt-2 text-[11px] text-muted-foreground/90 line-clamp-2" title={p.points_rule}>
                    {p.points_rule}
                  </div>
                )}
                {dead && p.notes && (
                  <div className="mt-1 text-[11px] text-amber-600 dark:text-amber-400 line-clamp-2" title={p.notes}>
                    {p.notes}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
