import React, { useMemo } from 'react'
import {
  Activity, ArrowDownRight, ArrowUpRight, Brain, DollarSign,
  ShieldCheck, Star, Target, TrendingUp, Wallet, History,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ArbitragePaperDashboard as DashboardData, ArbitragePaperTradeRecord, RebatePosition } from '@/lib/arbitrageApi'
import { EXCHANGE_LABELS } from './ExchangeAllocationGrid'

const STATUS_LABEL: Record<string, string> = {
  trading: '交易中',
  ready: '待命',
  empty: '未分配',
}

const STATUS_TONE: Record<string, string> = {
  trading: 'bg-green-500/15 text-green-700 dark:text-green-300 border-green-500/30',
  ready: 'bg-blue-500/15 text-blue-700 dark:text-blue-300 border-blue-500/30',
  empty: 'bg-muted text-muted-foreground border-border',
}


export default function ArbitragePaperDashboard({
  data,
}: {
  data: DashboardData
}) {
  const { summary, session, exchanges, positions, trade_records, account, s8_report } = data
  const records = useMemo(
    () => mergeTradeRecords(trade_records ?? [], positions ?? []),
    [trade_records, positions],
  )
  const tradingExchanges = exchanges.filter(e => e.exchange !== 'reserve' && e.allocated_usd > 0)
  const reserve = exchanges.find(e => e.exchange === 'reserve')
  const s8Main = s8_report?.positions?.[0]
  const s8Metrics = s8Main?.rh_metrics
  const s8Stage6 = s8Metrics?.stage6
  const s8Wash = s8_report?.wash_safety
  const s8CumulativePoints = s8_report?.cumulative_points ?? 0
  const s8LastClosed = s8_report?.last_closed ?? []
  const exp = s8_report?.experiment_metrics
  const mem = s8_report?.learning_memory
  const cashPerPoint = exp?.cash_per_point ?? summary.cash_per_point
  const netExperiment = exp?.net_experiment_pnl ?? summary.net_experiment_pnl
  const recoveryMode = Boolean(exp?.recovery_mode ?? summary.recovery_mode)
  const paperAdvisory = Boolean(mem?.gate?.paper_advisory ?? exp?.paper_advisory)

  return (
    <div className="space-y-4">
      {session?.running && (
        <div className="rounded-xl border border-green-500/30 bg-green-500/10 px-4 py-3 text-sm flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="font-medium text-green-700 dark:text-green-300 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
            Paper 验证运行中
          </span>
          <span className="text-muted-foreground">
            策略 {session.strategies?.join(' / ')} · 每 {session.interval_seconds ?? 90}s 扫描
            {session.last_tick ? ` · ${session.last_tick.viable_count ?? 0} 个可行机会` : ''}
          </span>
          {session.last_tick?.auto_exec_error && (
            <span className="text-amber-600 dark:text-amber-400 text-xs">
              上轮未开仓：{session.last_tick.auto_exec_error}
            </span>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <StatCard label="总权益" value={`$${summary.total_equity.toFixed(2)}`} icon={<DollarSign className="w-4 h-4" />} tone="blue" />
        <StatCard label="可用余额" value={`$${summary.available_balance.toFixed(2)}`} icon={<Wallet className="w-4 h-4" />} tone="green" />
        <StatCard label="冻结资金" value={`$${summary.frozen_balance.toFixed(2)}`} icon={<Activity className="w-4 h-4" />} tone="amber" />
        <StatCard
          label="现金账（已实现）"
          value={`${summary.realized_pnl >= 0 ? '+' : ''}$${summary.realized_pnl.toFixed(2)}`}
          icon={summary.realized_pnl >= 0 ? <TrendingUp className="w-4 h-4" /> : <ArrowDownRight className="w-4 h-4" />}
          tone={summary.realized_pnl >= 0 ? 'green' : 'red'}
        />
        <StatCard
          label="净实验收益"
          value={netExperiment != null ? `${netExperiment >= 0 ? '+' : ''}$${netExperiment.toFixed(2)}` : '—'}
          sub="现金 + 积分×学习折扣"
          icon={<Target className="w-4 h-4" />}
          tone={(netExperiment ?? 0) >= 0 ? 'green' : 'red'}
        />
        <StatCard
          label="cash / 积分"
          value={cashPerPoint != null ? `${cashPerPoint >= 0 ? '+' : ''}$${cashPerPoint.toFixed(4)}` : '—'}
          sub={recoveryMode ? 'recovery：短持≤3x' : '每分积分对应现金'}
          icon={<Star className="w-4 h-4" />}
          tone={(cashPerPoint ?? 0) >= 0 ? 'purple' : 'red'}
        />
        <StatCard
          label="活跃仓位"
          value={`${summary.active_positions}`}
          sub={`名义 $${summary.position_notional_usd.toFixed(0)} · 浮盈 ${summary.unrealized_pnl >= 0 ? '+' : ''}$${summary.unrealized_pnl.toFixed(2)}`}
          icon={<Target className="w-4 h-4" />}
          tone="blue"
        />
      </div>

      {(summary.total_fees_paid ?? 0) > 0 && (
        <div className="flex flex-wrap gap-4 text-xs text-muted-foreground px-1">
          <span>累计手续费 ${(summary.total_fees_paid ?? 0).toFixed(4)}</span>
          <span>累计返佣 +${(summary.total_rebates_received ?? 0).toFixed(4)}</span>
          <span>滑点成本 ${(summary.total_slippage_cost ?? 0).toFixed(4)}</span>
        </div>
      )}

      {s8_report && (
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
            <div className="flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-purple-500" />
              <h3 className="font-semibold text-sm">S8 Stage 6 积分引擎</h3>
              <span className={cn(
                'text-[11px] px-2 py-0.5 rounded-full border',
                recoveryMode && !paperAdvisory
                  ? 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300'
                  : paperAdvisory
                    ? 'border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-300'
                    : s8_report.mode === 'stage6_optimal'
                    ? 'border-purple-500/40 bg-purple-500/10 text-purple-600 dark:text-purple-300'
                    : 'border-border bg-muted/40 text-muted-foreground',
              )}>
                {paperAdvisory ? '学习中' : recoveryMode ? 'recovery' : (s8_report.mode || 'safe')}
              </span>
            </div>
            <span className="text-xs text-muted-foreground">{s8_report.recommendation}</span>
          </div>
          {exp && (
            <div className="mb-3 rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-[11px] text-muted-foreground">
              净实验收益 <span className={cn('font-semibold', (exp.net_experiment_pnl ?? 0) >= 0 ? 'text-green-600' : 'text-red-600')}>
                {(exp.net_experiment_pnl ?? 0) >= 0 ? '+' : ''}${(exp.net_experiment_pnl ?? 0).toFixed(2)}
              </span>
              {' · '}cash/pt <span className={cn('font-semibold', (exp.cash_per_point ?? 0) >= 0 ? 'text-foreground' : 'text-red-600')}>
                {(exp.cash_per_point ?? 0) >= 0 ? '+' : ''}${(exp.cash_per_point ?? 0).toFixed(4)}
              </span>
              {exp.recovery_mode && !paperAdvisory ? ' · 悲观 EV≤0 或中性/低置信度将跳过开仓' : paperAdvisory ? ' · Paper 继续开仓收样本' : null}
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <MiniMetric label="预估本轮积分" value={s8Metrics?.estimated_rh != null ? `+${s8Metrics.estimated_rh.toFixed(1)}` : '—'} />
            <MiniMetric
              label="单轮净 EV"
              value={s8Metrics?.net_ev_usd != null ? `${s8Metrics.net_ev_usd >= 0 ? '+' : ''}$${s8Metrics.net_ev_usd.toFixed(4)}` : '—'}
              sub={s8Metrics?.points_value_usd != null ? `积分估值 $${s8Metrics.points_value_usd.toFixed(4)}（投机性折扣后）` : undefined}
            />
            <MiniMetric
              label="单轮成本"
              value={s8Metrics?.estimated_cost_usd != null ? `$${s8Metrics.estimated_cost_usd.toFixed(4)}` : '—'}
              sub={s8Metrics?.funding_cost_usd != null ? `含资金费 $${s8Metrics.funding_cost_usd.toFixed(4)}` : undefined}
            />
            <MiniMetric
              label="Maker 占比"
              value={s8Stage6?.maker_ratio != null ? `${(s8Stage6.maker_ratio * 100).toFixed(0)}%` : '—'}
              sub={s8Stage6?.maker_volume_usd != null
                ? `Maker $${s8Stage6.maker_volume_usd.toFixed(0)} / Taker $${(s8Stage6.taker_volume_usd ?? 0).toFixed(0)}`
                : undefined}
            />
            <MiniMetric label="成交量预算" value={s8Metrics?.round_volume_usd != null ? `$${s8Metrics.round_volume_usd.toFixed(0)}` : '—'} />
            <MiniMetric label="质量分" value={s8Metrics?.round_quality_score != null ? `${s8Metrics.round_quality_score.toFixed(0)}/100` : '—'} />
            <MiniMetric
              label="今日 Asterdex 余量"
              value={s8Wash?.remaining_daily_volume_usd != null ? `$${s8Wash.remaining_daily_volume_usd.toFixed(0)}` : '—'}
              sub={s8Wash?.max_daily_volume_usd != null ? `上限 $${s8Wash.max_daily_volume_usd.toFixed(0)}` : undefined}
            />
            <MiniMetric
              label="刷量安全"
              value={s8Wash?.daily_budget_ok === false ? '预算不足' : s8Wash?.timing_ok === false ? `等 ${s8Wash.wait_seconds?.toFixed(0) ?? 0}s` : '可交易'}
              sub={s8Wash?.pattern_score != null ? `pattern ${s8Wash.pattern_score.toFixed(2)}` : undefined}
            />
          </div>
          {s8Stage6 && (
            <div className="mt-3 pt-3 border-t border-border/50">
              <div className="text-[11px] text-muted-foreground mb-2">
                Stage 6 积分类别拆分（团队加成 {s8Stage6.team_boost ?? 1}x · 盈亏积分按 0 保守估）
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                <div className="rounded-lg bg-muted/30 px-3 py-2 text-[11px]">
                  <div className="text-muted-foreground">交易积分</div>
                  <div className="font-semibold text-foreground mt-0.5">{(s8Stage6.trading_points ?? 0).toFixed(2)}</div>
                </div>
                <div className="rounded-lg bg-muted/30 px-3 py-2 text-[11px]">
                  <div className="text-muted-foreground">持仓积分</div>
                  <div className="font-semibold text-foreground mt-0.5">{(s8Stage6.position_points ?? 0).toFixed(2)}</div>
                </div>
                <div className="rounded-lg bg-muted/30 px-3 py-2 text-[11px]">
                  <div className="text-muted-foreground">资产积分 (USDF 全仓)</div>
                  <div className="font-semibold text-foreground mt-0.5">{(s8Stage6.asset_points ?? 0).toFixed(2)}</div>
                </div>
                <div className="rounded-lg bg-muted/30 px-3 py-2 text-[11px]">
                  <div className="text-muted-foreground">盈亏积分</div>
                  <div className="font-semibold text-foreground mt-0.5">{(s8Stage6.pnl_points ?? 0).toFixed(2)}</div>
                </div>
              </div>
            </div>
          )}
          {mem && (
            <div className="mt-3 pt-3 border-t border-border/50">
              <div className="flex items-center gap-2 mb-2">
                <Brain className="w-3.5 h-3.5 text-violet-500" />
                <span className="text-[11px] font-medium text-foreground">学习记忆</span>
                <span className={cn(
                  'text-[10px] px-1.5 py-0.5 rounded border',
                  mem.engine_status === 'paper_learning'
                    ? 'border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300'
                    : mem.engine_status === 'learning_active'
                    ? 'border-green-500/30 bg-green-500/10 text-green-700 dark:text-green-300'
                    : mem.engine_status === 'collecting'
                      ? 'border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300'
                      : 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300',
                )}>
                  {mem.engine_status === 'paper_learning' ? '收样本' : mem.engine_status === 'learning_active' ? '学习中' : mem.engine_status === 'collecting' ? '收样本' : '门禁暂停'}
                </span>
                {mem.samples != null && (
                  <span className="text-[10px] text-muted-foreground">{mem.samples} 轮样本</span>
                )}
              </div>
              <p className="text-[11px] text-muted-foreground mb-2">{mem.status_note}</p>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2">
                <div className="rounded-lg bg-muted/30 px-2 py-1.5 text-[10px]">
                  <div className="text-muted-foreground">已学折扣</div>
                  <div className="font-semibold">{mem.learned?.speculative_discount != null ? mem.learned.speculative_discount.toFixed(2) : '—'}</div>
                </div>
                <div className="rounded-lg bg-muted/30 px-2 py-1.5 text-[10px]">
                  <div className="text-muted-foreground">已学持仓</div>
                  <div className="font-semibold">{mem.learned?.stage6_hold_default_hours != null ? `${mem.learned.stage6_hold_default_hours}h` : '—'}</div>
                </div>
                <div className="rounded-lg bg-muted/30 px-2 py-1.5 text-[10px]">
                  <div className="text-muted-foreground">回看窗口</div>
                  <div className="font-semibold">{mem.lookback_days ?? 14} 天</div>
                </div>
                <div className="rounded-lg bg-muted/30 px-2 py-1.5 text-[10px]">
                  <div className="text-muted-foreground">记忆更新</div>
                  <div className="font-semibold truncate" title={mem.updated_at ?? ''}>
                    {mem.updated_at ? mem.updated_at.slice(0, 16).replace('T', ' ') : '—'}
                  </div>
                </div>
              </div>
              {mem.hold_buckets && mem.hold_buckets.some(b => (b.samples ?? 0) > 0) && (
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {mem.hold_buckets.filter(b => (b.samples ?? 0) > 0).map(b => (
                    <span
                      key={b.label}
                      className={cn(
                        'text-[10px] px-2 py-0.5 rounded-full border',
                        b.is_best ? 'border-violet-500/40 bg-violet-500/10 text-violet-700 dark:text-violet-300' : 'border-border bg-muted/20',
                      )}
                    >
                      {b.label} · n={b.samples} · {b.score_per_hour != null ? `${b.score_per_hour}/h` : '—'}
                      {b.is_best ? ' ★' : ''}
                    </span>
                  ))}
                </div>
              )}
              {mem.recent_rounds && mem.recent_rounds.length > 0 && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
                  {mem.recent_rounds.slice(0, 6).map((row, idx) => (
                    <div key={`${row.symbol}-${idx}`} className="rounded-lg bg-muted/20 px-2 py-1.5 text-[10px] flex justify-between gap-2">
                      <span className="truncate">
                        {row.symbol} · {row.direction ?? '?'} · +{row.points?.toFixed(1)} 分
                      </span>
                      <span className={cn('shrink-0', row.direction_correct ? 'text-green-600' : 'text-red-600')}>
                        {row.pnl_usd != null ? `${row.pnl_usd >= 0 ? '+' : ''}$${row.pnl_usd.toFixed(2)}` : '—'}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              <p className="text-[10px] text-muted-foreground mt-2">
                记忆写入：每轮平仓 → 参数文件 + AI 选币 prompt（recent_rounds）+ 统一学习闭环
              </p>
            </div>
          )}
          {!s8_report.active && s8CumulativePoints > 0 && (
            <div className="mt-3 pt-3 border-t border-border/50">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                <div className="text-[11px] text-muted-foreground">
                  已结算积分（历史成交写入 performance_logs，非本轮预估）
                </div>
                <div className="text-sm font-semibold text-purple-600 dark:text-purple-300">
                  累计 +{s8CumulativePoints.toFixed(1)} Rh
                  {s8_report.cumulative_points_value_usd != null && (
                    <span className="text-xs font-normal text-muted-foreground ml-2">
                      ≈ ${s8_report.cumulative_points_value_usd.toFixed(2)}
                    </span>
                  )}
                </div>
              </div>
              {s8LastClosed.length > 0 && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {s8LastClosed.slice(0, 4).map(row => (
                    <div key={row.position_id} className="rounded-lg bg-muted/30 px-3 py-2 text-[11px]">
                      <div className="font-medium truncate">{row.position_id.replace('rebate_S8_', '')}</div>
                      <div className="text-muted-foreground mt-0.5">
                        +{row.points.toFixed(1)} 积分 · 持仓 {row.hold_hours.toFixed(1)}h · PnL {row.pnl >= 0 ? '+' : ''}${row.pnl.toFixed(2)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {s8Main?.paper_ab_test_matrix && s8Main.paper_ab_test_matrix.length > 0 && (
            <div className="mt-3 pt-3 border-t border-border/50">
              <div className="text-[11px] text-muted-foreground mb-2">Paper A/B 持仓实验：比较不同持仓时长的单位积分效率（Stage6 主模式为 2-8h 动态持仓）</div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                {s8Main.paper_ab_test_matrix.slice(0, 4).map((row, idx) => (
                  <div key={idx} className="rounded-lg bg-muted/30 px-3 py-2 text-[11px]">
                    <div className="font-medium text-foreground">{Number(row.hold_seconds || 0) / 60} 分钟</div>
                    <div className="text-muted-foreground mt-0.5">
                      Rh {Number(row.estimated_rh || 0).toFixed(1)} · 效率 {Number(row.rh_per_margin_hour || 0).toFixed(2)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold">各交易所分账户</h3>
          <span className="text-xs text-muted-foreground">
            账户状态 · {account.status === 'running' ? '验证运行中' : '待命'} · 模板 {account.allocation_preset || '自定义'}
          </span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {tradingExchanges.map(ex => (
            <ExchangeDetailCard key={ex.exchange} row={ex} />
          ))}
          {reserve && reserve.allocated_usd > 0 && (
            <ExchangeDetailCard key="reserve" row={reserve} isReserve />
          )}
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card">
        <div className="px-4 py-3 border-b border-border flex items-center gap-2">
          <Target className="w-4 h-4 text-blue-500" />
          <h3 className="font-semibold text-sm">Paper 积分仓位</h3>
          <span className="text-xs text-muted-foreground">({positions.length})</span>
        </div>
        <div className="p-4 overflow-x-auto">
          {positions.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">
              暂无 Paper 仓位。启动「启动配置」后，系统会每 90 秒扫描并尝试 Paper 开仓。
            </p>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-muted-foreground">
                  <th className="text-left py-2 pr-3">策略</th>
                  <th className="text-left py-2 pr-3">交易所</th>
                  <th className="text-left py-2 pr-3">币对</th>
                  <th className="text-left py-2 pr-3">方向</th>
                  <th className="text-right py-2 pr-3">开仓价</th>
                  <th className="text-right py-2 pr-3">现价</th>
                  <th className="text-right py-2 pr-3">数量</th>
                  <th className="text-right py-2 pr-3">杠杆</th>
                  <th className="text-right py-2 pr-3">名义</th>
                  <th className="text-right py-2 pr-3">浮盈</th>
                  <th className="text-right py-2 pr-3">积分</th>
                  <th className="text-right py-2 pr-3">Rh 进度</th>
                  <th className="text-right py-2">持仓</th>
                </tr>
              </thead>
              <tbody>
                {positions.map(p => {
                  const openCost = p.open_cost as { fee_paid?: number; rebate_received?: number; slippage_cost_usd?: number } | undefined
                  return (
                  <React.Fragment key={p.position_id}>
                  <tr className="border-b border-border/40">
                    <td className="py-2.5 pr-3 font-medium">{p.strategy_type}</td>
                    <td className="py-2.5 pr-3">{EXCHANGE_LABELS[p.source_exchange?.toLowerCase()] || p.source_exchange}</td>
                    <td className="py-2.5 pr-3">{p.symbol || '—'}</td>
                    <td className="py-2.5 pr-3">
                      {p.side === 'buy' ? (
                        <span className="text-green-600">多</span>
                      ) : p.side === 'sell' ? (
                        <span className="text-red-600">空</span>
                      ) : '—'}
                    </td>
                    <td className="py-2.5 pr-3 text-right font-mono text-[11px]">
                      {p.entry_price != null ? `$${p.entry_price.toFixed(4)}` : '—'}
                    </td>
                    <td className="py-2.5 pr-3 text-right font-mono text-[11px]">
                      {p.mark_price != null ? `$${p.mark_price.toFixed(4)}` : '—'}
                    </td>
                    <td className="py-2.5 pr-3 text-right font-mono text-[11px]">
                      {p.size_coins_display || (p.size_coins != null ? p.size_coins.toFixed(6) : '—')}
                    </td>
                    <td className="py-2.5 pr-3 text-right text-muted-foreground">
                      {p.leverage ? `${p.leverage}x` : '—'}
                    </td>
                    <td className="py-2.5 pr-3 text-right">${p.side_a_size.toFixed(2)}</td>
                    <td className={cn('py-2.5 pr-3 text-right font-medium', p.current_pnl >= 0 ? 'text-green-600' : 'text-red-600')}>
                      {p.current_pnl >= 0 ? '+' : ''}{p.current_pnl.toFixed(2)}
                      {p.pnl_pct != null && (
                        <span className="text-[10px] text-muted-foreground ml-1">
                          ({p.pnl_pct >= 0 ? '+' : ''}{p.pnl_pct.toFixed(2)}%)
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 text-right">{p.accumulated_points.toFixed(1)}</td>
                    <td className="py-2.5 pr-3 text-right text-[11px]">
                      {p.strategy_type === 'S8' && p.rh_target_hours != null ? (
                        <div className="space-y-0.5">
                          <div className={cn(
                            p.rh_time_bonus_active ? 'text-green-600' : 'text-amber-600',
                          )}>
                            {p.rh_time_bonus_active
                              ? '1h 加成 ✓'
                              : `剩 ${(p.rh_hold_remaining_minutes ?? 0).toFixed(0)}m`}
                          </div>
                          <div className="text-muted-foreground">
                            目标 {p.rh_target_hours}h · {p.rh_hold_progress_pct?.toFixed(0) ?? 0}%
                          </div>
                          {p.estimated_round_rh != null && (
                            <div className="text-purple-600">
                              预估 +{p.estimated_round_rh.toFixed(1)} Rh
                              {p.symbol_boost && p.symbol_boost > 1 ? ` · Boost ${p.symbol_boost}x` : ''}
                            </div>
                          )}
                        </div>
                      ) : '—'}
                    </td>
                    <td className="py-2.5 text-right text-muted-foreground">
                      {p.hold_duration_hours < 0.1 && p.hold_duration_hours > 0
                        ? '<0.1h'
                        : `${p.hold_duration_hours.toFixed(1)}h`}
                    </td>
                  </tr>
                  {(openCost || p.price_source || p.funding_pnl != null) && (
                    <tr className="border-b border-border/20 last:border-0">
                      <td colSpan={13} className="pb-2.5 pt-0 px-0 text-[10px] text-muted-foreground leading-relaxed">
                        {p.margin_usd != null && p.margin_usd > 0 && (
                          <span className="mr-3">保证金 ${p.margin_usd.toFixed(2)}</span>
                        )}
                        {openCost?.fee_paid != null && <span className="mr-3">开仓费 ${openCost.fee_paid.toFixed(4)}</span>}
                        {openCost?.rebate_received != null && openCost.rebate_received > 0 && (
                          <span className="mr-3 text-green-600">返佣 +${openCost.rebate_received.toFixed(4)}</span>
                        )}
                        {openCost?.slippage_cost_usd != null && openCost.slippage_cost_usd > 0 && (
                          <span className="mr-3">滑点 ${openCost.slippage_cost_usd.toFixed(4)}</span>
                        )}
                        {p.funding_pnl != null && Math.abs(p.funding_pnl) > 0.0001 && (
                          <span className="mr-3">资金费 {p.funding_pnl >= 0 ? '+' : ''}${p.funding_pnl.toFixed(4)}</span>
                        )}
                        {p.spread_bps != null && p.spread_bps > 0 && (
                          <span className="mr-3">价差 {p.spread_bps.toFixed(1)}bps</span>
                        )}
                        {p.price_source && (
                          <span>价源 {p.quote_exchange || ''}{p.quote_exchange ? ' · ' : ''}{p.price_source}</span>
                        )}
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <History className="w-4 h-4 text-blue-500" />
          <h3 className="font-semibold text-sm">交易记录</h3>
          <span className="text-[11px] text-muted-foreground">
            共 {records.length} 笔 · 每仓一条
          </span>
        </div>
        {records.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4 text-center">暂无交易记录</p>
        ) : (
          <div className="overflow-x-auto max-h-80 overflow-y-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[11px] text-muted-foreground border-b border-border">
                  <th className="text-left py-2 pr-2 font-medium">时间</th>
                  <th className="text-left py-2 pr-2 font-medium">品种</th>
                  <th className="text-left py-2 pr-2 font-medium">方向</th>
                  <th className="text-right py-2 pr-2 font-medium">保证金</th>
                  <th className="text-right py-2 pr-2 font-medium">盈亏</th>
                  <th className="text-right py-2 pr-2 font-medium">获得积分</th>
                  <th className="text-left py-2 font-medium">状态</th>
                </tr>
              </thead>
              <tbody>
                {records.map(row => (
                  <tr key={row.position_id} className="border-b border-border/40 last:border-0 hover:bg-muted/20">
                    <td className="py-2.5 pr-2 text-xs whitespace-nowrap">
                      <div>{formatUnixTime(row.opened_at)}</div>
                      {row.closed_at && (
                        <div className="text-[10px] text-muted-foreground">
                          平 {formatUnixTime(row.closed_at)}
                          {row.hold_hours != null && ` · ${row.hold_hours}h`}
                        </div>
                      )}
                    </td>
                    <td className="py-2.5 pr-2 font-medium whitespace-nowrap">
                      {row.symbol}
                      <div className="text-[10px] text-muted-foreground font-normal">
                        {row.strategy_type} · {EXCHANGE_LABELS[row.exchange] || row.exchange}
                      </div>
                    </td>
                    <td className="py-2.5 pr-2 whitespace-nowrap">
                      <span className={cn(
                        'text-xs px-1.5 py-0.5 rounded',
                        row.side === '多' ? 'bg-green-500/10 text-green-700' : 'bg-red-500/10 text-red-700',
                      )}>
                        {row.side} {row.leverage}x
                      </span>
                    </td>
                    <td className="py-2.5 pr-2 text-right tabular-nums whitespace-nowrap">
                      ${row.margin_usd.toFixed(2)}
                      <div className="text-[10px] text-muted-foreground">名义 ${row.notional_usd.toFixed(0)}</div>
                    </td>
                    <td className={cn(
                      'py-2.5 pr-2 text-right font-semibold tabular-nums whitespace-nowrap',
                      row.realized_pnl >= 0 ? 'text-green-600' : 'text-red-600',
                    )}>
                      {`${row.realized_pnl >= 0 ? '+' : ''}$${row.realized_pnl.toFixed(2)}`}
                      {row.status === '持仓中' && (
                        <div className="text-[10px] text-muted-foreground font-normal">浮动</div>
                      )}
                      {row.fees_usd > 0 && (
                        <div className="text-[10px] text-muted-foreground font-normal">费 ${row.fees_usd.toFixed(2)}</div>
                      )}
                    </td>
                    <td className="py-2.5 pr-2 text-right tabular-nums whitespace-nowrap">
                      <div className="font-semibold text-purple-600">
                        +{row.points_earned.toFixed(2)}
                      </div>
                      {row.status === '持仓中' && row.estimated_round_rh != null && (
                        <div className="text-[10px] text-muted-foreground">
                          满轮约 {Number(row.estimated_round_rh).toFixed(1)}
                        </div>
                      )}
                    </td>
                    <td className="py-2.5 text-xs whitespace-nowrap">
                      <span className={cn(
                        'px-1.5 py-0.5 rounded',
                        row.status === '持仓中'
                          ? 'bg-blue-500/10 text-blue-700'
                          : 'bg-muted text-muted-foreground',
                      )}>
                        {row.status}
                      </span>
                      {row.close_reason && row.status === '已平仓' && (
                        <div className="text-[10px] text-muted-foreground mt-0.5 max-w-[88px] truncate" title={row.close_reason}>
                          {row.close_reason}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function formatUnixTime(ts?: number | null) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** API 交易记录为空时，用上方活跃仓位补一条，避免「有仓无记录」。 */
function mergeTradeRecords(
  tradeRecords: ArbitragePaperTradeRecord[],
  positions: RebatePosition[],
): ArbitragePaperTradeRecord[] {
  if (tradeRecords.length > 0) return tradeRecords
  return positions.map(p => {
    const lev = Number(p.leverage ?? 10)
    const notional = Number(p.side_a_size ?? 0)
    let margin = Number(p.margin_usd ?? 0)
    if (margin <= 0 && notional > 0) margin = notional / Math.max(lev, 1)
    const sideRaw = String(p.side ?? '')
    const side = sideRaw === 'buy' ? '多' : sideRaw === 'sell' ? '空' : '—'
    const isOpen = ['active', 'holding'].includes(String(p.status ?? '').toLowerCase())
    const estimatedRh = p.rh_metrics?.estimated_rh ?? null
    return {
      position_id: p.position_id,
      symbol: p.symbol,
      strategy_type: p.strategy_type,
      exchange: p.source_exchange,
      side,
      leverage: lev,
      margin_usd: margin,
      notional_usd: notional,
      status: isOpen ? '持仓中' : '已平仓',
      opened_at: p.entry_time ?? null,
      closed_at: null,
      hold_hours: p.hold_duration_hours ?? null,
      realized_pnl: Number(p.current_pnl ?? 0),
      fees_usd: Number(p.open_fees_paid ?? 0),
      points_earned: Number(p.accumulated_points ?? 0),
      estimated_round_rh: estimatedRh,
      close_reason: null,
    }
  })
}

function ExchangeDetailCard({
  row,
  isReserve,
}: {
  row: DashboardData['exchanges'][number]
  isReserve?: boolean
}) {
  const label = EXCHANGE_LABELS[row.exchange] || row.exchange
  const limits = Object.entries(row.strategy_limits || {}).filter(([, v]) => Number(v) > 0)

  return (
    <div className={cn(
      'rounded-xl border bg-card p-4 flex flex-col gap-3',
      isReserve ? 'border-amber-500/30 bg-amber-500/5' : 'border-border',
    )}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-semibold">{label}</div>
          <div className="text-xs text-muted-foreground mt-0.5">
            分配 ${row.allocated_usd.toFixed(0)} · 已用 {row.utilization_pct.toFixed(0)}%
          </div>
        </div>
        <span className={cn('text-[10px] px-2 py-0.5 rounded-full border font-medium', STATUS_TONE[row.status] || STATUS_TONE.ready)}>
          {STATUS_LABEL[row.status] || row.status}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 text-xs">
        <MiniMetric label="可用" value={`$${row.available_usd.toFixed(0)}`} />
        <MiniMetric label="冻结" value={`$${row.frozen_usd.toFixed(0)}`} />
        <MiniMetric label="仓位" value={`${row.active_positions}`} />
      </div>

      {!isReserve && (
        <div className="grid grid-cols-2 gap-2 text-xs rounded-lg bg-muted/30 p-2.5">
          <div>
            <div className="text-muted-foreground">交易名义</div>
            <div className="font-semibold mt-0.5">${row.position_notional_usd.toFixed(2)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">浮盈</div>
            <div className={cn('font-semibold mt-0.5', row.unrealized_pnl >= 0 ? 'text-green-600' : 'text-red-600')}>
              {row.unrealized_pnl >= 0 ? '+' : ''}${row.unrealized_pnl.toFixed(2)}
            </div>
          </div>
          <div>
            <div className="text-muted-foreground">累计积分</div>
            <div className="font-semibold mt-0.5">{(row.points_earned_total ?? row.accumulated_points).toFixed(1)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">积分估值</div>
            <div className="font-semibold mt-0.5">${row.estimated_value_usd.toFixed(2)}</div>
          </div>
        </div>
      )}

      {limits.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {limits.map(([sid, ratio]) => (
            <span key={sid} className="text-[10px] px-2 py-0.5 rounded bg-background border border-border">
              {sid} {(Number(ratio) * 100).toFixed(0)}%
            </span>
          ))}
        </div>
      )}

      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className={cn('h-full rounded-full', isReserve ? 'bg-amber-500' : 'bg-blue-500')}
          style={{ width: `${Math.min(row.utilization_pct, 100)}%` }}
        />
      </div>
    </div>
  )
}

function MiniMetric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg bg-muted/25 px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="font-semibold text-foreground mt-0.5">{value}</div>
      {sub && <div className="text-[10px] text-muted-foreground mt-0.5">{sub}</div>}
    </div>
  )
}

function StatCard({
  label, value, sub, icon, tone,
}: {
  label: string
  value: string
  sub?: string
  icon: React.ReactNode
  tone: 'blue' | 'green' | 'amber' | 'red' | 'purple'
}) {
  const toneClass = {
    blue: 'border-blue-500/20 bg-blue-500/5',
    green: 'border-green-500/20 bg-green-500/5',
    amber: 'border-amber-500/20 bg-amber-500/5',
    red: 'border-red-500/20 bg-red-500/5',
    purple: 'border-purple-500/20 bg-purple-500/5',
  }[tone]

  return (
    <div className={cn('rounded-xl border p-3', toneClass)}>
      <div className="flex items-center justify-between text-muted-foreground">
        <span className="text-[11px]">{label}</span>
        {icon}
      </div>
      <div className="text-lg font-bold mt-1">{value}</div>
      {sub && <div className="text-[10px] text-muted-foreground mt-0.5">{sub}</div>}
    </div>
  )
}

