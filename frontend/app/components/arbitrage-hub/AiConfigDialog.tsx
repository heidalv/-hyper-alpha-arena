/**
 * AiConfigDialog — AI 一键配置对话框
 *
 * 三阶段流程:
 *   1. 输入: 风险偏好 + 资金量 + 目标交易所 + 附加目标
 *   2. 加载: 调用 LLM/fallback API
 *   3. 预览: 差异对比视图 → 确认应用
 */
import React, { useState } from 'react'
import { cn } from '@/lib/utils'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  Sparkles, Loader2, Shield, Zap, TrendingUp,
  Check, X, ArrowRight, AlertCircle,
} from 'lucide-react'
import {
  aiGenerateConfig,
  STRATEGY_META,
  fmt,
  type AiConfigGenerateResponse,
  type AiGeneratedConfig,
} from '@/lib/arbitrageApi'

// ════════════════════════════════════════════════════════
//  Types
// ════════════════════════════════════════════════════════

interface EngineForm {
  min_monthly_value: number
  max_position_usd: number
  max_total_volume_7d: number
  max_holding_days: number
}

interface RiskForm {
  max_daily_volume_per_exchange: number
  max_weekly_volume_per_exchange: number
  max_daily_loss_pct: number
}

interface StrategyPanelState {
  params: Record<string, number | string | boolean>
  overrides: Record<string, any>
  enabled: boolean
  expanded: boolean
}

export interface AiConfigDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentEngine: EngineForm
  currentRisk: RiskForm
  currentPanels: Record<string, StrategyPanelState>
  onApply: (config: AiGeneratedConfig) => void
}

type RiskProfile = 'conservative' | 'balanced' | 'aggressive'

const EXCHANGE_OPTIONS = [
  { id: 'asterdex', label: 'Asterdex' },
  { id: 'binance', label: 'Binance' },
  { id: 'hyperliquid', label: 'Hyperliquid' },
  { id: 'okx', label: 'OKX' },
  { id: 'bybit', label: 'Bybit' },
  { id: 'gate', label: 'Gate.io' },
]

const RISK_PROFILES: { key: RiskProfile; label: string; icon: React.ReactNode; desc: string }[] = [
  { key: 'conservative', label: '保守', icon: <Shield className="w-4 h-4" />, desc: '低风险·稳健收益' },
  { key: 'balanced', label: '平衡', icon: <Zap className="w-4 h-4" />, desc: '中等风险·均衡配置' },
  { key: 'aggressive', label: '激进', icon: <TrendingUp className="w-4 h-4" />, desc: '高风险·最大收益' },
]

// ════════════════════════════════════════════════════════
//  Component
// ════════════════════════════════════════════════════════

