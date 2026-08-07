/**
 * 锁仓强度配置 — 模拟盘 / 实盘独立调节
 */

import React, { useCallback, useEffect, useState } from 'react'
import {
  Shield, ShieldOff, Save, RefreshCw, Info, FlaskConical, Wallet,
  ChevronDown, ChevronUp,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { toast } from 'react-hot-toast'

interface EffectiveProfile {
  disable_loss_locks: boolean
  symbol_daily_loss_pct: number
  global_extreme_drawdown: number
  global_extreme_daily_loss_pct: number
  consecutive_loss_protection: boolean
  mental_loss_to_frozen: number
  risk_score_block_threshold: number | null
  paper_risk_gate: boolean
  strategy_guard: boolean
  ranging_pause: boolean
  preset_label: string
  strength: number
}

interface ModeState {
  strength: number
  preset_label: string
  updated_at: string | null
  effective: EffectiveProfile
}

interface LockStrengthResponse {
  paper: ModeState
  live: ModeState
  presets: Array<{
    strength: number
    label: string
    paper_summary: string
    live_summary: string
  }>
}

const PRESET_MARKS = [0, 25, 50, 75, 100]
const PRESET_LABELS: Record<number, string> = {
  0: '关闭',
  25: '宽松',
  50: '标准',
  75: '偏紧',
  100: '严格',
}

function formatPct(v: number) {
  if (v >= 0.5) return '—'
  return `${(v * 100).toFixed(0)}%`
}

function ProfileDetails({ profile, mode }: { profile: EffectiveProfile; mode: 'paper' | 'live' }) {
  if (profile.disable_loss_locks) {
    return (
      <p className="text-sm text-emerald-600 dark:text-emerald-400">
        {mode === 'paper' ? '训练模式：亏损不会触发锁仓，AI 可持续试错。' : '当前档位最宽松。'}
      </p>
    )
  }
  const items = [
    `单币日亏 > ${formatPct(profile.symbol_daily_loss_pct)} → 冻结该币`,
    `总回撤 > ${formatPct(profile.global_extreme_drawdown)} → 进入防守`,
    `全局日亏 > ${formatPct(profile.global_extreme_daily_loss_pct)} → 熔断`,
    profile.consecutive_loss_protection
      ? `连亏 ${profile.mental_loss_to_frozen} 笔 → 心理冻结`
      : '连亏保护：关',
    profile.risk_score_block_threshold != null
      ? `风险分 > ${profile.risk_score_block_threshold} → 禁止开新仓`
      : '风险分拦截：关',
    mode === 'paper' && profile.paper_risk_gate ? '模拟盘统一风控门：开' : null,
    profile.strategy_guard ? '低胜率策略冷却：开' : '低胜率策略冷却：关',
    profile.ranging_pause ? '震荡市暂停短/中线：开' : '震荡市暂停：关',
  ].filter(Boolean) as string[]

  return (
    <ul className="text-xs text-muted-foreground space-y-1">
      {items.map((t) => (
        <li key={t} className="flex items-start gap-1.5">
          <span className="text-purple-500 mt-0.5">•</span>
          <span>{t}</span>
        </li>
      ))}
    </ul>
  )
}

function ModeCard({
  mode,
  title,
  subtitle,
  icon: Icon,
  accent,
  state,
  draftStrength,
  onStrengthChange,
  onSave,
  saving,
}: {
  mode: 'paper' | 'live'
  title: string
  subtitle: string
  icon: React.ElementType
  accent: string
  state: ModeState | null
  draftStrength: number
  onStrengthChange: (v: number) => void
  onSave: () => void
  saving: boolean
}) {
  const [expanded, setExpanded] = useState(true)
  const dirty = state != null && draftStrength !== state.strength
  const previewLabel = PRESET_LABELS[
    PRESET_MARKS.reduce((a, b) =>
      Math.abs(b - draftStrength) < Math.abs(a - draftStrength) ? b : a
    )
  ]

  return (
    <div className={cn(
      'rounded-2xl border bg-card/50 backdrop-blur-sm overflow-hidden',
      accent,
    )}>
      <div className="p-5 border-b border-border/50">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className={cn(
              'w-10 h-10 rounded-xl flex items-center justify-center',
              mode === 'paper' ? 'bg-emerald-500/15 text-emerald-600' : 'bg-amber-500/15 text-amber-600',
            )}>
              <Icon className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-semibold text-base">{title}</h3>
              <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>
            </div>
          </div>
          <span className={cn(
            'text-xs font-bold px-2.5 py-1 rounded-full shrink-0',
            draftStrength < 15
              ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
              : draftStrength < 50
                ? 'bg-blue-500/15 text-blue-700 dark:text-blue-300'
                : draftStrength < 75
                  ? 'bg-orange-500/15 text-orange-700 dark:text-orange-300'
                  : 'bg-red-500/15 text-red-700 dark:text-red-300',
          )}>
            {previewLabel} · {draftStrength}
          </span>
        </div>
      </div>

      <div className="p-5 space-y-5">
        <div>
          <div className="flex justify-between text-xs text-muted-foreground mb-2">
            <span>锁仓强度</span>
            <span>{draftStrength} / 100</span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={draftStrength}
            onChange={(e) => onStrengthChange(Number(e.target.value))}
            className="w-full h-2 rounded-lg appearance-none cursor-pointer accent-purple-600 bg-muted"
          />
          <div className="flex justify-between mt-2">
            {PRESET_MARKS.map((mark) => (
              <button
                key={mark}
                type="button"
                onClick={() => onStrengthChange(mark)}
                className={cn(
                  'text-[10px] px-1.5 py-0.5 rounded transition-colors',
                  Math.abs(draftStrength - mark) <= 3
                    ? 'bg-purple-600/20 text-purple-700 dark:text-purple-300 font-semibold'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {PRESET_LABELS[mark]}
              </button>
            ))}
          </div>
        </div>

        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground w-full"
        >
          <Info className="w-3.5 h-3.5" />
          当前档位会触发什么？
          {expanded ? <ChevronUp className="w-3.5 h-3.5 ml-auto" /> : <ChevronDown className="w-3.5 h-3.5 ml-auto" />}
        </button>
        {expanded && state && (
          <ProfileDetails profile={state.effective} mode={mode} />
        )}

        <button
          type="button"
          onClick={onSave}
          disabled={!dirty || saving}
          className={cn(
            'w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-medium transition-all',
            dirty
              ? 'bg-purple-600 hover:bg-purple-700 text-white shadow-lg shadow-purple-500/20'
              : 'bg-muted text-muted-foreground cursor-not-allowed',
          )}
        >
          <Save className="w-4 h-4" />
          {saving ? '保存中…' : dirty ? '保存并生效' : '已是最新配置'}
        </button>
      </div>
    </div>
  )
}

export default function LockStrengthPanel() {
  const [data, setData] = useState<LockStrengthResponse | null>(null)
  const [paperDraft, setPaperDraft] = useState(0)
  const [liveDraft, setLiveDraft] = useState(50)
  const [loading, setLoading] = useState(true)
  const [savingMode, setSavingMode] = useState<'paper' | 'live' | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/risk/lock-strength')
      if (!res.ok) throw new Error(await res.text())
      const json: LockStrengthResponse = await res.json()
      setData(json)
      setPaperDraft(json.paper.strength)
      setLiveDraft(json.live.strength)
    } catch (e) {
      console.error('[LockStrength]', e)
      toast.error('加载锁仓配置失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const saveMode = async (mode: 'paper' | 'live', strength: number) => {
    setSavingMode(mode)
    try {
      const res = await fetch('/api/risk/lock-strength', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, strength }),
      })
      if (!res.ok) throw new Error(await res.text())
      const json: LockStrengthResponse = await res.json()
      setData(json)
      setPaperDraft(json.paper.strength)
      setLiveDraft(json.live.strength)
      toast.success(mode === 'paper' ? '模拟盘锁仓强度已更新' : '实盘锁仓强度已更新')
    } catch (e) {
      console.error('[LockStrength] save', e)
      toast.error('保存失败，请重试')
    } finally {
      setSavingMode(null)
    }
  }

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground gap-2">
        <RefreshCw className="w-5 h-5 animate-spin" />
        加载锁仓配置…
      </div>
    )
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold flex items-center gap-2">
            <Shield className="w-5 h-5 text-purple-500" />
            锁仓强度
          </h2>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            分别调节模拟盘和实盘的「亏多了要不要停手」。模拟盘建议关或宽松，方便 AI 训练；
            实盘建议标准或偏紧，保护真金白银。
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg border"
        >
          <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
          刷新
        </button>
      </div>

      <div className="rounded-xl border border-blue-500/20 bg-blue-500/5 p-4 flex gap-3 text-sm">
        <ShieldOff className="w-5 h-5 text-blue-500 shrink-0 mt-0.5" />
        <div className="text-muted-foreground space-y-1">
          <p><strong className="text-foreground">模拟盘</strong>：强度 &lt; 8 视为「关闭」，不会因亏损锁仓。</p>
          <p><strong className="text-foreground">实盘</strong>：最低档仍保留极端回撤保护，无法完全关闭（防止大亏）。</p>
          <p className="text-xs">保存后立即生效，无需重启后端；调低模拟盘强度会自动解除当前锁仓。</p>
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        <ModeCard
          mode="paper"
          title="模拟盘"
          subtitle="Paper · AI 训练 / 回测"
          icon={FlaskConical}
          accent="border-emerald-500/20"
          state={data?.paper ?? null}
          draftStrength={paperDraft}
          onStrengthChange={setPaperDraft}
          onSave={() => saveMode('paper', paperDraft)}
          saving={savingMode === 'paper'}
        />
        <ModeCard
          mode="live"
          title="实盘"
          subtitle="Live · 真实资金"
          icon={Wallet}
          accent="border-amber-500/20"
          state={data?.live ?? null}
          draftStrength={liveDraft}
          onStrengthChange={setLiveDraft}
          onSave={() => saveMode('live', liveDraft)}
          saving={savingMode === 'live'}
        />
      </div>

      {data?.presets && (
        <div className="rounded-xl border p-4">
          <h4 className="text-sm font-medium mb-3">档位对照表</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted-foreground border-b">
                  <th className="text-left py-2 pr-4">档位</th>
                  <th className="text-left py-2 pr-4">模拟盘</th>
                  <th className="text-left py-2">实盘</th>
                </tr>
              </thead>
              <tbody>
                {data.presets.map((p) => (
                  <tr key={p.strength} className="border-b border-border/50 last:border-0">
                    <td className="py-2 pr-4 font-medium whitespace-nowrap">
                      {p.label} ({p.strength})
                    </td>
                    <td className="py-2 pr-4 text-muted-foreground">{p.paper_summary}</td>
                    <td className="py-2 text-muted-foreground">{p.live_summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
