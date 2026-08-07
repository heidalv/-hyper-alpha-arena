/**
 * ExchangeConfigPanel — 交易所统一配置
 *
 * - Hyperliquid：钱包私钥（Testnet / Mainnet）
 * - 其他交易所：API Key / Secret
 */
import { useState, useEffect, useCallback }from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { Button } from '../ui/button'
import {
  Key, Plus, Trash2, RefreshCw, CheckCircle2, XCircle,
  Eye, EyeOff, TestTube, Shield, Wallet, Bot,
} from 'lucide-react'
import {
  getSupportedExchanges, getCredentials, saveCredential,
  deleteCredential, testCredential,
  type SupportedExchange, type ExchangeCredential,
} from '@/lib/exchangeCredentialApi'
import { getAccounts, type TradingAccount } from '@/lib/api'
import WalletConfigPanel from '@/components/trader/WalletConfigPanel'

const FALLBACK_EXCHANGES: SupportedExchange[] = [
  { id: 'binance', name: 'Binance', supports_spot: true, supports_futures: true, needs_passphrase: false },
  { id: 'bybit', name: 'Bybit', supports_spot: true, supports_futures: true, needs_passphrase: false },
  { id: 'okx', name: 'OKX', supports_spot: true, supports_futures: true, needs_passphrase: true },
  { id: 'gateio', name: 'Gate.io', supports_spot: true, supports_futures: true, needs_passphrase: false },
  { id: 'asterdex', name: 'Asterdex', supports_spot: false, supports_futures: true, needs_passphrase: false },
]

const EXCHANGE_COLORS: Record<string, string> = {
  binance: 'text-yellow-600 dark:text-yellow-400',
  bybit: 'text-orange-600 dark:text-orange-400',
  okx: 'text-blue-600 dark:text-blue-400',
  gateio: 'text-green-600 dark:text-green-400',
  asterdex: 'text-purple-600 dark:text-purple-400',
}