export default function AiConfigDialog({
  open,
  onOpenChange,
  currentEngine,
  currentRisk,
  currentPanels,
  onApply,
}: AiConfigDialogProps) {
  // ── Input state ──
  const [riskProfile, setRiskProfile] = useState<RiskProfile>('balanced')
  const [totalEquity, setTotalEquity] = useState(300)
  const [selectedExchanges, setSelectedExchanges] = useState<string[]>(['asterdex', 'binance', 'hyperliquid'])
  const [goal, setGoal] = useState('')

  // ── Loading / Result state ──
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<AiConfigGenerateResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  // ── Handlers ──

  const toggleExchange = (id: string) => {
    setSelectedExchanges(prev =>
      prev.includes(id) ? prev.filter(e => e !== id) : [...prev, id]
    )
  }

  const handleGenerate = async () => {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await aiGenerateConfig({
        risk_profile: riskProfile,
        total_equity: totalEquity,
        target_exchanges: selectedExchanges,
        goal,
      })
      if (res.success && res.config) {
        setResult(res)
      } else {
        setError(res.error || '生成失败')
        if (res.config) setResult(res)
      }
    } catch (e: any) {
      setError(e.message || '网络错误')
    } finally {
      setLoading(false)
    }
  }

  const handleApply = () => {
    if (result?.config) {
      onApply(result.config)
      // Reset & close
      setResult(null)
      setError(null)
      onOpenChange(false)
    }
  }

  const handleReset = () => {
    setResult(null)
    setError(null)
  }

  // ── Helpers ──

  const formatVal = (v: any): string => {
    const n = Number(v)
    if (!Number.isFinite(n)) return '-'
    if (Math.abs(n) < 0.01 && n !== 0) return n.toExponential(2)
    if (n >= 1000) return fmt(n, 0)
    if (n >= 1) return fmt(n, 2)
    return fmt(n, 4)
  }

  const hasConfig = result?.config != null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-blue-400" />
            AI 一键配置
          </DialogTitle>
          <DialogDescription>
            根据风险偏好和资金量，AI 为您生成最优引擎 + 风控 + 策略配置
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 py-2">
          {/* ── Input Section ── */}
          {!hasConfig && !loading && (
            <>
              {/* Risk Profile */}
              <div>
                <label className="text-xs font-semibold text-muted-foreground mb-2 block">风险偏好</label>
                <div className="grid grid-cols-3 gap-2">
                  {RISK_PROFILES.map(rp => (
                    <button
                      key={rp.key}
                      onClick={() => setRiskProfile(rp.key)}
                      className={cn(
                        'flex flex-col items-center gap-1 p-3 rounded-xl border text-sm transition-colors',
                        riskProfile === rp.key
                          ? 'border-blue-500/50 bg-blue-500/10 text-blue-400'
                          : 'border-border bg-muted/10 text-muted-foreground hover:bg-muted/20',
                      )}
                    >
                      {rp.icon}
                      <span className="font-semibold">{rp.label}</span>
                      <span className="text-[10px]">{rp.desc}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Total Equity */}
              <div>
                <label className="text-xs font-semibold text-muted-foreground mb-2 block">总权益 (USD)</label>
                <input
                  type="number"
                  value={totalEquity}
                  min={10}
                  step={50}
                  onChange={e => setTotalEquity(Number(e.target.value))}
                  className="w-full rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-blue-500/50"
                />
              </div>

              {/* Target Exchanges */}
              <div>
                <label className="text-xs font-semibold text-muted-foreground mb-2 block">目标交易所</label>
                <div className="flex flex-wrap gap-2">
                  {EXCHANGE_OPTIONS.map(ex => (
                    <button
                      key={ex.id}
                      onClick={() => toggleExchange(ex.id)}
                      className={cn(
                        'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border transition-colors',
                        selectedExchanges.includes(ex.id)
                          ? 'border-blue-500/50 bg-blue-500/10 text-blue-400'
                          : 'border-border bg-muted/10 text-muted-foreground hover:bg-muted/20',
                      )}
                    >
                      {selectedExchanges.includes(ex.id) ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
                      {ex.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Goal */}
              <div>
                <label className="text-xs font-semibold text-muted-foreground mb-2 block">附加目标 (可选)</label>
                <input
                  type="text"
                  value={goal}
                  onChange={e => setGoal(e.target.value)}
                  placeholder="如: 最大化 S8 Rh 积分收益"
                  className="w-full rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500/50 placeholder:text-muted-foreground/40"
                />
              </div>

              {/* Generate Button */}
              <button
                onClick={handleGenerate}
                disabled={totalEquity < 10}
                className={cn(
                  'flex items-center justify-center gap-2 w-full py-3 rounded-xl text-sm font-semibold transition-colors',
                  'bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 border border-blue-500/30',
                  totalEquity < 10 && 'opacity-50 cursor-not-allowed',
                )}
              >
                <Sparkles className="w-4 h-4" />
                AI 生成配置
              </button>

              {/* Error */}
              {error && (
                <div className="flex items-start gap-2 p-3 rounded-lg border border-red-500/30 bg-red-500/5 text-xs text-red-400">
                  <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                  <span>{error}</span>
                </div>
              )}
            </>
          )}

          {/* ── Loading ── */}
          {loading && (
            <div className="flex flex-col items-center justify-center py-12 gap-4">
              <div className="relative">
                <Loader2 className="w-10 h-10 text-blue-400 animate-spin" />
                <Sparkles className="w-5 h-5 text-blue-300 absolute -top-1 -right-1 animate-pulse" />
              </div>
              <div className="text-sm text-muted-foreground">AI 正在分析市场环境并生成最优配置...</div>
              <div className="text-xs text-muted-foreground/50">这可能需要 5-15 秒</div>
            </div>
          )}

          {/* ── Result / Diff View ── */}
          {hasConfig && !loading && (
            <>
              {/* Source Badge */}
              <div className="flex items-center gap-2 mb-2">
                <span className={cn(
                  'text-xs font-semibold px-2.5 py-1 rounded-full',
                  result!.source === 'llm'
                    ? 'bg-blue-500/20 text-blue-400'
                    : result!.source === 'fallback'
                      ? 'bg-yellow-500/20 text-yellow-400'
                      : 'bg-red-500/20 text-red-400',
                )}>
                  {result!.source === 'llm' ? 'LLM 动态生成' : '模板回退'}
                </span>
              </div>

              {/* Engine Diff */}
              <DiffSection
                title="引擎参数"
                items={buildEngineDiff(currentEngine, result!.config.engine)}
              />

              {/* Risk Gate Diff */}
              <DiffSection
                title="风控参数"
                items={buildRiskDiff(currentRisk, result!.config.risk_gate)}
              />

              {/* Strategy Enable / Params Diff */}
              <DiffSection
                title="策略启用"
                items={buildStrategyEnableDiff(currentPanels, result!.config.strategies)}
              />

              {/* Strategy Params Diff */}
              {buildStrategyParamsDiff(currentPanels, result!.config.strategies).length > 0 && (
                <DiffSection
                  title="策略参数变更"
                  items={buildStrategyParamsDiff(currentPanels, result!.config.strategies)}
                />
              )}

              {/* Reasoning */}
              {result!.config.reasoning && (
                <div className="p-3 rounded-lg bg-muted/20 border border-border/50">
                  <div className="text-xs font-semibold text-muted-foreground mb-1">AI 建议</div>
                  <div className="text-xs text-foreground/80">{result!.config.reasoning}</div>
                </div>
              )}

              {/* Error note */}
              {error && (
                <div className="flex items-start gap-2 p-2 rounded-lg border border-yellow-500/30 bg-yellow-500/5 text-xs text-yellow-400">
                  <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                  <span>LLM 调用失败，已使用模板回退: {error}</span>
                </div>
              )}
            </>
          )}
        </div>

        {/* ── Footer ── */}
        {hasConfig && !loading && (
          <DialogFooter className="gap-2">
            <button
              onClick={handleReset}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              重新配置
            </button>
            <button
              onClick={handleApply}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 border border-blue-500/30 transition-colors"
            >
              <Check className="w-3.5 h-3.5" />
              确认应用配置
            </button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}

// ════════════════════════════════════════════════════════
//  Diff Helpers
// ════════════════════════════════════════════════════════

interface DiffItem {
  label: string
  old: string
  new: string
  changed: boolean
}

function DiffSection({ title, items }: { title: string; items: DiffItem[] }) {
  return (
    <div className="rounded-lg border border-border/50 bg-muted/10 overflow-hidden">
      <div className="px-3 py-2 border-b border-border/30 text-xs font-semibold text-muted-foreground">{title}</div>
      <div className="divide-y divide-border/20">
        {items.map((item, i) => (
          <div key={i} className="flex items-center justify-between px-3 py-1.5 text-xs">
            <span className="text-muted-foreground w-36 truncate">{item.label}</span>
            <div className="flex items-center gap-2 font-mono">
              <span className={cn(item.changed ? 'text-red-400/60 line-through' : 'text-foreground/40')}>{item.old}</span>
              {item.changed && (
                <>
                  <ArrowRight className="w-3 h-3 text-muted-foreground/40" />
                  <span className="text-green-400 font-semibold">{item.new}</span>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

const ENGINE_LABELS: Record<string, string> = {
  min_monthly_value: '最低月收益',
  max_position_usd: '最大单仓',
  max_total_volume_7d: '7日最大总量',
  max_holding_days: '最大持仓天数',
}

const RISK_LABELS: Record<string, string> = {
  max_daily_volume_per_exchange: '日均量上限/所',
  max_weekly_volume_per_exchange: '周均量上限/所',
  max_daily_loss_pct: '日最大亏损%',
}

function buildEngineDiff(current: EngineForm, generated: Record<string, number>): DiffItem[] {
  return Object.entries(ENGINE_LABELS).map(([key, label]) => {
    const oldVal = (current as any)[key] ?? 0
    const newVal = generated[key] ?? oldVal
    const oldStr = formatDiffVal(oldVal, key)
    const newStr = formatDiffVal(newVal, key)
    return { label, old: oldStr, new: newStr, changed: oldStr !== newStr }
  })
}

function buildRiskDiff(current: RiskForm, generated: Record<string, number>): DiffItem[] {
  return Object.entries(RISK_LABELS).map(([key, label]) => {
    const oldVal = (current as any)[key] ?? 0
    const newVal = generated[key] ?? oldVal
    const oldStr = formatDiffVal(oldVal, key)
    const newStr = formatDiffVal(newVal, key)
    return { label, old: oldStr, new: newStr, changed: oldStr !== newStr }
  })
}

function buildStrategyEnableDiff(
  currentPanels: Record<string, StrategyPanelState>,
  strategies: Record<string, { enabled: boolean; params: Record<string, any>; risk_overrides: Record<string, any> }>,
): DiffItem[] {
  return Object.keys(STRATEGY_META).map(sid => {
    const oldEnabled = currentPanels[sid]?.enabled ?? false
    const newEnabled = strategies[sid]?.enabled ?? oldEnabled
    const oldStr = oldEnabled ? '启用' : '禁用'
    const newStr = newEnabled ? '启用' : '禁用'
    const meta = STRATEGY_META[sid]
    return { label: `${sid} ${meta?.name ?? ''}`, old: oldStr, new: newStr, changed: oldEnabled !== newEnabled }
  })
}

function buildStrategyParamsDiff(
  currentPanels: Record<string, StrategyPanelState>,
  strategies: Record<string, { enabled: boolean; params: Record<string, any>; risk_overrides: Record<string, any> }>,
): DiffItem[] {
  const items: DiffItem[] = []
  for (const sid of Object.keys(STRATEGY_META)) {
    const oldParams = currentPanels[sid]?.params ?? {}
    const newParams = strategies[sid]?.params ?? {}
    for (const [key, newVal] of Object.entries(newParams)) {
      const oldVal = oldParams[key]
      if (oldVal === undefined) continue
      const oldStr = fmt(Number(oldVal), 4)
      const newStr = fmt(Number(newVal), 4)
      if (oldStr !== newStr) {
        items.push({ label: `${sid}.${key}`, old: oldStr, new: newStr, changed: true })
      }
    }
    // Also check risk_overrides
    const oldOverrides = currentPanels[sid]?.overrides ?? {}
    const newOverrides = strategies[sid]?.risk_overrides ?? {}
    for (const [key, newVal] of Object.entries(newOverrides)) {
      if (key === 'enabled') continue
      const oldVal = oldOverrides[key]
      if (oldVal === undefined && newVal === undefined) continue
      const oldStr = oldVal != null ? fmt(Number(oldVal), 2) : '-'
      const newStr = newVal != null ? fmt(Number(newVal), 2) : '-'
      if (oldStr !== newStr) {
        items.push({ label: `${sid}.${key}`, old: oldStr, new: newStr, changed: true })
      }
    }
  }
  return items
}

function formatDiffVal(val: any, key: string): string {
  const n = Number(val)
  if (!Number.isFinite(n)) return '-'
  if (key.includes('pct') || key.includes('PCT')) return fmt(n * 100, 2) + '%'
  if (n >= 10000) return '$' + fmt(n, 0)
  if (n >= 1) return '$' + fmt(n, 2)
  return fmt(n, 4)
}
