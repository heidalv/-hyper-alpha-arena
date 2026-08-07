import React, { useEffect, useRef } from 'react'
import { createChart, ColorType, CandlestickSeries, IChartApi, ISeriesApi, CandlestickData, Time } from 'lightweight-charts'
import type { KlineData } from '@/hooks/useKlines'

interface KlineMiniChartProps {
  data: KlineData[]
  loading?: boolean
  symbol: string
  height?: number
  period: string
  onPeriodChange: (period: string) => void
}

const PERIODS = ['1m', '5m', '15m', '1h', '4h', '1d']

export default function KlineMiniChart({ data, loading, symbol, height = 200, period, onPeriodChange }: KlineMiniChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: '#121214' },
        textColor: '#6b7280',
      },
      grid: {
        vertLines: { color: '#1a1a1f' },
        horzLines: { color: '#1a1a1f' },
      },
      crosshair: {
        mode: 0,
      },
      rightPriceScale: {
        borderColor: '#2a2a30',
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: '#2a2a30',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: number) => {
          const d = new Date(time * 1000)
          if (period === '1d') return `${d.getMonth() + 1}/${d.getDate()}`
          return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
        },
      },
      handleScroll: false,
      handleScale: false,
    })

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    })

    chartRef.current = chart
    seriesRef.current = series

    const handleResize = () => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth })
      }
    }

    // Use ResizeObserver for more reliable resize detection
    let ro: ResizeObserver | null = null
    if (containerRef.current) {
      ro = new ResizeObserver(handleResize)
      ro.observe(containerRef.current)
    }

    return () => {
      ro?.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [height, period])

  useEffect(() => {
    if (seriesRef.current && data.length > 0) {
      const candleData: CandlestickData[] = data.map(d => ({
        time: d.time as Time,
        open: d.open,
        high: d.high,
        low: d.low,
        close: d.close,
      }))
      seriesRef.current.setData(candleData)
    }
  }, [data])

  return (
    <div>
      {/* Symbol + Period selector */}
      <div className="flex items-center justify-between px-1 mb-2">
        <span className="text-sm font-semibold text-terminal-text">{symbol}</span>
        <div className="flex gap-1">
          {PERIODS.map(p => (
            <button
              key={p}
              onClick={() => onPeriodChange(p)}
              className={`px-2 py-0.5 rounded text-xs font-medium ${
                period === p ? 'bg-terminal-primary text-white' : 'text-terminal-muted bg-terminal-card'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* Chart */}
      <div className="relative rounded overflow-hidden bg-terminal-bg border border-terminal-border">
        {loading && data.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-terminal-bg/80 z-10">
            <span className="text-xs text-terminal-muted">加载K线...</span>
          </div>
        )}
        <div ref={containerRef} style={{ width: '100%', height }} />
      </div>
    </div>
  )
}
