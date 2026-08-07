import React, { useRef, useEffect, useState, useCallback } from 'react'
import type { KlineData } from '@/hooks/useKlines'
import {
  calcMACD, calcKDJ, calcMALines, calcBOLL, calcRSI, calcATR, calcOBV,
  type MACDResult, type KDJResult, type BOLLResult, type MALines
} from '@/utils/indicators'

// ── Types ──
interface Props {
  data: KlineData[]
  loading?: boolean
  symbol: string
  period: string
  onPeriodChange: (p: string) => void
  lastUpdated?: Date | null
  onRefresh?: () => void
}

type CandleSlice = KlineData[]

interface TrendingObject {
  type: 'trend' | 'horiz' | 'vert' | 'ray' | 'rect' | 'fib'
  points: { x: number; y: number }[]
  label?: string
}

type DrawTool = 'trend' | 'horiz' | 'vert' | 'ray' | 'rect' | 'fib' | null

interface SubPanel {
  id: string
  label: string
  height: number
  visible: boolean
  color: string
}

// ── Constants ──
const PERIODS = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M']

// ── Color schemes ──
const DARK_COLORS = {
  up: '#10b981', down: '#ef4444', grid: '#1a1a2e', gridMinor: '#0f0f23',
  text: '#4b5563', textBright: '#6b7280',
  bg: '#06060f',
  ma5: '#f59e0b', ma10: '#3b82f6', ma20: '#a855f7', ma60: '#ec4899',
  ema12: '#06b6d4', ema26: '#84cc16',
  dif: '#f59e0b', dea: '#3b82f6', macdUp: '#ef4444', macdDown: '#10b981',
  k: '#f59e0b', d: '#3b82f6', j: '#a855f7',
  rsi: '#c084fc', rsiOverbought: '#ef4444', rsiOversold: '#10b981',
  bollUpper: '#ef444480', bollMid: '#f59e0b', bollLower: '#10b98180',
  atr: '#fb923c',
  obv: '#a78bfa',
  vol: '#3b82f640',
  draw: '#f59e0b', drawHover: '#fbbf24',
  crosshair: '#6b7280',
}

const LIGHT_COLORS = {
  up: '#059669', down: '#dc2626', grid: '#e2e8f0', gridMinor: '#f1f5f9',
  text: '#94a3b8', textBright: '#64748b',
  bg: '#ffffff',
  ma5: '#d97706', ma10: '#2563eb', ma20: '#9333ea', ma60: '#db2777',
  ema12: '#0891b2', ema26: '#65a30d',
  dif: '#d97706', dea: '#2563eb', macdUp: '#dc2626', macdDown: '#059669',
  k: '#d97706', d: '#2563eb', j: '#9333ea',
  rsi: '#7c3aed', rsiOverbought: '#dc2626', rsiOversold: '#059669',
  bollUpper: '#dc262640', bollMid: '#d97706', bollLower: '#05966940',
  atr: '#ea580c',
  obv: '#7c3aed',
  vol: '#2563eb30',
  draw: '#d97706', drawHover: '#f59e0b',
  crosshair: '#94a3b8',
}

function getColors() {
  if (typeof document === 'undefined') return DARK_COLORS
  return document.documentElement.classList.contains('light') ? LIGHT_COLORS : DARK_COLORS
}

const MAIN_H = 200
const DEFAULT_SUB_H = 80
const GAP = 2
const MAX_SUB_PANELS = 3
const DEFAULT_VISIBLE = 80
const MIN_VISIBLE = 10
const MAX_VISIBLE = 400

// Fib retracement levels
const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]

