/**
 * useChartOverlays — 图表覆盖层管理 Hook
 *
 * 管理模式标记、S/R 线、形态标注等图表覆盖元素。
 * 与 TradingViewChart 的核心 series ref 解耦。
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { formatChartTime } from '@/lib/dateTime'
import type { PatternInfo, SRLevel, VolumeAnomalyInfo } from './types'

interface UseChartOverlaysOptions {
  symbol: string
  period: string
  chartRef: React.RefObject<any>
  seriesRef: React.RefObject<any>
  chartData: any[]
  enabled?: boolean
}

interface UseChartOverlaysResult {
  patterns: PatternInfo[]
  /** 模式标记已应用到图表 */
  patternMarkersApplied: boolean
}

// --- Volume anomaly color/label map ---

const VOLUME_ANOMALY_COLORS: Record<string, string> = {
  volume_spike: '#f59e0b',
  climax_volume: '#ef4444',
  volume_dry_up: '#6b7280',
  accumulation: '#22c55e',
  distribution: '#8b5cf6',
}

const ANOMALY_LABELS: Record<string, string> = {
  volume_spike: '量增',
  climax_volume: '极量',
  volume_dry_up: '地量',
  accumulation: '吸筹',
  distribution: '派发',
}

// --- 统一标记管理层（共享 single source of truth for setMarkers） ---

/** Internal shape stored by each overlay source */
interface MarkerSource {
  key: string
  markers: any[]
}

// Module-level ref to collect markers from multiple hooks before unified render
let _markerSources: MarkerSource[] = []
let _pendingRender: ReturnType<typeof setTimeout> | null = null
let _lastSeries: any = null

function _unifiedRenderMarkers() {
  const series = _lastSeries
  if (!series?.setMarkers) return
  try {
    const all = _markerSources.flatMap(s => s.markers)
    series.setMarkers(all)
  } catch {
    // 静默处理 setMarkers 错误（series 可能已销毁）
  }
}

function _registerMarkers(key: string, markers: any[], series: any) {
  const idx = _markerSources.findIndex(s => s.key === key)
  if (markers.length === 0 && idx === -1) return
  if (idx >= 0) {
    _markerSources[idx] = { key, markers }
  } else {
    _markerSources.push({ key, markers })
  }
  _lastSeries = series
  if (_pendingRender) clearTimeout(_pendingRender)
  _pendingRender = setTimeout(_unifiedRenderMarkers, 0)
}

function _unregisterMarkers(key: string) {
  const idx = _markerSources.findIndex(s => s.key === key)
  if (idx >= 0) {
    _markerSources.splice(idx, 1)
  }
  if (_pendingRender) clearTimeout(_pendingRender)
  _pendingRender = setTimeout(_unifiedRenderMarkers, 0)
}

/**
 * Hooks for managing chart pattern detection and display.
 * Fetches candlestick patterns from the API and registers markers.
 */
export function useChartPatterns({
  symbol,
  period,
  chartRef,
  seriesRef,
  chartData,
  enabled = true,
}: UseChartOverlaysOptions): UseChartOverlaysResult {
  const [patterns, setPatterns] = useState<PatternInfo[]>([])
  const [applied, setApplied] = useState(false)
  const MARKER_KEY = 'patterns'

  // 获取形态
  useEffect(() => {
    if (!enabled || !symbol || !period || chartData.length < 5) return
    let cancelled = false
    const fetchPatterns = async () => {
      try {
        const url = `/api/klines/patterns/${symbol}?period=${period}&count=100&min_confidence=0.3`
        const res = await fetch(url)
        if (res.ok && !cancelled) {
          const data = await res.json()
          if (data.patterns) {
            setPatterns(data.patterns)
          }
        }
      } catch {}
    }
    fetchPatterns()
    return () => { cancelled = true }
  }, [symbol, period, chartData.length, enabled])

  // 注册/注销形态标记
  useEffect(() => {
    const markers = patterns
      .filter((p: PatternInfo) => p.confidence >= 0.35)
      .map((p: PatternInfo) => ({
        time: formatChartTime(p.timestamp),
        position: (p.pattern_type === 'bullish' ? 'belowBar' : 'aboveBar') as const,
        color: p.pattern_type === 'bullish' ? '#22c55e' : p.pattern_type === 'bearish' ? '#ef4444' : '#eab308',
        shape: (p.pattern_type === 'bullish' ? 'arrowUp' : p.pattern_type === 'bearish' ? 'arrowDown' : 'circle') as const,
        text: p.name.split(' (')[0],
        size: 2,
      }))

    _registerMarkers(MARKER_KEY, markers, seriesRef.current)
    setApplied(true)

    return () => {
      _unregisterMarkers(MARKER_KEY)
    }
  }, [patterns, seriesRef])

  return { patterns, patternMarkersApplied: applied }
}


