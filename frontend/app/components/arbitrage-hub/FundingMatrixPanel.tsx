/**
 * FundingMatrixPanel — 实时多场所资金费矩阵 + delta-neutral 净EV机会
 *
 * 数据源：/api/rebate/funding-matrix（perp_funding 采集器 → funding_rate_provider）。
 * 诚实原则：无≥2场所覆盖时明确提示"数据不足、无法凑双腿"，绝不臆造机会。
 *
 * 两块内容：
 *   1) 净EV机会：每个 symbol 的最优 delta-neutral 组合（长/空腿、毛年化、持有期净年化、保本天数）
 *   2) 资金费矩阵：每个 symbol 在各场所的资金费率（结算周期口径）
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { RefreshCw, TrendingUp, Layers, AlertTriangle, Coins, Activity } from 'lucide-react'
import {
  getFundingMatrix,
  getFundingCollectorStatus,
  type FundingMatrixResponse,
  type FundingCollectorStatus,
} from '@/lib/arbitrageApi'

const HORIZONS = [7, 14, 21]
const AUTO_REFRESH_MS = 30_000

const pctApr = (v: number | null | undefined) => `${(Number(v ?? 0) * 100).toFixed(2)}%`
const pctRate = (v: number | null | undefined) => `${(Number(v ?? 0) * 100).toFixed(4)}%`

// 采集器场所状态 → 展示样式（诚实映射：ok=绿 / empty=灰 / 其余故障=红/黄）
const VENUE_STATUS_STYLE: Record<string, { cls: string; label: string }> = {
  ok: { cls: 'bg-green-500/15 text-green-700 dark:text-green-300 border-green-500/30', label: '正常' },
  empty: { cls: 'bg-muted text-muted-foreground border-border', label: '无匹配' },
  error: { cls: 'bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/30', label: '错误' },
  timeout: { cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30', label: '超时' },
  thread_timeout: { cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30', label: '超时' },
  cancelled: { cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30', label: '取消' },
  unknown: { cls: 'bg-muted text-muted-foreground border-border', label: '未知' },
}

export default function FundingMatrixPanel() {
  const [data, setData] = useState<FundingMatrixResponse | null>(null)
  const [collector, setCollector] = useState<FundingCollectorStatus | null>(null)
  const [horizon, setHorizon] = useState(7)
  const [loading, setLoading] = useState(true)
  const [auto, setAuto] = useState(true)
  // 用 ref 承载最新 horizon，让轮询定时器无需随 horizon 重建即可取当前值
  const horizonRef = useRef(horizon)
  horizonRef.current = horizon

  const load = useCallback((h: number) => {
    setLoading(true)
    getFundingMatrix(h, true, -1e9)
      .then(setData)
      .finally(() => setLoading(false))
    // 采集健康度独立拉取，失败不影响矩阵展示
    getFundingCollectorStatus().then(setCollector).catch(() => {})
  }, [])

  // horizon 变化 / 首次挂载：立即拉取
  useEffect(() => { load(horizon) }, [horizon, load])

  // 自动轮询：每 30s 刷新一次；页面隐藏时暂停，重新可见时立即补一次
  useEffect(() => {
    if (!auto) return
    const tick = () => {
      if (typeof document !== 'undefined' && document.hidden) return
      load(horizonRef.current)
    }
    const timer = window.setInterval(tick, AUTO_REFRESH_MS)
    const onVisible = () => { if (!document.hidden) load(horizonRef.current) }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [auto, load])

  const venueCols = data ? Object.keys(data.venues || {}).sort() : []
  const combos = data?.combos || []

  return (
    <div className="rounded-xl border border-border bg-card p-4 mb-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <h3 className="font-semibold flex items-center gap-2">
          <Layers className="w-4 h-4 text-blue-500" />
          实时资金费矩阵 · delta-neutral 净EV
          {data && (
            <span className="text-xs font-normal text-muted-foreground">
              {data.venue_count} 场所 · {data.symbol_count} symbol · {data.combo_count} 组合
              {data.as_of ? ` · ${new Date(data.as_of * 1000).toLocaleTimeString()}` : ''}
            </span>
          )}
        </h3>
        <div className="flex items-center gap-2">
          {/* 持有期切换：影响净年化与保本口径 */}
          <div className="flex rounded-lg border border-border overflow-hidden text-xs">
            {HORIZONS.map(h => (
              <button
                key={h}
                onClick={() => setHorizon(h)}
                className={cn(
                  'px-2.5 py-1 transition-colors',
                  horizon === h ? 'bg-blue-600 text-white' : 'hover:bg-secondary text-muted-foreground',
                )}
              >
                {h}天
              </button>
            ))}
          </div>
          {/* 自动刷新开关（30s；页面隐藏时暂停） */}
          <button
            onClick={() => setAuto(a => !a)}
            title={auto ? '自动刷新已开启（每 30s，页面隐藏时暂停）' : '自动刷新已关闭'}
            className={cn(
              'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs transition-colors border',
              auto
                ? 'bg-green-500/15 text-green-700 dark:text-green-300 border-green-500/30'
                : 'bg-secondary text-muted-foreground border-border hover:bg-secondary/80',
            )}
          >
            <span className={cn('w-1.5 h-1.5 rounded-full', auto ? 'bg-green-500 animate-pulse' : 'bg-muted-foreground/50')} />
            自动 30s
          </button>
          <button
            onClick={() => load(horizon)}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-secondary hover:bg-secondary/80 text-secondary-foreground text-xs transition-colors"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
            刷新
          </button>
        </div>
      </div>

      {/* 采集器健康度：各场所连通状态一目了然（运维视角） */}
      {collector && (
        <div className="mb-3 rounded-lg border border-border bg-secondary/30 p-2.5">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="flex items-center gap-1.5 text-muted-foreground font-medium">
              <Activity className="w-3.5 h-3.5" />
              采集器
            </span>
            {collector.enabled ? (
              <span className="inline-block px-1.5 py-0.5 rounded-full bg-green-500/15 text-green-700 dark:text-green-300 text-[10px] border border-green-500/30">
                已启用 · {collector.interval_seconds ?? '?'}s
              </span>
            ) : (
              <span className="inline-block px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground text-[10px] border border-border" title="MULTI_VENUE_FUNDING_COLLECTOR_ENABLED=false，需运维在有网环境开启">
                未启用
              </span>
            )}
            {!collector.has_report ? (
              <span className="text-[10px] text-muted-foreground/70">尚无采集快照（等首轮采集）</span>
            ) : (
              <>
                {Object.entries(collector.venue_report).map(([v, d]) => {
                  const st = VENUE_STATUS_STYLE[d.status] || VENUE_STATUS_STYLE.unknown
                  const fails = collector.consecutive_failures?.[v] ?? 0
                  const alerted = (collector.alerted_venues || []).includes(v)
                  return (
                    <span
                      key={v}
                      className={cn('inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] border', st.cls)}
                      title={[
                        `状态: ${d.status}(${st.label})`,
                        d.count != null ? `symbol数: ${d.count}` : '',
                        d.elapsed_ms != null ? `耗时: ${d.elapsed_ms}ms` : '',
                        d.via ? `方式: ${d.via}` : '',
                        d.error ? `错误: ${d.error}` : '',
                        fails > 0 ? `连续失败: ${fails}轮` : '',
                        alerted ? '已飞书告警(未恢复)' : '',
                      ].filter(Boolean).join(' · ')}
                    >
                      {alerted && <AlertTriangle className="w-3 h-3" />}
                      {v}
                      {d.status === 'ok' && d.count != null ? `·${d.count}` : ''}
                      {fails > 0 ? `·失${fails}` : ''}
                    </span>
                  )
                })}
                {collector.as_of_iso && (
                  <span className="text-[10px] text-muted-foreground/60 ml-auto">
                    {new Date(collector.as_of_iso).toLocaleTimeString()} · 写入{collector.rows_written ?? 0}行
                  </span>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* 数据不足提示（诚实） */}
      {data && !data.multi_venue && (
        <div className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 flex items-start gap-2 text-sm">
          <AlertTriangle className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" />
          <div className="text-amber-700 dark:text-amber-300">
            当前不足 2 个场所对同一 symbol 有资金费率，无法凑齐 delta-neutral 双腿。
            启用多场所采集器（MULTI_VENUE_FUNDING_COLLECTOR_ENABLED）补齐第二场所后自动出现机会。
          </div>
        </div>
      )}

      {loading && !data ? (
        <div className="text-sm text-muted-foreground py-6 text-center">加载中…</div>
      ) : (
        <>
          {/* 1) 净EV机会 */}
          {combos.length > 0 && (
            <div className="mb-4">
              <div className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1.5">
                <TrendingUp className="w-3.5 h-3.5" />
                净EV机会（持有 {data?.horizon_days ?? horizon} 天 · taker 费保守估计）
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted-foreground border-b border-border">
                      <th className="text-left py-1.5 pr-3">Symbol</th>
                      <th className="text-left py-1.5 pr-3">长腿 → 空腿</th>
                      <th className="text-right py-1.5 pr-3">毛年化</th>
                      <th className="text-right py-1.5 pr-3">净年化@持有期</th>
                      <th className="text-right py-1.5 pr-3">保本天数</th>
                      <th className="text-right py-1.5 pr-3">SDN持有/净APR</th>
                      <th className="text-center py-1.5 pr-3">SDN可行</th>
                      <th className="text-left py-1.5">积分</th>
                    </tr>
                  </thead>
                  <tbody>
                    {combos.map((c, i) => {
                      const netPos = (c.net_apr_at_horizon ?? 0) > 0
                      return (
                        <tr key={`${c.symbol}-${i}`} className="border-b border-border/50 hover:bg-secondary/40">
                          <td className="py-1.5 pr-3 font-medium">{c.symbol}</td>
                          <td className="py-1.5 pr-3">
                            <span className="text-green-600 dark:text-green-400">{c.long_exchange}</span>
                            <span className="text-muted-foreground"> → </span>
                            <span className="text-red-600 dark:text-red-400">{c.short_exchange}</span>
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">{pctApr(c.gross_funding_apr)}</td>
                          <td className={cn('py-1.5 pr-3 text-right tabular-nums font-medium', netPos ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400')}>
                            {pctApr(c.net_apr_at_horizon)}
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums text-muted-foreground">
                            {c.breakeven_days == null ? '—' : `${c.breakeven_days.toFixed(1)}d`}
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums text-muted-foreground">
                            {c.sdn_horizon_days == null ? '—' : (
                              <span title={c.sdn_horizon_adaptive ? '保本期超默认窗口，SDN 已自适应延长持有' : '未触发自适应'}>
                                {c.sdn_horizon_days.toFixed(1)}d
                                {c.sdn_horizon_adaptive ? '*' : ''}
                                {' · '}
                                <span className={cn((c.sdn_net_apr ?? 0) > 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400')}>
                                  {pctApr(c.sdn_net_apr)}
                                </span>
                              </span>
                            )}
                          </td>
                          <td className="py-1.5 pr-3 text-center">
                            {c.sdn_viable == null ? (
                              <span className="text-muted-foreground/60 text-[10px]">—</span>
                            ) : c.sdn_viable ? (
                              <span className="inline-block px-1.5 py-0.5 rounded-full bg-green-500/15 text-green-700 dark:text-green-300 text-[10px] border border-green-500/30">可行</span>
                            ) : (
                              <span className="inline-block px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground text-[10px] border border-border" title={`未达 SDN 净年化阈值 ${pctApr(c.sdn_min_net_apr)}`}>不可行</span>
                            )}
                          </td>
                          <td className="py-1.5">
                            {c.points_long_leg ? (
                              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-blue-500/15 text-blue-700 dark:text-blue-300 text-[10px] border border-blue-500/30">
                                <Coins className="w-3 h-3" />{c.points_program_id || '积分'}
                              </span>
                            ) : (
                              <span className="text-muted-foreground/60 text-[10px]">—</span>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <div className="text-[10px] text-muted-foreground/70 mt-1">
                净年化 = 持有期内净资金费累计 − 一次性手续费，再年化；为负表示该持有期摊不平手续费（可切更长持有期）。
                「SDN持有/净APR」= 策略按保本期自适应选的持有天数（<code>*</code>=已延长）及其净年化；「SDN可行」需净年化达到策略阈值。
              </div>
            </div>
          )}

          {/* 2) 资金费矩阵 */}
          {data && data.matrix.length > 0 ? (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-2">资金费率矩阵（结算周期口径）</div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted-foreground border-b border-border">
                      <th className="text-left py-1.5 pr-3">Symbol</th>
                      {venueCols.map(v => (
                        <th key={v} className="text-right py-1.5 pr-3">{v}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.matrix.map(row => (
                      <tr key={row.symbol} className="border-b border-border/50 hover:bg-secondary/40">
                        <td className="py-1.5 pr-3 font-medium">{row.symbol}</td>
                        {venueCols.map(v => {
                          const r = row.venues[v]
                          if (r == null) return <td key={v} className="py-1.5 pr-3 text-right text-muted-foreground/40">—</td>
                          const pos = r >= 0
                          return (
                            <td key={v} className={cn('py-1.5 pr-3 text-right tabular-nums', pos ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400')}>
                              {pctRate(r)}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            !loading && (
              <div className="text-sm text-muted-foreground py-6 text-center">暂无资金费数据</div>
            )
          )}
        </>
      )}
    </div>
  )
}
