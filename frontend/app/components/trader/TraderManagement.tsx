/**
 * AI 交易员管理 — 统一页面
 * 左侧: 交易员列表
 * 右侧: 选中交易员的配置（AI触发参数 + 钱包）
 *
 * 大模型配置通过下拉选择「设置 → LLM 配置库」中的配置，不在此重复填写
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { cn } from '@/lib/utils'
import {
  Bot, Plus, Pencil, Trash2, Save, X,
  Power, PowerOff, RefreshCw, Clock, Activity, Wallet, Settings2, ExternalLink,
  CheckCircle, AlertTriangle, XCircle, ChevronDown, User, Zap, Shield, Target, Flame
} from 'lucide-react'
import {
  getAccounts,
  createAccount,
  updateAccount,
  deleteAccount,
  getPersonalityPresets,
  checkBuilderAuthorization,
  approveBuilder,
  getArbitrageProfile,
  saveArbitrageProfile,
  type TradingAccount,
  type TradingAccountCreate,
  type TraderPersonality,
  type UnauthorizedAccount,
  type ArbitrageProfile,
} from '@/lib/api'
import { AuthorizationModal } from '@/components/hyperliquid'
import UnifiedWalletConfigPanel from '@/components/trader/UnifiedWalletConfigPanel'
import { EXCHANGE_OPTIONS, EXCHANGE_NAMES } from '@/components/trader/ExchangeConstants'
import toast from 'react-hot-toast'
import { usePageActive } from '@/hooks/usePageActive'

interface AIAccount extends TradingAccount {
  model?: string
  base_url?: string
  llm_config_id?: number | null
  llm_config_name?: string | null
  llm_config_id_deep?: number | null
  llm_config_name_deep?: string | null
  personality?: TraderPersonality | null
  selected_exchange?: string
}

interface LLMConfig {
  id: number
  name: string
  provider: string
  model: string
  model_deep?: string | null
  base_url: string
  api_key_masked: string
  is_default: boolean
  is_active: boolean
}

interface StrategyConfig {
  interval_seconds: number
  enabled: boolean
  last_trigger_at?: string | null
  signal_pool_id?: number | null
}

interface PersonalityPreset {
  key: string
  display_name: string
  benchmark_trader: string
  description: string
  trading_style: string
  time_horizon: string
  risk_appetite: number
  min_confidence: number
  loss_tolerance: number
  win_aggression: number
  max_position_pct: number
  preferred_leverage: number
  max_leverage: number
  specialty_symbols: string
  special_skills: string
}

const STYLE_LABELS: Record<string, string> = {
  trend_following: '趋势跟踪', mean_reversion: '均值回归', breakout: '突破交易',
  momentum: '动量交易', scalping: '超短线', swing: '波段交易',
}

const HORIZON_LABELS: Record<string, string> = {
  scalper: '超短线', day_trader: '日内交易', swing_trader: '波段交易', position_trader: '趋势持仓',
}

const PRESET_ICONS: Record<string, string> = {
  jesse_livermore: '📈', george_soros: '🦅', paul_tudor_jones: '🛡️',
  stanley_druckenmiller: '🎯', ed_seykota: '🤖', mark_minervini: '⚡',
  ray_dalio: '⚖️', larry_williams: '🔥',
}

type RightTab = 'config' | 'wallet' | 'risk'

export default function TraderManagement() {
  const pageActive = usePageActive()
  const [accounts, setAccounts] = useState<AIAccount[]>([])
  const [paperAccounts, setPaperAccounts] = useState<AIAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<number | null>(null)

  // LLM Config library
  const [llmConfigs, setLlmConfigs] = useState<LLMConfig[]>([])

  // Personality presets
  const [presets, setPresets] = useState<PersonalityPreset[]>([])

  // Create / Edit form
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [formName, setFormName] = useState('')
  const [formLlmConfigId, setFormLlmConfigId] = useState<number | null>(null)
  const [formLlmConfigIdDeep, setFormLlmConfigIdDeep] = useState<number | null>(null)
  const [formSelectedExchange, setFormSelectedExchange] = useState<string>('asterdex')
  const [formAutoTrading, setFormAutoTrading] = useState(true)
  const [formArbitrageEnabled, setFormArbitrageEnabled] = useState(false)
  const [saving, setSaving] = useState(false)

  // Exchange credentials (for readiness check)
  const [exchangeCredentials, setExchangeCredentials] = useState<any[]>([])

  // Risk control config
  const [riskConfig, setRiskConfig] = useState<any>(null)
  const [riskLoading, setRiskLoading] = useState(false)

  // Personality form state
  const [formPresetKey, setFormPresetKey] = useState<string | null>(null)
  const [formPersonality, setFormPersonality] = useState<TraderPersonality>({})
  const [showPersonalityDetail, setShowPersonalityDetail] = useState(false)

  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)
  const [toggleLoadingId, setToggleLoadingId] = useState<number | null>(null)

  // Auth modal
  const [authModalOpen, setAuthModalOpen] = useState(false)
  const [unauthorizedAccounts, setUnauthorizedAccounts] = useState<UnauthorizedAccount[]>([])

  // Right panel
  const [rightTab, setRightTab] = useState<RightTab>('config')

  // Per-trader strategy config
  const [strategyConfig, setStrategyConfig] = useState<StrategyConfig | null>(null)
  const [triggerInterval, setTriggerInterval] = useState('150')
  const [strategySaving, setStrategySaving] = useState(false)
  const [strategyLoading, setStrategyLoading] = useState(false)

  // 积分套利开关状态（详情配置在套利中心）
  const [arbEnabled, setArbEnabled] = useState(false)

  // Live execution status (polled)
  const [executionStatus, setExecutionStatus] = useState<Record<string, any>>({})

  const selectedAccount = accounts.find(a => a.id === selectedId) || null

  useEffect(() => {
    const ex = selectedAccount?.selected_exchange || 'hyperliquid'
    if (ex === 'hyperliquid' && rightTab === 'wallet') {
      setRightTab('config')
    }
  }, [selectedAccount?.id, selectedAccount?.selected_exchange, rightTab])

  // ── Load data ──

  const loadAccounts = useCallback(async () => {
    try {
      setLoading(true)
      const data = await getAccounts()
      // 过滤掉模拟账户（trading_mode='paper'），交易员管理仅显示真实交易账户
      const traderAccounts = data.filter((a: any) => a.trading_mode !== 'paper')
      setPaperAccounts(data.filter((a: any) => (a.account_type || '').toUpperCase() === 'PAPER' || a.trading_mode === 'paper'))
      setAccounts(traderAccounts)
      if (traderAccounts.length > 0 && !selectedId) {
        setSelectedId(traderAccounts[0].id)
      }
    } catch (e) {
      console.error('Failed to load accounts:', e)
    } finally {
      setLoading(false)
    }
  }, [selectedId])

  const loadLLMConfigs = useCallback(async () => {
    try {
      const res = await fetch('/api/llm-configs', { cache: 'no-store' })
      if (res.ok) {
        const data = await res.json()
        setLlmConfigs((data.items || []).filter((c: LLMConfig) => c.is_active))
      }
    } catch (e) {
      console.error('Failed to load LLM configs:', e)
    }
  }, [])

  const loadExchangeCredentials = useCallback(async () => {
    try {
      const res = await fetch('/api/exchange/credentials?user_id=1')
      if (res.ok) {
        const data = await res.json()
        setExchangeCredentials(data)
      }
    } catch { /* silent */ }
  }, [])

  const loadRiskConfig = useCallback(async (accountId: number) => {
    try {
      setRiskLoading(true)
      const res = await fetch(`/api/risk/${accountId}/config`)
      if (res.ok) {
        const data = await res.json()
        setRiskConfig(data)
      }
    } catch { /* silent */ }
    finally { setRiskLoading(false) }
  }, [])

  const pollExecutionStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/account/strategies/status')
      if (res.ok) {
        const data = await res.json()
        setExecutionStatus(data)
      }
    } catch { /* silent */ }
  }, [])

  const loadPresets = useCallback(async () => {
    try {
      const data = await getPersonalityPresets()
      setPresets(data)
    } catch (e) {
      console.error('Failed to load personality presets:', e)
    }
  }, [])

  const initialLoadDone = useRef(false)
  useEffect(() => {
    if (!initialLoadDone.current) {
      initialLoadDone.current = true
      loadAccounts()
      loadLLMConfigs()
      loadExchangeCredentials()
      loadPresets()
    }
    if (!pageActive) return
    pollExecutionStatus()
    const timer = setInterval(pollExecutionStatus, 8000)
    return () => clearInterval(timer)
  }, [pageActive])

  // ── Load per-trader strategy ──

  const loadStrategy = useCallback(async (accountId: number) => {
    setStrategyLoading(true)
    try {
      const res = await fetch(`/api/account/${accountId}/strategy`)
      if (res.ok) {
        const data: StrategyConfig = await res.json()
        setStrategyConfig(data)
        setTriggerInterval((data.interval_seconds ?? 150).toString())
      }
    } catch (e) {
      console.error('Failed to load strategy:', e)
    } finally {
      setStrategyLoading(false)
    }
  }, [])

  const loadArbitrageEnabled = useCallback(async (accountId: number) => {
    try {
      const profile = await getArbitrageProfile(accountId)
      setArbEnabled(Boolean(profile.enabled))
    } catch {
      setArbEnabled(false)
    }
  }, [])

  useEffect(() => {
    if (selectedId) loadStrategy(selectedId)
    if (selectedId) loadArbitrageEnabled(selectedId)
  }, [selectedId, loadStrategy, loadArbitrageEnabled])

  const syncArbitrageEnabledFlag = async (accountId: number, enabled: boolean) => {
    let existing: ArbitrageProfile | null = null
    try {
      existing = await getArbitrageProfile(accountId)
    } catch { /* new profile */ }
    await saveArbitrageProfile(accountId, {
      id: existing?.id ?? null,
      account_id: accountId,
      enabled,
      mode: existing?.mode ?? 'paper',
      paper_account_id: null,
      paper_account_mode: 'dedicated_arbitrage_paper',
      arbitrage_paper_account_id: existing?.arbitrage_paper_account_id ?? null,
      enabled_strategies: existing?.enabled_strategies ?? ['S8'],
      strategy_overrides: existing?.strategy_overrides ?? {},
      wash_trade_profile: existing?.wash_trade_profile ?? 'balanced',
      ai_config_source: existing?.ai_config_source ?? 'manual',
      linked_llm_config_id: existing?.linked_llm_config_id ?? null,
      strategy_llm_config_id: existing?.strategy_llm_config_id ?? null,
      execution_llm_config_id: existing?.execution_llm_config_id ?? null,
    })
  }

  const handleSaveStrategy = async () => {
    if (!selectedId) return
    setStrategySaving(true)
    try {
      const interval = parseInt(triggerInterval)
      if (!Number.isFinite(interval) || interval < 30) {
        toast.error('分析间隔至少 30 秒')
        setStrategySaving(false)
        return
      }
      const res = await fetch(`/api/account/${selectedId}/strategy`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          interval_seconds: interval,
          enabled: true,
          trigger_mode: 'unified',
          tick_batch_size: 1,
          signal_pool_id: strategyConfig?.signal_pool_id ?? null,
        })
      })
      if (!res.ok) throw new Error('保存失败')
      const result = await res.json()
      setTriggerInterval((result.interval_seconds ?? 150).toString())
      toast.success('配置已保存')
    } catch {
      toast.error('保存失败')
    } finally {
      setStrategySaving(false)
    }
  }

  const refresh = () => {
    loadAccounts()
    loadLLMConfigs()
    if (selectedId) loadStrategy(selectedId)
  }

  // ── Form helpers ──

  const resetForm = () => {
    setShowForm(false)
    setEditingId(null)
    setFormName('')
    setFormLlmConfigId(null)
    setFormLlmConfigIdDeep(null)
    setFormSelectedExchange('hyperliquid')
    setFormAutoTrading(true)
    setFormArbitrageEnabled(false)
    setFormPresetKey(null)
    setFormPersonality({})
    setShowPersonalityDetail(false)
  }

  const applyPreset = (preset: PersonalityPreset) => {
    setFormPresetKey(preset.key)
    setFormPersonality({
      preset_key: preset.key,
      display_name: preset.display_name,
      benchmark_trader: preset.benchmark_trader,
      description: preset.description,
      trading_style: preset.trading_style,
      time_horizon: preset.time_horizon,
      risk_appetite: preset.risk_appetite,
      min_confidence: preset.min_confidence,
      loss_tolerance: preset.loss_tolerance,
      win_aggression: preset.win_aggression,
      max_position_pct: preset.max_position_pct,
      preferred_leverage: preset.preferred_leverage,
      max_leverage: preset.max_leverage,
      specialty_symbols: preset.specialty_symbols,
      special_skills: preset.special_skills,
    })
    setShowPersonalityDetail(true)
  }

  const openCreateForm = () => {
    resetForm()
    setShowForm(true)
    const defaultCfg = llmConfigs.find(c => c.is_default)
    if (defaultCfg) {
      setFormLlmConfigId(defaultCfg.id)
      setFormLlmConfigIdDeep(defaultCfg.id)
    } else if (llmConfigs.length > 0) {
      setFormLlmConfigId(llmConfigs[0].id)
      setFormLlmConfigIdDeep(llmConfigs[0].id)
    }
  }

  const selectUnifiedLlmConfig = (cfgId: number) => {
    setFormLlmConfigId(cfgId)
    // 同一条双模型配置：后端按 tier=quick/deep 自动选 Flash 或 Pro
    setFormLlmConfigIdDeep(cfgId)
  }

  const formatLlmConfigSummary = (cfg: LLMConfig) => {
    if (cfg.model_deep) {
      return `Flash: ${cfg.model} · Pro: ${cfg.model_deep}（系统自动切换）`
    }
    return cfg.model
  }

  const isDualModelConfig = (cfg: LLMConfig) => Boolean(cfg.model_deep)

  const openEditForm = (acc: AIAccount) => {
    setEditingId(acc.id)
    setShowForm(true)
    setFormName(acc.name)
    setFormLlmConfigId(acc.llm_config_id || null)
    setFormLlmConfigIdDeep(acc.llm_config_id_deep || null)
    setFormSelectedExchange(acc.selected_exchange || 'hyperliquid')
    setFormAutoTrading(acc.auto_trading_enabled ?? true)
    getArbitrageProfile(acc.id)
      .then(p => setFormArbitrageEnabled(Boolean(p.enabled)))
      .catch(() => setFormArbitrageEnabled(false))
    if (acc.personality) {
      setFormPersonality(acc.personality)
      setFormPresetKey(null)
      setShowPersonalityDetail(true)
    }
  }

  const handleSave = async () => {
    if (!formName.trim()) {
      toast.error('请输入交易员名称')
      return
    }
    const linkedConfigId = formLlmConfigId || formLlmConfigIdDeep
    if (!linkedConfigId && !editingId) {
      toast.error('请选择大模型配置')
      return
    }
    // 分析与执行共用同一个模型是允许的（单模型部署是常态）

    setSaving(true)
    try {
      const payload: TradingAccountCreate & Record<string, any> = {
        name: formName.trim(),
        auto_trading_enabled: formAutoTrading,
        selected_exchange: formSelectedExchange,
      }

      const unifiedConfigId = formLlmConfigId || formLlmConfigIdDeep
      if (unifiedConfigId) {
        payload.llm_config_id = unifiedConfigId
        payload.llm_config_id_deep = formLlmConfigIdDeep || unifiedConfigId
        const cfg = llmConfigs.find(c => c.id === unifiedConfigId)
        if (cfg) {
          payload.model = cfg.model
          payload.base_url = cfg.base_url
        }
      }

      // Attach personality if any trait is set
      if (formPersonality.display_name || formPersonality.preset_key || formPersonality.trading_style) {
        payload.personality = { ...formPersonality }
      }

      let targetId = editingId
      if (editingId) {
        await updateAccount(editingId, payload)
        toast.success('交易员已更新')
      } else {
        payload.api_key = 'via-llm-config-library'
        const created = await createAccount(payload)
        targetId = created.id
        toast.success('交易员已创建')
      }

      if (targetId) {
        try {
          await syncArbitrageEnabledFlag(targetId, formArbitrageEnabled)
          setArbEnabled(formArbitrageEnabled)
        } catch (e: any) {
          toast.error(e?.message || '套利开关保存失败')
        }
      }

      resetForm()
      await loadAccounts()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '操作失败'
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteAccount(id)
      setDeleteConfirmId(null)
      toast.success('交易员已删除')
      if (selectedId === id) setSelectedId(null)
      await loadAccounts()
    } catch {
      toast.error('删除失败')
    }
  }

  const handleToggleAutoTrading = async (account: AIAccount, nextValue: boolean) => {
    try {
      setToggleLoadingId(account.id)

      if (nextValue && account.has_mainnet_wallet && account.wallet_address) {
        const authStatus = await checkBuilderAuthorization(account.wallet_address)
        if (!authStatus.authorized) {
          try {
            const authResult = await approveBuilder(account.id)
            if (!authResult.success || authResult.result?.status === 'err') {
              setUnauthorizedAccounts([{
                account_id: account.id, account_name: account.name,
                wallet_address: account.wallet_address,
                max_fee: authStatus.max_fee, required_fee: authStatus.required_fee
              }])
              setAuthModalOpen(true)
              return
            }
          } catch {
            setUnauthorizedAccounts([{
              account_id: account.id, account_name: account.name,
              wallet_address: account.wallet_address,
              max_fee: authStatus.max_fee, required_fee: authStatus.required_fee
            }])
            setAuthModalOpen(true)
            return
          }
        }
      }

      await updateAccount(account.id, { auto_trading_enabled: nextValue })
      setAccounts(prev => prev.map(a => a.id === account.id ? { ...a, auto_trading_enabled: nextValue } : a))
      toast.success(nextValue ? `${account.name} 自动交易已开启` : `${account.name} 自动交易已暂停`)
    } catch {
      toast.error('操作失败')
    } finally {
      setToggleLoadingId(null)
    }
  }

  const handleAuthComplete = async () => {
    setAuthModalOpen(false)
    for (const ua of unauthorizedAccounts) {
      try {
        await updateAccount(ua.account_id, { auto_trading_enabled: true })
        setAccounts(prev => prev.map(a => a.id === ua.account_id ? { ...a, auto_trading_enabled: true } : a))
      } catch { /* ignore */ }
    }
    setUnauthorizedAccounts([])
  }

  // Find config name for display
  const getConfigDisplay = (acc: AIAccount) => {
    if (acc.llm_config_id) {
      const cfg = llmConfigs.find(c => c.id === acc.llm_config_id)
      return cfg ? cfg.name : acc.llm_config_name || `配置 #${acc.llm_config_id}`
    }
    return acc.model || '未配置'
  }

  // ── Render ──

  return (
    <div className="h-full w-full flex flex-col bg-background">
      {/* Top bar */}
      <div className="flex-shrink-0 px-6 py-4 border-b border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Bot className="w-6 h-6 text-purple-500" />
            <div>
              <h1 className="text-lg font-bold">AI 交易员管理</h1>
              <p className="text-xs text-muted-foreground">多交易员并行运行 · 独立大模型 · 独立策略</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {executionStatus.running && (
              <div className="flex items-center gap-1.5 text-xs text-green-600 dark:text-green-400 bg-green-500/10 px-2.5 py-1 rounded-full">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                {executionStatus.strategy_count || 0} 个交易员已加载
              </div>
            )}
            <button onClick={refresh} className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/50">
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 min-h-0 flex">
        {/* ── Left: Trader list ── */}
        <div className="w-80 flex-shrink-0 border-r border-border flex flex-col">
          <div className="p-4 border-b border-border">
            <button onClick={openCreateForm}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-purple-600 hover:bg-purple-700 text-white rounded-xl text-sm font-medium transition-colors">
              <Plus className="w-4 h-4" /> 新建交易员
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {loading && accounts.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground text-sm">加载中...</div>
            ) : accounts.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <Bot className="w-12 h-12 mx-auto mb-3 opacity-30" />
                <p className="text-sm font-medium">暂无交易员</p>
                <p className="text-xs mt-1">点击上方按钮创建第一个 AI 交易员</p>
              </div>
            ) : (
              accounts.map(acc => (
                <div key={acc.id}
                  onClick={() => { setSelectedId(acc.id); if (showForm && editingId !== acc.id) resetForm() }}
                  className={cn(
                    'rounded-xl border p-3 cursor-pointer transition-all group',
                    selectedId === acc.id
                      ? 'border-purple-500/50 bg-purple-500/5'
                      : 'border-border hover:border-foreground/20 hover:bg-muted/30'
                  )}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 min-w-0">
                      <div className={cn(
                        'w-2 h-2 rounded-full flex-shrink-0',
                        acc.auto_trading_enabled ? 'bg-green-500' : 'bg-gray-400'
                      )} />
                      <span className="font-medium text-sm truncate">{acc.name}</span>
                    </div>
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button onClick={(e) => { e.stopPropagation(); openEditForm(acc) }}
                        className="p-1 rounded hover:bg-muted"><Pencil className="w-3 h-3" /></button>
                      {deleteConfirmId === acc.id ? (
                        <div className="flex gap-1" onClick={e => e.stopPropagation()}>
                          <button onClick={() => handleDelete(acc.id)}
                            className="px-2 py-0.5 text-xs bg-red-600 text-white rounded">确认</button>
                          <button onClick={() => setDeleteConfirmId(null)}
                            className="px-2 py-0.5 text-xs bg-muted rounded">取消</button>
                        </div>
                      ) : (
                        <button onClick={(e) => { e.stopPropagation(); setDeleteConfirmId(acc.id) }}
                          className="p-1 rounded hover:bg-red-500/10 text-muted-foreground hover:text-red-500">
                          <Trash2 className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="mt-1.5 text-xs text-muted-foreground truncate">
                    {getConfigDisplay(acc)}
                  </div>
                  {acc.personality?.display_name && (
                    <div className="mt-1 flex items-center gap-1 text-[10px] text-purple-500">
                      <User className="w-2.5 h-2.5" />
                      {acc.personality.display_name}
                      {acc.personality.benchmark_trader && (
                        <span className="text-muted-foreground">· {acc.personality.benchmark_trader}</span>
                      )}
                    </div>
                  )}
                  <div className="mt-1 flex items-center justify-between">
                    {(() => {
                      const strat = (executionStatus.strategies || {})[acc.id]
                      const isExecuting = strat?.executing
                      if (isExecuting) {
                        return (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-500/10 text-blue-600 dark:text-blue-400 flex items-center gap-1">
                            <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
                            AI 决策中
                          </span>
                        )
                      }
                      const isLoaded = !!strat
    return (
                        <span className={cn(
                          'text-[10px] px-1.5 py-0.5 rounded-full',
                          acc.auto_trading_enabled && isLoaded
                            ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                            : acc.auto_trading_enabled
                              ? 'bg-yellow-500/10 text-yellow-600 dark:text-yellow-400'
                              : 'bg-muted text-muted-foreground'
                        )}>
                          {acc.auto_trading_enabled && isLoaded ? '并行运行中' :
                           acc.auto_trading_enabled ? '等待就绪' : '已暂停'}
                        </span>
                      )
                    })()}
                    <button
                      onClick={(e) => { e.stopPropagation(); handleToggleAutoTrading(acc, !acc.auto_trading_enabled) }}
                      disabled={toggleLoadingId === acc.id}
                      className={cn(
                        'p-1 rounded-lg transition-colors',
                        acc.auto_trading_enabled
                          ? 'text-green-500 hover:bg-green-500/10'
                          : 'text-muted-foreground hover:bg-muted'
                      )}>
                      {acc.auto_trading_enabled ? <Power className="w-3.5 h-3.5" /> : <PowerOff className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* ── Right: Config panel ── */}
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
          {/* Create / Edit form */}
          {showForm && (
            <div className="border-b border-border bg-muted/30 p-6 overflow-y-auto max-h-[80vh]">
              <div className="max-w-2xl">
                <h3 className="text-base font-semibold mb-4">
                  {editingId ? '编辑交易员' : '新建 AI 交易员'}
                </h3>
                <div className="space-y-4">
                  {/* Name */}
                  <div>
                    <label className="block text-sm text-muted-foreground mb-1">交易员名称 *</label>
                    <input type="text" value={formName} onChange={e => setFormName(e.target.value)}
                      placeholder="例如：BTC 主力交易员"
                      className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:border-purple-500 outline-none" />
                  </div>

                  {/* Exchange selector */}
                  <div>
                    <label className="block text-sm text-muted-foreground mb-1.5">
                      目标交易所 <span className="text-yellow-500">*</span>
                    </label>
                    <div className="grid grid-cols-3 gap-2">
                      {EXCHANGE_OPTIONS.map(ex => {
                        const cred = exchangeCredentials.find((c: any) => c.exchange === ex.id && c.enabled)
                        const isSelected = formSelectedExchange === ex.id
                        return (
                          <button key={ex.id} type="button"
                            onClick={() => setFormSelectedExchange(ex.id)}
                            className={cn(
                              'text-left px-3 py-2.5 rounded-xl border transition-all',
                              isSelected
                                ? 'border-purple-500 bg-purple-500/15 ring-2 ring-purple-500/40 shadow-sm shadow-purple-500/20'
                                : 'border-border hover:border-foreground/20 hover:bg-muted/30'
                            )}>
                            <div className="flex items-center gap-1.5">
                              <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: ex.color }} />
                              <span className={cn(
                                'font-semibold text-xs',
                                isSelected ? 'text-purple-300' : ''
                              )}>{ex.name}</span>
                              {isSelected && (
                                <div className="ml-auto w-3.5 h-3.5 rounded-full bg-purple-500 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1.5 h-1.5 rounded-full bg-white" />
                                </div>
                              )}
                            </div>
                            <div className="flex items-center gap-1 mt-1">
                              {ex.id === 'hyperliquid' ? (
                                <span className="text-[9px] text-muted-foreground">钱包模式</span>
                              ) : cred ? (
                                <span className="text-[9px] px-1 py-0.5 rounded bg-green-500/10 text-green-600">已配置</span>
                              ) : (
                                <span className="text-[9px] px-1 py-0.5 rounded bg-orange-500/10 text-orange-600">未配置</span>
                              )}
                            </div>
                          </button>
                        )
                      })}
                    </div>
                    <p className="text-[10px] text-muted-foreground/70 mt-1">
                      Hyperliquid 请在「交易所配置」中设置钱包；其他交易所使用全局 API 密钥
                    </p>
                  </div>

                  {/* 统一大模型配置：双模型条目由后端按任务自动选 Flash / Pro */}
                  {llmConfigs.length > 0 ? (
                    <div className="space-y-3">
                      <div>
                        <label className="block text-sm text-muted-foreground mb-1.5">
                          大模型配置 <span className="text-yellow-500">*</span>
                        </label>
                        <p className="text-[10px] text-muted-foreground/70 mb-2">
                          只需选一条配置。若该配置同时启用了 Flash + Pro，系统会按任务自动切换：
                          短线扫描/执行判断走 Flash，策略分析/多空辩论/OpenCode 走 Pro。
                        </p>
                        <div className="grid gap-1.5 max-h-[360px] overflow-y-auto pr-1">
                          {llmConfigs.map(cfg => {
                            const selected = formLlmConfigId === cfg.id || formLlmConfigIdDeep === cfg.id
                            return (
                              <button key={`llm-${cfg.id}`}
                                type="button"
                                onClick={() => selectUnifiedLlmConfig(cfg.id)}
                                className={cn(
                                  'w-full text-left px-3 py-2 rounded-lg border transition-all',
                                  selected
                                    ? 'border-purple-500 bg-purple-500/5'
                                    : 'border-border hover:border-foreground/20 hover:bg-muted/30'
                                )}>
                                <div className="flex items-center justify-between gap-2">
                                  <div className="flex items-center gap-1.5 min-w-0 flex-wrap">
                                    <span className="font-medium text-xs truncate">{cfg.name}</span>
                                    <span className="text-[9px] px-1 py-0.5 rounded bg-muted text-muted-foreground flex-shrink-0">{cfg.provider}</span>
                                    {isDualModelConfig(cfg) && (
                                      <span className="text-[9px] px-1 py-0.5 rounded bg-purple-500/10 text-purple-600 flex-shrink-0">Flash+Pro 自动切换</span>
                                    )}
                                    {cfg.is_default && (
                                      <span className="text-[9px] px-1 py-0.5 rounded bg-yellow-500/10 text-yellow-600 flex-shrink-0">默认</span>
                                    )}
                                  </div>
                                  {selected && (
                                    <div className="w-3.5 h-3.5 rounded-full bg-purple-500 flex items-center justify-center flex-shrink-0">
                                      <div className="w-1.5 h-1.5 rounded-full bg-white" />
                                    </div>
                                  )}
                                </div>
                                <div className="text-[10px] text-muted-foreground mt-0.5 truncate">
                                  {formatLlmConfigSummary(cfg)}
                                </div>
                              </button>
                            )
                          })}
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="rounded-xl border border-dashed border-border p-4 text-center">
                      <Settings2 className="w-8 h-8 mx-auto mb-2 text-muted-foreground/50" />
                      <p className="text-sm text-muted-foreground">暂无大模型配置</p>
                      <p className="text-xs text-muted-foreground mt-1">请先到「设置 → LLM 配置库」中创建配置</p>
                      <button
                        onClick={() => { window.location.hash = 'settings' }}
                        className="mt-3 inline-flex items-center gap-1.5 text-xs text-purple-500 hover:text-purple-600">
                        <ExternalLink className="w-3 h-3" /> 前往设置
                      </button>
                    </div>
                  )}

                  {/* ── Personality selector ── */}
                  <div>
                    <label className="block text-sm text-muted-foreground mb-1.5">交易员性格 (可选)</label>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                      {presets.map(p => (
                        <button key={p.key}
                          onClick={() => applyPreset(p)}
                          className={cn(
                            'text-left px-3 py-2.5 rounded-xl border transition-all text-xs',
                            formPresetKey === p.key
                              ? 'border-purple-500 bg-purple-500/10'
                              : 'border-border hover:border-foreground/20 hover:bg-muted/30'
                          )}>
                          <div className="flex items-center gap-1.5">
                            <span className="text-base">{PRESET_ICONS[p.key] || '🤖'}</span>
                            <span className="font-semibold truncate">{p.display_name}</span>
                          </div>
                          <div className="text-muted-foreground mt-1 truncate">{p.benchmark_trader}</div>
                        </button>
                      ))}
                      <button
                        onClick={() => {
                          setFormPresetKey('custom')
                          setFormPersonality({ display_name: '', trading_style: 'trend_following', time_horizon: 'swing_trader', risk_appetite: 5, min_confidence: 0.30, loss_tolerance: 5, win_aggression: 5, max_position_pct: 0.15, preferred_leverage: 10, max_leverage: 20 })
                          setShowPersonalityDetail(true)
                        }}
                        className={cn(
                          'text-left px-3 py-2.5 rounded-xl border transition-all text-xs border-dashed',
                          formPresetKey === 'custom'
                            ? 'border-purple-500 bg-purple-500/10'
                            : 'border-border hover:border-foreground/20 hover:bg-muted/30'
                        )}>
                        <div className="flex items-center gap-1.5">
                          <span className="text-base">✏️</span>
                          <span className="font-semibold">自定义</span>
                        </div>
                        <div className="text-muted-foreground mt-1">设计独特性格</div>
                      </button>
                    </div>

                    {/* Detail panel */}
                    {showPersonalityDetail && formPersonality && (
                      <div className="mt-3 rounded-xl border border-border bg-muted/20 p-4 space-y-3">
                        <button onClick={() => setShowPersonalityDetail(!showPersonalityDetail)}
                          className="flex items-center gap-1.5 text-xs text-purple-500 hover:text-purple-600">
                          <ChevronDown className="w-3 h-3" /> 性格参数微调
                        </button>

                        {formPersonality.benchmark_trader && (
                          <div className="text-xs text-muted-foreground italic">{formPersonality.description}</div>
                        )}

                        {/* Display name */}
                        <div>
                          <label className="block text-[11px] text-muted-foreground mb-0.5">性格名称</label>
                          <input type="text" value={formPersonality.display_name || ''}
                            onChange={e => setFormPersonality(p => ({ ...p, display_name: e.target.value }))}
                            placeholder="如：趋势猎手"
                            className="w-full bg-background border border-border rounded-lg px-2.5 py-1.5 text-xs focus:border-purple-500 outline-none" />
                        </div>

                        {/* Style + Horizon */}
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className="block text-[11px] text-muted-foreground mb-0.5">交易风格</label>
                            <select value={formPersonality.trading_style || 'trend_following'}
                              onChange={e => setFormPersonality(p => ({ ...p, trading_style: e.target.value }))}
                              className="w-full bg-background border border-border rounded-lg px-2.5 py-1.5 text-xs outline-none">
                              {Object.entries(STYLE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                            </select>
                          </div>
                          <div>
                            <label className="block text-[11px] text-muted-foreground mb-0.5">时间周期</label>
                            <select value={formPersonality.time_horizon || 'swing_trader'}
                              onChange={e => setFormPersonality(p => ({ ...p, time_horizon: e.target.value }))}
                              className="w-full bg-background border border-border rounded-lg px-2.5 py-1.5 text-xs outline-none">
                              {Object.entries(HORIZON_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                            </select>
                          </div>
                        </div>

                        {/* Sliders */}
                        <div className="space-y-2.5">
                          <SliderField label="风险偏好" value={formPersonality.risk_appetite ?? 5} min={1} max={10}
                            onChange={v => setFormPersonality(p => ({ ...p, risk_appetite: v }))}
                            lowLabel="保守" highLabel="激进" />
                          <SliderField label="最低置信度" value={Math.round((formPersonality.min_confidence ?? 0.3) * 100)} min={10} max={80} step={5}
                            onChange={v => setFormPersonality(p => ({ ...p, min_confidence: v / 100 }))}
                            lowLabel="10%" highLabel="80%" suffix="%" />
                          <SliderField label="亏损容忍" value={formPersonality.loss_tolerance ?? 5} min={1} max={10}
                            onChange={v => setFormPersonality(p => ({ ...p, loss_tolerance: v }))}
                            lowLabel="脆弱" highLabel="坚韧" />
                          <SliderField label="连赢激进度" value={formPersonality.win_aggression ?? 5} min={1} max={10}
                            onChange={v => setFormPersonality(p => ({ ...p, win_aggression: v }))}
                            lowLabel="稳定" highLabel="猛加" />
                          <SliderField label="单仓上限" value={Math.round((formPersonality.max_position_pct ?? 0.15) * 100)} min={5} max={30}
                            onChange={v => setFormPersonality(p => ({ ...p, max_position_pct: v / 100 }))}
                            lowLabel="5%" highLabel="30%" suffix="%" />
                        </div>

                        {/* Leverage */}
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className="block text-[11px] text-muted-foreground mb-0.5">偏好杠杆</label>
                            <input type="number" min={5} max={20} value={formPersonality.preferred_leverage ?? 10}
                              onChange={e => setFormPersonality(p => ({ ...p, preferred_leverage: parseInt(e.target.value) || 10 }))}
                              className="w-full bg-background border border-border rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                          </div>
                          <div>
                            <label className="block text-[11px] text-muted-foreground mb-0.5">杠杆上限</label>
                            <input type="number" min={5} max={20} value={formPersonality.max_leverage ?? 20}
                              onChange={e => setFormPersonality(p => ({ ...p, max_leverage: parseInt(e.target.value) || 20 }))}
                              className="w-full bg-background border border-border rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                          </div>
                        </div>

                        {/* Special skills */}
                        <div>
                          <label className="block text-[11px] text-muted-foreground mb-0.5">专属技能</label>
                          <input type="text" value={formPersonality.special_skills || ''}
                            onChange={e => setFormPersonality(p => ({ ...p, special_skills: e.target.value }))}
                            placeholder="如：擅长识别假突破、金字塔加仓"
                            className="w-full bg-background border border-border rounded-lg px-2.5 py-1.5 text-xs focus:border-purple-500 outline-none" />
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Auto trading */}
                  <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
                    <input type="checkbox" className="h-4 w-4 accent-purple-500"
                      checked={formAutoTrading} onChange={e => setFormAutoTrading(e.target.checked)} />
                    {editingId ? '启用自动交易' : '创建后立即启用自动交易'}
                  </label>

                  <label className="flex items-start gap-2 text-sm cursor-pointer rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2.5">
                    <input type="checkbox" className="h-4 w-4 accent-amber-500 mt-0.5"
                      checked={formArbitrageEnabled} onChange={e => setFormArbitrageEnabled(e.target.checked)} />
                    <span>
                      <span className="font-medium text-foreground">可用于积分套利（S1–S8）</span>
                      <span className="block text-xs text-muted-foreground mt-0.5">
                        只标记身份；策略与 Paper 在套利中心配置，模型与分析/执行模型共用。
                      </span>
                    </span>
                  </label>

                  {/* Buttons */}
                  <div className="flex items-center gap-2 pt-1">
                    <button onClick={handleSave} disabled={saving}
                      className="flex items-center gap-1.5 px-5 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors">
                      <Save className="w-3.5 h-3.5" />
                      {saving ? '保存中...' : (editingId ? '保存修改' : '创建交易员')}
                    </button>
                    <button onClick={resetForm}
                      className="flex items-center gap-1.5 px-4 py-2 bg-muted hover:bg-muted/80 text-muted-foreground rounded-lg text-sm transition-colors">
                      <X className="w-3.5 h-3.5" /> 取消
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Selected trader config */}
          {selectedAccount ? (
            <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
              {/* Tab bar */}
              <div className="flex-shrink-0 px-6 pt-4 flex items-center gap-1 border-b border-border">
                {([
                  { key: 'config' as const, label: 'AI 配置', icon: Activity },
                  ...((selectedAccount.selected_exchange || 'hyperliquid') !== 'hyperliquid'
                    ? [{ key: 'wallet' as const, label: '钱包配置', icon: Wallet }]
                    : []),
                  { key: 'risk' as const, label: '风控配置', icon: Shield },
                ] as const).map(tab => (
                  <button key={tab.key} onClick={() => {
                    setRightTab(tab.key)
                    if (tab.key === 'risk' && selectedAccount) loadRiskConfig(selectedAccount.id)
                  }}
                    className={cn(
                      'flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px',
                      rightTab === tab.key
                        ? 'border-purple-500 text-foreground'
                        : 'border-transparent text-muted-foreground hover:text-foreground'
                    )}>
                    <tab.icon className="w-3.5 h-3.5" />
                    {tab.label}
                  </button>
                ))}
                <div className="ml-auto flex items-center gap-2 pb-2">
                  <span className="text-xs text-muted-foreground">{selectedAccount.name}</span>
                </div>
              </div>

              {/* Tab content */}
              <div className="flex-1 min-h-0 overflow-y-auto p-6">
                {rightTab === 'config' && (
                  <div className="max-w-2xl space-y-6">
                    <div className={cn(
                      'rounded-lg border px-3 py-2.5 text-xs',
                      arbEnabled
                        ? 'border-green-500/30 bg-green-500/5 text-green-800 dark:text-green-200'
                        : 'border-border bg-muted/20 text-muted-foreground',
                    )}>
                      {arbEnabled ? (
                        <>
                          已开启<strong className="text-foreground">积分套利</strong>能力。
                          策略授权、套利专用模型、Paper 绑定请去
                          <button type="button" onClick={() => { window.location.hash = 'arbitrage-hub' }}
                            className="text-amber-600 hover:underline mx-1">套利中心 → 交易员套利</button>
                          配置。
                        </>
                      ) : (
                        <>未开启积分套利。编辑交易员时可勾选「可用于积分套利」。</>
                      )}
                    </div>
                    {/* Trader summary + readiness */}
                    <div className="rounded-xl border border-border p-4 bg-card">
                      <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
                        <Bot className="w-4 h-4 text-purple-500" /> 交易员信息
                      </h3>
                      <div className="grid grid-cols-2 gap-3 text-sm">
                        <div>
                          <span className="text-muted-foreground text-xs">名称</span>
                          <div className="font-medium">{selectedAccount.name}</div>
                        </div>
                        <div>
                          <span className="text-muted-foreground text-xs">大模型配置</span>
                          <div className="text-xs font-medium">{getConfigDisplay(selectedAccount)}</div>
                          {selectedAccount.llm_config_id && selectedAccount.llm_config_id_deep
                            && selectedAccount.llm_config_id === selectedAccount.llm_config_id_deep && (
                            <div className="text-[10px] text-purple-600 mt-0.5">Flash / Pro 按任务自动切换</div>
                          )}
                        </div>
                        <div>
                          <span className="text-muted-foreground text-xs">模型</span>
                          <div className="font-mono text-xs">{selectedAccount.model || '—'}</div>
                        </div>
                        <div>
                          <span className="text-muted-foreground text-xs">交易所</span>
                          <div className="text-xs font-medium flex items-center gap-1">
                            {(() => {
                              const ex = EXCHANGE_OPTIONS.find(e => e.id === (selectedAccount.selected_exchange || 'hyperliquid'))
                              return ex ? <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: ex.color }} /> : null
                            })()}
                            {EXCHANGE_NAMES[selectedAccount.selected_exchange || 'hyperliquid'] || selectedAccount.selected_exchange}
                          </div>
                        </div>
                        <div>
                          <span className="text-muted-foreground text-xs">自动交易</span>
                          <div className={cn('text-xs font-medium', selectedAccount.auto_trading_enabled ? 'text-green-600 dark:text-green-400' : 'text-muted-foreground')}>
                            {selectedAccount.auto_trading_enabled ? '● 运行中' : '○ 已暂停'}
                          </div>
                        </div>
                      </div>

                      {/* Readiness checklist */}
                      <div className="mt-4 pt-3 border-t border-border space-y-1.5">
                        <div className="text-xs text-muted-foreground font-medium mb-2">就绪检查</div>
                        <ReadinessItem ok={!!selectedAccount.llm_config_id || !!selectedAccount.api_key_set}
                          label="大模型配置" hint={!selectedAccount.llm_config_id && !selectedAccount.api_key_set ? '请选择 LLM 配置' : undefined} />
                        {(selectedAccount.selected_exchange || 'hyperliquid') === 'hyperliquid' ? (
                          <ReadinessItem ok={!!selectedAccount.hyperliquid_enabled}
                            label="Hyperliquid 钱包" hint={!selectedAccount.hyperliquid_enabled ? '请在「交易所配置」中设置' : undefined} />
                        ) : (
                          <ReadinessItem
                            ok={!!exchangeCredentials.find((c: any) => c.exchange === selectedAccount.selected_exchange && c.enabled)}
                            label={`${EXCHANGE_NAMES[selectedAccount.selected_exchange || ''] || selectedAccount.selected_exchange} 已配置`}
                            hint={!exchangeCredentials.find((c: any) => c.exchange === selectedAccount.selected_exchange && c.enabled) ? '请在「设置 → 交易所配置」中添加 API 密钥' : undefined}
                          />
                        )}
                        <ReadinessItem ok={!!selectedAccount.auto_trading_enabled}
                          label="自动交易已开启" hint={!selectedAccount.auto_trading_enabled ? '点击左侧列表中的电源按钮' : undefined} />
                      </div>

                      {/* Personality card */}
                      {selectedAccount.personality?.display_name && (
                        <div className="mt-4 pt-3 border-t border-border">
                          <div className="text-xs text-muted-foreground font-medium mb-2">性格档案</div>
                          <div className="flex items-start gap-3">
                            <div className="text-2xl">{
                              selectedAccount.personality.benchmark_trader
                                ? PRESET_ICONS[presets.find(p => p.benchmark_trader === selectedAccount.personality?.benchmark_trader)?.key || ''] || '🤖'
                                : '✏️'
                            }</div>
                            <div className="flex-1 min-w-0">
                              <div className="font-medium text-sm">{selectedAccount.personality.display_name}</div>
                              {selectedAccount.personality.benchmark_trader && (
                                <div className="text-xs text-muted-foreground">对标 {selectedAccount.personality.benchmark_trader}</div>
                              )}
                              <div className="mt-1 flex flex-wrap gap-1.5">
                                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-purple-500/10 text-purple-600 dark:text-purple-400">
                                  {STYLE_LABELS[selectedAccount.personality.trading_style || ''] || selectedAccount.personality.trading_style}
                                </span>
                                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-500/10 text-blue-600 dark:text-blue-400">
                                  {HORIZON_LABELS[selectedAccount.personality.time_horizon || ''] || selectedAccount.personality.time_horizon}
                                </span>
                                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-orange-500/10 text-orange-600 dark:text-orange-400">
                                  风险 {selectedAccount.personality.risk_appetite}/10
                                </span>
                                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-500/10 text-green-600 dark:text-green-400">
                                  杠杆 {selectedAccount.personality.preferred_leverage}x~{selectedAccount.personality.max_leverage}x
                                </span>
                              </div>
                              {selectedAccount.personality.special_skills && (
                                <div className="mt-1.5 text-[11px] text-muted-foreground">
                                  <Zap className="w-3 h-3 inline mr-1 text-yellow-500" />
                                  {selectedAccount.personality.special_skills}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      )}

                      <button onClick={() => openEditForm(selectedAccount)}
                        className="mt-3 flex items-center gap-1.5 text-xs text-purple-500 hover:text-purple-600">
                        <Pencil className="w-3 h-3" /> 编辑
                      </button>
                    </div>

                    {/* AI trigger config */}
                    <div className="rounded-xl border border-border p-4 bg-card">
                      <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
                        <Clock className="w-4 h-4 text-purple-500" /> AI 决策频率
                      </h3>
                      {strategyLoading ? (
                        <div className="text-sm text-muted-foreground py-4">加载中...</div>
                      ) : (
                        <div className="space-y-4">
                          <div>
                            <label className="block text-xs text-muted-foreground mb-1">分析间隔（秒）</label>
                            <input type="number" min={30} step={30}
                              value={triggerInterval} onChange={e => setTriggerInterval(e.target.value)}
                              className="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm focus:border-purple-500 outline-none" />
                            <p className="text-[11px] text-muted-foreground mt-1">
                              AI 每隔多少秒分析市场并决策（建议 60~300 秒）
                            </p>
                          </div>

                          {strategyConfig?.last_trigger_at && (
                            <div className="text-xs text-muted-foreground">
                              上次执行: {new Date(strategyConfig.last_trigger_at).toLocaleString()}
                            </div>
                          )}

                          <button onClick={handleSaveStrategy} disabled={strategySaving}
                            className="w-full flex items-center justify-center gap-1.5 px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors">
                            <Save className="w-3.5 h-3.5" />
                            {strategySaving ? '保存中...' : '保存配置'}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {rightTab === 'wallet' && (
                  <div className="max-w-2xl">
                    <UnifiedWalletConfigPanel
                      accountId={selectedAccount.id}
                      accountName={selectedAccount.name}
                      exchange={selectedAccount.selected_exchange || 'hyperliquid'}
                      onWalletConfigured={loadAccounts}
                    />
                  </div>
                )}

                {rightTab === 'risk' && (
                  <div className="max-w-2xl space-y-4">
                    <h3 className="text-sm font-semibold flex items-center gap-2">
                      <Shield className="w-4 h-4 text-purple-500" /> 风控配置
                    </h3>
                    {riskLoading ? (
                      <div className="text-sm text-muted-foreground py-4">加载中...</div>
                    ) : riskConfig ? (
                      <RiskControlPanel
                        accountId={selectedAccount.id}
                        config={riskConfig}
                        onSaved={() => loadRiskConfig(selectedAccount.id)}
                      />
                    ) : (
                      <div className="text-sm text-muted-foreground py-4">暂无风控配置</div>
                    )}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center text-muted-foreground">
              <div className="text-center">
                <Bot className="w-16 h-16 mx-auto mb-4 opacity-20" />
                <p className="text-sm font-medium">
                  {accounts.length === 0 ? '创建你的第一个 AI 交易员' : '选择一个交易员查看配置'}
                </p>
                <p className="text-xs mt-1 text-muted-foreground/70">
                  {accounts.length === 0 ? '点击左侧「新建交易员」按钮开始' : '在左侧列表中点击选择'}
                </p>
              </div>
            </div>
          )}
        </div>
      </div>

      <AuthorizationModal
        isOpen={authModalOpen}
        onClose={() => { setAuthModalOpen(false); setUnauthorizedAccounts([]); loadAccounts() }}
        unauthorizedAccounts={unauthorizedAccounts}
        onAuthorizationComplete={handleAuthComplete}
      />
    </div>
  )
}

function ReadinessItem({ ok, label, hint }: { ok: boolean; label: string; hint?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      {ok ? (
        <CheckCircle className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
      ) : (
        <XCircle className="w-3.5 h-3.5 text-orange-500 flex-shrink-0" />
      )}
      <span className={ok ? 'text-foreground' : 'text-muted-foreground'}>{label}</span>
      {!ok && hint && <span className="text-muted-foreground/60">— {hint}</span>}
      </div>
    )
  }

function SliderField({
  label, value, min, max, step = 1, onChange, lowLabel, highLabel, suffix = ''
}: {
  label: string; value: number; min: number; max: number; step?: number
  onChange: (v: number) => void; lowLabel: string; highLabel: string; suffix?: string
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-0.5">
        <label className="text-[11px] text-muted-foreground">{label}</label>
        <span className="text-[11px] font-mono text-foreground">{value}{suffix}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(parseInt(e.target.value))}
        className="w-full h-1.5 rounded-full appearance-none cursor-pointer accent-purple-500 bg-border" />
      <div className="flex justify-between text-[10px] text-muted-foreground mt-0.5">
        <span>{lowLabel}</span>
        <span>{highLabel}</span>
      </div>
    </div>
  )
}

/* ──────────── Risk Control Panel ──────────── */
interface RiskConfig {
  max_trade_amount: number
  daily_trade_count_limit: number
  max_concurrent_positions: number
  per_symbol_max_position: number
  global_stop_loss_pct: number
  enable_trade_amount_limit: string
  enable_trade_count_limit: string
  enable_concurrent_position_limit: string
}

function RiskControlPanel({ accountId, config, onSaved }: {
  accountId: number; config: RiskConfig; onSaved: () => void
}) {
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState<RiskConfig>({ ...config })

  useEffect(() => { setForm({ ...config }) }, [config])

  const set = (key: keyof RiskConfig, val: number | string) =>
    setForm(prev => ({ ...prev, [key]: val }))

  const toggle = (key: 'enable_trade_amount_limit' | 'enable_trade_count_limit' | 'enable_concurrent_position_limit') =>
    setForm(prev => ({ ...prev, [key]: prev[key] === 'true' ? 'false' : 'true' }))

  const handleSave = async () => {
    setSaving(true)
    try {
      const res = await fetch(`/api/risk/${accountId}/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      if (!res.ok) throw new Error('保存失败')
      toast.success('风控配置已保存')
      onSaved()
    } catch (e: any) {
      toast.error(e.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-5">
      {/* 仓位限制 */}
      <div className="space-y-3">
        <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
          <Target className="w-3.5 h-3.5" /> 仓位限制
        </h4>
        <SliderField label="单笔最大交易金额" min={100} max={100000} step={100}
          value={form.max_trade_amount} onChange={v => set('max_trade_amount', v)}
          lowLabel="$100" highLabel="$100,000" suffix=" $" />
        <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
          <input type="checkbox" checked={form.enable_trade_amount_limit === 'true'}
            onChange={() => toggle('enable_trade_amount_limit')}
            className="rounded border-border accent-purple-500" />
          启用单笔金额限制
        </label>
        <SliderField label="单币种最大持仓数" min={1} max={20} step={1}
          value={form.per_symbol_max_position} onChange={v => set('per_symbol_max_position', v)}
          lowLabel="1" highLabel="20" />
        <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
          <input type="checkbox" checked={form.enable_concurrent_position_limit === 'true'}
            onChange={() => toggle('enable_concurrent_position_limit')}
            className="rounded border-border accent-purple-500" />
          启用持仓数量限制
        </label>
      </div>

      {/* 交易限制 */}
      <div className="space-y-3">
        <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
          <Zap className="w-3.5 h-3.5" /> 交易限制
        </h4>
        <SliderField label="每日最大交易次数" min={1} max={200} step={1}
          value={form.daily_trade_count_limit} onChange={v => set('daily_trade_count_limit', v)}
          lowLabel="1" highLabel="200" suffix=" 次" />
        <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
          <input type="checkbox" checked={form.enable_trade_count_limit === 'true'}
            onChange={() => toggle('enable_trade_count_limit')}
            className="rounded border-border accent-purple-500" />
          启用交易次数限制
        </label>
        <SliderField label="最大并发持仓" min={1} max={50} step={1}
          value={form.max_concurrent_positions} onChange={v => set('max_concurrent_positions', v)}
          lowLabel="1" highLabel="50" />
      </div>

      {/* 熔断与止损 */}
      <div className="space-y-3">
        <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
          <Flame className="w-3.5 h-3.5" /> 熔断与止损
        </h4>
        <SliderField label="全局止损百分比" min={1} max={50} step={1}
          value={Math.round(form.global_stop_loss_pct * 100)} onChange={v => set('global_stop_loss_pct', v / 100)}
          lowLabel="1%" highLabel="50%" suffix="%" />
        <p className="text-[10px] text-muted-foreground">
          当账户总亏损超过此比例时自动停止交易
        </p>
      </div>

      <button onClick={handleSave} disabled={saving}
        className="w-full py-2 px-4 bg-purple-600 hover:bg-purple-700 text-white text-sm rounded-lg disabled:opacity-50 flex items-center justify-center gap-2">
        {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
        {saving ? '保存中...' : '保存风控配置'}
      </button>
    </div>
  )
}
