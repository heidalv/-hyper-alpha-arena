/**
 * useKlinesData — K线数据获取 Hook
 *
 * 从 TradingViewChart 中提取数据获取逻辑：
 * - 初始全量加载 (fetchKlineData)
 * - 5s 增量轮询
 * - WS 推送触发的轻量刷新
 * - 指标缓存管理
 */

import { useState, useRef, useCallback, useEffect } from 'react'
import { formatChartTime } from '@/lib/dateTime'
import { POLLING_INTERVAL } from './chartConfig'
import type { KlineBar } from './types'

interface UseKlinesDataOptions {
  symbol: string
  period: string
  market: string
  selectedIndicators: string[]
  wsRefreshKey?: number
}

interface UseKlinesDataResult {
  chartData: KlineBar[]
  indicatorData: Record<string, any>
  cachedIndicators: string[]
  hasData: boolean
  loading: boolean
  indicatorLoading: boolean
  /** 手动触发全量刷新 */
  refresh: (forceAllIndicators?: boolean) => void
}

export function useKlinesData({
  symbol,
  period,
  market,
  selectedIndicators,
  wsRefreshKey = 0,
}: UseKlinesDataOptions): UseKlinesDataResult {
  const [chartData, setChartData] = useState<KlineBar[]>([])
  const [indicatorData, setIndicatorData] = useState<Record<string, any>>({})
  const [cachedIndicators, setCachedIndicators] = useState<string[]>([])
  const [hasData, setHasData] = useState(false)
  const [loading, setLoading] = useState(false)
  const [indicatorLoading, setIndicatorLoading] = useState(false)

  const refreshIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const selectedIndicatorsRef = useRef(selectedIndicators)
  selectedIndicatorsRef.current = selectedIndicators

  // 全量获取数据
  const fetchFull = useCallback(async (forceAllIndicators = false) => {
    if (!symbol || !period) return

    setLoading(true)
    setIndicatorLoading(true)

    try {
      const indicatorsToFetch = forceAllIndicators
        ? selectedIndicatorsRef.current
        : [...new Set([...selectedIndicatorsRef.current])]
      const indicatorsParam = indicatorsToFetch.length > 0
        ? `&indicators=${indicatorsToFetch.join(',')}`
        : ''
      const url = `/api/market/kline-with-indicators/${symbol}?market=${market}&period=${period}&count=500${indicatorsParam}`

      const res = await fetch(url)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const result = await res.json()

      if (!result.klines || result.klines.length === 0) {
        setHasData(false)
        return
      }

      const bars = result.klines.map((item: any) => ({
        time: formatChartTime(item.timestamp),
        open: item.open || 0,
        high: item.high || 0,
        low: item.low || 0,
        close: item.close || 0,
        volume: item.volume || 0,
      }))

      setChartData(bars)

      if (result.indicators) {
        setIndicatorData(prev => ({ ...prev, ...result.indicators }))
        setCachedIndicators(prev => [...new Set([...prev, ...indicatorsToFetch])])
      }

      setHasData(true)
    } catch (err) {
      console.error('Failed to fetch kline data:', err)
      setHasData(false)
    } finally {
      setLoading(false)
      setIndicatorLoading(false)
    }
  }, [symbol, period, market])

  // 增量轮询
  const poll = useCallback(async () => {
    if (!symbol || !period) return
    try {
      const indicatorsParam = selectedIndicatorsRef.current.length > 0
        ? `&indicators=${selectedIndicatorsRef.current.join(',')}`
        : ''
      const url = `/api/market/kline-with-indicators/${symbol}?market=${market}&period=${period}&count=5${indicatorsParam}`
      const res = await fetch(url)
      if (!res.ok) return
      const result = await res.json()
      if (result.klines && result.klines.length > 0) {
        setChartData(prev => {
          const updated = result.klines.map((item: any) => ({
            time: formatChartTime(item.timestamp),
            open: item.open || 0,
            high: item.high || 0,
            low: item.low || 0,
            close: item.close || 0,
            volume: item.volume || 0,
          }))
          if (prev.length === 0) return updated
          const merged = [...prev]
          for (const bar of updated) {
            const idx = merged.findIndex((b: any) => b.time === bar.time)
            if (idx >= 0) merged[idx] = bar
            else merged.push(bar)
          }
          return merged.sort((a: any, b: any) => (a.time < b.time ? -1 : 1))
        })
        if (result.indicators) {
          setIndicatorData((prev: any) => ({ ...prev, ...result.indicators }))
        }
      }
    } catch {}
  }, [symbol, period, market])

  // symbol/period 变更 → 全量重新获取 + 启动轮询
  useEffect(() => {
    setHasData(false)
    setChartData([])
    setIndicatorData({})
    setCachedIndicators([])

    fetchFull(true)

    if (refreshIntervalRef.current) clearInterval(refreshIntervalRef.current)
    refreshIntervalRef.current = setInterval(poll, POLLING_INTERVAL)

    return () => {
      if (refreshIntervalRef.current) {
        clearInterval(refreshIntervalRef.current)
        refreshIntervalRef.current = null
      }
    }
  }, [symbol, period])

  // WS 推送 → 轻量刷新
  useEffect(() => {
    if (wsRefreshKey > 0) {
      poll()
    }
  }, [wsRefreshKey])

  // 指标变化 → 检查缺失
  useEffect(() => {
    if (symbol && period && selectedIndicators.length > 0) {
      const missing = selectedIndicators.filter(
        ind => !cachedIndicators.includes(ind) || !indicatorData[ind]
      )
      if (missing.length > 0) {
        fetchFull(false)
      }
    }
  }, [selectedIndicators])

  return {
    chartData,
    indicatorData,
    cachedIndicators,
    hasData,
    loading,
    indicatorLoading,
    refresh: fetchFull,
  }
}
