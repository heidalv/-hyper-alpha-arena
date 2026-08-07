import React, { useState, useEffect, useCallback } from 'react'
import { apiRequest } from '@/api/client'

interface SignalItem {
  id?: number
  signal_name?: string
  symbol: string
  direction: 'buy' | 'sell' | 'hold'
  confidence: number
  reason?: string
  detected_at?: string
  timestamp?: string
  timeframe?: string
}

export default function SignalFeed() {
  const [signals, setSignals] = useState<SignalItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchSignals = useCallback(async () => {
    try {
      const data = await apiRequest<any[]>('/signals/logs?limit=30')
      const parsed: SignalItem[] = (data || []).map((item: any) => ({
        id: item.id,
        signal_name: item.signal_name || item.signal_id,
        symbol: item.symbol || item.trigger_symbol || '-',
        direction: item.direction || item.signal_direction || 'hold',
        confidence: item.confidence || item.trigger_confidence || 0,
        reason: item.reason || item.trigger_reason || item.description || '',
        detected_at: item.detected_at || item.triggered_at || item.timestamp,
        timeframe: item.timeframe || item.period || '',
      }))
      setSignals(parsed)
      setError(null)
    } catch (e: any) {
      setError(e.message || '加载失败')
      console.error('[SignalFeed] load:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchSignals() }, [fetchSignals])

  useEffect(() => {
    const iv = setInterval(fetchSignals, 10000)
    return () => clearInterval(iv)
  }, [fetchSignals])

  if (loading) {
    return <div className="card text-center text-terminal-muted py-8">加载信号...</div>
  }

  if (error && signals.length === 0) {
    return (
      <div className="card text-center py-8">
        <p className="text-terminal-muted text-sm">信号数据加载失败</p>
        <p className="text-xs text-terminal-muted mt-1">{error}</p>
        <button onClick={fetchSignals} className="mt-3 text-xs text-terminal-primary active:opacity-70">重试</button>
      </div>
    )
  }

  if (signals.length === 0) {
    return (
      <div className="card text-center text-terminal-muted py-10">
        <p className="text-sm">暂无信号</p>
        <p className="text-xs mt-1">启动全自动交易后自动检测</p>
      </div>
    )
  }

  return (
    <div className="space-y-2 max-h-[500px] overflow-y-auto">
      {signals.map((s, i) => {
        const isBuy = s.direction === 'buy'
        const isSell = s.direction === 'sell'
        const barColor = isBuy ? 'border-l-terminal-profit' : isSell ? 'border-l-terminal-loss' : 'border-l-terminal-border'
        const dotColor = isBuy ? 'bg-terminal-profit' : isSell ? 'bg-terminal-loss' : 'bg-terminal-muted'
        const dirLabel = isBuy ? 'BUY' : isSell ? 'SELL' : 'HOLD'
        const dirColor = isBuy ? 'text-terminal-profit' : isSell ? 'text-terminal-loss' : 'text-terminal-muted'
        const timeStr = s.detected_at
          ? new Date(s.detected_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
          : ''

        return (
          <div key={i} className={`card border-l-4 ${barColor} py-2 px-3`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${dotColor}`} />
                <span className="font-semibold text-sm">{s.symbol}</span>
                <span className={`text-xs px-1.5 py-0.5 rounded font-bold ${dirColor} bg-terminal-card`}>
                  {dirLabel}
                </span>
                <span className="text-xs font-mono text-terminal-muted">
                  {Math.round(s.confidence * 100)}%
                </span>
              </div>
              <span className="text-[10px] text-terminal-muted">{timeStr}</span>
            </div>
            {(s.reason || s.signal_name || s.timeframe) && (
              <div className="mt-1 flex items-center gap-2 text-xs text-terminal-muted">
                {s.signal_name && <span className="text-terminal-primary/70">{s.signal_name}</span>}
                {s.timeframe && <span className="px-1 py-0.5 rounded bg-terminal-card text-[10px]">{s.timeframe}</span>}
                {s.reason && <span className="truncate">{s.reason}</span>}
              </div>
            )}
          </div>
        )
      })}
      <p className="text-center text-[10px] text-terminal-muted py-2">最近 {signals.length} 条信号</p>
    </div>
  )
}
