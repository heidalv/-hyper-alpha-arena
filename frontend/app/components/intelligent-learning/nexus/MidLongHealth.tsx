/**
 * MidLongHealth — 中长线健康视图（阶段三 C1）
 *
 * 展示"中长线激活改造是否见效"：
 * - 每 tier（mid 中线 / long 长线）滚动胜率 / 净期望 / 笔数 / 日均开仓；
 * - 开仓活跃度（判断是否还在停摆）；
 * - 长线周开单 vs 上限（是否触顶）；
 * - 各层预算利用率（预算是否被真正使用）；
 * - 当前生效的开仓门槛与激活开关状态。
 *
 * 数据源：GET /api/factors/midlong-health
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { SectionCard, StatCard, RefreshButton, EmptyState, InfoBanner } from '../IlcUi'
import { apiRequest } from '@/lib/api'

interface FactorJob {
  job_id?: string
  kind?: string
  status?: string // pending | running | done | error
  progress?: number
  total?: number
  percent?: number | null
  message?: string
  result?: { scored?: number; promoted?: number; registered?: number; skipped?: number } | null
  error?: string | null
}

interface TierStat {
  trade_count?: number
  win_rate?: number | null
  avg_return_pct?: number | null
  net_expectancy_pct?: number | null
  round_trip_cost_pct?: number | null
  opens_per_day?: number
}

interface BudgetLayer {
  alloc?: number
  cap?: number
  used?: number
  utilization?: number
  idle_pct?: number
}

interface CalibStat {
  calibrated?: boolean
  n_samples?: number
  base_rate?: number
  curve?: { score: number; p_win: number }[]
}

interface EvNatureStat {
  pass_count?: number
  block_count?: number
  total?: number
  pass_rate?: number | null
}

interface MtfTierStat {
  total?: number
  veto?: number
  downsize?: number
  veto_rate?: number | null
  downsize_rate?: number | null
}

interface SignalQuality {
  calibration?: { swing?: CalibStat; trend?: CalibStat; error?: string }
  ev_gate?: Record<string, EvNatureStat> & { error?: string }
  mtf_constraint?: Record<string, MtfTierStat> & { error?: string }
  flags?: Record<string, boolean>
}

interface FactorSet {
  active?: number
  candidate?: number
  rejected?: number
  avg_active_ic?: number | null
  by_timeframe?: { '4h'?: number; '1d'?: number }
  top_active?: { factor_id: string; grade?: string; timeframe?: string; ic_mean?: number | null; runtime_weight?: number }[]
  error?: string
}

interface MidLongHealth {
  lookback_days?: number
  account_id?: number | null
  equity?: number
  tiers?: { mid?: TierStat; long?: TierStat }
  open_positions?: { mid?: number; long?: number; short?: number }
  long_weekly?: { opens_7d?: number; cap?: number; at_cap?: boolean; error?: string }
  budget?: Record<string, BudgetLayer> & { error?: string }
  gates?: {
    swing?: { min_confidence?: number; min_risk_reward?: number } | null
    trend_follow?: { min_score?: number; min_risk_reward?: number } | null
    global_min_risk_reward?: number
    error?: string
  }
  activation?: { enabled?: boolean; scan_batch?: number; active_exit?: boolean }
  signal_quality?: SignalQuality
  factor_set?: FactorSet
  error?: string
}

const pct = (v?: number | null, digits = 1) => (v == null ? '—' : `${(v * 100).toFixed(digits)}%`)

function TierBlock({ title, stat, openCount }: { title: string; stat?: TierStat; openCount?: number }) {
  const wr = stat?.win_rate ?? null
  const gross = stat?.avg_return_pct ?? null
  const net = stat?.net_expectancy_pct ?? null
  const cnt = stat?.trade_count ?? 0
  const opd = stat?.opens_per_day ?? 0
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <StatCard label={`${title}·胜率`} value={pct(wr)} tone={wr == null ? 'default' : wr >= 0.45 ? 'good' : 'warn'} />
      <StatCard
        label={`${title}·净扣费期望/笔`}
        value={net == null ? '—' : `${(net * 100).toFixed(3)}%`}
        tone={net == null ? 'default' : net > 0 ? 'good' : 'bad'}
        hint={gross == null ? '' : `毛 ${(gross * 100).toFixed(3)}%`}
      />
      <StatCard label={`${title}·成交笔数`} value={cnt} hint={`当前持仓 ${openCount ?? 0}`} />
      <StatCard
        label={`${title}·日均开仓`}
        value={opd}
        tone={opd <= 0 ? 'bad' : opd < 0.5 ? 'warn' : 'good'}
        hint={opd <= 0 ? '仍停摆' : ''}
      />
    </div>
  )
}

export function MidLongHealth() {
  const [data, setData] = useState<MidLongHealth>({})
  const [loading, setLoading] = useState(false)
  const [lookback, setLookback] = useState(14)

  const [job, setJob] = useState<FactorJob | null>(null)
  const [jobBusy, setJobBusy] = useState(false)
  const pollRef = useRef<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await apiRequest(`/factors/midlong-health?lookback_days=${lookback}`)
      setData(await r.json())
    } catch {
      /* ignore */
    } finally {
      setLoading(false)
    }
  }, [lookback])

  useEffect(() => {
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [load])

  // 组件卸载时停止轮询
  useEffect(() => () => {
    if (pollRef.current) window.clearTimeout(pollRef.current)
  }, [])

  const pollJob = useCallback(
    async (id: string) => {
      try {
        const r = await apiRequest(`/factors/jobs/${id}`)
        const j: FactorJob = await r.json()
        setJob(j)
        if (j.status === 'done' || j.status === 'error') {
          setJobBusy(false)
          load() // 验证完成后刷新活跃因子集
          return
        }
      } catch {
        setJobBusy(false)
        return
      }
      pollRef.current = window.setTimeout(() => pollJob(id), 1500)
    },
    [load],
  )

  // 一键灌库：Alpha101 公式因子登记为中长线候选（幂等）
  const runSeed = useCallback(async () => {
    setJobBusy(true)
    setJob({ status: 'running', kind: 'seed', message: 'Alpha101 灌库中…' })
    try {
      const r = await apiRequest('/factors/alpha101/seed', { method: 'POST' })
      const s = await r.json()
      setJob({
        status: 'done',
        kind: 'seed',
        message: `灌库完成：登记 ${s.registered ?? 0} / 跳过 ${s.skipped ?? 0}`,
        result: s,
      })
    } catch (e) {
      setJob({ status: 'error', kind: 'seed', error: String(e) })
    } finally {
      setJobBusy(false)
      load()
    }
  }, [load])

  // 一键验证：后台异步样本外打分晋升，轮询进度
  const runValidate = useCallback(async () => {
    setJobBusy(true)
    setJob({ status: 'running', kind: 'alpha101_validate', progress: 0, total: 0, message: '提交中…' })
    try {
      const r = await apiRequest('/factors/alpha101/validate?limit=50', { method: 'POST' })
      const j: FactorJob = await r.json()
      setJob(j)
      if (j.job_id && j.status !== 'done' && j.status !== 'error') {
        pollRef.current = window.setTimeout(() => pollJob(j.job_id as string), 1200)
      } else {
        setJobBusy(false)
        load()
      }
    } catch (e) {
      setJob({ status: 'error', kind: 'alpha101_validate', error: String(e) })
      setJobBusy(false)
    }
  }, [load, pollJob])

  const act = data.activation || {}
  const lw = data.long_weekly || {}
  const gates = data.gates || {}
  const budget = data.budget || {}
  const budgetLayers = Object.entries(budget).filter(([k]) => ['scalp', 'swing', 'trend'].includes(k)) as [string, BudgetLayer][]
  const sq = data.signal_quality || {}
  const cal = sq.calibration || {}
  const evGate = sq.ev_gate || {}
  const mtf = sq.mtf_constraint || {}
  const flags = sq.flags || {}
  const fs = data.factor_set || {}
  const evNatures = Object.entries(evGate).filter(([k]) => k !== 'error') as [string, EvNatureStat][]
  const mtfTiers = Object.entries(mtf).filter(([k]) => k !== 'error') as [string, MtfTierStat][]
  const flagLabels: Record<string, string> = {
    calibrator: '置信度校准',
    ev_gate: 'EV闸门',
    mtf_enforce: 'MTF约束',
    quant_brief: '量化简报进脑',
    paper_probe_strict: '模拟盘严格',
    factor_research: '因子科研',
  }
  const calBadge = (c?: CalibStat) =>
    c?.calibrated ? `已校准 n=${c.n_samples ?? 0} base=${pct(c.base_rate, 0)}` : `冷启动 n=${c?.n_samples ?? 0}（线性映射）`

  return (
    <div className="space-y-4">
      <InfoBanner title="中长线健康">
        判断中长线"激活"改造是否见效：日均开仓是否&gt;0（脱离停摆）、门槛是否已校准、预算是否被真正使用、
        长线周开单是否触顶。中长线主体应"少而稳"，胜率目标 ≥45%、净期望转正。
      </InfoBanner>

      {/* 激活状态条 */}
      <SectionCard
        title="激活状态"
        action={
          <div className="flex items-center gap-2">
            <div className="flex items-center rounded-md border overflow-hidden text-xs">
              {[7, 14, 30].map((d) => (
                <button
                  key={d}
                  onClick={() => setLookback(d)}
                  className={'px-2 py-1 ' + (lookback === d ? 'bg-primary text-primary-foreground font-semibold' : 'hover:bg-muted')}
                >
                  {d}天
                </button>
              ))}
            </div>
            <RefreshButton onClick={load} loading={loading} />
          </div>
        }
      >
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className={'rounded px-2 py-1 font-semibold ' + (act.enabled ? 'bg-green-500/15 text-green-600 dark:text-green-400' : 'bg-muted text-muted-foreground')}>
            总开关 {act.enabled ? 'ON' : 'OFF'}
          </span>
          <span className="rounded px-2 py-1 bg-muted">扫描批量 {act.scan_batch ?? '—'} 币/tick</span>
          <span className={'rounded px-2 py-1 ' + (act.active_exit ? 'bg-sky-500/15 text-sky-600 dark:text-sky-400' : 'bg-muted')}>
            主动退出 {act.active_exit ? 'ON' : 'OFF'}
          </span>
          <span className="rounded px-2 py-1 bg-muted">权益 {data.equity != null ? data.equity.toLocaleString() : '—'}</span>
        </div>
      </SectionCard>

      {/* 中线 */}
      <SectionCard title="中线 (mid / swing)">
        <TierBlock title="中线" stat={data.tiers?.mid} openCount={data.open_positions?.mid} />
      </SectionCard>

      {/* 长线 */}
      <SectionCard title="长线 (long / trend_follow)">
        <TierBlock title="长线" stat={data.tiers?.long} openCount={data.open_positions?.long} />
        <div className="mt-3 flex items-center gap-3 text-xs">
          {lw.error ? (
            <span className="text-muted-foreground">周开单：{lw.error}</span>
          ) : (
            <span
              className={
                'rounded px-2 py-1 font-medium ' +
                (lw.at_cap ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400' : 'bg-muted')
              }
            >
              本周长线开单 {lw.opens_7d ?? 0} / 上限 {lw.cap ?? '—'}
              {lw.at_cap ? '（已触顶，本周不再开）' : ''}
            </span>
          )}
        </div>
      </SectionCard>

      {/* 门槛 */}
      <SectionCard title="当前开仓门槛（校准后）" description="来自 runtime_tuning，unified_gate 以 max() 合并生效">
        {gates.error ? (
          <EmptyState message={`读取失败：${gates.error}`} />
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
            <StatCard label="中线 置信度门槛" value={gates.swing?.min_confidence ?? '—'} hint={`RR ≥ ${gates.swing?.min_risk_reward ?? '—'}`} />
            <StatCard label="长线 评分门槛" value={gates.trend_follow?.min_score ?? '—'} hint={`RR ≥ ${gates.trend_follow?.min_risk_reward ?? '—'}`} />
            <StatCard label="全局最小盈亏比" value={gates.global_min_risk_reward ?? '—'} />
          </div>
        )}
      </SectionCard>

      {/* 预算利用率 */}
      <SectionCard title="预算利用率（各层）" description="中长线激活后 swing/trend 层利用率长期≈0 说明开仓侧仍被卡">
        {budget.error ? (
          <EmptyState message={`读取失败：${budget.error}`} />
        ) : budgetLayers.length === 0 ? (
          <EmptyState message="暂无预算数据（需有运行中的模拟会话）" />
        ) : (
          <div className="rounded-md border overflow-hidden">
            <div className="grid grid-cols-12 gap-2 text-xs font-medium text-muted-foreground px-3 py-2 bg-muted/50 border-b">
              <div className="col-span-3">层</div>
              <div className="col-span-2 text-right">分配</div>
              <div className="col-span-3 text-right">额度/已用</div>
              <div className="col-span-4 text-right">利用率</div>
            </div>
            {budgetLayers.map(([layer, b]) => {
              const util = b.utilization ?? 0
              const label = layer === 'scalp' ? 'scalp 短线' : layer === 'swing' ? 'swing 中线' : 'trend 长线'
              return (
                <div key={layer} className="grid grid-cols-12 gap-2 text-xs px-3 py-2 border-b last:border-0">
                  <div className="col-span-3">{label}</div>
                  <div className="col-span-2 text-right tabular-nums">{pct(b.alloc, 0)}</div>
                  <div className="col-span-3 text-right tabular-nums">
                    {(b.cap ?? 0).toLocaleString()} / {(b.used ?? 0).toLocaleString()}
                  </div>
                  <div className="col-span-4 text-right">
                    <div className="flex items-center gap-2 justify-end">
                      <div className="w-24 h-1.5 rounded bg-muted overflow-hidden">
                        <div
                          className={'h-full ' + (util >= 0.9 ? 'bg-red-500' : util >= 0.5 ? 'bg-amber-500' : 'bg-sky-500')}
                          style={{ width: `${Math.min(100, util * 100)}%` }}
                        />
                      </div>
                      <span className="tabular-nums w-12 text-right">{pct(util)}</span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </SectionCard>

      {/* 信号质量（S3 汇合：校准 / EV / MTF） */}
      <SectionCard title="信号质量闸门" description="少而准：校准胜率 → EV 期望为正 → 多周期不逆向，三道闸叠加">
        {/* 开关灯条 */}
        <div className="flex flex-wrap items-center gap-2 text-xs mb-3">
          {Object.entries(flagLabels).map(([k, label]) => (
            <span
              key={k}
              className={
                'rounded px-2 py-1 font-medium ' +
                (flags[k] ? 'bg-green-500/15 text-green-600 dark:text-green-400' : 'bg-muted text-muted-foreground')
              }
            >
              {label} {flags[k] ? 'ON' : 'OFF'}
            </span>
          ))}
        </div>

        {/* 校准器 */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
          <StatCard label="中线校准器 (swing)" value={cal.swing?.calibrated ? '已生效' : '冷启动'} hint={calBadge(cal.swing)} tone={cal.swing?.calibrated ? 'good' : 'default'} />
          <StatCard label="长线校准器 (trend)" value={cal.trend?.calibrated ? '已生效' : '冷启动'} hint={calBadge(cal.trend)} tone={cal.trend?.calibrated ? 'good' : 'default'} />
        </div>

        {/* EV 闸门放行率 */}
        {evNatures.length > 0 && (
          <div className="mb-3">
            <div className="text-xs text-muted-foreground mb-1">EV 闸门放行率（按 nature）</div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              {evNatures.map(([nat, s]) => (
                <StatCard
                  key={nat}
                  label={`EV·${nat}`}
                  value={pct(s.pass_rate)}
                  hint={`放行 ${s.pass_count ?? 0} / 拦截 ${s.block_count ?? 0}`}
                  tone={s.pass_rate == null ? 'default' : s.pass_rate >= 0.3 ? 'good' : 'warn'}
                />
              ))}
            </div>
          </div>
        )}

        {/* MTF 否决率 */}
        {mtfTiers.length > 0 && (
          <div>
            <div className="text-xs text-muted-foreground mb-1">MTF 约束（逆高周期）否决/缩仓率</div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              {mtfTiers.map(([tier, s]) => (
                <StatCard
                  key={tier}
                  label={`MTF·${tier}`}
                  value={pct(s.veto_rate)}
                  hint={`否决 ${s.veto ?? 0} / 缩仓 ${s.downsize ?? 0} / 共 ${s.total ?? 0}`}
                  tone="default"
                />
              ))}
            </div>
          </div>
        )}
        {evNatures.length === 0 && mtfTiers.length === 0 && !cal.swing && (
          <EmptyState message="暂无信号质量样本（需中长线开始产生开仓/拦截记录）" />
        )}
      </SectionCard>

      {/* 中长线活跃因子集 */}
      <SectionCard
        title="中长线活跃因子集"
        description="Alpha101 + AI 挖掘 → 4h/1d 样本外打分 → A/B 级晋升；IC 衰减自动退役"
        action={
          <div className="flex items-center gap-2">
            <button
              onClick={runSeed}
              disabled={jobBusy}
              className="rounded-md border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
              title="把 Alpha101 公式因子登记为中长线候选（幂等，已存在则跳过）"
            >
              灌库
            </button>
            <button
              onClick={runValidate}
              disabled={jobBusy}
              className="rounded-md border px-2 py-1 text-xs font-semibold bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
              title="后台异步：对候选因子逐个样本外打分，A/B 级晋升为活跃"
            >
              {jobBusy ? '验证中…' : '一键验证'}
            </button>
          </div>
        }
      >
        {/* 后台任务进度 */}
        {job && (
          <div className="mb-3 rounded-md border p-2 text-xs">
            <div className="flex items-center justify-between mb-1">
              <span className="font-medium">
                {job.kind === 'seed' ? '灌库' : '样本外验证'} ·{' '}
                {job.status === 'running'
                  ? '进行中'
                  : job.status === 'done'
                    ? '已完成'
                    : job.status === 'error'
                      ? '失败'
                      : job.status}
                {job.total ? ` (${job.progress ?? 0}/${job.total})` : ''}
              </span>
              <span className="text-muted-foreground truncate max-w-[60%] text-right">{job.message}</span>
            </div>
            {job.status === 'running' && (
              <div className="w-full h-1.5 rounded bg-muted overflow-hidden">
                <div
                  className={'h-full bg-sky-500 transition-all ' + (job.total ? '' : 'animate-pulse w-1/3')}
                  style={job.total ? { width: `${job.percent ?? 0}%` } : undefined}
                />
              </div>
            )}
            {job.status === 'done' && job.result && (job.result.scored != null || job.result.registered != null) && (
              <div className="mt-1 text-muted-foreground">
                {job.result.registered != null
                  ? `登记 ${job.result.registered} / 跳过 ${job.result.skipped ?? 0}`
                  : `打分 ${job.result.scored ?? 0} · 晋升 ${job.result.promoted ?? 0}`}
              </div>
            )}
            {job.status === 'error' && <div className="mt-1 text-red-500">失败：{job.error}</div>}
          </div>
        )}
        {fs.error ? (
          <EmptyState message={`读取失败：${fs.error}`} />
        ) : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
              <StatCard label="活跃因子" value={fs.active ?? 0} hint={`4h ${fs.by_timeframe?.['4h'] ?? 0} / 1d ${fs.by_timeframe?.['1d'] ?? 0}`} tone={(fs.active ?? 0) > 0 ? 'good' : 'default'} />
              <StatCard label="候选待验证" value={fs.candidate ?? 0} />
              <StatCard label="已淘汰" value={fs.rejected ?? 0} />
              <StatCard label="平均 IC" value={fs.avg_active_ic == null ? '—' : fs.avg_active_ic.toFixed(4)} tone={fs.avg_active_ic == null ? 'default' : Math.abs(fs.avg_active_ic) >= 0.03 ? 'good' : 'warn'} />
            </div>
            {fs.top_active && fs.top_active.length > 0 ? (
              <div className="rounded-md border overflow-hidden">
                <div className="grid grid-cols-12 gap-2 text-xs font-medium text-muted-foreground px-3 py-2 bg-muted/50 border-b">
                  <div className="col-span-5">因子</div>
                  <div className="col-span-2">周期</div>
                  <div className="col-span-2 text-center">级别</div>
                  <div className="col-span-1 text-right">IC</div>
                  <div className="col-span-2 text-right">权重</div>
                </div>
                {fs.top_active.map((f) => (
                  <div key={f.factor_id} className="grid grid-cols-12 gap-2 text-xs px-3 py-2 border-b last:border-0">
                    <div className="col-span-5 truncate font-mono">{f.factor_id}</div>
                    <div className="col-span-2">{f.timeframe ?? '—'}</div>
                    <div className="col-span-2 text-center">{f.grade ?? '—'}</div>
                    <div className="col-span-1 text-right tabular-nums">{f.ic_mean == null ? '—' : f.ic_mean.toFixed(3)}</div>
                    <div className="col-span-2 text-right tabular-nums">{(f.runtime_weight ?? 1).toFixed(2)}</div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState message="暂无活跃中长线因子（Alpha101 灌库后由调度器在 4h/1d 样本外打分晋升）" />
            )}
          </>
        )}
      </SectionCard>

      {data.error && <p className="text-xs text-red-500">加载异常：{data.error}</p>}
    </div>
  )
}