export default function TradingKlineChart({ data, loading, symbol, period, onPeriodChange, lastUpdated, onRefresh }: Props) {
  const COLORS = getColors()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const mainRef = useRef<HTMLDivElement>(null)

  // ── Drawing ──
  const [drawTool, setDrawTool] = useState<DrawTool>(null)
  const [trendingObjects, setTrendingObjects] = useState<TrendingObject[]>([])
  const [drawStart, setDrawStart] = useState<{ x: number; y: number; chartY: number } | null>(null)
  const [drawEnd, setDrawEnd] = useState<{ x: number; y: number } | null>(null)
  const [crosshair, setCrosshair] = useState<{ x: number; y: number; price: number; idx: number } | null>(null)

  // ── Panning + Zoom ──
  const [panOffset, setPanOffset] = useState(0)
  const maxPan = useRef(0)
  const [visibleCandles, setVisibleCandles] = useState(DEFAULT_VISIBLE)
  // Refs for latest values in touch handlers — avoids closure traps on panOffset/visibleCandles re-render
  const panOffsetRef = useRef(panOffset)
  panOffsetRef.current = panOffset
  const visibleCandlesRef = useRef(visibleCandles)
  visibleCandlesRef.current = visibleCandles
  const panStateRef = useRef({ dragging: false, lastX: 0, offset: 0, totalOffset: 0 })
  const pinchInitRef = useRef<{ initDist: number; initVisible: number; initPan: number } | null>(null)
  const longPressIdRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Sub-panels (up to 3, default MACD + KDJ + VOL) ──
  const [subPanels, setSubPanels] = useState<SubPanel[]>(() => {
    const c = getColors()
    return [
      { id: 'macd', label: 'MACD', height: DEFAULT_SUB_H, visible: true, color: c.dif },
      { id: 'kdj', label: 'KDJ', height: DEFAULT_SUB_H, visible: true, color: c.k },
      { id: 'vol', label: 'VOL', height: DEFAULT_SUB_H, visible: true, color: c.vol },
    ]
  })

  // ── Overlays toggle ──
  const [showMA5, setShowMA5] = useState(true)
  const [showMA10, setShowMA10] = useState(true)
  const [showMA20, setShowMA20] = useState(true)
  const [showMA60, setShowMA60] = useState(false)
  const [showEMA12, setShowEMA12] = useState(false)
  const [showEMA26, setShowEMA26] = useState(false)
  const [showBOLL, setShowBOLL] = useState(false)

  // ── Period row scroll ──
  const periodRowRef = useRef<HTMLDivElement>(null)

  // ── Pre-compute indicators ──
  const closes = data.map(d => d.close)
  const highs = data.map(d => d.high)
  const lows = data.map(d => d.low)
  const volumes = data.map(d => d.volume)
  const macd = calcMACD(closes)
  const kdj = calcKDJ(highs, lows, closes, 9)
  const ma = calcMALines(closes)
  const boll = calcBOLL(closes, 20, 2)
  const rsi = calcRSI(closes, 14)
  const atr = calcATR(highs, lows, closes, 14)
  const obv = calcOBV(closes, volumes)

  // Price info
  const lastPrice = data.length > 0 ? data[data.length - 1].close : null
  const prevPrice = data.length > 1 ? data[data.length - 2].close : null
  const priceChange = lastPrice && prevPrice ? ((lastPrice - prevPrice) / prevPrice * 100) : 0

  // ── Helper: draw line from array ──
  function drawLineArray(
    ctx: CanvasRenderingContext2D,
    values: number[], startIdx: number, sliceLen: number,
    candleW: number, gap: number, padLeft: number,
    scaleY: (v: number) => number, color: string, width: number
  ) {
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.beginPath()
    let started = false
    for (let i = 0; i < sliceLen; i++) {
      const vi = startIdx + i
      if (vi >= values.length || isNaN(values[vi])) continue
      const x = padLeft + i * (candleW + gap) + candleW / 2
      const y = scaleY(values[vi])
      if (!started) { ctx.moveTo(x, y); started = true }
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
  }

  // ── Helper: draw band between two arrays ──
  function drawBand(
    ctx: CanvasRenderingContext2D,
    upper: number[], lower: number[], startIdx: number, sliceLen: number,
    candleW: number, gap: number, padLeft: number,
    scaleY: (v: number) => number, fillColor: string
  ) {
    ctx.fillStyle = fillColor; ctx.beginPath()
    let started = false
    for (let i = 0; i < sliceLen; i++) {
      const vi = startIdx + i
      if (vi >= upper.length || isNaN(upper[vi]) || isNaN(lower[vi])) continue
      const x = padLeft + i * (candleW + gap) + candleW / 2
      const yU = scaleY(upper[vi])
      if (!started) { ctx.moveTo(x, yU); started = true }
      else ctx.lineTo(x, yU)
    }
    for (let i = sliceLen - 1; i >= 0; i--) {
      const vi = startIdx + i
      if (vi >= lower.length || isNaN(lower[vi])) continue
      const x = padLeft + i * (candleW + gap) + candleW / 2
      ctx.lineTo(x, scaleY(lower[vi]))
    }
    ctx.closePath(); ctx.fill()
  }

  // ── Helper: price label ──
  function formatPrice(v: number): string {
    if (v >= 10000) return (v / 1000).toFixed(1) + 'k'
    if (v >= 1000) return v.toFixed(0)
    if (v >= 1) return v.toFixed(2)
    return v.toPrecision(3)
  }

  // ── rAF throttle ref to avoid excessive redraws ──
  const _rafRef = useRef<number | null>(null)

  // Cleanup rAF on unmount
  useEffect(() => {
    return () => {
      if (_rafRef.current !== null) {
        cancelAnimationFrame(_rafRef.current)
        _rafRef.current = null
      }
    }
  }, [])

  // ── Main render ──
  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || !data.length) return

    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const rect = canvas.getBoundingClientRect()
    const cw = rect.width
    const w = cw * dpr

    // Calculate total height
    const visibleSubPanels = subPanels.filter(p => p.visible)
    const totalH = MAIN_H + visibleSubPanels.reduce((sum, p) => sum + p.height + GAP, 0)
    const pH = totalH * dpr

    canvas.width = w
    canvas.height = pH
    const ctx = canvas.getContext('2d')!
    ctx.scale(dpr, dpr)

    // ── Candle dimensions ──
    const visible = Math.min(data.length, visibleCandles)
    const startIdx = Math.max(0, data.length - visible - panOffset)
    const slice = data.slice(startIdx, startIdx + visible)
    const candleW = Math.max(1.5, Math.min(6, (cw - 16) / visible - 0.5))
    const gap = Math.max(0.3, (cw - 16 - candleW * visible) / Math.max(visible - 1, 1))
    maxPan.current = Math.max(0, data.length - visible)
    const padL = 8, padR = 4

    // ── Draw grid helper ──
    function drawGrid(panelY: number, panelH: number, padT: number, padB: number, gridCount: number) {
      const ch = panelH - padT - padB
      ctx.strokeStyle = COLORS.grid
      ctx.lineWidth = 0.3
      for (let i = 0; i <= gridCount; i++) {
        const y = panelY + padT + (ch * i) / gridCount
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(cw - padR, y); ctx.stroke()
      }
      // Minor grid
      ctx.strokeStyle = COLORS.gridMinor
      ctx.lineWidth = 0.2
      for (let i = 0; i < gridCount; i++) {
        const y = panelY + padT + (ch * (i + 0.5)) / gridCount
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(cw - padR, y); ctx.stroke()
      }
    }

    function drawPriceLabels(panelY: number, panelH: number, padT: number, padB: number, gridCount: number, minVal: number, maxVal: number, range: number, decimals: number) {
      const ch = panelH - padT - padB
      ctx.fillStyle = COLORS.text; ctx.font = '8px monospace'; ctx.textAlign = 'left'
      for (let i = 0; i <= gridCount; i++) {
        const y = panelY + padT + (ch * i) / gridCount
        const val = maxVal - (range * i) / gridCount
        ctx.fillText(val.toFixed(decimals), padL, y - 2)
      }
    }

    // ═══════════════════════════════════════
    // MAIN PANEL: Candles + MA/EMA/BOLL
    // ═══════════════════════════════════════
    const mainPrices = slice.flatMap(d => [d.high, d.low])
    const mainMin = Math.min(...mainPrices)
    const mainMax = Math.max(...mainPrices)
    const mainRange = mainMax - mainMin || 1
    const mainPadT = 6, mainPadB = 14
    const mainCh = MAIN_H - mainPadT - mainPadB
    const mainScale = (v: number) => mainPadT + mainCh * (1 - (v - mainMin) / mainRange)

    drawGrid(0, MAIN_H, mainPadT, mainPadB, 4)

    // Price labels
    ctx.fillStyle = COLORS.text; ctx.font = '8px monospace'; ctx.textAlign = 'left'
    for (let i = 0; i <= 4; i++) {
      const y = mainPadT + (mainCh * i) / 4
      const val = mainMax - (mainRange * i) / 4
      ctx.fillText(formatPrice(val), padL, y - 2)
    }

    // Time labels
    ctx.fillStyle = COLORS.text; ctx.font = '7px monospace'; ctx.textAlign = 'center'
    for (let i = 0; i < slice.length; i += Math.ceil(slice.length / 6)) {
      const x = padL + i * (candleW + gap) + candleW / 2
      const d = new Date(slice[i].time * 1000)
      const label = period === '1d' || period === '3d' || period === '1w' || period === '1M'
        ? `${d.getMonth() + 1}/${d.getDate()}`
        : `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
      ctx.fillText(label, x, MAIN_H - 2)
    }

    // BOLL band
    if (showBOLL) {
      drawBand(ctx, boll.upper, boll.lower, startIdx, slice.length, candleW, gap, padL, mainScale, 'rgba(248,113,113,0.06)')
      drawLineArray(ctx, boll.mid, startIdx, slice.length, candleW, gap, padL, mainScale, COLORS.bollMid, 0.8)
      drawLineArray(ctx, boll.upper, startIdx, slice.length, candleW, gap, padL, mainScale, COLORS.bollUpper, 0.5)
      drawLineArray(ctx, boll.lower, startIdx, slice.length, candleW, gap, padL, mainScale, COLORS.bollLower, 0.5)
    }

    // MA lines
    if (showMA5) drawLineArray(ctx, ma.ma5, startIdx, slice.length, candleW, gap, padL, mainScale, COLORS.ma5, 0.8)
    if (showMA10) drawLineArray(ctx, ma.ma10, startIdx, slice.length, candleW, gap, padL, mainScale, COLORS.ma10, 0.8)
    if (showMA20) drawLineArray(ctx, ma.ma20, startIdx, slice.length, candleW, gap, padL, mainScale, COLORS.ma20, 0.8)
    if (showMA60) drawLineArray(ctx, ma.ma60, startIdx, slice.length, candleW, gap, padL, mainScale, COLORS.ma60, 0.8)
    if (showEMA12) drawLineArray(ctx, ma.ema12, startIdx, slice.length, candleW, gap, padL, mainScale, COLORS.ema12, 0.6)
    if (showEMA26) drawLineArray(ctx, ma.ema26, startIdx, slice.length, candleW, gap, padL, mainScale, COLORS.ema26, 0.6)

    // Candles
    for (let i = 0; i < slice.length; i++) {
      const d = slice[i]
      const x = padL + i * (candleW + gap)
      const isUp = d.close >= d.open
      const color = isUp ? COLORS.up : COLORS.down

      // Volume bar at bottom (small)
      const maxVol = Math.max(...volumes.slice(startIdx, startIdx + visible), 1)
      const volH = Math.max(1, (d.volume / maxVol) * 20)
      ctx.fillStyle = `${color}30`
      ctx.fillRect(x, MAIN_H - mainPadB - volH, candleW, volH)

      // Wick
      ctx.strokeStyle = color; ctx.lineWidth = 1
      const cx = x + candleW / 2
      ctx.beginPath(); ctx.moveTo(cx, mainScale(d.high)); ctx.lineTo(cx, mainScale(d.low)); ctx.stroke()
      // Body
      const bt = mainScale(Math.max(d.open, d.close))
      const bh = Math.max(1, Math.abs(mainScale(d.open) - mainScale(d.close)))
      ctx.fillStyle = color
      ctx.fillRect(x, bt, candleW, bh)
    }

    // ═══════════════════════════════════════
    // SUB-PANELS
    // ═══════════════════════════════════════
    let subY = MAIN_H
    for (const panel of visibleSubPanels) {
      subY += GAP
      const ph = panel.height
      const pPadT = 4, pPadB = 11
      const pch = ph - pPadT - pPadB

      drawGrid(subY, ph, pPadT, pPadB, 3)

      switch (panel.id) {
        // ── MACD ──
        case 'macd': {
          const vals = [...macd.dif.slice(startIdx, startIdx + visible), ...macd.dea.slice(startIdx, startIdx + visible), ...macd.histogram.slice(startIdx, startIdx + visible)].filter(v => !isNaN(v))
          const minV = Math.min(...vals, 0), maxV = Math.max(...vals, 0)
          const rangeV = maxV - minV || 1
          const sc = (v: number) => subY + pPadT + pch * (1 - (v - minV) / rangeV)

          drawPriceLabels(subY, ph, pPadT, pPadB, 3, minV, maxV, rangeV, 2)
          // Zero line
          const zy = sc(0)
          ctx.strokeStyle = '#334155'; ctx.lineWidth = 0.3
          ctx.beginPath(); ctx.moveTo(padL, zy); ctx.lineTo(cw - padR, zy); ctx.stroke()

          drawLineArray(ctx, macd.dif, startIdx, slice.length, candleW, gap, padL, sc, COLORS.dif, 0.8)
          drawLineArray(ctx, macd.dea, startIdx, slice.length, candleW, gap, padL, sc, COLORS.dea, 0.8)

          // Histogram
          for (let i = 0; i < slice.length; i++) {
            const vi = startIdx + i
            if (vi >= macd.histogram.length || isNaN(macd.histogram[vi])) continue
            const x = padL + i * (candleW + gap)
            const val = macd.histogram[vi]
            const hTop = sc(Math.max(0, val)), hBot = sc(Math.min(0, val))
            const hH = Math.max(1, Math.abs(hBot - hTop))
            ctx.fillStyle = val >= 0 ? COLORS.macdDown : COLORS.macdUp
            ctx.fillRect(x, hTop, candleW * 0.8, hH)
          }

          ctx.fillStyle = COLORS.textBright; ctx.font = '8px monospace'; ctx.textAlign = 'left'
          ctx.fillText('MACD(12,26,9)', padL, subY + ph - 1)
          break
        }

        // ── KDJ ──
        case 'kdj': {
          const kv = [...kdj.k.slice(startIdx, startIdx + visible), ...kdj.d.slice(startIdx, startIdx + visible), ...kdj.j.slice(startIdx, startIdx + visible)].filter(v => !isNaN(v))
          const minK = Math.min(...kv, 0), maxK = Math.max(...kv, 100)
          const kr = maxK - minK || 1
          const sc = (v: number) => subY + pPadT + pch * (1 - (v - minK) / kr)

          drawPriceLabels(subY, ph, pPadT, pPadB, 3, minK, maxK, kr, 0)

          // Reference lines at 20, 50, 80
          ;[20, 50, 80].forEach(lvl => {
            const ly = sc(lvl)
            ctx.strokeStyle = lvl === 50 ? '#334155' : '#1a1a2e'; ctx.lineWidth = 0.3
            ctx.beginPath(); ctx.moveTo(padL, ly); ctx.lineTo(cw - padR, ly); ctx.stroke()
          })

          drawLineArray(ctx, kdj.k, startIdx, slice.length, candleW, gap, padL, sc, COLORS.k, 0.8)
          drawLineArray(ctx, kdj.d, startIdx, slice.length, candleW, gap, padL, sc, COLORS.d, 0.8)
          drawLineArray(ctx, kdj.j, startIdx, slice.length, candleW, gap, padL, sc, COLORS.j, 0.8)

          ctx.fillStyle = COLORS.textBright; ctx.font = '8px monospace'; ctx.textAlign = 'left'
          ctx.fillText('KDJ(9,3,3)', padL, subY + ph - 1)
          break
        }

        // ── RSI ──
        case 'rsi': {
          const sc = (v: number) => subY + pPadT + pch * (1 - v / 100)
          drawPriceLabels(subY, ph, pPadT, pPadB, 3, 0, 100, 100, 0)

          // Overbought/Oversold lines at 70/30
          ;[30, 50, 70].forEach(lvl => {
            const ly = sc(lvl)
            ctx.strokeStyle = lvl === 50 ? '#334155' : (lvl === 70 ? '#ef444430' : '#10b98130'); ctx.lineWidth = 0.3
            ctx.beginPath(); ctx.moveTo(padL, ly); ctx.lineTo(cw - padR, ly); ctx.stroke()
          })

          drawLineArray(ctx, rsi, startIdx, slice.length, candleW, gap, padL, sc, COLORS.rsi, 1)

          ctx.fillStyle = COLORS.textBright; ctx.font = '8px monospace'; ctx.textAlign = 'left'
          ctx.fillText('RSI(14)', padL, subY + ph - 1)
          break
        }

        // ── ATR ──
        case 'atr': {
          const aSlice = atr.slice(startIdx, startIdx + visible).filter(v => !isNaN(v))
          const minA = Math.min(...aSlice, 0), maxA = Math.max(...aSlice, 0.01)
          const ar = maxA - minA || 1
          const sc = (v: number) => subY + pPadT + pch * (1 - (v - minA) / ar)

          drawPriceLabels(subY, ph, pPadT, pPadB, 3, minA, maxA, ar, 2)
          drawLineArray(ctx, atr, startIdx, slice.length, candleW, gap, padL, sc, COLORS.atr, 0.8)

          ctx.fillStyle = COLORS.textBright; ctx.font = '8px monospace'; ctx.textAlign = 'left'
          ctx.fillText('ATR(14)', padL, subY + ph - 1)
          break
        }

        // ── OBV ──
        case 'obv': {
          const oSlice = obv.slice(startIdx, startIdx + visible).filter(v => !isNaN(v))
          const minO = Math.min(...oSlice), maxO = Math.max(...oSlice)
          const or_ = maxO - minO || 1
          const sc = (v: number) => subY + pPadT + pch * (1 - (v - minO) / or_)

          drawPriceLabels(subY, ph, pPadT, pPadB, 3, minO, maxO, or_, 0)
          drawLineArray(ctx, obv, startIdx, slice.length, candleW, gap, padL, sc, COLORS.obv, 0.8)

          ctx.fillStyle = COLORS.textBright; ctx.font = '8px monospace'; ctx.textAlign = 'left'
          ctx.fillText('OBV', padL, subY + ph - 1)
          break
        }

        // ── VOL ──
        case 'vol': {
          const vSlice = volumes.slice(startIdx, startIdx + visible)
          const maxV = Math.max(...vSlice, 1)
          const sc = (v: number) => subY + pPadT + pch * (1 - v / maxV)

          ctx.fillStyle = COLORS.text; ctx.font = '8px monospace'; ctx.textAlign = 'left'
          ctx.fillText(maxV.toFixed(0), padL, subY + pPadT + 6)

          for (let i = 0; i < slice.length; i++) {
            const vi = startIdx + i
            const x = padL + i * (candleW + gap)
            const isUp = slice[i].close >= slice[i].open
            const v = volumes[vi] || 0
            const barH = Math.max(1, sc(v) - (subY + ph - pPadB))
            ctx.fillStyle = isUp ? `${COLORS.up}40` : `${COLORS.down}40`
            ctx.fillRect(x, subY + ph - pPadB - barH, candleW, barH)
          }

          ctx.fillStyle = COLORS.textBright; ctx.font = '8px monospace'; ctx.textAlign = 'left'
          ctx.fillText('VOL', padL, subY + ph - 1)
          break
        }
      }

      subY += ph
    }

    // ═══════════════════════════════════════
    // DRAW OBJECTS
    // ═══════════════════════════════════════
    for (const obj of trendingObjects) {
      ctx.strokeStyle = COLORS.draw; ctx.lineWidth = 1.2
      ctx.fillStyle = COLORS.draw
      switch (obj.type) {
        case 'trend': {
          const [p1, p2] = obj.points
          if (!p1 || !p2) break
          ctx.setLineDash([])
          ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); ctx.stroke()
          break
        }
        case 'horiz': {
          const [p1, p2] = obj.points
          const y = p1?.y || p2?.y || 0
          ctx.setLineDash([4, 2])
          ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(cw - padR, y); ctx.stroke()
          ctx.fillText(obj.label || '', cw - 40, y - 4)
          break
        }
        case 'vert': {
          const x = obj.points[0]?.x || 0
          ctx.setLineDash([3, 3])
          ctx.beginPath(); ctx.moveTo(x, mainPadT); ctx.lineTo(x, MAIN_H - mainPadB); ctx.stroke()
          break
        }
        case 'ray': {
          const [p1, p2] = obj.points
          if (!p1 || !p2) break
          const dx = p2.x - p1.x, dy = p2.y - p1.y
          const len = Math.sqrt(dx * dx + dy * dy) || 1
          const extX = p1.x + dx / len * 2000
          const extY = p1.y + dy / len * 2000
          ctx.setLineDash([4, 3])
          ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(extX, extY); ctx.stroke()
          break
        }
        case 'rect': {
          const [p1, p2] = obj.points
          if (!p1 || !p2) break
          const rX = Math.min(p1.x, p2.x), rY = Math.min(p1.y, p2.y)
          const rW = Math.abs(p2.x - p1.x), rH = Math.abs(p2.y - p1.y)
          ctx.setLineDash([5, 3])
          ctx.strokeStyle = COLORS.draw; ctx.lineWidth = 1
          ctx.strokeRect(rX, rY, rW, rH)
          break
        }
        case 'fib': {
          const [p1, p2] = obj.points
          if (!p1 || !p2) break
          const fY1 = Math.min(p1.y, p2.y), fY2 = Math.max(p1.y, p2.y)
          const fh = fY2 - fY1
          ctx.setLineDash([3, 2])
          for (let i = 0; i < FIB_LEVELS.length; i++) {
            const lvl = FIB_LEVELS[i]
            const y = fY2 - fh * lvl
            ctx.strokeStyle = `rgba(245,158,11,${0.3 + 0.6 * (1 - lvl)})`
            ctx.lineWidth = lvl % 0.5 === 0 ? 0.8 : 0.5
            ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(cw - padR, y); ctx.stroke()
            ctx.fillStyle = `rgba(245,158,11,0.9)`; ctx.font = '7px monospace'
            ctx.fillText(lvl.toFixed(lvl === 0 ? 0 : 3), cw - 50, y - 2)
          }
          break
        }
      }
      ctx.setLineDash([])
    }

    // Drawing in progress
    if (drawTool && drawStart && drawEnd) {
      ctx.strokeStyle = COLORS.draw; ctx.lineWidth = 1.2
      ctx.setLineDash([4, 3])
      ctx.beginPath()
      if (drawTool === 'rect') {
        const [x1, y1] = [drawStart.x, drawStart.y]
        const [x2, y2] = [drawEnd.x, drawEnd.y]
        ctx.strokeRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1))
      } else if (drawTool === 'fib') {
        const fy1 = Math.min(drawStart.y, drawEnd.y), fy2 = Math.max(drawStart.y, drawEnd.y)
        const fh = fy2 - fy1
        for (let i = 0; i < FIB_LEVELS.length; i++) {
          const y = fy2 - fh * FIB_LEVELS[i]
          ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(cw - padR, y); ctx.stroke()
          ctx.fillText(FIB_LEVELS[i].toFixed(FIB_LEVELS[i] === 0 ? 0 : 3), cw - 48, y - 2)
        }
      } else {
        ctx.moveTo(drawStart.x, drawStart.y); ctx.lineTo(drawEnd.x, drawEnd.y)
        ctx.stroke()
      }
      ctx.setLineDash([])
    }

    // Cross-hair
    if (crosshair) {
      ctx.strokeStyle = COLORS.crosshair; ctx.lineWidth = 0.5
      ctx.setLineDash([2, 3])
      // Vertical
      ctx.beginPath(); ctx.moveTo(crosshair.x, mainPadT); ctx.lineTo(crosshair.x, MAIN_H - mainPadB); ctx.stroke()
      // Horizontal
      ctx.beginPath(); ctx.moveTo(padL, crosshair.y); ctx.lineTo(cw - padR, crosshair.y); ctx.stroke()
      // Price label
      const dispPrice = crosshair.price < 1 ? crosshair.price.toPrecision(3) : (crosshair.price >= 1000 ? (crosshair.price / 1000).toFixed(2) + 'k' : crosshair.price.toFixed(2))
      ctx.fillStyle = COLORS.bg; ctx.fillRect(cw - 72, crosshair.y - 9, 68, 16)
      ctx.fillStyle = COLORS.draw; ctx.font = '9px monospace'; ctx.textAlign = 'right'
      ctx.fillText(`$${dispPrice}`, cw - 8, crosshair.y + 2)
      // Time label
      if (crosshair.idx >= 0 && crosshair.idx < slice.length) {
        const d = new Date(slice[crosshair.idx].time * 1000)
        const tLabel = `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
        ctx.fillStyle = COLORS.bg; ctx.fillRect(crosshair.x - 30, MAIN_H - mainPadB, 60, 12)
        ctx.fillStyle = COLORS.draw; ctx.font = '7px monospace'; ctx.textAlign = 'center'
        ctx.fillText(tLabel, crosshair.x, MAIN_H - mainPadB + 8)
      }
      ctx.setLineDash([])
    }
  }, [data, panOffset, visibleCandles, subPanels, showMA5, showMA10, showMA20, showMA60, showEMA12, showEMA26, showBOLL, trendingObjects, drawTool, drawStart, drawEnd, crosshair])

  // ── rAF-throttled schedule helper (avoids excessive redraws) ──
  const scheduleDraw = useCallback(() => {
    if (_rafRef.current !== null) return  // already scheduled
    _rafRef.current = requestAnimationFrame(() => {
      _rafRef.current = null
      draw()
    })
  }, [draw])

  useEffect(() => { scheduleDraw() }, [scheduleDraw])

  // Redraw when canvas resizes (e.g. tab becomes visible after being hidden)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const observer = new ResizeObserver(() => scheduleDraw())
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [scheduleDraw])

  useEffect(() => {
    const onResize = () => scheduleDraw()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [scheduleDraw])

  // ═══════════════════════════════════════
  // Native touch handlers (NOT React synthetic — must use passive:false)
  // ═══════════════════════════════════════
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const getPinchDistNative = (touches: TouchList) => {
      if (touches.length < 2) return 0
      const dx = touches[0].clientX - touches[1].clientX
      const dy = touches[0].clientY - touches[1].clientY
      return Math.sqrt(dx * dx + dy * dy)
    }

    const getCrosshairInfoNative = (cx: number, cy: number) => {
      if (cy > MAIN_H) return null
      const rect = canvas.getBoundingClientRect()
      const visible = Math.min(data.length, visibleCandlesRef.current)
      const sIdx = Math.max(0, data.length - visible - panOffsetRef.current)
      const sl = data.slice(sIdx, sIdx + visible)
      const cwl = Math.max(1.5, Math.min(6, (rect.width - 16) / visible - 0.5))
      const gapVal = Math.max(0.3, (rect.width - 16 - cwl * visible) / Math.max(visible - 1, 1))
      const padL = 8
      const idx = Math.round((cx - padL - cwl / 2) / (cwl + gapVal))
      if (idx < 0 || idx >= sl.length) return null
      return { x: cx, y: cy, price: sl[idx].close, idx }
    }

    // Gesture state held in refs (panStateRef, pinchInitRef, longPressIdRef) — see top-level declarations

    const onTouchStart = (e: TouchEvent) => {
      if (drawTool) {
        const rect = canvas.getBoundingClientRect()
        const x = e.touches[0].clientX - rect.left
        const y = e.touches[0].clientY - rect.top
        setDrawStart({ x, y } as { x: number; y: number; chartY: number })
        setDrawEnd({ x, y } as { x: number; y: number; chartY: number })
        e.preventDefault()
        return
      }

      if (e.touches.length >= 2) {
        pinchInitRef.current = {
          initDist: getPinchDistNative(e.touches),
          initVisible: visibleCandlesRef.current,
          initPan: panOffsetRef.current,
        }
        panStateRef.current.dragging = false
        e.preventDefault()
        return
      }

      e.preventDefault()
      panStateRef.current = { dragging: true, lastX: e.touches[0].clientX, offset: panOffsetRef.current, totalOffset: panOffsetRef.current }
      const startX = e.touches[0].clientX
      longPressIdRef.current = setTimeout(() => {
        const info = getCrosshairInfoNative(startX, e.touches[0]?.clientY ?? 0)
        // Use closure values at time of timer creation
        const rect2 = canvas.getBoundingClientRect()
        const vis2 = Math.min(data.length, visibleCandlesRef.current)
        const sIdx2 = Math.max(0, data.length - vis2 - panOffsetRef.current)
        const sl2 = data.slice(sIdx2, sIdx2 + vis2)
        const cwl2 = Math.max(1.5, Math.min(6, (rect2.width - 16) / vis2 - 0.5))
        const gpl2 = Math.max(0.3, (rect2.width - 16 - cwl2 * vis2) / Math.max(vis2 - 1, 1))
        const padL2 = 8
        const posX = startX - rect2.left
        const posY = (e.touches[0]?.clientY ?? 0) - rect2.top
        const idx2 = Math.round((posX - padL2 - cwl2 / 2) / (cwl2 + gpl2))
        if (idx2 >= 0 && idx2 < sl2.length) {
          setCrosshair({ x: posX, y: posY, price: sl2[idx2].close, idx: idx2 })
        }
        panStateRef.current.dragging = false
      }, 500)
    }

    const onTouchMove = (e: TouchEvent) => {
      if (drawTool) {
        const rect = canvas.getBoundingClientRect()
        const x = e.touches[0].clientX - rect.left
        const y = e.touches[0].clientY - rect.top
        setDrawEnd({ x, y } as { x: number; y: number; chartY: number })
        e.preventDefault()
        return
      }

      if (e.touches.length >= 2 && pinchInitRef.current) {
        e.preventDefault()
        if (longPressIdRef.current) { clearTimeout(longPressIdRef.current); longPressIdRef.current = null }
        const dist = getPinchDistNative(e.touches)
        const scale = pinchInitRef.current.initDist > 0 ? dist / pinchInitRef.current.initDist : 1
        const newVisible = Math.round(pinchInitRef.current.initVisible / scale)
        setVisibleCandles(Math.max(MIN_VISIBLE, Math.min(MAX_VISIBLE, newVisible)))
        return
      }

      if (longPressIdRef.current) {
        clearTimeout(longPressIdRef.current)
        longPressIdRef.current = null
      }
      setCrosshair(null)
      if (!panStateRef.current.dragging) return
      e.preventDefault()
      const dx = panStateRef.current.lastX - e.touches[0].clientX
      const candleShift = Math.round(dx / 7)
      const newOffset = Math.max(0, Math.min(maxPan.current, panStateRef.current.totalOffset + candleShift))
      setPanOffset(newOffset)
    }

    const onTouchEnd = (e: TouchEvent) => {
      if (longPressIdRef.current) {
        clearTimeout(longPressIdRef.current)
        longPressIdRef.current = null
      }

      if (pinchInitRef.current) {
        pinchInitRef.current = null
        return
      }

      if (drawTool && drawStart) {
        const rect = canvas.getBoundingClientRect()
        const pos = {
          x: (e.changedTouches[0]?.clientX ?? 0) - rect.left,
          y: (e.changedTouches[0]?.clientY ?? 0) - rect.top,
        }
        const objPoints = [{ x: drawStart.x, y: drawStart.y }, { x: pos.x, y: pos.y }]
        setTrendingObjects(prev => [...prev, {
          type: drawTool,
          points: objPoints,
          label: drawTool === 'horiz' ? formatPrice(drawStart.y) : undefined,
        }])
        setDrawStart(null)
        setDrawEnd(null)
        setDrawTool(null)
      }
      panStateRef.current.dragging = false
      setTimeout(() => setCrosshair(null), 2000)
    }

    canvas.addEventListener('touchstart', onTouchStart, { passive: false })
    canvas.addEventListener('touchmove', onTouchMove, { passive: false })
    canvas.addEventListener('touchend', onTouchEnd, { passive: false })

    return () => {
      canvas.removeEventListener('touchstart', onTouchStart)
      canvas.removeEventListener('touchmove', onTouchMove)
      canvas.removeEventListener('touchend', onTouchEnd)
    }
  }, [data, drawTool, drawStart, maxPan.current])

  // ── Sub-panel toggle ──
  const toggleSubPanel = (id: string) => {
    setSubPanels(prev => prev.map(p => p.id === id ? { ...p, visible: !p.visible } : p))
  }

  const changeSubPanel = (idx: number, id: string) => {
    const configs: Record<string, { label: string; color: string }> = {
      macd: { label: 'MACD', color: COLORS.dif },
      kdj: { label: 'KDJ', color: COLORS.k },
      rsi: { label: 'RSI', color: COLORS.rsi },
      atr: { label: 'ATR', color: COLORS.atr },
      obv: { label: 'OBV', color: COLORS.obv },
      vol: { label: 'VOL', color: COLORS.vol },
    }
    const cfg = configs[id] || { label: id, color: '#fff' }
    setSubPanels(prev => prev.map((p, i) => i === idx ? { ...p, id, label: cfg.label, color: cfg.color, visible: true } : p))
  }

  // ── Visible sub-panels ──
  const visibleSubPanels = subPanels.filter(p => p.visible)
  const totalH = MAIN_H + visibleSubPanels.reduce((sum, p) => sum + p.height + GAP, 0)

  // ── Available sub-panel indicators ──
  const AVAILABLE_INDICATORS = [
    { id: 'macd', label: 'MACD' },
    { id: 'kdj', label: 'KDJ' },
    { id: 'rsi', label: 'RSI' },
    { id: 'atr', label: 'ATR' },
    { id: 'obv', label: 'OBV' },
    { id: 'vol', label: 'VOL' },
  ]

  // Scroll period row to current period
  useEffect(() => {
    if (!periodRowRef.current) return
    const btn = periodRowRef.current.querySelector(`[data-period="${period}"]`)
    btn?.scrollIntoView?.({ behavior: 'smooth', inline: 'center', block: 'nearest' })
  }, [period])

  return (
    <div>
      {/* ═══ Header: Symbol + Price ═══ */}
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-terminal-text">{symbol}</span>
          {lastPrice && (
            <span className={`text-base font-mono font-bold ${priceChange >= 0 ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
              ${lastPrice < 1 ? lastPrice.toPrecision(4) : lastPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          )}
          {data.length > 1 && (
            <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${priceChange >= 0 ? 'bg-terminal-profit/10 text-terminal-profit' : 'bg-terminal-loss/10 text-terminal-loss'}`}>
              {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(2)}%
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {lastUpdated && (
            <span className="text-[9px] text-terminal-muted">
              {lastUpdated.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
          {onRefresh && (
            <button
              onClick={onRefresh}
              className="p-1 rounded text-terminal-muted hover:bg-terminal-card/50 active:opacity-70"
              title="刷新数据"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="23 4 23 10 17 10" />
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* ═══ Period Row (scrollable) ═══ */}
      <div ref={periodRowRef} className="flex gap-0.5 mb-1.5 overflow-x-auto no-scrollbar pb-0.5" style={{ WebkitOverflowScrolling: 'touch' }}>
        {PERIODS.map(p => (
          <button
            key={p}
            data-period={p}
            onClick={() => onPeriodChange(p)}
            className={`shrink-0 px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${period === p ? 'bg-terminal-primary text-white shadow-sm' : 'text-terminal-muted bg-terminal-card/60 hover:bg-terminal-card'}`}
          >
            {p}
          </button>
        ))}
      </div>

      {/* ═══ Toolbar: Indicators toggle ═══ */}
      <div className="flex flex-wrap items-center gap-0.5 mb-1">
        {/* Main chart overlays */}
        <span className="text-[9px] text-terminal-muted mr-1">主图:</span>
        {[
          { key: 'MA5', show: showMA5, set: setShowMA5, color: '#f59e0b' },
          { key: 'MA10', show: showMA10, set: setShowMA10, color: '#3b82f6' },
          { key: 'MA20', show: showMA20, set: setShowMA20, color: '#a855f7' },
          { key: 'MA60', show: showMA60, set: setShowMA60, color: '#ec4899' },
          { key: 'EMA12', show: showEMA12, set: setShowEMA12, color: '#06b6d4' },
          { key: 'EMA26', show: showEMA26, set: setShowEMA26, color: '#84cc16' },
          { key: 'BOLL', show: showBOLL, set: setShowBOLL, color: '#ef4444' },
        ].map(({ key, show, set, color }) => (
          <button
            key={key}
            onClick={() => set(!show)}
            className={`px-1 py-0.5 rounded text-[8px] font-medium transition-colors ${show ? 'text-white' : 'text-terminal-muted bg-terminal-card/40'}`}
            style={show ? { background: `${color}30`, border: `0.5px solid ${color}60` } : { border: '0.5px solid transparent' }}
          >
            {key}
          </button>
        ))}
        <span className="text-[9px] text-terminal-muted ml-2 mr-1">副图:</span>
        {subPanels.map((p, i) => (
          <button
            key={i}
            onClick={() => toggleSubPanel(p.id)}
            className={`px-1 py-0.5 rounded text-[8px] font-medium transition-colors ${p.visible ? 'text-white' : 'text-terminal-muted bg-terminal-card/40'}`}
            style={p.visible ? { background: `${p.color}30`, border: `0.5px solid ${p.color}60` } : { border: '0.5px solid transparent' }}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* ═══ Drawing tools ═══ */}
      <div className="flex items-center gap-0.5 mb-1">
        <span className="text-[9px] text-terminal-muted mr-1">画线:</span>
        {([
          { tool: 'trend' as DrawTool, icon: '↗', label: '趋势线' },
          { tool: 'horiz' as DrawTool, icon: '━', label: '水平' },
          { tool: 'vert' as DrawTool, icon: '┃', label: '垂直' },
          { tool: 'ray' as DrawTool, icon: '➤', label: '射线' },
          { tool: 'rect' as DrawTool, icon: '▭', label: '矩形' },
          { tool: 'fib' as DrawTool, icon: '≡', label: '斐波' },
        ]).map(({ tool, icon, label }) => (
          <button
            key={tool}
            onClick={() => setDrawTool(drawTool === tool ? null : tool)}
            className={`px-1.5 py-0.5 rounded text-[8px] font-medium transition-colors ${drawTool === tool ? 'bg-terminal-primary text-white shadow-sm' : 'text-terminal-muted bg-terminal-card/60 hover:bg-terminal-card'}`}
            title={label}
          >
            {icon} {label}
          </button>
        ))}
        {trendingObjects.length > 0 && (
          <button
            onClick={() => setTrendingObjects([])}
            className="ml-1 px-1.5 py-0.5 rounded text-[8px] font-medium text-red-400 bg-red-900/20 hover:bg-red-900/30"
          >
            ✕ 清除
          </button>
        )}
        {drawTool && (
          <span className="text-[9px] text-yellow-400 ml-1">⚡ 画线中</span>
        )}
      </div>

      {/* ═══ Canvas Chart ═══ */}
      <div
        ref={mainRef}
        className="relative rounded-lg bg-terminal-bg border border-terminal-border overflow-hidden"
        style={{ height: totalH }}
      >
        {loading && data.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-terminal-bg/90 z-10">
            <div className="flex flex-col items-center gap-2">
              <div className="w-4 h-4 border-2 border-terminal-primary border-t-transparent rounded-full animate-spin" />
              <span className="text-xs text-terminal-muted">加载K线...</span>
            </div>
          </div>
        )}
        {data.length > 0 && (
          <div className="absolute top-1.5 right-2 z-10 text-[8px] text-terminal-muted">
            {Math.min(visibleCandles, data.length)}/{data.length} 根
          </div>
        )}
        <canvas
          ref={canvasRef}
          className="w-full"
          style={{ height: totalH, display: 'block', touchAction: 'none' }}
        />
      </div>

      {/* ═══ Sub-panel selector ═══ */}
      <div className="flex items-center gap-0.5 mt-1">
        <span className="text-[8px] text-terminal-muted">副图选择:</span>
        <select
          className="text-[8px] bg-terminal-card border border-terminal-border rounded px-1 py-0.5 text-terminal-muted"
          value=""
          onChange={(e) => {
            const val = e.target.value
            if (!val) return
            // Find first invisible panel or replace visible ones
            const idx = subPanels.findIndex(p => !p.visible)
            if (idx >= 0) {
              changeSubPanel(idx, val)
            } else {
              changeSubPanel(0, val)
            }
            e.target.value = ''
          }}
        >
          <option value="">+ 添加</option>
          {AVAILABLE_INDICATORS.map(ind => (
            !subPanels.some(p => p.visible && p.id === ind.id) ? (
              <option key={ind.id} value={ind.id}>{ind.label}</option>
            ) : null
          ))}
        </select>
      </div>

      {/* ═══ Legend ═══ */}
      <div className="flex flex-wrap gap-x-2.5 gap-y-0.5 mt-1 text-[8px] text-terminal-muted">
        {(showMA5 || showMA10 || showMA20 || showMA60 || showEMA12 || showEMA26 || showBOLL) && (
          <>
            {showMA5 && <span><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.ma5 }} />MA5</span>}
            {showMA10 && <span><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.ma10 }} />MA10</span>}
            {showMA20 && <span><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.ma20 }} />MA20</span>}
            {showMA60 && <span><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.ma60 }} />MA60</span>}
            {showEMA12 && <span><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.ema12 }} />EMA12</span>}
            {showEMA26 && <span><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.ema26 }} />EMA26</span>}
            {showBOLL && <span><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.bollMid }} />BOLL</span>}
          </>
        )}
        {visibleSubPanels.some(p => p.id === 'macd') && (
          <><span className="ml-1"><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.dif }} />DIF</span>
          <span><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.dea }} />DEA</span></>
        )}
        {visibleSubPanels.some(p => p.id === 'kdj') && (
          <><span className="ml-1"><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.k }} />K</span>
          <span><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.d }} />D</span>
          <span><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.j }} />J</span></>
        )}
        {visibleSubPanels.some(p => p.id === 'rsi') && (
          <span className="ml-1"><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.rsi }} />RSI</span>
        )}
        {visibleSubPanels.some(p => p.id === 'atr') && (
          <span className="ml-1"><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.atr }} />ATR</span>
        )}
        {visibleSubPanels.some(p => p.id === 'obv') && (
          <span className="ml-1"><span className="inline-block w-3 h-0.5 rounded mr-0.5" style={{ background: COLORS.obv }} />OBV</span>
        )}
      </div>
    </div>
  )
}
