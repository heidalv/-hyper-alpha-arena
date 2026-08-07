import { useState, useEffect, useCallback, useRef } from 'react'
import { apiRequest } from '@/api/client'

export interface KlineData {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

function parseItems(items: any[]): KlineData[] {
  return (Array.isArray(items) ? items : []).map((item: any) => ({
    time: item.timestamp || 0,
    open: parseFloat(item.open) || 0,
    high: parseFloat(item.high) || 0,
    low: parseFloat(item.low) || 0,
    close: parseFloat(item.close) || 0,
    volume: parseFloat(item.volume || 0),
  })).sort((a: KlineData, b: KlineData) => a.time - b.time)
}

export function useKlines(symbol: string, period: string = '1h', count: number = 200) {
  const [data, setData] = useState<KlineData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const mountedRef = useRef(true)

  // Full fetch (initial or period change)
  const fetchFull = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    setError(null)
    try {
      const raw = await apiRequest<any>(`/market/kline/${symbol}?period=${period}&count=${count}`)
      const items = raw?.data || raw || []
      const parsed = parseItems(items)
      if (mountedRef.current) {
        setData(parsed)
        setLastUpdated(new Date())
      }
    } catch (e: any) {
      if (mountedRef.current) {
        setError(e.message || '加载失败')
        console.error(`[useKlines] ${symbol} ${period}:`, e)
      }
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [symbol, period, count])

  // Delta refresh: only fetch latest 2 candles to merge
  const refreshDelta = useCallback(async () => {
    if (!symbol) return
    try {
      const raw = await apiRequest<any>(`/market/kline/${symbol}?period=${period}&count=2`)
      const items = raw?.data || raw || []
      const latest = parseItems(items)
      if (latest.length === 0) return
      setData(prev => {
        const map = new Map(prev.map(d => [d.time, d]))
        for (const d of latest) map.set(d.time, d)
        const merged = Array.from(map.values()).sort((a, b) => a.time - b.time)
        return merged.slice(-count)
      })
      if (mountedRef.current) setLastUpdated(new Date())
    } catch (e: any) {
      console.error('[useKlines delta] error:', e.message || e)
    }
  }, [symbol, period, count])

  // Initial + period change full fetch
  useEffect(() => { fetchFull() }, [fetchFull])

  // Fast delta refresh every 3s
  useEffect(() => {
    if (!symbol) return
    const iv = setInterval(refreshDelta, 3000)
    return () => clearInterval(iv)
  }, [refreshDelta, symbol])

  // Cleanup
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  return { data, loading, error, lastUpdated, refetch: fetchFull }
}
