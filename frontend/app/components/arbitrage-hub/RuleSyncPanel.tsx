/**
 * RuleSyncPanel — 规则同步面板（从 ArbitrageHubPage 拆出）
 *
 * 六所规则源抓取、规则变更队列、手动规则快照、策略规则参数、进化提案、最近事件。
 */
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { Shield } from 'lucide-react'
import {
  type RuleSyncGateState, type EvolutionProposal,
  type RuleSource, type RuleChangeEvent, type RuleSyncSchedulerStatus,
  type RebateEvent,
  getEvolutionProposals, runEvolutionBacktest,
  getRuleSources, getRuleChanges, ingestRuleSnapshot, analyzeRuleChange,
  fetchAllRuleSources, fetchRuleSource, markRuleChange, markEvolutionProposal,
  getRuleSyncScheduler, getRuleStrategyParams, generateEvolutionProposals,
  formatRebateEventMessage,
} from '@/lib/arbitrageApi'
import KpiTile from './KpiTile'

export default function RuleSyncPanel({
  gate,
  events,
  onRefresh,
}: {
  gate: RuleSyncGateState | null
  events: RebateEvent[]
  onRefresh: () => void
}) {
  const [proposals, setProposals] = useState<EvolutionProposal[]>([])
  const [backtestResult, setBacktestResult] = useState<any>(null)
  const [sources, setSources] = useState<RuleSource[]>([])
  const [changes, setChanges] = useState<RuleChangeEvent[]>([])
  const [scheduler, setScheduler] = useState<RuleSyncSchedulerStatus | null>(null)
  const [ruleParams, setRuleParams] = useState<Record<string, Record<string, any>>>({})
  const [ingestSourceId, setIngestSourceId] = useState('binance_alpha_rules')
  const [ingestText, setIngestText] = useState('')
  const [ingesting, setIngesting] = useState(false)
  const [fetchingRules, setFetchingRules] = useState(false)
  const [generatingEvolution, setGeneratingEvolution] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.allSettled([
      getEvolutionProposals(),
      getRuleSources(),
      getRuleChanges('', 80),
      getRuleSyncScheduler(),
      getRuleStrategyParams(),
    ]).then(([proposalRes, sourceRes, changeRes, schedulerRes, paramsRes]) => {
      if (cancelled) return
      setProposals(proposalRes.status === 'fulfilled' ? (proposalRes.value.proposals || []) : [])
      setSources(sourceRes.status === 'fulfilled' ? (sourceRes.value.sources || []) : [])
      setChanges(changeRes.status === 'fulfilled' ? (changeRes.value.events || []) : [])
      setScheduler(schedulerRes.status === 'fulfilled' ? schedulerRes.value : null)
      setRuleParams(paramsRes.status === 'fulfilled' ? (paramsRes.value.strategies || {}) : {})
    })
    return () => { cancelled = true }
  }, [])

  const refreshRuleData = async () => {
    const [proposalRes, changeRes, schedulerRes, paramsRes] = await Promise.all([getEvolutionProposals(), getRuleChanges('', 80), getRuleSyncScheduler(), getRuleStrategyParams()])
    setProposals(proposalRes.proposals || [])
    setChanges(changeRes.events || [])
    setScheduler(schedulerRes)
    setRuleParams(paramsRes.strategies || {})
    onRefresh()
  }

  const handleManualIngest = async () => {
    if (!ingestText.trim()) return
    setIngesting(true)
    try {
      await ingestRuleSnapshot({ source_id: ingestSourceId, content_text: ingestText })
      setIngestText('')
      await refreshRuleData()
    } finally {
      setIngesting(false)
    }
  }

  const handleFetchAllRules = async () => {
    setFetchingRules(true)
    try {
      await fetchAllRuleSources()
      await refreshRuleData()
    } finally {
      setFetchingRules(false)
    }
  }

  const handleGenerateEvolution = async () => {
    setGeneratingEvolution(true)
    try {
      await generateEvolutionProposals()
      await refreshRuleData()
    } finally {
      setGeneratingEvolution(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-card p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Shield className="w-5 h-5 text-blue-500" /> 规则同步
            </h2>
            <p className="text-sm text-muted-foreground mt-1">
              已接入六所规则源、快照 diff、AI/规则影响评估、L3/L4 自动暂停与人工确认队列。
            </p>
          </div>
          <button
            onClick={handleFetchAllRules}
            disabled={fetchingRules}
            className="px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm"
          >
            {fetchingRules ? '抓取中...' : '抓取六所规则源'}
          </button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-4">
          <KpiTile
            label="Rebate/S1-S8"
            value={gate?.is_rebate_paused ? '暂停' : '正常'}
            sub={gate?.pause_reason || '可执行'}
            tone={gate?.is_rebate_paused ? 'red' : 'green'}
          />
          <KpiTile
            label="V3 合约套利"
            value={gate?.is_v3_paused ? '暂停' : '正常'}
            sub="仅系统级风险暂停"
            tone={gate?.is_v3_paused ? 'red' : 'green'}
          />
          <KpiTile
            label="手动 override"
            value={gate?.allow_manual_override ? '允许' : '默认禁止'}
            sub="全局暂停时快速执行"
            tone={gate?.allow_manual_override ? 'amber' : 'purple'}
          />
          <KpiTile
            label="后台采集"
            value={scheduler?.registered ? '已注册' : scheduler?.enabled ? '待启动' : '关闭'}
            sub={scheduler?.next_run_time || `${Math.round((scheduler?.interval_seconds || 0) / 3600)}h interval`}
            tone={scheduler?.registered ? 'green' : 'amber'}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-4">
        <div className="rounded-xl border border-border bg-card p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">规则变更队列</h3>
            <button onClick={refreshRuleData} className="text-xs px-3 py-1 rounded bg-secondary hover:bg-secondary/80">
              刷新
            </button>
          </div>
          <div className="space-y-2">
            {changes.length === 0 ? (
              <div className="text-sm text-muted-foreground">暂无规则变更。可以右侧手动粘贴规则文本做一次快照。</div>
            ) : changes.map(ev => (
              <div key={ev.id} className="rounded-lg border border-border bg-muted/20 p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-medium text-sm">
                    {ev.exchange} / {ev.rule_type} · {ev.severity}
                  </div>
                  <span className={cn(
                    'text-[10px] px-2 py-1 rounded-full',
                    ev.auto_pause_applied ? 'bg-red-500/10 text-red-600' :
                    ev.severity === 'L3' || ev.severity === 'L4' ? 'bg-yellow-500/10 text-yellow-600' :
                    'bg-green-500/10 text-green-600'
                  )}>
                    {ev.auto_pause_applied ? '已自动暂停' : ev.status}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  影响策略：{ev.affected_strategies?.join('/') || '待分析'} · {ev.diff_summary || '暂无摘要'}
                </div>
                <div className="mt-2 flex gap-2">
                  <button
                    onClick={async () => { await analyzeRuleChange(ev.id); await refreshRuleData() }}
                    className="text-xs px-2 py-1 rounded bg-secondary hover:bg-secondary/80"
                  >
                    重新分析
                  </button>
                  <button
                    onClick={async () => { await markRuleChange(ev.id, 'dismissed'); await refreshRuleData() }}
                    className="text-xs px-2 py-1 rounded bg-secondary hover:bg-secondary/80"
                  >
                    忽略
                  </button>
                  <button
                    onClick={async () => { await markRuleChange(ev.id, 'applied'); await refreshRuleData() }}
                    className="text-xs px-2 py-1 rounded bg-green-600/90 hover:bg-green-700 text-white"
                  >
                    确认已处理
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-card p-5">
            <h3 className="font-semibold mb-3">六所规则源</h3>
            <div className="space-y-2 max-h-[260px] overflow-auto pr-1">
              {sources.map(src => (
                <div key={src.source_id} className="rounded-lg border border-border bg-muted/20 p-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium">{src.title}</div>
                    <span className={cn(
                      'text-[10px] px-1.5 py-0.5 rounded-full',
                      src.auto_pause_enabled ? 'bg-red-500/10 text-red-600' : 'bg-blue-500/10 text-blue-600'
                    )}>
                      {src.auto_pause_enabled ? 'L3/L4 自动暂停' : '只告警'}
                    </span>
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-1">
                    {src.exchange} · {src.rule_type} · {src.affected_strategies.join('/')}
                  </div>
                  <button
                    onClick={async () => { await fetchRuleSource(src.source_id); await refreshRuleData() }}
                    className="mt-2 text-[11px] px-2 py-1 rounded bg-secondary hover:bg-secondary/80"
                  >
                    抓取此源
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-border bg-card p-5">
            <h3 className="font-semibold mb-3">手动规则快照</h3>
            <select
              value={ingestSourceId}
              onChange={e => setIngestSourceId(e.target.value)}
              className="w-full mb-2 rounded-lg border border-border bg-background px-3 py-2 text-sm"
            >
              {sources.map(src => <option key={src.source_id} value={src.source_id}>{src.title}</option>)}
            </select>
            <textarea
              value={ingestText}
              onChange={e => setIngestText(e.target.value)}
              placeholder="粘贴交易所公告/规则文本。第二次提交同一 source 且内容变化时会生成 diff 和 AI 影响评估。"
              className="w-full h-32 rounded-lg border border-border bg-background px-3 py-2 text-sm"
            />
            <button
              onClick={handleManualIngest}
              disabled={ingesting || !ingestText.trim()}
              className="mt-2 w-full px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm"
            >
              {ingesting ? '采集中...' : '采集并分析'}
            </button>
          </div>

          <div className="rounded-xl border border-border bg-card p-5">
            <h3 className="font-semibold mb-3">策略规则参数</h3>
            <div className="space-y-2">
              {Object.entries(ruleParams).map(([sid, params]) => (
                <div key={sid} className="rounded-lg border border-border bg-muted/20 p-2">
                  <div className="flex items-center justify-between">
                    <div className="text-sm font-medium">{sid}</div>
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-500/10 text-blue-600">
                      RuleRegistry
                    </span>
                  </div>
                  <div className="mt-1 text-[11px] text-muted-foreground">
                    {sid === 'S7'
                      ? `mode=${params.MODE}, token/point=${params.TOKENS_PER_POINT}`
                      : `USDF=${params.USDF_AU_MULTIPLIER}x, hold=${Math.round((params.MIN_HOLD_SECONDS || 0) / 60)}min, ASTER=$${params.ASTER_PRICE}`}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="font-semibold">进化提案</h3>
            <p className="text-xs text-muted-foreground mt-1">
              Paper 回测通过后仍需人工点击应用到 Live，禁止静默切换。
            </p>
          </div>
          <button
            onClick={async () => setBacktestResult(await runEvolutionBacktest('S8'))}
            className="text-xs px-3 py-1 rounded bg-secondary hover:bg-secondary/80"
          >
            回测 S8
          </button>
          <button
            onClick={handleGenerateEvolution}
            disabled={generatingEvolution}
            className="text-xs px-3 py-1 rounded bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white"
          >
            {generatingEvolution ? '生成中...' : '生成进化提案'}
          </button>
        </div>
        {proposals.length === 0 ? (
          <div className="text-sm text-muted-foreground">暂无可应用提案；样本不足时系统只监控不改配置。</div>
        ) : (
          <div className="space-y-2">
            {proposals.map((p, idx) => (
              <div key={`${p.strategy_type}-${idx}`} className="rounded-lg border border-border bg-muted/20 px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-medium text-sm">{p.title}</div>
                  <span className={cn(
                    'text-[10px] px-2 py-1 rounded-full',
                    p.severity === 'low' ? 'bg-green-500/10 text-green-600' : 'bg-yellow-500/10 text-yellow-600'
                  )}>
                    {p.severity}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  {JSON.stringify(p.change)} · Live 需人工确认
                </div>
                {'id' in p && typeof (p as any).id === 'number' && (
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={async () => { await markEvolutionProposal((p as any).id, 'paper_validated'); await refreshRuleData() }}
                      className="text-xs px-2 py-1 rounded bg-secondary hover:bg-secondary/80"
                    >
                      标记 Paper 通过
                    </button>
                    <button
                      onClick={async () => { await markEvolutionProposal((p as any).id, 'dismissed'); await refreshRuleData() }}
                      className="text-xs px-2 py-1 rounded bg-secondary hover:bg-secondary/80"
                    >
                      忽略
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        {backtestResult && (
          <div className="mt-3 rounded-lg border border-blue-500/20 bg-blue-500/5 p-3 text-sm">
            <div className="font-medium">S8 回测结果：{backtestResult.recommendation}</div>
            <div className="text-xs text-muted-foreground mt-1">
              样本 {backtestResult.sample_count} · 胜率 {Math.round((backtestResult.win_rate || 0) * 100)}% · 净值 ${Number(backtestResult.net_value || 0).toFixed(2)}
            </div>
          </div>
        )}
      </div>

      <div className="rounded-xl border border-border bg-card p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold">最近规则/引擎事件</h3>
          <button onClick={onRefresh} className="text-xs px-3 py-1 rounded bg-secondary hover:bg-secondary/80">
            刷新
          </button>
        </div>
        <div className="space-y-2">
          {events.length === 0 ? (
            <div className="text-sm text-muted-foreground">暂无事件</div>
          ) : events.slice(0, 12).map((ev, idx) => (
            <div key={`${ev.ts}-${idx}`} className="rounded-lg border border-border bg-muted/20 px-3 py-2 text-sm">
              <div className="font-medium">{ev.type === 'config_changed' ? '策略配置' : ev.type}</div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {new Date(ev.ts * 1000).toLocaleString()} · {formatRebateEventMessage(ev)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
