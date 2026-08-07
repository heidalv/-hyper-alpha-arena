import React, { useState, useEffect, useCallback } from 'react'
import { getSessions, getSessionStatus } from '@/api/fullauto'
import type { FullAutoSession } from '@/api/types'

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

export default function MonitorPage({ ws }: PageProps) {
  const [sessions, setSessions] = useState<FullAutoSession[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [session, setSession] = useState<FullAutoSession | null>(null)
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [tradeHistory, setTradeHistory] = useState<any[]>([])
  const [tradeFilter, setTradeFilter] = useState<'all' | 'open' | 'closed'>('all')
  const [loading, setLoading] = useState(true)

  const loadSessions = useCallback(async () => {
    try {
      const data = await getSessions()
      setSessions(data || [])
      if (!selectedId && data?.length > 0) {
        const active = data.find((s: FullAutoSession) => ['running', 'defensive', 'paused'].includes(s.status))
        setSelectedId(active?.session_id || data[0].session_id)
      }
    } catch (e) {
      console.error('[Monitor] load sessions:', e)
    }
  }, [selectedId])

  useEffect(() => { loadSessions() }, [])
  useEffect(() => {
    if (!selectedId) return
    let mounted = true
    async function loadData() {
      try {
        const detail = await getSessionStatus(selectedId!) as any
        if (!mounted) return
        setSession(detail as any)
        setStrategies(detail.active_strategies || [])
        setTradeHistory(detail.trade_history || [])
      } catch (e) {
        console.error('[Monitor] load detail:', e)
      } finally {
        if (mounted) setLoading(false)
      }
    }
    loadData()
    const iv = setInterval(loadData, 10000)
    return () => { mounted = false; clearInterval(iv) }
  }, [selectedId])

  const totalPnl = (session as any)?.total_pnl || 0
  const totalTrades = (session as any)?.total_trades || 0
  const winRate = (session as any)?.win_rate || 0
  const symbols = session?.symbols || []
  const drawdown = (session as any)?.current_drawdown || 0
  const maxDrawdown = (session as any)?.max_drawdown || 0
  const activeCount = (session as any)?.active_count || strategies.length
  const bal = (session as any)?.account_balance as {
    initial_capital: number; total_equity: number; available_balance: number;
    frozen_margin: number; unrealized_pnl: number; realized_pnl: number; total_fee_paid: number;
  } | undefined
  const totalEquity = bal?.total_equity || 0
  const initialCapital = bal?.initial_capital || 0
  const available = bal?.available_balance || 0
  const frozenMargin = bal?.frozen_margin || 0
  const unrealizedPnl = bal?.unrealized_pnl || 0
  const realizedPnl = bal?.realized_pnl || 0
  const totalFee = bal?.total_fee_paid || 0
  const pnlPct = initialCapital > 0 ? ((totalEquity - initialCapital) / initialCapital) * 100 : 0
  // Group strategies by symbol for position view
  const symbolMap = new Map<string, StrategyInfo[]>()
  strategies.forEach(s => {
    const list = symbolMap.get(s.primary_symbol) || []
    list.push(s)
    symbolMap.set(s.primary_symbol, list)
  })
  // Aggregate per symbol
  const symbolRows = Array.from(symbolMap.entries()).map(([sym, stgs]) => ({
    symbol: sym,
    pnl: stgs.reduce((sum, s) => sum + s.total_pnl, 0),
    count: stgs.length,
    tiers: stgs.map(s => s.timeframe_tier).join(','),
    winRate: stgs.length > 0 ? stgs.reduce((sum, s) => sum + s.win_rate, 0) / stgs.length : 0,
  })).sort((a, b) => b.pnl - a.pnl)

  const statusText = session?.status === 'running' ? '运行中' : session?.status === 'defensive' ? '防守中' : session?.status === 'paused' ? '已暂停' : '已停止'

  if (loading) {
    return (
      <div className="p-4 flex items-center justify-center h-64">
        <div className="text-terminal-muted">加载中...</div>
      </div>
    )
  }

  return (
    <div className="p-4 space-y-4">
      {/* ── Session Selector ── */}
      {sessions.length > 1 && (
        <div className="flex items-center gap-2 overflow-x-auto pb-1">
          {sessions.map(s => {
            const isSel = s.session_id === selectedId
            return (
              <button
                key={s.session_id}
                onClick={() => { setSelectedId(s.session_id); setSession(null); setLoading(true) }}
                className={`flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium whitespace-nowrap ${
                  isSel ? 'bg-terminal-primary text-white' : 'bg-terminal-card text-terminal-muted'
                }`}
              >
                <span className={`w-2 h-2 rounded-full ${s.status === 'running' ? 'bg-terminal-profit' : s.status === 'defensive' ? 'bg-terminal-warning' : 'bg-terminal-muted'}`} />
                {s.account_name || `#${s.account_id}`}
              </button>
            )
          })}
        </div>
      )}
      {/* Account Card */}
      <div className="card">
        <div className="flex justify-between items-center mb-1">
          <p className="text-xs text-terminal-muted">模拟账户总览</p>
          <span className={`badge ${session?.status === 'running' ? 'badge-success' : session?.status === 'defensive' ? 'badge-warning' : 'badge-error'}`}>{statusText}</span>
        </div>
        <p className="text-2xl font-bold font-mono tabular-nums">
          ${totalEquity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </p>
        <p className={`text-sm font-mono mt-1 ${totalPnl >= 0 ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
          {totalPnl >= 0 ? '+' : ''}${totalPnl.toLocaleString('en-US', { minimumFractionDigits: 2 })} ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)
        </p>
        <div className="flex gap-6 mt-3">
          <div>
            <p className="text-xs text-terminal-muted">初始资金</p>
            <p className="text-sm font-mono">${initialCapital.toLocaleString('en-US', { maximumFractionDigits: 0 })}</p>
          </div>
          <div>
            <p className="text-xs text-terminal-muted">可用余额</p>
            <p className="text-sm font-mono">${available.toLocaleString('en-US', { maximumFractionDigits: 0 })}</p>
          </div>
          <div>
            <p className="text-xs text-terminal-muted">持仓保证金</p>
            <p className="text-sm font-mono">${frozenMargin.toLocaleString('en-US', { maximumFractionDigits: 0 })}</p>
          </div>
        </div>
        <div className="h-px bg-terminal-border my-3" />
        <div className="flex gap-6">
          <div>
            <p className="text-xs text-terminal-muted">未实现盈亏</p>
            <p className={`text-sm font-mono ${unrealizedPnl >= 0 ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
              {unrealizedPnl >= 0 ? '+' : ''}${unrealizedPnl.toLocaleString('en-US', { maximumFractionDigits: 0 })}
            </p>
          </div>
          <div>
            <p className="text-xs text-terminal-muted">已实现盈亏</p>
            <p className={`text-sm font-mono ${realizedPnl >= 0 ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
              {realizedPnl >= 0 ? '+' : ''}${realizedPnl.toLocaleString('en-US', { maximumFractionDigits: 0 })}
            </p>
          </div>
          <div>
            <p className="text-xs text-terminal-muted">手续费</p>
            <p className="text-sm font-mono text-terminal-loss">-${totalFee.toLocaleString('en-US', { maximumFractionDigits: 0 })}</p>
          </div>
        </div>
        <div className="h-px bg-terminal-border my-3" />
        <div className="flex gap-6">
          <div>
            <p className="text-xs text-terminal-muted">胜率</p>
            <p className="text-sm font-mono">{winRate.toFixed(1)}% ({totalTrades}笔)</p>
          </div>
          <div>
            <p className="text-xs text-terminal-muted">当前回撤</p>
            <p className="text-sm font-mono text-terminal-loss">{(drawdown * 100).toFixed(2)}%</p>
          </div>
          <div>
            <p className="text-xs text-terminal-muted">最大回撤</p>
            <p className="text-sm font-mono text-terminal-loss">{(maxDrawdown * 100).toFixed(2)}%</p>
          </div>
        </div>
        <div className="flex gap-6 mt-2">
          <div>
            <p className="text-xs text-terminal-muted">策略数</p>
            <p className="text-sm font-mono">{activeCount}</p>
          </div>
          <div>
            <p className="text-xs text-terminal-muted">Symbols</p>
            <p className="text-sm font-mono">{symbols.length}</p>
          </div>
        </div>
      </div>

      {/* Symbol PnL Grid */}
      <div>
        <p className="text-xs text-terminal-muted mb-2">按 Symbol 盈亏</p>
        {symbolRows.length === 0 ? (
          <div className="card text-center text-terminal-muted text-sm py-8">暂无策略</div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {symbolRows.map(row => {
              const isProfit = row.pnl >= 0
              return (
                <div
                  key={row.symbol}
                  className={`card border-l-4 ${isProfit ? 'border-l-terminal-profit' : 'border-l-terminal-loss'}`}
                >
                  <div className="flex justify-between items-start">
                    <p className="font-semibold">{row.symbol}</p>
                    <span className={`text-xs font-mono ${isProfit ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
                      {isProfit ? '+' : ''}${row.pnl.toFixed(0)}
                    </span>
                  </div>
                  <div className="flex justify-between mt-1">
                    <span className="text-xs text-terminal-muted">
                      {row.count}策略 | {row.tiers}
                    </span>
                    <span className="text-xs font-mono text-terminal-muted">
                      胜率{row.winRate.toFixed(0)}%
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Trade History */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <p className="text-xs text-terminal-muted">交易历史</p>
          <div className="flex gap-1">
            {(['all', 'open', 'closed'] as const).map(f => (
              <button key={f} onClick={() => setTradeFilter(f)}
                className={`px-2 py-0.5 rounded text-xs ${tradeFilter === f ? 'bg-terminal-primary text-white' : 'text-terminal-muted'}`}>
                {f === 'all' ? '全部' : f === 'open' ? '持仓' : '已平仓'}
              </button>
            ))}
          </div>
        </div>
        <div className="space-y-2 max-h-[400px] overflow-y-auto">
          {(() => {
            const filtered = tradeFilter === 'all' ? tradeHistory : tradeHistory.filter((t: any) => t.status === tradeFilter)
            if (filtered.length === 0) {
              return <div className="card text-center text-terminal-muted text-sm py-4">暂无交易记录</div>
            }
            return filtered.map((t: any) => {
              const isClosed = t.status === 'closed'
              const isProfit = t.pnl >= 0
              return (
                <div key={t.id} className={`card border-l-4 ${isProfit ? 'border-l-terminal-profit' : 'border-l-terminal-loss'}`}>
                  <div className="flex justify-between items-start">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{t.symbol}</span>
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        t.side === 'long' ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'
                      }`}>
                        {t.side === 'long' ? 'Long' : 'Short'} {t.leverage}x
                      </span>
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        isClosed ? 'bg-gray-800 text-gray-400' : 'bg-blue-900/40 text-blue-400'
                      }`}>
                        {isClosed ? '已平仓' : '持仓中'}
                      </span>
                    </div>
                    <span className={`font-mono font-bold text-sm ${isProfit ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
                      {isProfit ? '+' : ''}${t.pnl.toFixed(2)}
                    </span>
                  </div>
                  <div className="flex gap-4 mt-1 text-xs text-terminal-muted">
                    <span>入场: ${t.entry_price.toFixed(2)}</span>
                    {isClosed && t.close_price && <span>平仓: ${t.close_price.toFixed(2)}</span>}
                    {t.timeframe_tier && <span>{t.timeframe_tier}</span>}
                    {t.trade_nature && <span>{t.trade_nature}</span>}
                  </div>
                  <div className="flex justify-between mt-1 text-xs text-terminal-muted">
                    <span>{t.opened_at ? new Date(t.opened_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}</span>
                    {isClosed && t.close_reason && (
                      <span className="truncate ml-2">{t.close_reason}</span>
                    )}
                  </div>
                </div>
              )
            })
          })()}
        </div>
      </div>
    </div>
  )
}
