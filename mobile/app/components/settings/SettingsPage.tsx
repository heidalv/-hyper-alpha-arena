import React, { useState, useEffect } from 'react'
import TouchButton from '@/components/ui/TouchButton'
import { apiRequest } from '@/api/client'

interface SettingsPageProps {
  onBack: () => void
}

type SubPage = 'main' | 'api-keys' | 'llm-config' | 'trading-pairs' | 'notifications'

export default function SettingsPage({ onBack }: SettingsPageProps) {
  const [subPage, setSubPage] = useState<SubPage>('main')

  const backToMain = () => setSubPage('main')

  if (subPage !== 'main') {
    return (
      <div className="flex flex-col h-full">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
          <button onClick={backToMain} className="p-1.5 rounded hover:bg-terminal-card/50 active:opacity-70">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          <h2 className="text-lg font-semibold">{
            { 'api-keys': 'API 密钥', 'llm-config': 'LLM 配置库', 'trading-pairs': '交易对管理', 'notifications': '通知设置' }[subPage]
          }</h2>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          {subPage === 'api-keys' && <ApiKeySettings />}
          {subPage === 'llm-config' && <LLMConfigSettings />}
          {subPage === 'trading-pairs' && <TradingPairsSettings />}
          {subPage === 'notifications' && <NotificationSettings />}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
        <button onClick={onBack} className="p-1.5 rounded hover:bg-terminal-card/50 active:opacity-70">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
        <h2 className="text-lg font-semibold">设置</h2>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        <button onClick={() => setSubPage('api-keys')} className="w-full card text-left flex items-center justify-between active:opacity-70">
          <div className="flex items-center gap-3">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#a855f7" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" /></svg>
          </div>
          <div className="flex-1 ml-3 text-left">
            <p className="text-sm font-medium">API 密钥</p>
            <p className="text-xs text-muted">管理交易所和外部数据源密钥</p>
          </div>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth="2"><polyline points="9 18 15 12 9 6" /></svg>
        </button>
        <button onClick={() => setSubPage('llm-config')} className="w-full card text-left flex items-center justify-between active:opacity-70">
          <div className="flex items-center gap-3">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2a4 4 0 0 1 4 4v1h2a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h2V6a4 4 0 0 1 4-4z" /></svg>
          </div>
          <div className="flex-1 ml-3 text-left">
            <p className="text-sm font-medium">LLM 配置库</p>
            <p className="text-xs text-muted">管理大模型 API 配置</p>
          </div>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth="2"><polyline points="9 18 15 12 9 6" /></svg>
        </button>
        <button onClick={() => setSubPage('trading-pairs')} className="w-full card text-left flex items-center justify-between active:opacity-70">
          <div className="flex items-center gap-3">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /><path d="M2 12h20" /></svg>
          </div>
          <div className="flex-1 ml-3 text-left">
            <p className="text-sm font-medium">交易对管理</p>
            <p className="text-xs text-muted">配置常用交易对</p>
          </div>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth="2"><polyline points="9 18 15 12 9 6" /></svg>
        </button>
        <button onClick={() => setSubPage('notifications')} className="w-full card text-left flex items-center justify-between active:opacity-70">
          <div className="flex items-center gap-3">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#ec4899" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 0 1-3.46 0" /></svg>
          </div>
          <div className="flex-1 ml-3 text-left">
            <p className="text-sm font-medium">通知设置</p>
            <p className="text-xs text-muted">飞书通知和告警配置</p>
          </div>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth="2"><polyline points="9 18 15 12 9 6" /></svg>
        </button>
      </div>
    </div>
  )
}

// ── Sub-pages ──

function ApiKeySettings() {
  const [keys, setKeys] = useState<Array<{ name: string; key_preview: string; exchange: string }>>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function load() {
      try {
        const data = await apiRequest<any[]>('/config/api-keys')
        setKeys(data || [])
      } catch (e) {
        console.error('[ApiKeySettings] load:', e)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  if (loading) return <div className="text-center text-terminal-muted py-8">加载中...</div>

  return (
    <div className="space-y-4">
      <p className="text-sm text-terminal-muted">管理连接外部交易所和 API 数据源的密钥。密钥将加密存储。</p>
      {keys.length === 0 ? (
        <div className="card text-center text-terminal-muted py-8">
          <p className="text-sm">暂无 API 密钥</p>
          <p className="text-xs mt-1">在桌面端或配置文件中添加</p>
        </div>
      ) : (
        <div className="space-y-2">
          {keys.map((k, i) => (
            <div key={i} className="card flex items-center justify-between">
              <div>
                <p className="text-sm font-medium">{k.name}</p>
                <p className="text-xs text-terminal-muted">{k.exchange} · {k.key_preview || '***'}</p>
              </div>
              <span className="text-xs text-terminal-muted">● 已配置</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function LLMConfigSettings() {
  const [configs, setConfigs] = useState<Array<{ id: number; name: string; provider: string; model: string }>>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function load() {
      try {
        const data = await apiRequest<any[]>('/llm-config')
        setConfigs(data || [])
      } catch (e) {
        console.error('[LLMConfig] load:', e)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  if (loading) return <div className="text-center text-terminal-muted py-8">加载中...</div>

  return (
    <div className="space-y-4">
      <p className="text-sm text-terminal-muted">管理大语言模型 API 配置，用于 AI 交易决策。</p>
      {configs.length === 0 ? (
        <div className="card text-center text-terminal-muted py-8">
          <p className="text-sm">暂无 LLM 配置</p>
          <p className="text-xs mt-1">在桌面端「设置→LLM 配置」中添加</p>
        </div>
      ) : (
        <div className="space-y-2">
          {configs.map(c => (
            <div key={c.id} className="card">
              <p className="text-sm font-medium">{c.name}</p>
              <div className="flex gap-3 mt-1 text-xs text-terminal-muted">
                <span>{c.provider}</span>
                <span>{c.model}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function TradingPairsSettings() {
  const [pairs, setPairs] = useState<string[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function load() {
      try {
        const data = await apiRequest<any>('/config/trading-pairs')
        setPairs(data?.symbols || data?.pairs || [])
      } catch (e) {
        console.error('[TradingPairs] load:', e)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  if (loading) return <div className="text-center text-terminal-muted py-8">加载中...</div>

  return (
    <div className="space-y-4">
      <p className="text-sm text-terminal-muted">管理全自动交易可用的交易对列表。</p>
      {pairs.length === 0 ? (
        <div className="card text-center text-terminal-muted py-8">
          <p className="text-sm">使用默认交易对</p>
          <p className="text-xs mt-1">BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, LINK, DOT</p>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {pairs.map(p => (
            <span key={p} className="px-3 py-1.5 rounded-full text-sm bg-terminal-primary/10 text-terminal-primary border border-terminal-primary/30">
              {p}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function NotificationSettings() {
  return (
    <div className="space-y-4">
      <p className="text-sm text-terminal-muted">配置飞书通知和告警规则。</p>

      <div className="card space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">飞书通知</p>
            <p className="text-xs text-terminal-muted">通过飞书 Webhook 发送交易通知</p>
          </div>
          <button className="w-10 h-6 rounded-full relative bg-terminal-border">
            <span className="absolute top-0.5 left-[2px] w-5 h-5 rounded-full bg-white transition-transform" />
          </button>
        </div>

        <div className="h-px bg-terminal-border" />

        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">告警通知</p>
            <p className="text-xs text-terminal-muted">重大事件和风控告警推送</p>
          </div>
          <button className="w-10 h-6 rounded-full relative bg-terminal-primary">
            <span className="absolute top-0.5 left-[18px] w-5 h-5 rounded-full bg-white transition-transform" />
          </button>
        </div>

        <div className="h-px bg-terminal-border" />

        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">日终摘要</p>
            <p className="text-xs text-terminal-muted">每日定时推送交易摘要报告</p>
          </div>
          <button className="w-10 h-6 rounded-full relative bg-terminal-primary">
            <span className="absolute top-0.5 left-[18px] w-5 h-5 rounded-full bg-white transition-transform" />
          </button>
        </div>

        <div className="h-px bg-terminal-border" />

        <div>
          <label className="text-xs text-terminal-muted block mb-1">Webhook URL</label>
          <input
            type="text"
            placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
            className="w-full bg-terminal-bg border border-terminal-border rounded px-3 py-2 text-sm text-terminal-text placeholder-terminal-muted/50 focus:outline-none focus:border-terminal-primary"
          />
        </div>
      </div>
    </div>
  )
}
