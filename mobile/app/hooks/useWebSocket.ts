import { useEffect, useRef, useCallback, useState } from 'react'
import type { Overview, Position, Order, Trade, AIDecision, AssetCurvePoint, WSMessage, WSSnapshot, WSDelta } from '@/api/types'

// ── Delta merge helpers ──
function mergePositions(current: Position[], changes: any[]): Position[] {
  const map = new Map(current.map(p => [p.id, p]))
  for (const c of changes) {
    if (c._removed) map.delete(c.id)
    else { const e = map.get(c.id); map.set(c.id, e ? { ...e, ...c } : c) }
  }
  return Array.from(map.values())
}

function mergeOrders(current: Order[], items: any[], removed: number[]): Order[] {
  const rs = new Set(removed)
  const map = new Map(current.filter(o => !rs.has(o.id)).map(o => [o.id, o]))
  for (const i of items) { const e = map.get(i.id); map.set(i.id, e ? { ...e, ...i } : i) }
  return Array.from(map.values())
}

interface WSState {
  connected: boolean
  overview: Overview | null
  positions: Position[]
  orders: Order[]
  trades: Trade[]
  aiDecisions: AIDecision[]
  assetCurves: Record<string, AssetCurvePoint[]>
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const seqRef = useRef(0)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()
  const [state, setState] = useState<WSState>({
    connected: false,
    overview: null,
    positions: [],
    orders: [],
    trades: [],
    aiDecisions: [],
    assetCurves: {},
  })

  const getSnapshot = useCallback(() => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'get_snapshot', trading_mode: 'paper' }))
    }
  }, [])

  const connect = useCallback(() => {
    try {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${proto}//${window.location.host}/ws`
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        setState(s => ({ ...s, connected: true }))
        ws.send(JSON.stringify({
          type: 'bootstrap',
          username: 'default',
          initial_capital: 10000,
          trading_mode: 'paper',
        }))
      }

      ws.onmessage = (e) => {
        try {
          const msg: WSMessage = JSON.parse(e.data)

          if (msg.type === 'bootstrap_ok') {
            ws.send(JSON.stringify({ type: 'get_snapshot', trading_mode: 'paper' }))
            return
          }

          if (['snapshot', 'full_snapshot', 'snapshot_fast', 'snapshot_full'].includes(msg.type)) {
            const s = msg as WSSnapshot
            if (s.seq) seqRef.current = s.seq
            setState(prev => ({
              ...prev,
              overview: s.overview || prev.overview,
              positions: s.positions || prev.positions,
              orders: s.orders || prev.orders,
              trades: s.trades || prev.trades,
              aiDecisions: s.ai_decisions || prev.aiDecisions,
              assetCurves: s.all_asset_curves || prev.assetCurves,
            }))
            return
          }

          if (msg.type === 'delta') {
            const d = msg as WSDelta
            if (d.seq) seqRef.current = d.seq
            const c = d.changes
            if (c) {
              setState(prev => ({
                ...prev,
                overview: c.overview ? (prev.overview ? { ...prev.overview, ...c.overview } : c.overview as any) : prev.overview,
                positions: c.positions ? mergePositions(prev.positions, c.positions) : prev.positions,
                orders: c.orders ? mergeOrders(prev.orders, c.orders, c.orders_removed || []) : prev.orders,
                trades: c.trades ? [...c.trades, ...prev.trades].slice(0, 100) : prev.trades,
                aiDecisions: c.ai_decisions ? [...c.ai_decisions, ...prev.aiDecisions].slice(0, 50) : prev.aiDecisions,
                assetCurves: c.all_asset_curves || prev.assetCurves,
              }))
            }
            return
          }

          if (msg.type === 'position_update') {
            setState(prev => ({ ...prev, positions: msg.positions || [] }))
            return
          }
          if (msg.type === 'trade_update') {
            setState(prev => ({ ...prev, trades: [msg.trade, ...prev.trades].slice(0, 100) }))
            return
          }
          if (msg.type === 'asset_curve_update' || msg.type === 'asset_curve_data') {
            setState(prev => ({ ...prev, assetCurves: msg.data || prev.assetCurves }))
            return
          }
          if (msg.type === 'order_filled' || msg.type === 'order_pending') {
            setTimeout(getSnapshot, 500)
            return
          }
        } catch {}
      }

      ws.onclose = (ev) => {
        wsRef.current = null
        setState(s => ({ ...s, connected: false }))
        if (ev.code !== 1000 && ev.code !== 1001) {
          reconnectTimer.current = setTimeout(connect, 3000)
        }
      }

      ws.onerror = () => {}
    } catch {}
  }, [getSnapshot])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close(1000)
    }
  }, [connect])

  return { ...state, getSnapshot }
}
