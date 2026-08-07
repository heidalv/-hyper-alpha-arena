import React, { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Info, Loader2, Play, RefreshCw, Shield, Sparkles, Square, Wallet } from 'lucide-react'
import { toast } from 'react-hot-toast'
import { cn } from '@/lib/utils'
import {
  type ArbitragePaperAccount,
  type ArbitragePaperSessionStatus,
  type ArbitrageStartValidation,
  STRATEGY_META,
  estimateMonthlyUsd,
  recommendStrategies,
  getArbitragePaperAccounts,
  getArbitragePaperSession,
  startArbitragePaperVerification,
  stopArbitragePaperVerification,
  validateArbitragePaperStart,
} from '@/lib/arbitrageApi'
import ExchangeAllocationGrid from './ExchangeAllocationGrid'
import StartReadinessChecklist from './StartReadinessChecklist'
import ArbitrageTraderBinding from './ArbitrageTraderBinding'
import ArbitrageConfigMap from './ArbitrageConfigMap'
import ArbitrageSetupGuide from './ArbitrageSetupGuide'
import CollapsibleHelpPanel from './CollapsibleHelpPanel'

// M4 处置后：S1/S5 已下线不再展示；S6 保留展示但带关闭原因
const STRATEGY_ORDER = ['S8', 'S3', 'S7', 'S2', 'S4', 'S6'] as const

function toAmounts(account: ArbitragePaperAccount | null): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [exchange, row] of Object.entries(account?.exchange_balances || {})) {
    out[exchange] = row.allocated_usd
  }
  return out
}

function riskTone(level: string) {
  if (level === '低') return 'bg-green-500/10 text-green-700 dark:text-green-400 border-green-500/20'
  if (level === '高') return 'bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/20'
  return 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20'
}

