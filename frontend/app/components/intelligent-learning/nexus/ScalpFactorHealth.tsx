/**
 * ScalpFactorHealth — 短线因子健康视图（阶段三 3.3 可观测性）
 *
 * 集中展示"短线转正改造是否见效"：
 * - 滚动胜率 / 净期望 / 笔数（scalp_health_report）；
 * - EV 闸门放行率、置信度校准器状态；
 * - 因子发现闸门通过率（active/(active+rejected)）；
 * - 短线活跃因子集 Top 因子及其运行时 IC 权重。
 *
 * 数据源：GET /api/factors/scalp-health
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
  result?: { scored?: number; promoted?: number } | null
  error?: string | null
}

interface TradeStats {
  trade_count?: number
  win_rate?: number | null
  avg_return_pct?: number | null
  avg_win_pct?: number | null
  avg_loss_pct?: number | null
  error?: string
}

interface EvGateStats {
  pass_count?: number
  block_count?: number
  total?: number
  pass_rate?: number | null
  last_reason?: string
  [k: string]: unknown
}

interface Acceptance {
  target_win_rate?: number
  win_rate_ok?: boolean
  expectancy_positive?: boolean
  passed?: boolean
}

interface TopFactor {
  factor_id: string
  grade?: string
  ic_mean?: number | null
  runtime_weight?: number
}

interface ActiveFactorSet {
  active?: number
  candidate?: number
  rejected?: number
  avg_active_ic?: number | null
  top_active?: TopFactor[]
  error?: string
}

interface FactorGate {
  active?: number
  rejected?: number
  candidate?: number
  pass_rate?: number | null
}

interface ScalpHealth {
  lookback_days?: number
  trades?: TradeStats
  ev_gate?: EvGateStats
  calibrator?: Record<string, unknown>
  acceptance?: Acceptance
  active_factor_set?: ActiveFactorSet
  factor_gate?: FactorGate
  error?: string
}

interface MetaProgress {
  raw?: number
  have?: number
  need?: number
  pos?: number
  neg?: number
  need_per_class?: number
  dedup_sec?: number
  percent?: number | null
  ready?: boolean
  error?: string
}

interface MetaFilterStats {
  coverage?: number
  win_rate?: number
  net_ret?: number
  n?: number
}

interface MetaReport {
  status?: string // insufficient | imbalanced | trained | error | no_report | ...
  usable?: boolean
  ts?: number
  n_settled?: number
  n_settled_raw?: number
  oos_auc_lgbm?: number
  oos_auc_linear?: number | null
  baseline?: { win_rate?: number; net_ret?: number }
  filter_top30pct?: MetaFilterStats | null
  filter_top15pct?: MetaFilterStats | null
  top_importance?: { name: string; importance: number }[]
  gate_reasons?: string[]
  note?: string
}

interface MetaBundle {
  progress?: MetaProgress
  report?: MetaReport
}

const pct = (v?: number | null, digits = 1) =>
  v == null ? '—' : `${(v * 100).toFixed(digits)}%`

const signed = (v?: number | null, digits = 4) =>
  v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`

export function ScalpFactorHealth() {
  const [data, setData] = useState<ScalpHealth>({})
  const [loading, setLoading] = useState(false)
  const [lookback, setLookback] = useState(14)

  const [job, setJob] = useState<FactorJob | null>(null)
  const [jobBusy, setJobBusy] = useState(false)
  const pollRef = useRef<number | null>(null)

  const [meta, setMeta] = useState<MetaBundle>({})
  const [metaJob, setMetaJob] = useState<FactorJob | null>(null)
  const [metaBusy, setMetaBusy] = useState(false)
  const metaPollRef = useRef<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await apiRequest(`/factors/scalp-health?lookback_days=${lookback}`)
      setData(await r.json())
    } catch {
      /* ignore */
    } finally {
      setLoading(false)
    }
  }, [lookback])

  const loadMeta = useCallback(async () => {
    try {
      const r = await apiRequest('/factors/scalp-meta-report')
      setMeta(await r.json())
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    load()
    loadMeta()
    const t = setInterval(() => {
      load()
      loadMeta()
    }, 30000)
    return () => clearInterval(t)
  }, [load, loadMeta])

  // 组件卸载时停止轮询
  useEffect(() => () => {
    if (pollRef.current) window.clearTimeout(pollRef.current)
    if (metaPollRef.current) window.clearTimeout(metaPollRef.current)
  }, [])

  const pollMetaJob = useCallback(
    async (id: string) => {
      try {
        const r = await apiRequest(`/factors/jobs/${id}`)
        const j: FactorJob = await r.json()
        setMetaJob(j)
        if (j.status === 'done' || j.status === 'error') {
          setMetaBusy(false)
          loadMeta()
          return
        }
      } catch {
        setMetaBusy(false)
        return
      }
      metaPollRef.current = window.setTimeout(() => pollMetaJob(id), 1500)
    },
    [loadMeta],
  )

  // 手动训练元标签模型：后台异步，样本不足会优雅跳过
  const runMetaTrain = useCallback(async () => {
    setMetaBusy(true)
    setMetaJob({ status: 'running', kind: 'scalp_meta_train', progress: 0, total: 0, message: '提交中…' })
    try {
      const r = await apiRequest('/factors/scalp-meta/train', { method: 'POST' })
      const j: FactorJob = await r.json()
      setMetaJob(j)
      if (j.job_id && j.status !== 'done' && j.status !== 'error') {
        metaPollRef.current = window.setTimeout(() => pollMetaJob(j.job_id as string), 1200)
      } else {
        setMetaBusy(false)
        loadMeta()
      }
    } catch (e) {
      setMetaJob({ status: 'error', kind: 'scalp_meta_train', error: String(e) })
      setMetaBusy(false)
    }
  }, [loadMeta, pollMetaJob])

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

  // 一键验证：后台异步样本外打分晋升（含短线候选），轮询进度
  const runValidate = useCallback(async () => {
    setJobBusy(true)
    setJob({ status: 'running', kind: 'candidates_validate', progress: 0, total: 0, message: '提交中…' })
    try {
      const r = await apiRequest('/factors/validate?limit=50', { method: 'POST' })
      const j: FactorJob = await r.json()
      setJob(j)
      if (j.job_id && j.status !== 'done' && j.status !== 'error') {
        pollRef.current = window.setTimeout(() => pollJob(j.job_id as string), 1200)
      } else {
        setJobBusy(false)
        load()
      }
    } catch (e) {
      setJob({ status: 'error', kind: 'candidates_validate', error: String(e) })
      setJobBusy(false)
    }
  }, [load, pollJob])

  const trades = data.trades || {}
  const ev = data.ev_gate || {}
  const acc = data.acceptance || {}
  const afs = data.active_factor_set || {}
  const gate = data.factor_gate || {}

  const wr = trades.win_rate ?? null
  const exp = trades.avg_return_pct ?? null
  const evPassRate = typeof ev.pass_rate === 'number' ? ev.pass_rate : null

  return (
    <div className="space-y-4">
      <InfoBanner title="短线因子健康">
        用于持续判断"短线转正"改造是否见效：滚动胜率 / 净期望 / EV 闸门放行率 / 因子发现闸门通过率 /
        活跃因子 IC。目标——胜率 ≥ {pct(acc.target_win_rate ?? 0.48, 0)}、净期望转正、笔数收敛。
      </InfoBanner>

      {/* 核心验收指标 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label={`滚动胜率 (${data.lookback_days ?? lookback}天)`}
          value={pct(wr)}
          hint={`目标 ≥ ${pct(acc.target_win_rate ?? 0.48, 0)}`}
          tone={acc.win_rate_ok ? 'good' : wr == null ? 'default' : 'bad'}
        />
        <StatCard
          label="净期望 / 笔"
          value={exp == null ? '—' : `${(exp * 100).toFixed(3)}%`}
          hint="扣费口径均值"
          tone={acc.expectancy_positive ? 'good' : exp == null ? 'default' : 'bad'}
        />
        <StatCard
          label="成交笔数"
          value={trades.trade_count ?? '—'}
          hint="已平仓 scalp 单"
        />
        <StatCard
          label="EV 闸门放行率"
          value={pct(evPassRate)}
          hint={`放行 ${ev.pass_count ?? 0} / 拦截 ${ev.block_count ?? 0}`}
          tone={evPassRate == null ? 'default' : evPassRate < 0.6 ? 'warn' : 'good'}
        />
      </div>

      {/* 验收结论 */}
      <SectionCard
        title="阶段一验收结论"
        description="模拟盘运行 1–2 周后据此判断是否达标转正"
        action={
          <div className="flex items-center gap-2">
            <div className="flex items-center rounded-md border overflow-hidden text-xs">
              {[7, 14, 30].map((d) => (
                <button
                  key={d}
                  onClick={() => setLookback(d)}
                  className={
                    'px-2 py-1 ' +
                    (lookback === d
                      ? 'bg-primary text-primary-foreground font-semibold'
                      : 'hover:bg-muted')
                  }
                >
                  {d}天
                </button>
              ))}
            </div>
            <RefreshButton onClick={load} loading={loading} />
          </div>
        }
      >
        <div className="flex items-center gap-3 flex-wrap">
          <span
            className={
              acc.passed
                ? 'inline-flex items-center rounded-md px-3 py-1 text-sm font-semibold bg-green-500/15 text-green-600 dark:text-green-400'
                : 'inline-flex items-center rounded-md px-3 py-1 text-sm font-semibold bg-amber-500/15 text-amber-600 dark:text-amber-400'
            }
          >
            {acc.passed ? '✅ 达标（胜率+净期望均通过）' : '⏳ 未达标 / 观测中'}
          </span>
          <span className="text-xs text-muted-foreground">
            胜率达标：{acc.win_rate_ok ? '是' : '否'} · 净期望转正：{acc.expectancy_positive ? '是' : '否'}
          </span>
        </div>
      </SectionCard>

      {/* 因子科研闭环 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="活跃因子" value={afs.active ?? 0} hint="通过闸门 A/B 级" tone="good" />
        <StatCard label="候选因子" value={afs.candidate ?? 0} hint="待回测打分" />
        <StatCard label="已淘汰" value={afs.rejected ?? 0} hint="未过闸/衰减退役" />
        <StatCard
          label="发现闸门通过率"
          value={pct(gate.pass_rate)}
          hint="active / (active+rejected)"
        />
      </div>

      <SectionCard
        title="短线活跃因子集"
        description={`平均 IC ${afs.avg_active_ic == null ? '—' : afs.avg_active_ic.toFixed(4)} · 按 |IC| 排序 Top 10`}
        action={
          <button
            onClick={runValidate}
            disabled={jobBusy}
            className="rounded-md border px-2 py-1 text-xs font-semibold bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
            title="后台异步：对候选因子逐个样本外打分，A/B 级晋升为活跃"
          >
            {jobBusy ? '验证中…' : '一键验证'}
          </button>
        }
      >
        {/* 后台任务进度 */}
        {job && (
          <div className="mb-3 rounded-md border p-2 text-xs">
            <div className="flex items-center justify-between mb-1">
              <span className="font-medium">
                样本外验证 ·{' '}
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
            {job.status === 'done' && job.result && (
              <div className="mt-1 text-muted-foreground">
                打分 {job.result.scored ?? 0} · 晋升 {job.result.promoted ?? 0}
              </div>
            )}
            {job.status === 'error' && <div className="mt-1 text-red-500">失败：{job.error}</div>}
          </div>
        )}
        {!afs.top_active || afs.top_active.length === 0 ? (
          <EmptyState message="暂无活跃因子（候选因子通过样本外回测打分后进入）" />
        ) : (
          <div className="rounded-md border overflow-hidden">
            <div className="grid grid-cols-12 gap-2 text-xs font-medium text-muted-foreground px-3 py-2 bg-muted/50 border-b">
              <div className="col-span-6">因子 ID</div>
              <div className="col-span-2">评级</div>
              <div className="col-span-2 text-right">IC 均值</div>
              <div className="col-span-2 text-right">运行权重</div>
            </div>
            {afs.top_active.map((f) => (
              <div
                key={f.factor_id}
                className="grid grid-cols-12 gap-2 text-xs px-3 py-2 border-b last:border-0 hover:bg-muted/30"
              >
                <div className="col-span-6 font-mono truncate" title={f.factor_id}>
                  {f.factor_id}
                </div>
                <div className="col-span-2">
                  <span className="inline-flex items-center rounded px-1.5 py-0.5 bg-primary/10 text-primary font-semibold">
                    {f.grade ?? '—'}
                  </span>
                </div>
                <div
                  className={
                    'col-span-2 text-right tabular-nums ' +
                    ((f.ic_mean ?? 0) >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400')
                  }
                >
                  {f.ic_mean == null ? '—' : f.ic_mean.toFixed(4)}
                </div>
                <div className="col-span-2 text-right tabular-nums">
                  {(f.runtime_weight ?? 1).toFixed(2)}
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      {/* ── 元标签模型（真假信号过滤器）── */}
      {(() => {
        const mp = meta.progress || {}
        const mr = meta.report || {}
        const trained = mr.status === 'trained'
        const base = mr.baseline || {}
        const f30 = mr.filter_top30pct || null
        const f15 = mr.filter_top15pct || null
        const pctW = Math.max(0, Math.min(100, mp.percent ?? 0))
        const statusLabel: Record<string, string> = {
          insufficient: '样本采集中',
          imbalanced: '类别不均衡·采集中',
          trained: '已训练',
          no_report: '尚未训练',
          error: '异常',
          no_valid_folds: '时间跨度不足·采集中',
          no_deps: '缺少依赖',
        }
        return (
          <SectionCard
            title="元标签模型 · 真假信号过滤器"
            description="在真实信号+真实输赢上训练，学「这一单会不会赢」。达标(usable)前只影子、不接入实盘。"
            action={
              <button
                onClick={runMetaTrain}
                disabled={metaBusy}
                className="rounded-md border px-2 py-1 text-xs font-semibold bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
                title="后台异步：读真实信号→训练+样本外验证；样本不足会优雅跳过"
              >
                {metaBusy ? '训练中…' : '立即训练'}
              </button>
            }
          >
            {/* 采集进度 */}
            <div className="mb-3">
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="font-medium">
                  独立样本采集 · {mp.have ?? 0} / {mp.need ?? '—'}
                  <span className="text-muted-foreground ml-2">
                    (原始 {mp.raw ?? 0} 条，按币×{Math.round((mp.dedup_sec ?? 1800) / 60)}分钟去重防虚高)
                  </span>
                </span>
                <span
                  className={
                    'inline-flex items-center rounded px-2 py-0.5 font-semibold ' +
                    (mr.usable
                      ? 'bg-green-500/15 text-green-600 dark:text-green-400'
                      : trained
                        ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                        : 'bg-muted text-muted-foreground')
                  }
                >
                  {mr.usable ? '✅ 已达标·可启用' : statusLabel[mr.status ?? 'no_report'] ?? mr.status}
                </span>
              </div>
              <div className="w-full h-2 rounded bg-muted overflow-hidden">
                <div
                  className={'h-full transition-all ' + (mp.ready ? 'bg-green-500' : 'bg-sky-500')}
                  style={{ width: `${pctW}%` }}
                />
              </div>
              <div className="mt-1 text-[11px] text-muted-foreground">
                赢 {mp.pos ?? 0} / 亏 {mp.neg ?? 0}（各需 ≥ {mp.need_per_class ?? 200}）
                {!trained && mr.note ? ` · ${mr.note}` : ''}
              </div>
            </div>

            {/* 训练任务进度 */}
            {metaJob && metaJob.status === 'running' && (
              <div className="mb-3 rounded-md border p-2 text-xs">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium">训练/验证进行中</span>
                  <span className="text-muted-foreground truncate max-w-[60%] text-right">{metaJob.message}</span>
                </div>
                <div className="w-full h-1.5 rounded bg-muted overflow-hidden">
                  <div className="h-full bg-sky-500 animate-pulse w-1/3" />
                </div>
              </div>
            )}
            {metaJob && metaJob.status === 'error' && (
              <div className="mb-3 text-xs text-red-500">训练失败：{metaJob.error}</div>
            )}

            {/* 训练达标后的验证指标 */}
            {trained ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <StatCard
                    label="样本外 AUC"
                    value={mr.oos_auc_lgbm == null ? '—' : mr.oos_auc_lgbm.toFixed(3)}
                    hint={`线性对比 ${mr.oos_auc_linear == null ? '—' : mr.oos_auc_linear.toFixed(3)}`}
                    tone={(mr.oos_auc_lgbm ?? 0) >= 0.55 ? 'good' : (mr.oos_auc_lgbm ?? 0) >= 0.53 ? 'warn' : 'bad'}
                  />
                  <StatCard
                    label="基线胜率 / 净收益"
                    value={pct(base.win_rate)}
                    hint={`照单全收 ${signed(base.net_ret)}/单`}
                  />
                  <StatCard
                    label="过滤后胜率 (前30%)"
                    value={pct(f30?.win_rate)}
                    hint={`覆盖 ${pct(f30?.coverage)} · ${f30?.n ?? 0} 单`}
                    tone={f30 && base.win_rate != null && (f30.win_rate ?? 0) > base.win_rate ? 'good' : 'default'}
                  />
                  <StatCard
                    label="过滤后净收益 (前30%)"
                    value={signed(f30?.net_ret)}
                    hint={`前15%: ${signed(f15?.net_ret)}`}
                    tone={(f30?.net_ret ?? -1) > 0 ? 'good' : 'bad'}
                  />
                </div>

                {!mr.usable && mr.gate_reasons && mr.gate_reasons.length > 0 && (
                  <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-xs text-amber-600 dark:text-amber-400">
                    未达可用门槛：{mr.gate_reasons.join('；')}
                  </div>
                )}

                {/* 因子重要性 Top */}
                {mr.top_importance && mr.top_importance.length > 0 && (
                  <div className="mt-3">
                    <div className="text-xs font-medium text-muted-foreground mb-2">因子重要性 Top 8</div>
                    <div className="space-y-1">
                      {mr.top_importance.slice(0, 8).map((fi) => (
                        <div key={fi.name} className="flex items-center gap-2 text-xs">
                          <div className="w-40 font-mono truncate" title={fi.name}>
                            {fi.name}
                          </div>
                          <div className="flex-1 h-2 rounded bg-muted overflow-hidden">
                            <div
                              className="h-full bg-indigo-500"
                              style={{ width: `${Math.min(100, fi.importance * 100)}%` }}
                            />
                          </div>
                          <div className="w-12 text-right tabular-nums text-muted-foreground">
                            {(fi.importance * 100).toFixed(1)}%
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <EmptyState
                message={
                  mr.status === 'no_report'
                    ? '尚未训练：等独立样本攒够门槛后每日自动训练，也可点"立即训练"试跑'
                    : '样本积累中：达标后自动训练并在此展示样本外胜率提升与因子重要性'
                }
              />
            )}
          </SectionCard>
        )
      })()}

      {data.error && (
        <p className="text-xs text-red-500">加载异常：{data.error}</p>
      )}
    </div>
  )
}
