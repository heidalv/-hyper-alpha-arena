import { Target } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ArbitrageProfile } from '@/lib/api'

export default function ArbitrageTraderProfilePanel({
  profile,
  loading,
  saving,
  traderName,
  analysisModelName,
  executionModelName,
  analysisModelId,
  executionModelId,
  arbitragePaperAccounts,
  onChange,
  onSave,
  onAiGenerate,
}: {
  profile: ArbitrageProfile | null
  loading: boolean
  saving: boolean
  traderName?: string
  analysisModelName?: string
  executionModelName?: string
  analysisModelId?: number | null
  executionModelId?: number | null
  arbitragePaperAccounts: Array<{ id: number; name: string; total_equity: number }>
  onChange: (profile: ArbitrageProfile) => void
  onSave: (profile: ArbitrageProfile) => void
  onAiGenerate: () => void
}) {
  const baseProfile: ArbitrageProfile = profile || {
    id: null,
    account_id: 0,
    enabled: false,
    mode: 'paper',
    paper_account_id: null,
    paper_account_mode: 'dedicated_arbitrage_paper',
    arbitrage_paper_account_id: null,
    enabled_strategies: ['S8'],
    strategy_overrides: {},
    wash_trade_profile: 'balanced',
    ai_config_source: 'manual',
    linked_llm_config_id: null,
    strategy_llm_config_id: null,
    execution_llm_config_id: null,
  }

  const update = (patch: Partial<ArbitrageProfile>) => onChange({ ...baseProfile, ...patch })
  const toggleStrategy = (sid: string) => {
    const current = new Set(baseProfile.enabled_strategies || [])
    if (current.has(sid)) current.delete(sid)
    else current.add(sid)
    update({ enabled_strategies: Array.from(current).sort() })
  }

  const modelsOk = Boolean(analysisModelId && executionModelId && analysisModelId !== executionModelId)

  if (loading) {
    return <div className="text-sm text-muted-foreground py-4">加载套利配置中...</div>
  }

  if (!baseProfile.enabled) {
    return (
      <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-800 dark:text-amber-200">
        {traderName ? `「${traderName}」` : '该交易员'}尚未开启积分套利。
        请先在 <strong>AI 交易员管理 → 编辑</strong> 中勾选「可用于积分套利」。
      </div>
    )
  }

  return (
    <div className="max-w-3xl space-y-4">
      <div className="rounded-xl border border-border p-4 bg-card">
        <h3 className="text-sm font-semibold flex items-center gap-2 mb-3">
          <Target className="w-4 h-4 text-amber-500" />
          {traderName ? `${traderName} · 套利档案` : '套利档案'}
        </h3>

        <div className={cn(
          'rounded-lg border px-3 py-2.5 text-xs mb-4',
          modelsOk ? 'border-green-500/30 bg-green-500/5' : 'border-red-500/30 bg-red-500/10',
        )}>
          <div className="font-medium text-foreground mb-1">大模型（与 AI 交易员共用，不在此重复配置）</div>
          <div className="text-muted-foreground">
            分析模型（深度 / reasoner）：{analysisModelName || '未配置'}
            {analysisModelId ? ` (#${analysisModelId})` : ''}
            <span className="block text-[10px] mt-0.5 opacity-80">
              S8 选币与方向 · 走 SSE 流式，收到 [DONE] 才结束，不用固定超时截断
            </span>
          </div>
          <div className="text-muted-foreground">
            执行模型（快速）：{executionModelName || '未配置'}
            {executionModelId ? ` (#${executionModelId})` : ''}
            <span className="block text-[10px] mt-0.5 opacity-80">
              仓位 / 杠杆 / 是否开单 · 建议 deepseek-chat 等快速模型
            </span>
          </div>
          {!modelsOk && (
            <div className="text-red-600 dark:text-red-400 mt-1">
              请到 AI 交易员编辑里分别选择分析模型与执行模型（不能选同一个）。
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">运行模式</label>
            <select
              value={baseProfile.mode}
              onChange={e => update({ mode: e.target.value as 'paper' | 'live' })}
              className="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm outline-none"
            >
              <option value="paper">Paper 模拟</option>
              <option value="live">Live 实盘</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">套利 Paper 账户</label>
            <select
              value={baseProfile.arbitrage_paper_account_id ?? ''}
              disabled={baseProfile.mode !== 'paper'}
              onChange={e => update({ arbitrage_paper_account_id: e.target.value ? Number(e.target.value) : null })}
              className="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm outline-none disabled:opacity-50"
            >
              <option value="">请选择（也可在「模拟账户」页绑定）</option>
              {arbitragePaperAccounts.map(acc => (
                <option key={acc.id} value={acc.id}>
                  {acc.name} · ${acc.total_equity.toFixed(0)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">刷量档位</label>
            <select
              value={baseProfile.wash_trade_profile}
              onChange={e => update({ wash_trade_profile: e.target.value })}
              className="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm outline-none"
            >
              <option value="conservative">保守</option>
              <option value="balanced">均衡</option>
              <option value="aggressive">激进</option>
            </select>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-border p-4 bg-card">
        <h4 className="text-sm font-semibold mb-3">授权策略</h4>
        <p className="text-xs text-muted-foreground mb-2">S1/S5 已下线不再提供授权；S6 已关闭（负 EV）。</p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {['S2', 'S3', 'S4', 'S6', 'S7', 'S8'].map(sid => (
            <button
              key={sid}
              type="button"
              onClick={() => toggleStrategy(sid)}
              className={cn(
                'px-3 py-2 rounded-lg border text-sm text-left transition-colors',
                baseProfile.enabled_strategies?.includes(sid)
                  ? 'border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-300'
                  : 'border-border bg-muted/30 text-muted-foreground hover:text-foreground',
              )}
            >
              <div className="font-medium">{sid}</div>
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={onAiGenerate} disabled={saving}
          className="px-4 py-2 rounded-lg bg-secondary hover:bg-secondary/80 text-sm disabled:opacity-50">
          AI 生成 300U 草案
        </button>
        <button type="button"
          onClick={() => onSave({ ...baseProfile, enabled: true, paper_account_mode: 'dedicated_arbitrage_paper' })}
          disabled={saving || !modelsOk}
          className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-sm disabled:opacity-50">
          {saving ? '保存中...' : '保存套利配置'}
        </button>
      </div>
    </div>
  )
}
