/**
 * 设置页面
 * 包含：API密钥、LLM配置库（集中管理可复用配置）、通知设置、系统日志
 */

import React, { useEffect, useState, useCallback } from 'react'
import {
  Key, Brain, Bell, FileText, Save, Eye, EyeOff,
  CheckCircle, AlertCircle, RefreshCw, Zap,
  Coins,
  Plus, X, GripVertical, Search, ArrowUpDown,
  Shield, ShieldAlert, ToggleLeft, ToggleRight
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { apiRequest } from '@/lib/api'
import { toast } from 'react-hot-toast'
import { LLMConfigManager } from '@/components/settings'
import { useTradingPairs } from '@/hooks/useTradingPairs'
import LockStrengthPanel from './LockStrengthPanel'

interface SystemLog {
  id: number
  level: string
  message: string
  created_at: string
  category?: string
}

type SettingsTab = 'api' | 'llm' | 'pairs' | 'lock' | 'notification' | 'logs'

const TAB_CONFIG = [
  { key: 'api' as const, label: 'API 密钥', icon: Key },
  { key: 'llm' as const, label: 'LLM 配置库', icon: Brain },
  { key: 'pairs' as const, label: '交易配置', icon: Coins },
  { key: 'lock' as const, label: '锁仓强度', icon: Shield },
  { key: 'notification' as const, label: '通知设置', icon: Bell },
  { key: 'logs' as const, label: '系统日志', icon: FileText },
]

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<SettingsTab>('api')
  const [logs, setLogs] = useState<SystemLog[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const pending = sessionStorage.getItem('settings_tab') as SettingsTab | null
    if (pending && TAB_CONFIG.some((t) => t.key === pending)) {
      setActiveTab(pending)
      sessionStorage.removeItem('settings_tab')
    }
  }, [])

  const fetchLogs = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiRequest('/system-logs?limit=50')
      const data = await res.json()
      setLogs(data.logs || data.items || [])
    } catch (e) {
      console.error('[Settings] fetchLogs error:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (activeTab === 'logs') fetchLogs()
  }, [activeTab, fetchLogs])

  return (
    <div className="min-h-screen bg-background text-foreground p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Zap className="w-7 h-7 text-purple-500" />
          系统设置
        </h1>
        <p className="text-muted-foreground text-sm mt-1">API密钥 · LLM配置库 · 交易配置 · 锁仓强度 · 通知配置 · 系统日志</p>
      </div>

      <div className="flex gap-6">
        <div className="w-48 flex-shrink-0">
          <nav className="space-y-1">
            {TAB_CONFIG.map(t => {
              const Icon = t.icon
              return (
                <button
                  key={t.key}
                  onClick={() => setActiveTab(t.key)}
                  className={cn(
                    'w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all',
                    activeTab === t.key
                      ? 'bg-purple-600/20 text-purple-600 dark:text-purple-300 border border-purple-500/30'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                  )}
                >
                  <Icon className="w-4 h-4" />
                  {t.label}
                </button>
              )
            })}
          </nav>
        </div>

        <div className="flex-1 min-w-0">
          {activeTab === 'api' && <APIKeyTab />}
          {activeTab === 'llm' && <LLMConfigLibraryTab />}
          {activeTab === 'pairs' && <TradingPairsTab />}
          {activeTab === 'lock' && <LockStrengthPanel />}
          {activeTab === 'notification' && <NotificationTab />}
          {activeTab === 'logs' && <SystemLogsTab logs={logs} loading={loading} onRefresh={fetchLogs} />}
        </div>
      </div>
    </div>
  )
}

// ── API 密钥 Tab ──

interface ExtKeyStatus {
  label: string
  configured: boolean
  masked: string
}

const EXT_KEY_CONFIG = [
  {
    name: 'COINALYZE_API_KEY',
    label: 'Coinalyze（合约数据）',
    hint: '免费注册即可获取 — 提供清算、OI、多空比等衍生品数据',
    link: 'https://coinalyze.net/account/api-key/',
    required: false,
  },
  {
    name: 'CRYPTOPANIC_API_KEY',
    label: 'CryptoPanic（新闻聚合）',
    hint: '免费注册 — 用于情报中心的新闻数据采集',
    link: 'https://cryptopanic.com/developers/api/',
    required: false,
  },
]

