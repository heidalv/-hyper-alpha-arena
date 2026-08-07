import React, { useEffect, useRef } from 'react'
import { createChart, ColorType, LineStyle, AreaSeries } from 'lightweight-charts'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'
import type { EquityPoint } from '../../api/types'

interface EquityChartProps {
  data: EquityPoint[]
  height?: number
}

export const EquityChart: React.FC<EquityChartProps> = ({ data, height = 200 }) => {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Area'> | null>(null)

  useEffect(() => {
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#6b7280',
        fontSize: 12
      },
      grid: {
        vertLines: { color: 'rgba(31, 41, 55, 0.5)', style: LineStyle.Dotted },
        horzLines: { color: 'rgba(31, 41, 55, 0.5)', style: LineStyle.Dotted }
      },
      rightPriceScale: {
        borderColor: '#1f2937',
        scaleMargins: { top: 0.1, bottom: 0.1 }
      },
      timeScale: {
        borderColor: '#1f2937',
        timeVisible: true,
        secondsVisible: false
      },
      crosshair: {
        vertLine: { color: 'rgba(59, 130, 246, 0.3)', style: LineStyle.Dashed },
        horzLine: { color: 'rgba(59, 130, 246, 0.3)', style: LineStyle.Dashed }
      }
    })

    // lightweight-charts v5: addSeries(SeriesDefinition, options)
    const areaSeries = chart.addSeries(AreaSeries, {
      topColor: 'rgba(0, 220, 130, 0.3)',
      bottomColor: 'rgba(0, 220, 130, 0.02)',
      lineColor: '#00dc82',
      lineWidth: 2,
      priceLineVisible: false
    })

    // Transform data
    const chartData = data.map(d => ({
      time: d.time as any,
      value: d.value
    }))

    areaSeries.setData(chartData)
    chart.timeScale().fitContent()

    chartRef.current = chart
    seriesRef.current = areaSeries

    // Handle resize
    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth })
      }
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [height])

  // Update data when it changes
  useEffect(() => {
    if (!seriesRef.current || data.length === 0) return
    const chartData = data.map(d => ({
      time: d.time as any,
      value: d.value
    }))
    seriesRef.current.setData(chartData)
  }, [data])

  return (
    <div className="mx-4 mt-3 bg-surface rounded-card border border-border p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-muted">权益曲线</span>
        <span className="text-xs text-muted">24h</span>
      </div>
      <div ref={chartContainerRef} />
    </div>
  )
}

export default EquityChart

