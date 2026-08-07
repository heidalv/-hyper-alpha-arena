/**
 * LLM / DeepSeek 计费统计面板
 * 数据来源: GET /api/llm-usage/billing
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Calendar, ChevronDown, ChevronRight, Coins, Cpu,
  ExternalLink, Hash, RefreshCw, TrendingUp, User, Zap,
} from 'lucide-react'
import { cn } from '@/lib/utils'

type BillingTab = 'overview' | 'daily' | 'modules' | 'traders' | 'models' | 'calls' | 'pricing' | 'recent'

interface BillingSummary {
  total_calls: number
  failed_calls: number
  success_calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost_usd: number
  cost_cny: number
  avg_cost_cny_per_call: number
  today_cny?: number
  today_calls?: number
  yesterday_cny?: number
  yesterday_calls?: number
  avg_daily_cny?: number
  avg_active_day_cny?: number
  active_days?: number
  peak_day?: { date: string; cost_cny: number; calls: number; tokens: number } | null
}

interface BillingModelRow {
  provider: string
  model: string
  total_calls: number
  failed_calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost_usd: number
  cost_cny: number
  avg_duration_ms: number
  pricing_usd_per_1m: { input: number; output: number }
}

interface BillingTrader {
  account_id: number | null
  account_name: string
  total_calls: number
  failed_calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost_usd: number
  cost_cny: number
  models: Array<{
    provider: string
    model: string
    calls: number
    prompt_tokens: number
    completion_tokens: number
    tokens: number
    cost_usd: number
    cost_cny: number
  }>
  call_types: Array<{
    call_type: string
    calls: number
    tokens: number
    cost_usd: number
    cost_cny: number
  }>
  daily: BillingDaily[]
}

interface BillingDaily {
  date: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  tokens: number
  cost_usd: number
  cost_cny: number
}

interface DeepSeekOfficialModel {
  model_id: string
  display_name: string
  aliases: string[]
  context_length: string
  max_output_tokens: string
  input_cache_hit_cny_per_1m: number
  input_cache_miss_cny_per_1m: number
  output_cny_per_1m: number
  concurrency_limit: number
  doc_url: string
}

interface BillingData {
  period_days: number
  cny_usd_rate: number
  billing_method?: string
  provider?: string
  cache_summary?: {
    cache_hit_tokens: number
    cache_miss_tokens: number
    cache_hit_rate: number
    cost_cny_actual: number
    cost_cny_if_all_miss: number
    cache_savings_cny: number
    has_cache_breakdown: boolean
  }
  summary: BillingSummary
  deepseek_summary: { total_calls: number; cost_usd: number; cost_cny: number; models: BillingModelRow[] }
  deepseek_official: {
    cny_usd_rate: number
    billing_rule: string
    note: string
    models: DeepSeekOfficialModel[]
  }
  traders: BillingTrader[]
  modules: Array<{
    module: string
    module_label: string
    calls: number
    prompt_tokens: number
    completion_tokens: number
    tokens: number
    cache_hit_tokens: number
    cache_miss_tokens: number
    cache_hit_rate: number | null
    cost_usd: number
    cost_cny: number
  }>
  call_types: Array<{
    call_type: string
    provider: string
    model: string
    calls: number
    tokens: number
    prompt_tokens: number
    completion_tokens: number
    cost_usd: number
    cost_cny: number
  }>
  daily: BillingDaily[]
  recent_calls: Array<{
    id: number
    account_id: number | null
    account_name: string
    provider: string
    model: string
    call_type: string | null
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    cost_usd: number
    cost_cny: number
    duration_ms: number | null
    success: boolean
    created_at: string | null
  }>
}

const PERIOD_OPTIONS = [
  { value: 7, label: '7 天' },
  { value: 30, label: '30 天' },
  { value: 90, label: '90 天' },
]

const SUB_TABS: { key: BillingTab; label: string }[] = [
  { key: 'overview', label: '总览' },
  { key: 'daily', label: '每日明细' },
  { key: 'modules', label: '项目模块' },
  { key: 'traders', label: '按交易员' },
  { key: 'models', label: '按模型' },
  { key: 'calls', label: '调用场景' },
  { key: 'pricing', label: 'DeepSeek 价目' },
  { key: 'recent', label: '最近调用' },
]

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString()
}

function fmtCny(n: number): string {
  if (n >= 100) return `¥${n.toFixed(2)}`
  if (n >= 1) return `¥${n.toFixed(3)}`
  return `¥${n.toFixed(4)}`
}

function MetricCard({
  icon, label, value, sub, highlight,
}: {
  icon: React.ReactNode
  label: string
  value: string
  sub?: string
  highlight?: boolean
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-center gap-1.5 text-muted-foreground mb-1.5">
        {icon}
        <span className="text-[11px] font-medium">{label}</span>
      </div>
      <div className={cn('text-xl font-bold tabular-nums', highlight ? 'text-amber-500' : 'text-foreground')}>
        {value}
      </div>
      {sub && <div className="text-[10px] text-muted-foreground mt-1">{sub}</div>}
    </div>
  )
}

function DailyTable({ rows, maxCny, showEmpty = false }: { rows: BillingDaily[]; maxCny: number; showEmpty?: boolean }) {
  const list = showEmpty ? rows : rows.filter(d => d.calls > 0)
  if (list.length === 0) {
    return <p className="text-xs text-muted-foreground py-4 text-center">该时段暂无调用记录</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted-foreground border-b border-border">
            <th className="text-left px-3 py-2">日期</th>
            <th className="text-right px-2 py-2">调用</th>
            <th className="text-right px-2 py-2">输入 Token</th>
            <th className="text-right px-2 py-2">输出 Token</th>
            <th className="text-left px-2 py-2 w-[28%]">费用占比</th>
            <th className="text-right px-3 py-2">费用 ¥</th>
          </tr>
        </thead>
        <tbody>
          {[...list].reverse().map(d => (
            <tr key={d.date} className="border-b border-border/40 hover:bg-muted/20">
              <td className="px-3 py-2 whitespace-nowrap">{d.date}</td>
              <td className="px-2 py-2 text-right tabular-nums">{d.calls.toLocaleString()}</td>
              <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">{fmtTokens(d.prompt_tokens)}</td>
              <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">{fmtTokens(d.completion_tokens)}</td>
              <td className="px-2 py-2">
                <div className="h-2 bg-muted/40 rounded overflow-hidden">
                  <div className="h-full bg-amber-500/60 rounded" style={{ width: `${Math.max((d.cost_cny / maxCny) * 100, d.calls > 0 ? 2 : 0)}%` }} />
                </div>
              </td>
              <td className="px-3 py-2 text-right tabular-nums font-medium text-amber-500">{fmtCny(d.cost_cny)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TraderRow({ trader }: { trader: BillingTrader }) {
  const [open, setOpen] = useState(false)
  const traderDaily = trader.daily || []
  const activeDays = traderDaily.filter(d => d.calls > 0)
  const maxTraderDailyCny = Math.max(...activeDays.map(d => d.cost_cny), 0.001)
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-muted/40 text-left"
      >
        {open ? <ChevronDown className="w-4 h-4 shrink-0" /> : <ChevronRight className="w-4 h-4 shrink-0" />}
        <User className="w-4 h-4 text-blue-500 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm truncate">{trader.account_name}</div>
          <div className="text-[10px] text-muted-foreground">
            {trader.total_calls} 次 · 输入 {fmtTokens(trader.prompt_tokens)} · 输出 {fmtTokens(trader.completion_tokens)}
            {activeDays.length > 0 && <span className="ml-1">· 活跃 {activeDays.length} 天</span>}
            {trader.failed_calls > 0 && <span className="text-red-500 ml-1">失败 {trader.failed_calls}</span>}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="font-semibold text-amber-500 tabular-nums">{fmtCny(trader.cost_cny)}</div>
          <div className="text-[10px] text-muted-foreground tabular-nums">${trader.cost_usd.toFixed(4)}</div>
        </div>
      </button>
      {open && (
        <div className="border-t border-border bg-muted/20 px-4 py-3 space-y-3 text-xs">
          {activeDays.length > 0 && (
            <div>
              <div className="font-medium text-muted-foreground mb-1.5 flex items-center gap-1">
                <Calendar className="w-3.5 h-3.5" /> 每日用量
              </div>
              <DailyTable rows={activeDays} maxCny={maxTraderDailyCny} />
            </div>
          )}
          <div>
            <div className="font-medium text-muted-foreground mb-1.5">按模型</div>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="text-muted-foreground">
                    <th className="text-left py-1">模型</th>
                    <th className="text-right py-1">次数</th>
                    <th className="text-right py-1">输入</th>
                    <th className="text-right py-1">输出</th>
                    <th className="text-right py-1">费用(¥)</th>
                  </tr>
                </thead>
                <tbody>
                  {trader.models.map(m => (
                    <tr key={`${m.provider}-${m.model}`} className="border-t border-border/40">
                      <td className="py-1.5">
                        <span className="font-mono">{m.model}</span>
                        <span className="ml-1 text-[10px] text-muted-foreground">{m.provider}</span>
                      </td>
                      <td className="text-right tabular-nums">{m.calls}</td>
                      <td className="text-right tabular-nums text-muted-foreground">{fmtTokens(m.prompt_tokens)}</td>
                      <td className="text-right tabular-nums text-muted-foreground">{fmtTokens(m.completion_tokens)}</td>
                      <td className="text-right tabular-nums text-amber-500">{fmtCny(m.cost_cny)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          {trader.call_types.length > 0 && (
            <div>
              <div className="font-medium text-muted-foreground mb-1.5">按调用场景</div>
              <div className="flex flex-wrap gap-2">
                {trader.call_types.map(c => (
                  <span key={c.call_type} className="px-2 py-1 rounded-md bg-background border border-border text-[10px]">
                    {c.call_type}: {c.calls}次 · {fmtCny(c.cost_cny)}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function LLMBillingPanel() {
  const [data, setData] = useState<BillingData | null>(null)
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(30)
  const [subTab, setSubTab] = useState<BillingTab>('overview')

  const load = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true)
      const res = await fetch(`/api/llm-usage/billing?days=${days}`)
      if (res.ok) setData(await res.json())
    } catch (e) {
      console.error('[Billing]', e)
    } finally {
      if (!silent) setLoading(false)
    }
  }, [days])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const t = setInterval(() => load(true), 30000)
    return () => clearInterval(t)
  }, [load])

  const maxDailyCny = useMemo(
    () => Math.max(...(data?.daily.filter(d => d.calls > 0).map(d => d.cost_cny) || [0.001]), 0.001),
    [data],
  )

  const activeDaily = useMemo(
    () => (data?.daily.filter(d => d.calls > 0) || []),
    [data],
  )

  if (loading && !data) {
    return <div className="text-center text-muted-foreground py-12 text-sm">加载计费数据...</div>
  }

  if (!data || data.summary.total_calls === 0) {
    return (
      <div className="rounded-xl border border-border bg-card p-10 text-center">
        <Cpu className="w-12 h-12 text-muted-foreground/30 mx-auto mb-3" />
        <p className="text-sm text-muted-foreground">暂无 LLM 调用记录</p>
        <p className="text-xs text-muted-foreground/60 mt-1">AI 交易员运行后，DeepSeek 等模型用量将在此统计</p>
      </div>
    )
  }

  const { summary, deepseek_summary, deepseek_official, cache_summary } = data

  return (
    <div className="space-y-4">
      {/* 顶栏 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Coins className="w-5 h-5 text-amber-500" />
            LLM 计费统计
          </h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            按 token × 官方价重算 · DeepSeek 以 CNY 为准 · 近 {days} 天
            {data.billing_method && <span className="block mt-0.5 opacity-80">{data.billing_method}</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {PERIOD_OPTIONS.map(opt => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setDays(opt.value)}
              className={cn(
                'px-2.5 py-1 rounded text-xs font-medium border transition-colors',
                days === opt.value
                  ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30'
                  : 'border-border text-muted-foreground hover:bg-muted',
              )}
            >
              {opt.label}
            </button>
          ))}
          <button type="button" onClick={() => load()} className="p-1.5 rounded border border-border hover:bg-muted">
            <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      {/* 核心指标 */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <MetricCard icon={<Coins className="w-4 h-4" />} label="总费用 (CNY)" value={fmtCny(summary.cost_cny)} highlight sub={`≈ $${summary.cost_usd.toFixed(2)} · ${days}天合计`} />
        <MetricCard icon={<Calendar className="w-4 h-4" />} label="今日 / 昨日" value={`${fmtCny(summary.today_cny || 0)} / ${fmtCny(summary.yesterday_cny || 0)}`} sub={`${summary.today_calls || 0} / ${summary.yesterday_calls || 0} 次`} highlight />
        <MetricCard icon={<TrendingUp className="w-4 h-4" />} label="日均费用" value={fmtCny(summary.avg_daily_cny || 0)} sub={`活跃日 ${summary.active_days || 0} 天 · 均 ${fmtCny(summary.avg_active_day_cny || 0)}/天`} />
        <MetricCard icon={<Zap className="w-4 h-4" />} label="DeepSeek 费用" value={fmtCny(deepseek_summary.cost_cny)} sub={`${deepseek_summary.total_calls} 次调用`} />
        <MetricCard icon={<Hash className="w-4 h-4" />} label="总请求" value={summary.total_calls.toLocaleString()} sub={`成功 ${summary.success_calls} / 失败 ${summary.failed_calls}`} />
        <MetricCard icon={<User className="w-4 h-4" />} label="活跃交易员" value={String(data.traders.length)} sub={`总 Token ${fmtTokens(summary.total_tokens)}`} />
      </div>

      {/* 子 Tab */}
      <div className="flex flex-wrap gap-1 border-b border-border pb-1">
        {SUB_TABS.map(t => (
          <button
            key={t.key}
            type="button"
            onClick={() => setSubTab(t.key)}
            className={cn(
              'px-3 py-1.5 rounded-t text-xs font-medium transition-colors',
              subTab === t.key
                ? 'bg-muted text-foreground border border-border border-b-transparent -mb-px'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* 总览 */}
      {subTab === 'overview' && (
        <div className="space-y-4">
          {cache_summary && (
            <div className="rounded-xl border border-blue-500/20 bg-blue-500/5 px-4 py-3 text-xs space-y-1">
              <div className="font-medium text-foreground">DeepSeek 硬盘缓存（官方默认开启）</div>
              {cache_summary.has_cache_breakdown ? (
                <>
                  <div>输入缓存命中 {fmtTokens(cache_summary.cache_hit_tokens)} · 未命中 {fmtTokens(cache_summary.cache_miss_tokens)} · 命中率 {(cache_summary.cache_hit_rate * 100).toFixed(1)}%</div>
                  <div>实际费用 {fmtCny(cache_summary.cost_cny_actual)} · 若全部未命中约 {fmtCny(cache_summary.cost_cny_if_all_miss)} · 缓存节省 {fmtCny(cache_summary.cache_savings_cny)}</div>
                </>
              ) : (
                <div>历史记录暂无 hit/miss 分拆（升级后新调用会自动记录）。当前按未命中价估算，实际可能更低。</div>
              )}
            </div>
          )}
          {summary.peak_day && (
            <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-xs flex flex-wrap gap-x-6 gap-y-1">
              <span><strong className="text-foreground">峰值日</strong> {summary.peak_day.date}</span>
              <span>费用 {fmtCny(summary.peak_day.cost_cny)}</span>
              <span>{summary.peak_day.calls} 次</span>
              <span>{fmtTokens(summary.peak_day.tokens)} Token</span>
            </div>
          )}
          <div className="rounded-xl border border-border bg-card p-4">
            <h3 className="text-sm font-medium mb-3">最近 {Math.min(activeDaily.length, 14)} 个活跃日</h3>
            <DailyTable rows={activeDaily.slice(-14)} maxCny={maxDailyCny} />
          </div>
          <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 text-xs text-muted-foreground">
            <strong className="text-foreground">估算说明：</strong>
            {deepseek_official.note} USD 由 CNY ÷ {data.cny_usd_rate} 换算，与 CNY 保持一致。
            <a href="https://api-docs.deepseek.com/zh-cn/quick_start/pricing/" target="_blank" rel="noreferrer" className="text-blue-500 hover:underline inline-flex items-center gap-0.5 ml-1">官方价目<ExternalLink className="w-3 h-3" /></a>
          </div>
        </div>
      )}

      {subTab === 'daily' && (
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <div className="px-4 py-3 border-b border-border bg-muted/30 flex items-center justify-between">
            <h3 className="text-sm font-medium">近 {days} 天每日明细</h3>
            <span className="text-[10px] text-muted-foreground">共 {activeDaily.length} 天有调用 · 合计 {fmtCny(summary.cost_cny)}</span>
          </div>
          <DailyTable rows={data.daily} maxCny={maxDailyCny} showEmpty />
        </div>
      )}

      {subTab === 'modules' && (
        <div className="rounded-xl border border-border overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-muted/50 text-muted-foreground border-b border-border">
                <th className="text-left px-3 py-2.5">项目模块</th>
                <th className="text-right px-2 py-2.5">调用</th>
                <th className="text-right px-2 py-2.5">输入 Token</th>
                <th className="text-right px-2 py-2.5">缓存命中</th>
                <th className="text-right px-2 py-2.5">命中率</th>
                <th className="text-right px-3 py-2.5">费用 ¥</th>
              </tr>
            </thead>
            <tbody>
              {(data.modules || []).map(m => (
                <tr key={m.module} className="border-b border-border/50 hover:bg-muted/20">
                  <td className="px-3 py-2">
                    <div className="font-medium">{m.module_label}</div>
                    <div className="text-[10px] text-muted-foreground font-mono">{m.module}</div>
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums">{m.calls.toLocaleString()}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">{fmtTokens(m.prompt_tokens)}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-blue-500">{fmtTokens(m.cache_hit_tokens)}</td>
                  <td className="px-2 py-2 text-right tabular-nums">
                    {m.cache_hit_rate == null ? '—' : `${(m.cache_hit_rate * 100).toFixed(1)}%`}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums font-semibold text-amber-500">{fmtCny(m.cost_cny)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 交易员 */}
      {subTab === 'traders' && (
        <div className="space-y-2">
          {data.traders.map(t => (
            <TraderRow key={String(t.account_id)} trader={t} />
          ))}
        </div>
      )}

      {/* 模型 */}
      {subTab === 'models' && (
        <div className="rounded-xl border border-border overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-muted/50 text-muted-foreground border-b border-border">
                <th className="text-left px-3 py-2.5">模型</th>
                <th className="text-left px-2 py-2.5">厂商</th>
                <th className="text-right px-2 py-2.5">次数</th>
                <th className="text-right px-2 py-2.5">输入 Token</th>
                <th className="text-right px-2 py-2.5">输出 Token</th>
                <th className="text-right px-2 py-2.5">均耗时</th>
                <th className="text-right px-3 py-2.5">费用 ¥</th>
                <th className="text-right px-3 py-2.5">费用 $</th>
              </tr>
            </thead>
            <tbody>
              {data.models.map((m, i) => (
                <tr key={`${m.model}-${i}`} className="border-b border-border/50 hover:bg-muted/20">
                  <td className="px-3 py-2 font-mono">{m.model}</td>
                  <td className="px-2 py-2">{m.provider}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{m.total_calls}{m.failed_calls > 0 && <span className="text-red-500 ml-1">({m.failed_calls}败)</span>}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">{fmtTokens(m.prompt_tokens)}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">{fmtTokens(m.completion_tokens)}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">{m.avg_duration_ms > 0 ? `${(m.avg_duration_ms / 1000).toFixed(1)}s` : '-'}</td>
                  <td className="px-3 py-2 text-right tabular-nums font-semibold text-amber-500">{fmtCny(m.cost_cny)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">${m.cost_usd.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 调用场景 */}
      {subTab === 'calls' && (
        <div className="rounded-xl border border-border overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-muted/50 text-muted-foreground border-b border-border">
                <th className="text-left px-3 py-2.5">调用场景 (call_type)</th>
                <th className="text-left px-2 py-2.5">模型</th>
                <th className="text-right px-2 py-2.5">次数</th>
                <th className="text-right px-2 py-2.5">Token</th>
                <th className="text-right px-3 py-2.5">费用 ¥</th>
              </tr>
            </thead>
            <tbody>
              {data.call_types.map((c, i) => (
                <tr key={`${c.call_type}-${i}`} className="border-b border-border/50 hover:bg-muted/20">
                  <td className="px-3 py-2 font-mono text-[11px] max-w-xs truncate" title={c.call_type}>{c.call_type}</td>
                  <td className="px-2 py-2 font-mono">{c.model}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{c.calls}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">{fmtTokens(c.tokens)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-amber-500">{fmtCny(c.cost_cny)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* DeepSeek 官方价目 */}
      {subTab === 'pricing' && (
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-card p-4 text-sm">
            <p className="text-muted-foreground">{deepseek_official.billing_rule}</p>
            <p className="text-xs text-muted-foreground mt-2">{deepseek_official.note}</p>
          </div>
          {deepseek_official.models.map(m => (
            <div key={m.model_id} className="rounded-xl border border-border bg-card overflow-hidden">
              <div className="px-4 py-3 border-b border-border bg-muted/30 flex items-center justify-between">
                <div>
                  <div className="font-semibold">{m.display_name}</div>
                  <div className="text-[10px] text-muted-foreground font-mono">别名: {m.aliases.join(', ')}</div>
                </div>
                <div className="text-[10px] text-muted-foreground text-right">
                  上下文 {m.context_length} · 最大输出 {m.max_output_tokens}<br />
                  并发 {m.concurrency_limit}
                </div>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-muted-foreground border-b border-border">
                    <th className="text-left px-4 py-2">计费项</th>
                    <th className="text-right px-4 py-2">价格 (CNY / 百万 tokens)</th>
                    <th className="text-right px-4 py-2">约合 USD / 1M</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-border/40">
                    <td className="px-4 py-2">输入（缓存命中）</td>
                    <td className="px-4 py-2 text-right tabular-nums">¥{m.input_cache_hit_cny_per_1m}</td>
                    <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">${(m.input_cache_hit_cny_per_1m / data.cny_usd_rate).toFixed(4)}</td>
                  </tr>
                  <tr className="border-b border-border/40">
                    <td className="px-4 py-2">输入（缓存未命中）</td>
                    <td className="px-4 py-2 text-right tabular-nums font-medium">¥{m.input_cache_miss_cny_per_1m}</td>
                    <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">${(m.input_cache_miss_cny_per_1m / data.cny_usd_rate).toFixed(4)}</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">输出</td>
                    <td className="px-4 py-2 text-right tabular-nums font-medium">¥{m.output_cny_per_1m}</td>
                    <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">${(m.output_cny_per_1m / data.cny_usd_rate).toFixed(4)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}

      {/* 最近调用 */}
      {subTab === 'recent' && (
        <div className="rounded-xl border border-border overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-muted/50 text-muted-foreground border-b border-border">
                <th className="text-left px-3 py-2.5">时间</th>
                <th className="text-left px-2 py-2.5">交易员</th>
                <th className="text-left px-2 py-2.5">模型</th>
                <th className="text-left px-2 py-2.5">场景</th>
                <th className="text-right px-2 py-2.5">入/出 Token</th>
                <th className="text-right px-2 py-2.5">耗时</th>
                <th className="text-right px-3 py-2.5">¥</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_calls.map(r => (
                <tr key={r.id} className={cn('border-b border-border/50', !r.success && 'bg-red-500/5')}>
                  <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">{r.created_at?.slice(0, 19) || '-'}</td>
                  <td className="px-2 py-2">{r.account_name}</td>
                  <td className="px-2 py-2 font-mono">{r.model}</td>
                  <td className="px-2 py-2 font-mono text-[10px] max-w-[120px] truncate" title={r.call_type || ''}>{r.call_type || '-'}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{r.prompt_tokens}/{r.completion_tokens}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">{r.duration_ms ? `${(r.duration_ms / 1000).toFixed(1)}s` : '-'}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-amber-500">{fmtCny(r.cost_cny)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