/**
 * Hook for S/R level price lines on chart.
 */
export function useChartSRLevels({
  symbol,
  chartRef,
  seriesRef,
  chartData,
  enabled = true,
}: UseChartOverlaysOptions) {
  const levelsRef = useRef<any[]>([])
  const hasChartData = chartData.length > 0

  useEffect(() => {
    if (!enabled || !symbol || !chartRef.current) return

    const fetchSR = async () => {
      try {
        const res = await fetch(`/api/klines/sr-levels/${symbol}?period=1d&count=100`)
        if (!res.ok || !chartRef.current) return
        const data = await res.json()
        const series = seriesRef.current
        if (!series) return

        // 清除旧线
        levelsRef.current.forEach((line: any) => {
          try { line?.series?.removePriceLine?.(line) } catch {}
        })
        levelsRef.current = []

        const latestClose = chartData?.[chartData.length - 1]?.close
        const supports = [...(data.supports || [])]
          .filter((s: SRLevel) => !latestClose || s.price <= latestClose)
          .sort((a: SRLevel, b: SRLevel) => latestClose ? b.price - a.price : b.strength - a.strength)
          .slice(0, 1)
        const resistances = [...(data.resistances || [])]
          .filter((r: SRLevel) => !latestClose || r.price >= latestClose)
          .sort((a: SRLevel, b: SRLevel) => latestClose ? a.price - b.price : b.strength - a.strength)
          .slice(0, 1)

        // 支撑线：只保留最近一条，隐藏价格轴标签，避免遮挡 K 线价格。
        supports.forEach((s: SRLevel) => {
          const line = series.createPriceLine({
            price: s.price,
            color: 'rgba(34, 197, 94, 0.55)',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: false,
            title: '',
          })
          levelsRef.current.push(line)
        })

        // 阻力线：只保留最近一条，隐藏价格轴标签。
        resistances.forEach((r: SRLevel) => {
          const line = series.createPriceLine({
            price: r.price,
            color: 'rgba(239, 68, 68, 0.55)',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: false,
            title: '',
          })
          levelsRef.current.push(line)
        })
      } catch {}
    }

    fetchSR()

    return () => {
      levelsRef.current.forEach((line: any) => {
        try { line?.series?.removePriceLine?.(line) } catch {}
      })
      levelsRef.current = []
    }
  }, [symbol, enabled, hasChartData])
}


/**
 * Hook for volume anomaly markers on chart.
 * Registers markers with the unified marker system to avoid overwriting pattern markers.
 */
export function useVolumeAnomalies({
  symbol,
  period,
  chartRef,
  seriesRef,
  enabled = true,
}: UseChartOverlaysOptions) {
  const [anomalies, setAnomalies] = useState<VolumeAnomalyInfo[]>([])
  const [applied, setApplied] = useState(false)
  const MARKER_KEY = 'volume_anomalies'

  // 获取成交量异动
  useEffect(() => {
    if (!enabled || !symbol || !period) return
    let cancelled = false

    const fetchAnomalies = async () => {
      try {
        const url = `/api/klines/comprehensive/${symbol}?period=${period}&kline_count=100`
        const res = await fetch(url)
        if (res.ok && !cancelled) {
          const data = await res.json()
          if (data.latest_volume_events?.length) {
            setAnomalies(data.latest_volume_events.map((e: any) => ({
              timestamp: e.timestamp,
              type: e.type,
              severity: e.severity,
              description: e.description,
              zscore: e.zscore,
            })))
          }
        }
      } catch {}
    }

    fetchAnomalies()

    return () => { cancelled = true }
  }, [symbol, period, enabled])

  // 注册/注销成交量异动标记
  useEffect(() => {
    const markers = anomalies.map((a: VolumeAnomalyInfo) => {
      const color = VOLUME_ANOMALY_COLORS[a.type] || '#f59e0b'
      const label = ANOMALY_LABELS[a.type] || a.type
      const position = a.severity === 'high' ? 'aboveBar' : 'belowBar' as const

      return {
        time: formatChartTime(a.timestamp),
        position,
        color,
        shape: 'circle' as const,
        text: label,
        size: a.severity === 'high' ? 3 : 2,
      }
    })

    _registerMarkers(MARKER_KEY, markers, seriesRef.current)
    setApplied(true)

    return () => {
      _unregisterMarkers(MARKER_KEY)
    }
  }, [anomalies, seriesRef])

  return { anomalies, volumeAnomaliesApplied: applied }
}
