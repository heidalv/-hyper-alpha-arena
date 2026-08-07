import { useCallback, useEffect, useState } from 'react'
import { Bot, RefreshCw } from 'lucide-react'
import { toast } from 'react-hot-toast'
import {
  getAccounts,
  getArbitrageProfile,
  saveArbitrageProfile,
  aiGenerateArbitrageProfile,
  type ArbitrageProfile,
} from '@/lib/api'
import { getArbitragePaperAccounts } from '@/lib/arbitrageApi'
import ArbitrageSetupGuide from './ArbitrageSetupGuide'
import ArbitrageTraderProfilePanel from './ArbitrageTraderProfilePanel'

interface LLMConfig {
  id: number
  name: string
  is_active: boolean
}

export default function ArbitrageTraderConfigTab() {
  const [traders, setTraders] = useState<Array<{
    id: number
    name: string
    llm_config_id?: number | null
    llm_config_id_deep?: number | null
    llm_config_name?: string | null
    llm_config_name_deep?: string | null
  }>>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [profile, setProfile] = useState<ArbitrageProfile | null>(null)
  const [paperAccounts, setPaperAccounts] = useState<Array<{ id: number; name: string; total_equity: number }>>([])
  const [llmConfigs, setLlmConfigs] = useState<LLMConfig[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const llmName = (id?: number | null, fallback?: string | null) => {
    if (!id) return fallback || ''
    return llmConfigs.find(c => c.id === id)?.name || fallback || `#${id}`
  }

  const loadTraders = useCallback(async () => {
    const data = await getAccounts()
    const rows = data
      .filter((a: any) => a.trading_mode !== 'paper')
      .map((a: any) => ({
        id: a.id,
        name: a.name,
        llm_config_id: a.llm_config_id,
        llm_config_id_deep: a.llm_config_id_deep,
        llm_config_name: a.llm_config_name,
        llm_config_name_deep: a.llm_config_name_deep,
      }))
    setTraders(rows)
    setSelectedId(prev => (prev && rows.some(r => r.id === prev) ? prev : rows[0]?.id ?? null))
  }, [])

  const loadMeta = useCallback(async () => {
    const [papers, llmRes] = await Promise.all([
      getArbitragePaperAccounts(),
      fetch('/api/llm-configs'),
    ])
    setPaperAccounts(papers.map(p => ({ id: p.id, name: p.name, total_equity: p.total_equity })))
    if (llmRes.ok) {
      const data = await llmRes.json()
      setLlmConfigs((data.items || []).filter((c: LLMConfig) => c.is_active))
    }
  }, [])

  const loadProfile = useCallback(async (traderId: number) => {
    setLoading(true)
    try {
      const p = await getArbitrageProfile(traderId)
      setProfile(p)
    } catch {
      setProfile(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadTraders()
    loadMeta()
  }, [loadTraders, loadMeta])

  useEffect(() => {
    if (selectedId) loadProfile(selectedId)
  }, [selectedId, loadProfile])

  const handleSave = async (p: ArbitrageProfile) => {
    if (!selectedId) return
    setSaving(true)
    try {
      const res = await saveArbitrageProfile(selectedId, { ...p, account_id: selectedId, enabled: true })
      setProfile(res.profile)
      toast.success('套利配置已保存')
    } catch (e: any) {
      toast.error(e?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleAiGenerate = async () => {
    if (!selectedId) return
    setSaving(true)
    try {
      const res = await aiGenerateArbitrageProfile(selectedId, {
        risk_profile: 'balanced',
        total_equity: 300,
        goal: '300U 小资金专用套利默认方案',
      })
      setProfile(prev => ({
        ...(prev || { account_id: selectedId, enabled: true }),
        ...res.profile,
        enabled: true,
      }))
      toast.success('AI 草案已生成，请确认后保存')
    } catch (e: any) {
      toast.error(e?.message || 'AI 生成失败')
    } finally {
      setSaving(false)
    }
  }

  const selectedTrader = traders.find(t => t.id === selectedId)

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-card p-5">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <Bot className="w-5 h-5 text-amber-500" /> 交易员套利配置
        </h2>
        <p className="text-sm text-muted-foreground mt-1">
          策略授权、Paper 绑定在此配置。大模型只在 AI 交易员里配一次（分析 + 执行），套利自动共用。
        </p>
      </div>

      <ArbitrageSetupGuide variant="trader" />

      <div className="rounded-xl border border-border bg-card p-5 space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm font-medium">选择交易员</label>
          <select
            value={selectedId ?? ''}
            onChange={e => setSelectedId(Number(e.target.value))}
            className="rounded-lg border border-border bg-background px-3 py-2 text-sm min-w-[200px]"
          >
            {traders.length === 0 && <option value="">暂无 AI 交易员</option>}
            {traders.map(t => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => { loadTraders(); if (selectedId) loadProfile(selectedId) }}
            className="px-3 py-2 rounded-lg bg-secondary hover:bg-secondary/80 text-sm flex items-center gap-1.5"
          >
            <RefreshCw className="w-4 h-4" /> 刷新
          </button>
        </div>

        {selectedId ? (
          <ArbitrageTraderProfilePanel
            profile={profile}
            loading={loading}
            saving={saving}
            traderName={selectedTrader?.name}
            analysisModelId={selectedTrader?.llm_config_id_deep ?? selectedTrader?.llm_config_id}
            executionModelId={selectedTrader?.llm_config_id ?? selectedTrader?.llm_config_id_deep}
            analysisModelName={llmName(selectedTrader?.llm_config_id_deep, selectedTrader?.llm_config_name_deep)}
            executionModelName={llmName(selectedTrader?.llm_config_id, selectedTrader?.llm_config_name)}
            arbitragePaperAccounts={paperAccounts}
            onChange={setProfile}
            onSave={handleSave}
            onAiGenerate={handleAiGenerate}
          />
        ) : (
          <p className="text-sm text-muted-foreground">请先在 AI 交易员管理中创建交易员。</p>
        )}
      </div>
    </div>
  )
}
