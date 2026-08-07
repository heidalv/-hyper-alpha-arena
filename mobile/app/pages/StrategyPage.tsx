import React, { useState, useEffect } from 'react'
import Badge from '@/components/ui/Badge'
import { getSessions, getSessionStatus } from '@/api/fullauto'
import type { FullAutoSession } from '@/api/types'
import SignalFeed from '@/components/strategy/SignalFeed'

interface StrategyInfo {
  strategy_id: string
  name: string
  status: string
  primary_symbol: string
  timeframe_tier: string
  total_trades: number
  win_rate: number
  total_pnl: number
}

interface PageProps {
  ws?: any
}

export default function StrategyPage({ ws }: PageProps) {
  const [subTab, setSubTab] = useState<'strategy' | 'signal'>('strategy')
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let mounted = true
    async function loadData() {
      try {
        const sessions = await getSessions()
        const active = (sessions as FullAutoSession[]).find(s =>
          ['running', 'defensive', 'paused'].includes(s.status)
        )
        if (active && mounted) {
          const detail = await getSessionStatus(active.session_id) as any
          if (mounted) setStrategies(detail.active_strategies || [])
        }
      } catch (e) {
        console.error('[Strategy] load failed:', e)
      } finally {
        if (mounted) setLoading(false)
      }
    }
    if (subTab === 'strategy') loadData()
    const iv = setInterval(() => { if (subTab === 'strategy') loadData() }, 15000)
    return () => { mounted = false; clearInterval(iv) }
  }, [subTab])

  // Group by symbol
  const grouped = strategies.reduce((acc, s) => {
    const sym = s.primary_symbol || 'OTHER'
    if (!acc[sym]) acc[sym] = []
    acc[sym].push(s)
    return acc
  }, {} as Record<string, StrategyInfo[]>)

  return (
    <div className="p-4 space-y-4">
      {/* Sub-tab switcher */}
      <div className="flex bg-terminal-card rounded-lg p-1">
        <button
          onClick={() => setSubTab('strategy')}
          className={`flex-1 py-2 rounded-md text-sm font-medium transition-colors
            ${subTab === 'strategy' ? 'bg-terminal-primary text-white' : 'text-terminal-muted'}`}
        >
          策略
        </button>
        <button
          onClick={() => setSubTab('signal')}
          className={`flex-1 py-2 rounded-md text-sm font-medium transition-colors
            ${subTab === 'signal' ? 'bg-terminal-primary text-white' : 'text-terminal-muted'}`}
        >
          信号
        </button>
      </div>

      {subTab === 'strategy' ? (
        loading ? (
          <div className="card text-center text-terminal-muted py-12">
            <p>加载中...</p>
          </div>
        ) : Object.keys(grouped).length === 0 ? (
          <div className="card text-center text-terminal-muted py-12">
            <p>暂无策略</p>
            <p className="text-xs mt-1">启动 FullAuto 会话后自动创建</p>
          </div>
        ) : (
          Object.entries(grouped).map(([symbol, items]) => (
            <div key={symbol}>
              <p className="text-sm font-semibold text-terminal-muted mb-2">{symbol}</p>
              <div className="space-y-2">
                {items.map(s => (
                  <div key={s.strategy_id} className="card">
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-sm truncate">{s.name}</span>
                      <Badge variant={s.status === 'active' ? 'active' : 'stopped'}>
                        {s.timeframe_tier}
                      </Badge>
                    </div>
                    <div className="flex gap-4 mt-2 text-xs text-terminal-muted">
                      <span>PnL: <span className={(s.total_pnl || 0) >= 0 ? 'text-terminal-profit' : 'text-terminal-loss'}>
                        ${(s.total_pnl || 0).toFixed(2)}
                      </span></span>
                      <span>交易: {s.total_trades || 0}</span>
                      <span>胜率: {(s.win_rate || 0).toFixed(0)}%</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))
        )
      ) : (
        <SignalFeed />
      )}
    </div>
  )
}
