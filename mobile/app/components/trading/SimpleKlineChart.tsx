import React, { useRef, useEffect } from 'react'
import type { KlineData } from '@/hooks/useKlines'

interface SimpleKlineChartProps {
  data: KlineData[]
  loading?: boolean
  symbol: string
  period: string
  onPeriodChange: (period: string) => void
  height?: number
}

const PERIODS = ['1m', '5m', '15m', '1h', '4h', '1d']

function drawChart(canvas: HTMLCanvasElement, data: KlineData[], h: number) {
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const rect = canvas.getBoundingClientRect()
  const w = rect.width * dpr
  const chartH = (h || rect.height) * dpr
  canvas.width = w
  canvas.height = chartH
  ctx.scale(dpr, dpr)

  const pad = { top: 8, right: 8, bottom: 18, left: 8 }
  const cw = rect.width - pad.left - pad.right
  const ch = h - pad.top - pad.bottom

  if (!data.length) return

  // Price range
  const prices = data.flatMap(d => [d.high, d.low])
  const minP = Math.min(...prices)
  const maxP = Math.max(...prices)
  const range = maxP - minP || 1
  const scaleY = (v: number) => pad.top + ch * (1 - (v - minP) / range)

  // Candle dimensions
  const count = Math.min(data.length, 200)
  const slice = data.slice(-count)
  const candleW = Math.max(1.5, Math.min(6, cw / count - 1))
  const gap = Math.max(0.5, (cw - candleW * count) / Math.max(count - 1, 1))

  // Clear
  ctx.clearRect(0, 0, rect.width, h)

  // Grid lines
  ctx.strokeStyle = '#1a1a2e'
  ctx.lineWidth = 0.5
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (ch * i) / 4
    ctx.beginPath()
    ctx.moveTo(pad.left, y)
    ctx.lineTo(rect.width - pad.right, y)
    ctx.stroke()

    // Price label
    const price = maxP - (range * i) / 4
    ctx.fillStyle = '#4b5563'
    ctx.font = '8px monospace'
    ctx.textAlign = 'left'
    const label = price < 1 ? price.toPrecision(3) : price >= 1000 ? (price / 1000).toFixed(1) + 'k' : price.toFixed(0)
    ctx.fillText(label, pad.left, y - 2)
  }

  // Candles
  for (let i = 0; i < slice.length; i++) {
    const d = slice[i]
    const x = pad.left + i * (candleW + gap)
    const isUp = d.close >= d.open
    const color = isUp ? '#10b981' : '#ef4444'

    // Wick
    const wickX = x + candleW / 2
    ctx.strokeStyle = color
    ctx.lineWidth = 0.8
    ctx.beginPath()
    ctx.moveTo(wickX, scaleY(d.high))
    ctx.lineTo(wickX, scaleY(d.low))
    ctx.stroke()

    // Body
    const bodyTop = scaleY(Math.max(d.open, d.close))
    const bodyH = Math.max(1, Math.abs(scaleY(d.open) - scaleY(d.close)))
    ctx.fillStyle = color
    ctx.fillRect(x, bodyTop, candleW, bodyH)
  }
}

export default function SimpleKlineChart({ data, loading, symbol, period, onPeriodChange, height = 160 }: SimpleKlineChartProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  // Last price
  const lastPrice = data.length > 0 ? data[data.length - 1].close : null
  const prevPrice = data.length > 1 ? data[data.length - 2].close : null
  const priceChange = lastPrice && prevPrice ? ((lastPrice - prevPrice) / prevPrice * 100) : 0

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    drawChart(canvas, data, height)

    const onResize = () => drawChart(canvas, data, height)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [data, height])

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">{symbol}</span>
          {lastPrice && (
            <span className={`text-sm font-mono font-bold ${priceChange >= 0 ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
              ${lastPrice < 1 ? lastPrice.toPrecision(4) : lastPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          )}
          {data.length > 1 && (
            <span className={`text-xs font-mono ${priceChange >= 0 ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
              {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(2)}%
            </span>
          )}
        </div>
        <div className="flex gap-0.5">
          {PERIODS.map(p => (
            <button
              key={p}
              onClick={() => onPeriodChange(p)}
              className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                period === p ? 'bg-terminal-primary text-white' : 'text-terminal-muted bg-terminal-card'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* Chart area */}
      <div className="relative rounded bg-terminal-bg border border-terminal-border overflow-hidden" style={{ height }}>
        {loading && data.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-terminal-bg/80 z-10">
            <span className="text-xs text-terminal-muted">加载K线...</span>
          </div>
        )}
        <canvas
          ref={canvasRef}
          className="w-full"
          style={{ height, display: 'block' }}
        />
      </div>
    </div>
  )
}