function APIKeyTab() {
  const [extKeyStatus, setExtKeyStatus] = useState<Record<string, ExtKeyStatus>>({})
  const [extKeyInputs, setExtKeyInputs] = useState<Record<string, string>>({})
  const [extKeySaving, setExtKeySaving] = useState<Record<string, boolean>>({})

  const loadExtKeys = useCallback(async () => {
    try {
      const res = await apiRequest('/config/external-keys')
      const data = await res.json()
      setExtKeyStatus(data)
    } catch (e) {
      console.error('[Settings] load ext keys error:', e)
    }
  }, [])

  useEffect(() => { loadExtKeys() }, [loadExtKeys])

  const handleSaveExtKey = async (keyName: string) => {
    const val = extKeyInputs[keyName]
    if (!val?.trim()) return
    setExtKeySaving(p => ({ ...p, [keyName]: true }))
    try {
      await apiRequest('/config/external-keys', {
        method: 'POST',
        body: JSON.stringify({ key_name: keyName, key_value: val.trim() }),
      })
      toast.success('Key 已保存')
      setExtKeyInputs(p => ({ ...p, [keyName]: '' }))
      loadExtKeys()
    } catch (e) {
      toast.error('保存失败')
    } finally {
      setExtKeySaving(p => ({ ...p, [keyName]: false }))
    }
  }

  return (
    <div className="space-y-4">
      <Card title="Hyperliquid 与交易所" icon={<Key className="w-4 h-4 text-blue-500" />}>
        <div className="space-y-3">
          <InfoBox>
            Hyperliquid 钱包私钥、访问令牌，以及 Binance / Bybit 等 CEX API 密钥，
            已统一到侧边栏「交易所配置」页面管理。
          </InfoBox>
          <button
            type="button"
            onClick={() => { window.location.hash = 'exchange-config' }}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors"
          >
            <Key className="w-4 h-4" />
            前往交易所配置
          </button>
        </div>
      </Card>

      <Card title="免费数据源 API Keys" icon={<Zap className="w-4 h-4 text-amber-500" />}>
        <InfoBox>
          以下数据源均为免费注册。Binance 和 Hyperliquid 的公开 API 无需 Key 即可使用。
          配置 Coinalyze Key 后可获取清算数据，大幅提升情报中心分析精度。
        </InfoBox>
        <div className="mt-4 space-y-5">
          {EXT_KEY_CONFIG.map(cfg => {
            const status = extKeyStatus[cfg.name]
            const isSaving = extKeySaving[cfg.name]
            return (
              <div key={cfg.name} className="border border-border/50 rounded-xl p-4">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm">{cfg.label}</span>
                    {status?.configured ? (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/10 text-green-600 dark:text-green-400 border border-green-500/20">
                        已配置
                      </span>
                    ) : (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border border-yellow-500/20">
                        未配置
                      </span>
                    )}
                  </div>
                  <a href={cfg.link} target="_blank" rel="noopener noreferrer"
                    className="text-xs text-purple-500 hover:text-purple-400 underline underline-offset-2">
                    免费注册
                  </a>
                </div>
                <p className="text-xs text-muted-foreground mb-3">{cfg.hint}</p>
                {status?.configured && status.masked && (
                  <p className="text-xs text-muted-foreground mb-2 font-mono">当前: {status.masked}</p>
                )}
                <div className="flex gap-2">
                  <input
                    type="password"
                    value={extKeyInputs[cfg.name] || ''}
                    onChange={e => setExtKeyInputs(p => ({ ...p, [cfg.name]: e.target.value }))}
                    placeholder={status?.configured ? '留空保持不变，输入新值覆盖' : '粘贴 API Key...'}
                    className="flex-1 bg-muted/50 border border-border rounded-lg px-3 py-1.5 text-sm focus:border-purple-500 outline-none"
                  />
                  <button
                    onClick={() => handleSaveExtKey(cfg.name)}
                    disabled={isSaving || !extKeyInputs[cfg.name]?.trim()}
                    className="px-3 py-1.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-40 text-white rounded-lg text-xs font-medium transition-colors"
                  >
                    {isSaving ? '...' : '保存'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>

        <div className="mt-4 p-3 rounded-lg bg-muted/30 border border-border/50">
          <p className="text-xs font-medium text-foreground/70 mb-2">免费数据源覆盖情况：</p>
          <div className="grid grid-cols-2 gap-1.5 text-xs text-muted-foreground">
            <span>Hyperliquid 原生 API — 资金费率/OI/标记价格</span>
            <span className="text-green-500">无需Key</span>
            <span>Binance 公开 API — OI历史/多空比/大户多空</span>
            <span className="text-green-500">无需Key</span>
            <span>blockchain.info + mempool.space — 鲸鱼链上大额交易</span>
            <span className="text-green-500">无需Key</span>
            <span>Alternative.me — 恐惧贪婪指数</span>
            <span className="text-green-500">无需Key</span>
            <span>Coinalyze — 清算数据/OI/多空比（补充）</span>
            <span className="text-amber-500">免费注册</span>
            <span>CryptoPanic — 新闻聚合/情绪分析</span>
            <span className="text-amber-500">免费注册</span>
          </div>
        </div>
      </Card>
    </div>
  )
}

// ── LLM 配置库 Tab（集中管理可复用的大模型配置）──
function LLMConfigLibraryTab() {
  return (
    <Card title="LLM 配置库" icon={<Brain className="w-4 h-4 text-purple-500" />}>
      <InfoBox>
        在此集中管理所有大模型配置。创建配置后，可在「AI交易员」页面创建交易员时直接填写，
        或通过此处统一管理 API Key、模型和端点信息。
      </InfoBox>
      <div className="mt-4">
        <LLMConfigManager />
      </div>
    </Card>
  )
}

// ── 交易对管理 Tab ──
function TradingPairsTab() {
  const { symbols, symbolsDetail, exchangeSymbols, loading, save, reload } = useTradingPairs()
  const [search, setSearch] = useState('')
  const [customInput, setCustomInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null)
  const [refreshingExchange, setRefreshingExchange] = useState(false)

  // 保证金模式
  const [marginMode, setMarginMode] = useState<'isolated' | 'cross'>('isolated')
  const [marginLoading, setMarginLoading] = useState(true)
  const [marginSwitching, setMarginSwitching] = useState(false)

  useEffect(() => {
    apiRequest('/config/margin-mode').then(r => r.json())
      .then(d => { setMarginMode(d.margin_mode || 'isolated'); setMarginLoading(false) })
      .catch(() => setMarginLoading(false))
  }, [])

  const toggleMarginMode = async () => {
    const newMode = marginMode === 'isolated' ? 'cross' : 'isolated'
    setMarginSwitching(true)
    try {
      const res = await apiRequest('/config/margin-mode', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ margin_mode: newMode }),
      })
      const data = await res.json()
      setMarginMode(data.margin_mode)
      toast.success(data.message || `已切换为${newMode === 'cross' ? '全仓' : '逐仓'}模式`)
    } catch {
      toast.error('切换失败')
    } finally {
      setMarginSwitching(false)
    }
  }

  const detailMap = new Map(symbolsDetail.map(d => [d.symbol, d.status]))
  const verifiedCount = symbolsDetail.filter(d => d.status === 'verified').length
  const exchangeSet = new Set(exchangeSymbols)

  const handleRemove = async (sym: string) => {
    if (symbols.length <= 1) {
      toast.error('至少保留一个交易对')
      return
    }
    setSaving(true)
    const ok = await save(symbols.filter(s => s !== sym))
    setSaving(false)
    if (ok) toast.success(`已移除 ${sym}`)
  }

  const handleAdd = async (sym: string) => {
    const s = sym.trim().toUpperCase()
    if (!s) return
    if (symbols.includes(s)) {
      toast.error(`${s} 已在列表中`)
      return
    }
    setSaving(true)
    const ok = await save([...symbols, s])
    setSaving(false)
    if (ok) toast.success(`已添加 ${s}`)
    setCustomInput('')
  }

  const handleCustomAdd = () => {
    const parts = customInput.split(/[,，\s]+/).filter(Boolean)
    if (parts.length === 0) return
    if (parts.length === 1) {
      handleAdd(parts[0])
      return
    }
    const newList = [...symbols]
    const added: string[] = []
    parts.forEach(p => {
      const s = p.trim().toUpperCase()
      if (s && !newList.includes(s)) {
        newList.push(s)
        added.push(s)
      }
    })
    if (added.length === 0) {
      toast.error('所有输入的交易对都已存在')
      return
    }
    setSaving(true)
    save(newList).then(ok => {
      setSaving(false)
      if (ok) toast.success(`已添加 ${added.join(', ')}`)
      setCustomInput('')
    })
  }

  const handleDragStart = (idx: number) => setDragIdx(idx)
  const handleDragOver = (e: React.DragEvent, idx: number) => {
    e.preventDefault()
    setDragOverIdx(idx)
  }
  const handleDrop = async (idx: number) => {
    if (dragIdx === null || dragIdx === idx) {
      setDragIdx(null)
      setDragOverIdx(null)
      return
    }
    const newList = [...symbols]
    const [moved] = newList.splice(dragIdx, 1)
    newList.splice(idx, 0, moved)
    setDragIdx(null)
    setDragOverIdx(null)
    await save(newList)
  }

  const handleRefreshExchange = async () => {
    setRefreshingExchange(true)
    try {
      await fetch('/api/config/trading-pairs/refresh-exchange', { method: 'POST' })
      toast.success('交易所币种列表已刷新')
      await reload()
    } catch {
      toast.error('刷新失败')
    } finally {
      setRefreshingExchange(false)
    }
  }

  // 快速添加列表：优先用交易所真实币种，去掉已添加的
  const quickAddSource = exchangeSymbols.length > 0 ? exchangeSymbols : []
  const notAdded = quickAddSource.filter(s => !symbols.includes(s))
  const filtered = search
    ? notAdded.filter(s => s.toLowerCase().includes(search.toLowerCase()))
    : notAdded

  return (
    <div className="space-y-4">
      {/* 保证金模式一键切换 */}
      <Card title="保证金模式" icon={<Shield className="w-4 h-4 text-purple-500" />}>
        {marginLoading ? (
          <div className="text-center text-muted-foreground py-4 text-sm">加载中...</div>
        ) : (
          <div className="flex items-center gap-4">
            <button
              onClick={toggleMarginMode}
              disabled={marginSwitching}
              className="flex items-center gap-3 flex-1"
            >
              <div className={cn(
                'relative w-14 h-7 rounded-full transition-colors duration-300 flex-shrink-0',
                marginMode === 'isolated'
                  ? 'bg-green-500'
                  : 'bg-orange-500'
              )}>
                <div className={cn(
                  'absolute top-0.5 w-6 h-6 rounded-full bg-white shadow-md transition-transform duration-300 flex items-center justify-center',
                  marginMode === 'cross' ? 'translate-x-7' : 'translate-x-0.5'
                )}>
                  {marginMode === 'isolated' ? (
                    <Shield className="w-3.5 h-3.5 text-green-600" />
                  ) : (
                    <ShieldAlert className="w-3.5 h-3.5 text-orange-600" />
                  )}
                </div>
              </div>
              <div className="text-left">
                <div className="flex items-center gap-2">
                  <span className={cn(
                    'text-base font-bold',
                    marginMode === 'isolated' ? 'text-green-500' : 'text-orange-500'
                  )}>
                    {marginMode === 'isolated' ? '逐仓模式' : '全仓模式'}
                  </span>
                  {marginSwitching && <RefreshCw className="w-3 h-3 animate-spin text-muted-foreground" />}
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {marginMode === 'isolated'
                    ? '每个仓位独立保证金 — 单个爆仓不影响其他仓位，风险隔离'
                    : '所有仓位共享保证金 — 资金利用率更高，但风险联动'}
                </p>
              </div>
            </button>
          </div>
        )}
        <div className="mt-3 pt-3 border-t border-border/50">
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className={cn(
              'rounded-lg p-2.5 border transition-all',
              marginMode === 'isolated'
                ? 'border-green-500/40 bg-green-500/5'
                : 'border-border/30 bg-muted/20 opacity-50'
            )}>
              <div className="flex items-center gap-1.5 mb-1">
                <Shield className="w-3 h-3 text-green-500" />
                <span className="font-semibold text-green-500">逐仓</span>
              </div>
              <p className="text-muted-foreground leading-relaxed">每个币种仓位使用独立保证金，爆仓只影响该仓位。适合多币种同时持仓。</p>
            </div>
            <div className={cn(
              'rounded-lg p-2.5 border transition-all',
              marginMode === 'cross'
                ? 'border-orange-500/40 bg-orange-500/5'
                : 'border-border/30 bg-muted/20 opacity-50'
            )}>
              <div className="flex items-center gap-1.5 mb-1">
                <ShieldAlert className="w-3 h-3 text-orange-500" />
                <span className="font-semibold text-orange-500">全仓</span>
              </div>
              <p className="text-muted-foreground leading-relaxed">全部仓位共享账户余额作为保证金，资金效率高但一个仓位亏损会拖累全部。</p>
            </div>
          </div>
        </div>
      </Card>

      <Card title="常用交易对" icon={<Coins className="w-4 h-4 text-purple-500" />}
        action={
          <button onClick={reload} disabled={loading}
            className="text-muted-foreground hover:text-foreground transition-colors">
            <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
          </button>
        }
      >
        <InfoBox>
          在此统一管理常用交易对。配置后，运行控制、一键启动、回测进化、策略向导等所有模块将共享此列表。
          交易所格式：内部使用大写短码（如 <code className="bg-muted px-1 rounded">BTC</code>），下单时自动转为 Hyperliquid 永续合约格式
          <code className="bg-muted px-1 rounded">BTC/USDC:USDC</code>。
        </InfoBox>

        {loading ? (
          <div className="text-center text-muted-foreground py-8 text-sm">加载中...</div>
        ) : (
          <div className="mt-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs text-muted-foreground">
                已配置 <span className="text-foreground font-bold">{symbols.length}</span> 个交易对
                {symbolsDetail.length > 0 && (
                  <span className="ml-2">
                    (<span className="text-green-500">{verifiedCount} 已验证</span>
                    {symbols.length - verifiedCount > 0 && (
                      <span className="text-yellow-500 ml-1">{symbols.length - verifiedCount} 待验证</span>
                    )})
                  </span>
                )}
                {saving && <span className="ml-2 text-purple-400">保存中...</span>}
              </span>
              <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <ArrowUpDown className="w-3 h-3" />
                拖拽排序
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              {symbols.map((sym, idx) => {
                const status = detailMap.get(sym)
                const isVerified = status === 'verified'
                return (
                  <div
                    key={sym}
                    draggable
                    onDragStart={() => handleDragStart(idx)}
                    onDragOver={(e) => handleDragOver(e, idx)}
                    onDrop={() => handleDrop(idx)}
                    onDragEnd={() => { setDragIdx(null); setDragOverIdx(null) }}
                    className={cn(
                      'group flex items-center gap-1.5 pl-2 pr-1 py-1.5 rounded-lg border text-sm font-medium transition-all cursor-grab active:cursor-grabbing select-none',
                      dragOverIdx === idx && dragIdx !== idx
                        ? 'border-purple-500 bg-purple-500/10 scale-105'
                        : isVerified
                          ? 'border-green-500/30 bg-green-500/5 hover:border-green-500/60'
                          : 'border-yellow-500/30 bg-yellow-500/5 hover:border-yellow-500/60',
                      dragIdx === idx && 'opacity-40'
                    )}
                  >
                    <GripVertical className="w-3 h-3 text-muted-foreground/40 group-hover:text-muted-foreground" />
                    {isVerified ? (
                      <CheckCircle className="w-3 h-3 text-green-500 flex-shrink-0" />
                    ) : (
                      <AlertCircle className="w-3 h-3 text-yellow-500 flex-shrink-0" />
                    )}
                    <span className="text-foreground">{sym}</span>
                    <span className="text-muted-foreground/60 text-[10px]">/USDC</span>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleRemove(sym) }}
                      className="ml-0.5 p-0.5 rounded hover:bg-red-500/20 text-muted-foreground hover:text-red-500 transition-colors opacity-0 group-hover:opacity-100"
                      title="移除"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                )
              })}
            </div>

            {symbolsDetail.length > 0 && (
              <div className="mt-3 flex items-center gap-4 text-[10px] text-muted-foreground">
                <span className="flex items-center gap-1">
                  <CheckCircle className="w-3 h-3 text-green-500" /> Hyperliquid 已验证可交易
                </span>
                <span className="flex items-center gap-1">
                  <AlertCircle className="w-3 h-3 text-yellow-500" /> 未在交易所验证（可能已下架或拼写错误）
                </span>
              </div>
            )}
          </div>
        )}
      </Card>

      <Card title="添加交易对" icon={<Plus className="w-4 h-4 text-green-500" />}
        action={
          exchangeSymbols.length > 0 ? (
            <span className="text-[10px] text-muted-foreground">
              交易所可用: {exchangeSymbols.length} 个币种
            </span>
          ) : (
            <button
              onClick={handleRefreshExchange}
              disabled={refreshingExchange}
              className="text-xs text-purple-500 hover:text-purple-400 transition-colors"
            >
              {refreshingExchange ? '刷新中...' : '从交易所获取币种列表'}
            </button>
          )
        }
      >
        <div className="space-y-4">
          <div>
            <label className="block text-xs text-muted-foreground mb-2">
              自定义输入（支持逗号分隔批量添加，如 MEME, TURBO, WLD）
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={customInput}
                onChange={e => setCustomInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleCustomAdd()}
                placeholder="输入币种短码，如 BTC（会自动转为大写）"
                className="flex-1 bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm focus:border-purple-500 outline-none font-mono"
              />
              <button
                onClick={handleCustomAdd}
                disabled={!customInput.trim() || saving}
                className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-40 text-white rounded-lg text-sm font-medium transition-colors"
              >
                添加
              </button>
            </div>
            <p className="text-[10px] text-muted-foreground mt-1.5">
              格式说明：只需输入币种短码（如 <code className="bg-muted px-1 rounded">BTC</code>），系统自动处理为 Hyperliquid 永续合约格式 <code className="bg-muted px-1 rounded">BTC/USDC:USDC</code>
            </p>
          </div>

          {notAdded.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <label className="text-xs text-muted-foreground">
                    Hyperliquid 可交易币种
                    <span className="text-green-500 ml-1">({notAdded.length} 可添加)</span>
                  </label>
                  <button
                    onClick={handleRefreshExchange}
                    disabled={refreshingExchange}
                    className="text-muted-foreground hover:text-foreground transition-colors"
                    title="刷新交易所币种"
                  >
                    <RefreshCw className={cn('w-3 h-3', refreshingExchange && 'animate-spin')} />
                  </button>
                </div>
                {notAdded.length > 8 && (
                  <div className="relative max-w-48">
                    <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-muted-foreground" />
                    <input
                      type="text"
                      value={search}
                      onChange={e => setSearch(e.target.value)}
                      placeholder="搜索币种..."
                      className="w-full bg-muted/30 border border-border/50 rounded pl-7 pr-2 py-1 text-xs focus:border-purple-500 outline-none"
                    />
                  </div>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5 max-h-48 overflow-y-auto">
                {filtered.slice(0, 60).map(sym => (
                  <button
                    key={sym}
                    onClick={() => handleAdd(sym)}
                    disabled={saving}
                    className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-dashed border-green-500/30 text-xs font-medium text-muted-foreground hover:text-green-600 hover:border-green-500/60 hover:bg-green-500/5 transition-all disabled:opacity-40"
                  >
                    <Plus className="w-3 h-3" />
                    {sym}
                  </button>
                ))}
                {filtered.length > 60 && (
                  <span className="text-[10px] text-muted-foreground self-center px-2">
                    还有 {filtered.length - 60} 个...使用搜索框筛选
                  </span>
                )}
              </div>
            </div>
          )}

          {exchangeSymbols.length === 0 && !loading && (
            <div className="p-3 rounded-lg bg-yellow-500/5 border border-yellow-500/20 text-xs text-yellow-600 dark:text-yellow-400">
              <AlertCircle className="w-3.5 h-3.5 inline mr-1.5" />
              交易所币种列表未加载。点击上方「从交易所获取币种列表」按钮刷新。
              首次加载可能需要几秒钟（需要逐个验证币种的可交易性）。
            </div>
          )}
        </div>
      </Card>
    </div>
  )
}

// ── 通知设置 Tab ──
function NotificationTab() {
  const [config, setConfig] = useState<Record<string, any>>({
    enabled: false,
    webhook_url: '',
    feishu_app_id: '',
    feishu_app_secret: '',
    feishu_chat_id: '',
    min_level: 'info',
    enable_open: true,
    enable_close: true,
    enable_tp_sl: true,
    enable_liquidation: true,
    enable_system: true,
    min_interval_seconds: 5,
    feishu_assistant_enabled: false,
    assistant_notify_actions: true,
    assistant_notify_p0: true,
    assistant_daily_report_enabled: true,
  })
  const [callbackUrl, setCallbackUrl] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<Record<string, any> | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    (async () => {
      try {
        const res = await apiRequest('/notification/config')
        if (res.config) setConfig((prev: Record<string, any>) => ({ ...prev, ...res.config }))
      } catch { /* 首次使用未配置 */ }
      try {
        const urlRes = await apiRequest('/feishu/events/url')
        if (urlRes.callback_url) setCallbackUrl(urlRes.callback_url)
      } catch { /* ignore */ }
      setLoading(false)
    })()
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await apiRequest('/notification/config', {
        method: 'POST',
        body: JSON.stringify(config),
      })
      toast.success('通知配置已保存')
    } catch (e) {
      console.error('[Settings] save notification error:', e)
      toast.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await apiRequest('/notification/test', {
        method: 'POST',
        body: JSON.stringify({ message: '飞书通知测试' }),
      })
      setTestResult(res)
      if (res.ok) toast.success('测试消息发送成功')
      else toast.error('测试失败，请检查配置')
    } catch (e) {
      toast.error('测试请求失败')
    } finally {
      setTesting(false)
    }
  }

  const update = (key: string, val: any) => setConfig((prev: Record<string, any>) => ({ ...prev, [key]: val }))

  const ToggleSwitch = ({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) => (
    <label className="flex items-center gap-2 cursor-pointer">
      <div onClick={() => onChange(!checked)}
        className={cn('w-10 h-5 rounded-full transition-colors relative cursor-pointer', checked ? 'bg-purple-600' : 'bg-muted')}>
        <div className={cn('absolute top-0.5 w-4 h-4 bg-white rounded-full transition-transform shadow-sm', checked ? 'translate-x-5' : 'translate-x-0.5')} />
      </div>
      <span className="text-sm text-muted-foreground">{label}</span>
    </label>
  )

  if (loading) return <div className="flex items-center justify-center py-12 text-muted-foreground">加载中...</div>

  return (
    <div className="space-y-4">
      {/* 总开关 */}
      <Card title="飞书通知" icon={<Bell className="w-4 h-4 text-purple-500" />}>
        <InfoBox>
          支持两种推送方式：飞书 Webhook 机器人（简单直接）和飞书应用 API（需 AppId/AppSecret）。
          可同时启用，消息会并行发送到所有已配置的渠道。
        </InfoBox>
        <div className="mt-4">
          <FormRow label="启用通知推送">
            <ToggleSwitch checked={config.enabled} onChange={v => update('enabled', v)} label={config.enabled ? '已启用' : '已关闭'} />
          </FormRow>
        </div>
      </Card>

      {/* 渠道 1: Webhook */}
      <Card title="渠道 1: 飞书 Webhook 机器人" icon={<Zap className="w-4 h-4 text-yellow-500" />}>
        <InfoBox>
          在飞书群中添加「自定义机器人」，获取 Webhook 地址后填入下方。
        </InfoBox>
        <div className="mt-4 space-y-3">
          <FormRow label="Webhook URL">
            <input type="url" value={config.webhook_url} onChange={e => update('webhook_url', e.target.value)}
              placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx"
              className="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm focus:border-purple-500 outline-none" />
          </FormRow>
        </div>
      </Card>

      {/* 渠道 2: 飞书应用 API */}
      <Card title="渠道 2: 飞书应用 API" icon={<Shield className="w-4 h-4 text-blue-500" />}>
        <InfoBox>
          复用 OpenClaw 配置的飞书应用凭据。AppId/AppSecret 已从 OpenClaw 自动读取，如需修改可手动填写。
          需额外填写目标群 Chat ID（可在飞书群设置中获取）。
        </InfoBox>
        <div className="mt-4 space-y-3">
          <FormRow label="App ID">
            <input type="text" value={config.feishu_app_id} onChange={e => update('feishu_app_id', e.target.value)}
              placeholder="cli_xxxxxxxx"
              className="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm focus:border-purple-500 outline-none" />
          </FormRow>
          <FormRow label="App Secret">
            <input type="password" value={config.feishu_app_secret} onChange={e => update('feishu_app_secret', e.target.value)}
              placeholder="****"
              className="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm focus:border-purple-500 outline-none" />
          </FormRow>
          <FormRow label="目标群 Chat ID">
            <input type="text" value={config.feishu_chat_id} onChange={e => update('feishu_chat_id', e.target.value)}
              placeholder="oc_xxxxxxxx"
              className="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm focus:border-purple-500 outline-none" />
          </FormRow>
        </div>
      </Card>

      <Card title="Alpha 助手 · 飞书双向对话" icon={<Brain className="w-4 h-4 text-purple-500" />}>
        <InfoBox>
          启用后，飞书群内 @机器人 可问 Alpha 助手并执行分析/评估等操作（需飞书应用 API + 事件订阅 im.message.receive_v1）。
          环境变量 FEISHU_ASSISTANT_ENABLED=true 且下方开关打开后生效。
        </InfoBox>
        <div className="mt-4 space-y-3">
          <FormRow label="启用助手飞书对话">
            <ToggleSwitch
              checked={!!config.feishu_assistant_enabled}
              onChange={(v) => update('feishu_assistant_enabled', v)}
              label={config.feishu_assistant_enabled ? '已启用' : '已关闭'}
            />
          </FormRow>
          <FormRow label="L2/L3 操作结果推送">
            <ToggleSwitch
              checked={config.assistant_notify_actions !== false}
              onChange={(v) => update('assistant_notify_actions', v)}
              label={config.assistant_notify_actions !== false ? '开' : '关'}
            />
          </FormRow>
          <FormRow label="P0 严重错误推送">
            <ToggleSwitch
              checked={config.assistant_notify_p0 !== false}
              onChange={(v) => update('assistant_notify_p0', v)}
              label={config.assistant_notify_p0 !== false ? '开' : '关'}
            />
          </FormRow>
          <FormRow label="每日日报推送">
            <ToggleSwitch
              checked={config.assistant_daily_report_enabled !== false}
              onChange={(v) => update('assistant_daily_report_enabled', v)}
              label={config.assistant_daily_report_enabled !== false ? '开' : '关'}
            />
          </FormRow>
          {callbackUrl && (
            <FormRow label="事件回调 URL（填入飞书开放平台）">
              <input
                type="text"
                readOnly
                value={callbackUrl}
                className="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-xs font-mono"
              />
            </FormRow>
          )}
          <button
            type="button"
            onClick={async () => {
              try {
                await apiRequest('/assistant/daily-report/push', { method: 'POST' })
                alert('日报推送已触发（需已配置飞书）')
              } catch (e: any) {
                alert(e?.message || '推送失败')
              }
            }}
            className="text-sm px-3 py-1.5 rounded-lg border border-border hover:bg-muted"
          >
            立即推送日报到飞书
          </button>
        </div>
      </Card>

      {/* 事件过滤 */}
      <Card title="通知事件过滤" icon={<ShieldAlert className="w-4 h-4 text-orange-500" />}>
        <div className="mt-2 space-y-3">
          <FormRow label="最低通知级别">
            <select value={config.min_level} onChange={e => update('min_level', e.target.value)}
              className="bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm focus:border-purple-500 outline-none">
              <option value="info">全部 (Info)</option>
              <option value="warning">警告及以上 (Warning+)</option>
              <option value="critical">仅紧急 (Critical)</option>
            </select>
          </FormRow>
          <div className="grid grid-cols-2 gap-3">
            <FormRow label="开仓通知"><ToggleSwitch checked={config.enable_open} onChange={v => update('enable_open', v)} label={config.enable_open ? '开' : '关'} /></FormRow>
            <FormRow label="平仓通知"><ToggleSwitch checked={config.enable_close} onChange={v => update('enable_close', v)} label={config.enable_close ? '开' : '关'} /></FormRow>
            <FormRow label="止盈止损触发"><ToggleSwitch checked={config.enable_tp_sl} onChange={v => update('enable_tp_sl', v)} label={config.enable_tp_sl ? '开' : '关'} /></FormRow>
            <FormRow label="爆仓预警"><ToggleSwitch checked={config.enable_liquidation} onChange={v => update('enable_liquidation', v)} label={config.enable_liquidation ? '开' : '关'} /></FormRow>
            <FormRow label="系统事件"><ToggleSwitch checked={config.enable_system} onChange={v => update('enable_system', v)} label={config.enable_system ? '开' : '关'} /></FormRow>
          </div>
          <FormRow label="同类消息最小间隔（秒）">
            <input type="number" min={1} max={3600} value={config.min_interval_seconds}
              onChange={e => update('min_interval_seconds', parseInt(e.target.value) || 5)}
              className="w-24 bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm focus:border-purple-500 outline-none" />
          </FormRow>
        </div>
      </Card>

      {/* 操作区 */}
      <div className="flex items-center gap-3">
        <button onClick={handleSave} disabled={saving}
          className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors">
          <Save className="w-4 h-4" />
          {saving ? '保存中...' : '保存配置'}
        </button>
        <button onClick={handleTest} disabled={testing || (!config.webhook_url && !config.feishu_chat_id)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors">
          <Zap className="w-4 h-4" />
          {testing ? '测试中...' : '发送测试消息'}
        </button>
      </div>

      {/* 测试结果 */}
      {testResult && (
        <Card title="测试结果" icon={testResult.ok ? <CheckCircle className="w-4 h-4 text-green-500" /> : <AlertCircle className="w-4 h-4 text-red-500" />}>
          <div className="text-sm space-y-1 mt-2">
            <p className={testResult.ok ? 'text-green-400' : 'text-red-400'}>{testResult.message}</p>
            {testResult.channels && Object.entries(testResult.channels).map(([ch, info]: [string, any]) => (
              <div key={ch} className="flex items-center gap-2 text-muted-foreground">
                <span className={info.ok ? 'text-green-400' : info.configured ? 'text-red-400' : 'text-muted-foreground'}>
                  {info.ok ? '  ' : info.configured ? '  ' : '  '}
                </span>
                <span>{ch === 'webhook' ? 'Webhook 机器人' : '飞书应用 API'}</span>
                <span className="text-xs">
                  {info.ok ? '发送成功' : info.configured ? '发送失败' : '未配置'}
                  {info.note ? ` (${info.note})` : ''}
                  {info.token_ok ? ' — 凭据验证通过' : ''}
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

// ── 系统日志 Tab ──
function SystemLogsTab({ logs, loading, onRefresh }: {
  logs: SystemLog[]; loading: boolean; onRefresh: () => void
}) {
  const LEVEL_COLOR: Record<string, string> = {
    ERROR: 'text-red-500',
    WARNING: 'text-yellow-500',
    INFO: 'text-blue-500',
    DEBUG: 'text-muted-foreground',
    CRITICAL: 'text-red-600 dark:text-red-400 font-bold',
  }

  return (
    <Card title="系统日志" icon={<FileText className="w-4 h-4 text-purple-500" />}
      action={
        <button onClick={onRefresh} disabled={loading} className="text-muted-foreground hover:text-foreground">
          <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
        </button>
      }>
      {loading && <div className="text-center text-muted-foreground py-8 text-sm">加载中...</div>}
      {!loading && logs.length === 0 && (
        <div className="text-center text-muted-foreground py-8 text-sm">暂无日志</div>
      )}
      {!loading && logs.length > 0 && (
        <div className="space-y-0.5 font-mono text-xs max-h-[28rem] overflow-y-auto">
          {logs.map((log, i) => (
            <div key={i} className="flex gap-3 py-1.5 border-b border-border/30">
              <span className="text-muted-foreground/50 flex-shrink-0 w-16">
                {log.created_at ? new Date(log.created_at).toLocaleTimeString() : '-'}
              </span>
              <span className={cn('flex-shrink-0 w-16 uppercase', LEVEL_COLOR[log.level] || 'text-muted-foreground')}>
                {log.level}
              </span>
              <span className="text-foreground/80 break-all">{log.message}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

// ── 通用组件 ──

function Card({ title, icon, children, action }: {
  title: string; icon: React.ReactNode; children: React.ReactNode; action?: React.ReactNode
}) {
  return (
    <div className="rounded-2xl border border-border bg-card">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          {icon}
          <h2 className="font-semibold text-sm">{title}</h2>
        </div>
        {action}
      </div>
      <div className="p-4">{children}</div>
    </div>
  )
}

function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm text-muted-foreground mb-1.5">{label}</label>
      {children}
    </div>
  )
}

function InfoBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 p-3 rounded-lg bg-blue-500/5 border border-blue-500/20 text-blue-700 dark:text-blue-300 text-sm">
      <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
      <span>{children}</span>
    </div>
  )
}