export default function ExchangeConfigPanel() {
  const [supported, setSupported] = useState<SupportedExchange[]>([])
  const [credentials, setCredentials] = useState<ExchangeCredential[]>([])
  const [traders, setTraders] = useState<TradingAccount[]>([])
  const [selectedTraderId, setSelectedTraderId] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [adding, setAdding] = useState(false)
  const [testResults, setTestResults] = useState<Record<number, any>>({})
  const [walletRefreshKey, setWalletRefreshKey] = useState(0)

  const [form, setForm] = useState({
    exchange: '',
    label: '',
    api_key: '',
    api_secret: '',
    passphrase: '',
    testnet: true,
  })
  const [showSecret, setShowSecret] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [s, c, accounts] = await Promise.allSettled([
        getSupportedExchanges(),
        getCredentials(),
        getAccounts(),
      ])
      const exchangeList = s.status === 'fulfilled' && s.value.length > 0
        ? s.value.filter(e => e.id !== 'hyperliquid')
        : FALLBACK_EXCHANGES
      setSupported(exchangeList)
      setCredentials(c.status === 'fulfilled' ? c.value : [])
      if (accounts.status === 'fulfilled') {
        const list = accounts.value.filter((a: TradingAccount) => a.trading_mode !== 'paper')
        setTraders(list)
        setSelectedTraderId(prev => {
          if (prev && list.some(a => a.id === prev)) return prev
          return list[0]?.id ?? null
        })
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const selectedTrader = traders.find(t => t.id === selectedTraderId)

  const handleSave = async () => {
    if (!form.exchange || !form.api_key) return
    setLoading(true)
    try {
      await saveCredential({
        exchange: form.exchange,
        label: form.label,
        api_key: form.api_key,
        api_secret: form.api_secret,
        passphrase: form.passphrase,
        testnet: form.testnet,
      })
      setAdding(false)
      setForm({ exchange: '', label: '', api_key: '', api_secret: '', passphrase: '', testnet: true })
      await refresh()
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('确定删除此凭证？')) return
    await deleteCredential(id)
    await refresh()
  }

  const handleTest = async (id: number) => {
    setTestResults(prev => ({ ...prev, [id]: { testing: true } }))
    const result = await testCredential(id)
    setTestResults(prev => ({ ...prev, [id]: result }))
  }

  const selectedExchange = supported.find(e => e.id === form.exchange)

  return (
    <div className="h-full w-full flex flex-col bg-background">
      <div className="flex-shrink-0 flex items-center justify-between border-b px-6 py-3">
        <div className="flex items-center gap-3">
          <Key className="w-5 h-5 text-amber-600" />
          <div>
            <span className="font-semibold text-sm">交易所配置</span>
            <span className="text-xs text-muted-foreground ml-2">Hyperliquid 钱包 · CEX API 密钥</span>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={refresh} disabled={loading} className="text-xs h-8">
          <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} /> 刷新
        </Button>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-4 max-w-4xl mx-auto w-full">

        {/* Hyperliquid */}
        <Card>
          <CardHeader className="py-3 px-4">
            <CardTitle className="text-sm flex items-center gap-2">
              <Wallet className="w-4 h-4 text-blue-600" />
              Hyperliquid 配置
              <span className="text-[10px] font-normal px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">
                私钥模式
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-0 space-y-5">
            <p className="text-xs text-muted-foreground">
              Hyperliquid 使用钱包私钥签名链上交易，无需传统 API Key/Secret。支持 Testnet 与 Mainnet 分别配置。
            </p>

            {/* 「访问令牌」输入框已移除：对应后端接口从未实现（/accounts/hyperliquid-config 必 404），
                Hyperliquid 走钱包私钥模式，无需额外令牌 */}

            <div className="border-t pt-4 space-y-4">
              <p className="text-xs font-medium flex items-center gap-1">
                <Bot className="w-3.5 h-3.5" /> 交易员钱包（Testnet / Mainnet）
              </p>

            {traders.length === 0 ? (
              <p className="text-xs text-muted-foreground py-4 text-center border rounded-md bg-muted/30">
                请先在「AI 交易员管理」中创建交易员，再在此配置 Hyperliquid 钱包。
              </p>
            ) : (
              <>
                <div>
                  <label className="text-xs text-muted-foreground mb-1.5 block flex items-center gap-1">
                    <Bot className="w-3 h-3" /> 选择 AI 交易员
                  </label>
                  <select
                    className="w-full max-w-md border border-border rounded-md px-3 py-2 text-sm bg-background"
                    value={selectedTraderId ?? ''}
                    onChange={e => setSelectedTraderId(Number(e.target.value))}
                    title="选择 AI 交易员"
                  >
                    {traders.map(t => (
                      <option key={t.id} value={t.id}>{t.name}</option>
                    ))}
                  </select>
                </div>

                {selectedTrader && (
                  <WalletConfigPanel
                    key={`${selectedTrader.id}-${walletRefreshKey}`}
                    accountId={selectedTrader.id}
                    accountName={selectedTrader.name}
                    onWalletConfigured={() => {
                      setWalletRefreshKey(k => k + 1)
                      refresh()
                    }}
                  />
                )}
              </>
            )}
            </div>
          </CardContent>
        </Card>

        {/* CEX API Keys */}
        <Card>
          <CardHeader className="py-3 px-4 flex flex-row items-center justify-between space-y-0">
            <CardTitle className="text-sm flex items-center gap-2">
              <Key className="w-4 h-4 text-muted-foreground" />
              中心化交易所 API
            </CardTitle>
            <Button size="sm" onClick={() => setAdding(!adding)} className="text-xs h-8">
              <Plus className="w-3 h-3 mr-1" /> 添加交易所
            </Button>
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-0 space-y-3">
            <p className="text-xs text-muted-foreground">
              Binance、Bybit、OKX 等使用 API Key / Secret，配置后所有关联交易员共用。
            </p>

            {adding && (
              <div className="border rounded-lg p-4 space-y-3 bg-muted/20">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">交易所</label>
                    <select
                      className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background"
                      value={form.exchange}
                      onChange={e => setForm(f => ({ ...f, exchange: e.target.value }))}
                      title="选择交易所"
                    >
                      <option value="">选择交易所...</option>
                      {supported.map(ex => (
                        <option key={ex.id} value={ex.id}>{ex.name}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">标签 (可选)</label>
                    <input
                      className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background"
                      placeholder="例: 主账户"
                      value={form.label}
                      onChange={e => setForm(f => ({ ...f, label: e.target.value }))}
                    />
                  </div>
                </div>

                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">API Key</label>
                  <input
                    className="w-full border border-border rounded-md px-3 py-2 text-sm font-mono bg-background"
                    placeholder="输入 API Key"
                    value={form.api_key}
                    onChange={e => setForm(f => ({ ...f, api_key: e.target.value }))}
                  />
                </div>

                <div className="relative">
                  <label className="text-xs text-muted-foreground mb-1 block">API Secret</label>
                  <input
                    className="w-full border border-border rounded-md px-3 py-2 text-sm font-mono pr-10 bg-background"
                    type={showSecret ? 'text' : 'password'}
                    placeholder="输入 API Secret"
                    value={form.api_secret}
                    onChange={e => setForm(f => ({ ...f, api_secret: e.target.value }))}
                  />
                  <button
                    type="button"
                    className="absolute right-2 top-7 text-muted-foreground hover:text-foreground"
                    onClick={() => setShowSecret(!showSecret)}
                  >
                    {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>

                {selectedExchange?.needs_passphrase && (
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">Passphrase (OKX)</label>
                    <input
                      className="w-full border border-border rounded-md px-3 py-2 text-sm font-mono bg-background"
                      type="password"
                      placeholder="OKX API Passphrase"
                      value={form.passphrase}
                      onChange={e => setForm(f => ({ ...f, passphrase: e.target.value }))}
                    />
                  </div>
                )}

                <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={form.testnet}
                    onChange={e => setForm(f => ({ ...f, testnet: e.target.checked }))}
                    className="rounded"
                  />
                  Testnet 模式
                </label>

                <div className="flex gap-2 pt-1">
                  <Button size="sm" onClick={handleSave} disabled={!form.exchange || !form.api_key} className="text-xs">
                    <Shield className="w-3 h-3 mr-1" /> 保存并加密
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setAdding(false)} className="text-xs">
                    取消
                  </Button>
                </div>
              </div>
            )}

            {credentials.length === 0 ? (
              <p className="text-xs text-muted-foreground text-center py-6 border rounded-md bg-muted/20">
                尚未配置 CEX API 密钥，点击「添加交易所」开始配置。
              </p>
            ) : (
              <div className="space-y-2">
                {credentials.map(c => {
                  const tr = testResults[c.id]
                  return (
                    <div key={c.id} className="flex items-center gap-3 px-3 py-2.5 rounded-md border bg-card">
                      <div className={`font-bold text-sm ${EXCHANGE_COLORS[c.exchange] || 'text-foreground'}`}>
                        {c.exchange.toUpperCase()}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs">{c.label || '默认'}</p>
                        <p className="text-[10px] text-muted-foreground">
                          {c.testnet ? 'Testnet' : 'Mainnet'} · Key: {c.has_key ? '✓' : '✗'} · Secret: {c.has_secret ? '✓' : '✗'}
                          {c.has_passphrase && ' · Pass: ✓'}
                        </p>
                      </div>

                      {tr && !tr.testing && (
                        <div className="flex items-center gap-1 text-xs">
                          {tr.connected
                            ? <><CheckCircle2 className="w-3.5 h-3.5 text-green-600" /><span className="text-green-600">连接成功</span></>
                            : <><XCircle className="w-3.5 h-3.5 text-red-600" /><span className="text-red-600">失败</span></>
                          }
                        </div>
                      )}

                      <div className="flex gap-1">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleTest(c.id)}
                          className="text-[10px] h-7 px-2"
                          disabled={tr?.testing}
                        >
                          <TestTube className={`w-3 h-3 ${tr?.testing ? 'animate-pulse' : ''}`} />
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleDelete(c.id)}
                          className="text-[10px] h-7 px-2 text-red-600 hover:text-red-700"
                        >
                          <Trash2 className="w-3 h-3" />
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