export default function ArbitrageStartWizard({
  onRefresh,
  onNavigate,
  externalSession,
}: {
  onRefresh?: () => void
  onNavigate?: (tab: string) => void
  /** 由主页面 30s 轮询传入的会话状态（消除组件内部二次轮询） */
  externalSession?: ArbitragePaperSessionStatus | null
}) {
  const [accounts, setAccounts] = useState<ArbitragePaperAccount[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>(['S8'])
  const [expandedId, setExpandedId] = useState<string | null>('S8')
  const [validation, setValidation] = useState<ArbitrageStartValidation | null>(null)
  const [loading, setLoading] = useState(false)
  const [checking, setChecking] = useState(false)
  const [starting, setStarting] = useState(false)
  const [message, setMessage] = useState('')
  const [started, setStarted] = useState(false)
  const [session, setSession] = useState<ArbitragePaperSessionStatus | null>(null)
  const [stopping, setStopping] = useState(false)

  const selected = useMemo(
    () => accounts.find(a => a.id === selectedId) || accounts[0] || null,
    [accounts, selectedId],
  )

  const equity = selected?.total_equity ?? 300
  const recommendation = useMemo(() => recommendStrategies(equity), [equity])
  const monthlyEst = useMemo(
    () => estimateMonthlyUsd(equity, selectedStrategies),
    [equity, selectedStrategies],
  )

  const load = async () => {
    setLoading(true)
    try {
      const [data, sess] = await Promise.all([
        getArbitragePaperAccounts(),
        getArbitragePaperSession(),
      ])
      setAccounts(data)
      setSession(sess)
      setStarted(Boolean(sess?.running))
      if (sess?.running && sess.account_id) {
        setSelectedId(sess.account_id)
        if (sess.strategies?.length) setSelectedStrategies(sess.strategies)
      } else {
        setSelectedId(prev => prev || data[0]?.id || null)
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  useEffect(() => {
    if (!selected?.trader_profile?.enabled_strategies?.length) return
      const authorized = selected.trader_profile.enabled_strategies.filter(
      sid => !STRATEGY_META[sid]?.monitorOnly,
    )
    if (authorized.length === 0) return
    setSelectedStrategies(prev => {
      const overlap = prev.filter(s => authorized.includes(s))
        if (overlap.length > 0) return overlap
        return authorized.includes('S8') ? ['S8'] : [authorized[0]]
    })
  }, [selected?.id, selected?.trader_profile?.enabled_strategies?.join(',')])

  const authorizedStrategies = selected?.trader_profile?.enabled_strategies || []
  const unauthorizedSelected = selectedStrategies.filter(
    sid => authorizedStrategies.length > 0 && !authorizedStrategies.includes(sid),
  )

  // 消除双重轮询：会话状态由主页面 30s 轮询统一刷新后传入，这里只做同步
  useEffect(() => {
    if (externalSession === undefined) return
    setSession(externalSession)
    setStarted(Boolean(externalSession?.running))
  }, [externalSession])

  const toggleStrategy = (id: string) => {
    const meta = STRATEGY_META[id]
    if (meta?.monitorOnly) return
    if (meta?.disabledReason) {
      toast.error(`${id} ${meta.disabledReason}`)
      return
    }
    if (authorizedStrategies.length > 0 && !authorizedStrategies.includes(id)) {
      toast.error(`${id} 未在交易员专用套利档案中授权`)
      return
    }
    setSelectedStrategies(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
    setValidation(null)
    setStarted(false)
  }

  const applyRecommendation = () => {
    const rec = recommendation.strategies.filter(
      sid => authorizedStrategies.length === 0 || authorizedStrategies.includes(sid),
    )
    if (rec.length === 0) {
      toast.error('推荐策略与交易员授权不一致，请先在专用套利里勾选策略')
      return
    }
    setSelectedStrategies(rec)
    setExpandedId(rec[0] || null)
    setValidation(null)
    setMessage(`已套用推荐组合：${rec.join(' / ')}`)
  }

  const handleValidate = async () => {
    if (!selected) return
    setChecking(true)
    setMessage('检查进行中：含 S8 AI 信号实时分析（调用大模型），通常需要 30~60 秒，请耐心等待…')
    try {
      const result = await validateArbitragePaperStart(selected.id, selectedStrategies)
      setValidation(result)
      setMessage(result.passed ? '检查通过，可以启动 Paper 验证。' : '检查未通过，请先处理红色项。')
    } catch (e) {
      setMessage(`检查失败：${e instanceof Error ? e.message : '网络或后端异常，请重试'}`)
      toast.error('启动前检查失败，请重试')
    } finally {
      setChecking(false)
    }
  }

  const handlePaperStart = async () => {
    if (!selected) {
      toast.error('请先选择套利 Paper 账户')
      return
    }
    if (selectedStrategies.length === 0) {
      toast.error('请至少选择一个策略')
      return
    }
    // P0 修复：启动必须先通过「启动前检查」，不允许跳过
    if (validation?.passed !== true) {
      toast.error('请先运行「启动前检查」并确保全部通过')
      return
    }

    setStarting(true)
    setMessage('')
    try {
      const result = await startArbitragePaperVerification(selected.id, selectedStrategies)
      if (result.checks?.length) {
        setValidation({
          success: result.success,
          passed: Boolean(result.passed),
          checks: result.checks,
          strategies: result.strategies || selectedStrategies,
          trader_profile: (result as any).trader_profile,
          strategy_runtime: (result as any).strategy_runtime,
        })
      }
      if (!result.success) {
        const err = result.error || '启动失败，请先处理检查项'
        setMessage(err)
        toast.error(err)
        return
      }

      setStarted(true)
      if (result.session) setSession(result.session)
      const scan = result.scan
      const equity = scan?.account_equity ?? selected.total_equity
      const scanHint = scan?.triggered
        ? `权益 $${Number(equity || 0).toFixed(0)} · 扫描 ${scan.total_evaluated ?? 0} 个策略 · ${scan.viable_count ?? 0} 个可行${scan.auto_executed ? ' · 已自动 Paper 开仓' : ''}`
        : '后台 tick 已启动'
      setMessage(`Paper 验证运行中 · ${result.strategies?.join(' / ') || selectedStrategies.join(' / ')} · ${scanHint}`)
      toast.success(`Paper 验证已启动，后台每 ${result.session?.interval_seconds ?? 90}s 自动扫描`)
      onRefresh?.()
    } catch {
      const err = '启动请求失败，请检查网络或后端服务'
      setMessage(err)
      toast.error(err)
    } finally {
      setStarting(false)
    }
  }

  const handlePaperStop = async () => {
    if (!selected) return
    setStopping(true)
    try {
      const result = await stopArbitragePaperVerification(selected.id)
      if (!result.success) {
        toast.error(result.error || '停止失败')
        return
      }
      setStarted(false)
      setSession({ running: false })
      setMessage('Paper 验证已停止')
      toast.success('Paper 验证已停止')
      await load()
      onRefresh?.()
    } catch {
      toast.error('停止请求失败')
    } finally {
      setStopping(false)
    }
  }

  const isRunning = Boolean(session?.running && session.account_id === selected?.id)

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-card p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Play className="w-5 h-5 text-green-500" /> 套利启动配置
            </h2>
            <p className="text-sm text-muted-foreground mt-1">
              按「账户 → 绑定专用套利交易员 → 交易所资金 → 策略组合 → 风控检查 → 启动」配置。
            </p>
          </div>
          <button onClick={load} disabled={loading} className="px-3 py-2 rounded-lg bg-secondary hover:bg-secondary/80 text-sm flex items-center gap-2">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> 刷新账户
          </button>
        </div>
      </div>

      <CollapsibleHelpPanel
        title="配置说明"
        summary="首次使用可展开：模拟账户分账 → 专用套利配交易员 → 绑定 → 选策略启动。"
        defaultOpen={false}
      >
        <ArbitrageSetupGuide variant="start" embedded />
        <ArbitrageConfigMap embedded />
      </CollapsibleHelpPanel>

      {isRunning && session && (
        <div className="rounded-xl border border-green-500/40 bg-green-500/10 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="font-semibold text-green-700 dark:text-green-300 flex items-center gap-2">
                <span className="inline-block w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                Paper 验证运行中
              </div>
              <div className="text-sm text-muted-foreground mt-1">
                账户 #{session.account_id} · 策略 {session.strategies?.join(' / ')} ·
                每 {session.interval_seconds ?? 90}s 自动扫描
                {session.last_tick ? (
                  <> · 最近 tick：权益 ${Number(session.last_tick.account_equity || 0).toFixed(0)}，{session.last_tick.viable_count ?? 0} 个可行
                  {session.last_tick.auto_executed
                    ? '，已 Paper 开仓'
                    : session.last_tick.auto_exec_error
                      ? `，未开仓：${session.last_tick.auto_exec_error}`
                      : ''}
                  </>
                ) : null}
              </div>
            </div>
            <button
              type="button"
              onClick={handlePaperStop}
              disabled={stopping}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white text-sm"
            >
              {stopping ? <Loader2 className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
              停止验证
            </button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-4">
        <div className="space-y-4">
          <section className="rounded-xl border border-border bg-card p-5">
            <div className="flex items-center gap-2 font-semibold mb-3">
              <Wallet className="w-4 h-4 text-blue-500" /> 1. 选择套利模拟账户组
            </div>
            {accounts.length === 0 ? (
              <div className="text-sm text-muted-foreground flex flex-wrap items-center gap-3">
                <span>还没有套利 Paper 账户，请先到“模拟账户”页创建。</span>
                {onNavigate && (
                  <button
                    type="button"
                    onClick={() => onNavigate('paper_account')}
                    className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs"
                  >
                    去创建模拟账户
                  </button>
                )}
              </div>
            ) : (
              <select
                value={selected?.id || ''}
                onChange={e => {
                  setSelectedId(Number(e.target.value))
                  setValidation(null)
                  setStarted(false)
                }}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              >
                {accounts.map(a => <option key={a.id} value={a.id}>{a.name} · ${a.total_equity.toFixed(2)}</option>)}
              </select>
            )}
          </section>

          {selected && (
            <section className="rounded-xl border border-border bg-card p-5">
              <div className="font-semibold mb-3">2. 绑定专用套利交易员</div>
              <ArbitrageTraderBinding account={selected} onUpdated={load} />
            </section>
          )}

          <section className="rounded-xl border border-border bg-card p-5">
            <div className="flex items-start justify-between gap-3 mb-1">
              <div className="font-semibold">3. 交易所分账户</div>
              {onNavigate && (
                <button
                  type="button"
                  onClick={() => onNavigate('paper_account')}
                  className="text-xs px-2 py-1 rounded bg-secondary hover:bg-secondary/80"
                >
                  去模拟账户页调整
                </button>
              )}
            </div>
            <p className="text-xs text-muted-foreground mb-3">
              此处只读预览。要调整各所资金比例，请切换到「模拟账户」页保存后再回来检查。
            </p>
            <ExchangeAllocationGrid balances={toAmounts(selected)} />
          </section>

          <section className="rounded-xl border border-border bg-card p-5">
            <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
              <div>
                <div className="font-semibold">4. 选择策略组合</div>
                <p className="text-xs text-muted-foreground mt-1">
                  只能选交易员档案里已授权的策略（在「AI 交易员 → 专用套利」勾选）。
                  S1/S5 已下线；S6 已关闭（负 EV）；S2/S4 保持关闭；S7 仅监控不可启动。
                </p>
              </div>
              <button
                type="button"
                onClick={applyRecommendation}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs"
              >
                <Sparkles className="w-3.5 h-3.5" /> 一键推荐（${equity.toFixed(0)}）
              </button>
            </div>

            <div className="rounded-lg border border-blue-500/20 bg-blue-500/5 p-3 mb-4 text-sm">
              <div className="font-medium text-blue-700 dark:text-blue-300">系统推荐</div>
              <div className="text-muted-foreground mt-1">{recommendation.reason}</div>
              <div className="mt-2 flex flex-wrap gap-2 text-xs">
                <span className="px-2 py-1 rounded bg-background border border-border">
                  推荐：{recommendation.strategies.join(' / ')}
                </span>
                <span className="px-2 py-1 rounded bg-background border border-border">
                  已选月化粗算：${monthlyEst.low} – ${monthlyEst.high}
                </span>
              </div>
            </div>

            {authorizedStrategies.length > 0 && (
              <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-3 mb-4 text-xs">
                交易员已授权：{authorizedStrategies.join(' / ')}
              </div>
            )}
            {!selected?.trader_profile && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 mb-4 text-xs text-amber-800 dark:text-amber-200">
                尚未绑定专用套利交易员。S3/S8 等策略在启动前检查时会失败，请先完成 Step 2。
              </div>
            )}
            {unauthorizedSelected.length > 0 && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 mb-4 text-xs text-red-700 dark:text-red-300">
                以下策略未在交易员档案中授权，检查将失败：{unauthorizedSelected.join(' / ')}
              </div>
            )}

            <div className="grid grid-cols-1 gap-3">
              {STRATEGY_ORDER.map(id => {
                const meta = STRATEGY_META[id]
                if (!meta || meta.deprecated) return null
                const isSelected = selectedStrategies.includes(id)
                const isExpanded = expandedId === id
                const capitalOk = equity >= meta.minCapitalUsd
                const group = ['S3', 'S5', 'S7', 'S8'].includes(id) ? '套利积分' : '交易积分'
                const isClosed = Boolean(meta.disabledReason)
                const notSelectable = meta.monitorOnly || isClosed

                return (
                  <div
                    key={id}
                    className={cn(
                      'rounded-xl border transition-colors',
                      isClosed
                        ? 'border-border bg-muted/30 opacity-75'
                        : meta.monitorOnly
                          ? 'border-amber-500/30 bg-amber-500/5'
                          : isSelected
                            ? 'border-blue-500 bg-blue-500/5'
                            : 'border-border bg-card',
                    )}
                  >
                    <div className="p-4">
                      <div className="flex items-start justify-between gap-3">
                        <button
                          type="button"
                          disabled={notSelectable}
                          onClick={() => toggleStrategy(id)}
                          className={cn(
                            'flex-1 text-left',
                            notSelectable ? 'cursor-not-allowed opacity-90' : 'cursor-pointer',
                          )}
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-semibold">{id} · {meta.name}</span>
                            <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground">{group}</span>
                            <span className={cn('text-[10px] px-2 py-0.5 rounded-full border', riskTone(meta.riskLevel))}>
                              风险 {meta.riskLevel}
                            </span>
                            {meta.defaultOn && !isClosed && (
                              <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-500/10 text-green-700 dark:text-green-400">
                                小资金默认
                              </span>
                            )}
                            {meta.monitorOnly && (
                              <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300">
                                仅监控
                              </span>
                            )}
                            {isClosed && (
                              <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-500/10 text-red-700 dark:text-red-300">
                                已关闭
                              </span>
                            )}
                          </div>
                          <div className="text-sm text-muted-foreground mt-1">{meta.description}</div>
                          {isClosed && (
                            <div className="text-xs text-red-700 dark:text-red-300 mt-1">{meta.disabledReason}</div>
                          )}
                        </button>
                        <div className="flex flex-col items-end gap-2 shrink-0">
                          {isSelected && !meta.monitorOnly && <CheckCircle2 className="w-5 h-5 text-blue-500" />}
                          <button
                            type="button"
                            onClick={() => setExpandedId(isExpanded ? null : id)}
                            className="text-xs px-2 py-1 rounded bg-muted hover:bg-muted/80"
                          >
                            {isExpanded ? '收起' : '详情'}
                          </button>
                        </div>
                      </div>

                      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3 text-xs">
                        <Metric label="年化 ROI 区间" value={meta.roiRange} />
                        <Metric label="最大回撤参考" value={meta.drawdown} />
                        <Metric label="最低建议资金" value={`$${meta.minCapitalUsd}`} warn={!capitalOk} />
                        <Metric label="主要交易所" value={meta.exchanges.join(' / ')} />
                      </div>

                      {!capitalOk && (
                        <div className="mt-2 flex items-start gap-2 text-xs text-amber-700 dark:text-amber-300">
                          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                          当前账户 ${equity.toFixed(0)} 低于建议 ${meta.minCapitalUsd}，Paper 可试但实盘风险更高。
                        </div>
                      )}

                      {isExpanded && (
                        <div className="mt-4 space-y-3 border-t border-border pt-4 text-sm">
                          <Block title="怎么赚钱" icon={<Info className="w-4 h-4 text-blue-500" />}>
                            {meta.howItWorks}
                          </Block>
                          <Block title="预计收益测算（Paper 粗算，非承诺）" icon={<Sparkles className="w-4 h-4 text-purple-500" />}>
                            {meta.profitEstimate}
                          </Block>
                          <Block title="风险提示" icon={<AlertTriangle className="w-4 h-4 text-red-500" />}>
                            <ul className="list-disc pl-4 space-y-1 text-muted-foreground">
                              {meta.risks.map(r => <li key={r}>{r}</li>)}
                            </ul>
                          </Block>
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                            <div className="rounded-lg bg-green-500/5 border border-green-500/15 p-3">
                              <div className="font-medium text-green-700 dark:text-green-400">适合</div>
                              <div className="text-muted-foreground mt-1">{meta.suitableFor}</div>
                            </div>
                            <div className="rounded-lg bg-red-500/5 border border-red-500/15 p-3">
                              <div className="font-medium text-red-700 dark:text-red-400">不适合</div>
                              <div className="text-muted-foreground mt-1">{meta.notSuitableFor}</div>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>

            <div className="mt-4 rounded-lg border border-border bg-muted/20 p-3 text-xs text-muted-foreground">
              {monthlyEst.note}
            </div>
          </section>
        </div>

        <div className="space-y-4">
          <section className="rounded-xl border border-border bg-card p-5">
            <div className="flex items-center gap-2 font-semibold mb-3">
              <Shield className="w-4 h-4 text-purple-500" /> 5. 风控与规则同步检查
            </div>
            <button
              disabled={!selected || checking}
              onClick={handleValidate}
              className="w-full rounded-lg bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white px-3 py-2 text-sm mb-4"
            >
              {checking ? '检查中（AI 信号分析约 30~60 秒）...' : '启动前检查'}
            </button>
            <StartReadinessChecklist validation={validation} />
          </section>

          <section className="rounded-xl border border-border bg-card p-5">
            <div className="font-semibold mb-2">6. 启动确认</div>
            <div className="text-sm text-muted-foreground space-y-2">
              <p>当前策略：{selectedStrategies.join(' / ') || '未选择'}</p>
              <p>当前账户：{selected ? `${selected.name} · $${selected.total_equity.toFixed(2)}` : '未选择'}</p>
              <p>绑定交易员：{selected?.trader_profile?.account_name || '未绑定'}</p>
              <p>组合月化粗算：${monthlyEst.low} – ${monthlyEst.high}（Paper 假设）</p>
              <p>
                确认说明：这是
                <span className="text-foreground font-medium"> 独立的套利 Paper 系统 </span>
                ，与 AI 策略方向交易分开，不占用 AI 策略模拟盘资金。
              </p>
              <p className="text-xs">
                启动前必须：① Paper 账户已创建并分账 ② 已绑定专用套利交易员（双模型已配） ③ 所选策略在交易员档案中已授权 ④「启动前检查」全部通过。
              </p>
            </div>
            <button
              type="button"
              onClick={handlePaperStart}
              disabled={starting || stopping || !selected || selectedStrategies.length === 0 || isRunning || validation?.passed !== true}
              className="mt-4 w-full rounded-lg bg-green-600 hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-3 py-2 text-sm inline-flex items-center justify-center gap-2"
            >
              {starting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  正在启动...
                </>
              ) : isRunning ? (
                <>
                  <CheckCircle2 className="w-4 h-4" />
                  已在运行中
                </>
              ) : (
                '确认启动 Paper 验证'
              )}
            </button>
            {!isRunning && validation?.passed !== true && (
              <div className="mt-2 text-xs text-amber-700 dark:text-amber-300 flex items-start gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                {validation === null
                  ? '请先运行左侧「启动前检查」，全部通过后才能启动。'
                  : '启动前检查未通过，请先处理红色项后重新检查。'}
              </div>
            )}
            {message && (
              <div className={cn(
                'mt-3 rounded-lg border px-3 py-2 text-sm',
                isRunning || started
                  ? 'border-green-500/30 bg-green-500/10 text-green-700 dark:text-green-300'
                  : 'border-border text-muted-foreground',
              )}>
                {message}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}

function Metric({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className={cn('rounded-lg border p-2', warn ? 'border-amber-500/30 bg-amber-500/5' : 'border-border bg-background/60')}>
      <div className="text-muted-foreground">{label}</div>
      <div className="font-medium mt-0.5">{value}</div>
    </div>
  )
}

function Block({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-center gap-2 font-medium mb-1">{icon}{title}</div>
      <div className="text-muted-foreground">{children}</div>
    </div>
  )
}
