/**
 * useKlinesWebSocket — React Hook: 通过 WebSocket 订阅实时 K 线数据
 *
 * 复用 wsManager 全局连接，避免与 main.tsx 争抢 message 监听器。
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { wsConnect, wsIsOpen, wsSend, wsSubscribe } from '@/lib/wsManager'

interface KlineBar {
  open: number
  high: number
  low: number
  close: number
  volume: number
  timestamp: number
}

interface KlineUpdate {
  type: 'kline_update' | 'resonance_update'
  symbol: string
  period: string
  bar?: KlineBar
  refresh_only?: boolean
  indicators?: Record<string, number[]>
  resonance?: Record<string, any>
}

interface UseKlinesWebSocketResult {
  latestUpdate: KlineUpdate | null
  connected: boolean
  resubscribe: () => void
}

export function useKlinesWebSocket(
  symbol: string,
  period: string
): UseKlinesWebSocketResult {
  const [latestUpdate, setLatestUpdate] = useState<KlineUpdate | null>(null)
  const [connected, setConnected] = useState(false)
  const subscribedRef = useRef<string | null>(null)
  const mountedRef = useRef(true)

  const sendUnsubscribe = useCallback((subKey: string) => {
    if (!wsIsOpen()) return
    const [s, p] = subKey.split(':')
    wsSend({ type: 'unsubscribe_klines', symbol: s, period: p })
  }, [])

  const sendSubscribe = useCallback((sym: string, per: string) => {
    if (!wsIsOpen()) return false
    wsSend({ type: 'subscribe_klines', symbol: sym, period: per })
    return true
  }, [])

  const subscribeCurrent = useCallback(() => {
    if (!mountedRef.current) return
    const subKey = `${symbol}:${period}`
    const oldKey = subscribedRef.current
    if (oldKey && oldKey !== subKey) {
      sendUnsubscribe(oldKey)
    }
    if (sendSubscribe(symbol, period)) {
      subscribedRef.current = subKey
      setConnected(true)
    }
  }, [symbol, period, sendSubscribe, sendUnsubscribe])

  const resubscribe = useCallback(() => {
    wsConnect(subscribeCurrent)
    subscribeCurrent()
  }, [subscribeCurrent])

  useEffect(() => {
    mountedRef.current = true

    const unsubscribe = wsSubscribe((msg) => {
      const type = msg.type as string | undefined
      if (
        (type === 'kline_update' || type === 'resonance_update') &&
        msg.symbol
      ) {
        setLatestUpdate(msg as unknown as KlineUpdate)
      } else if (type === 'kline_update' && msg.status === 'subscribed') {
        setConnected(true)
      }
    })

    wsConnect(() => {
      setConnected(true)
      subscribeCurrent()
    })

    if (wsIsOpen()) {
      setConnected(true)
      subscribeCurrent()
    }

    return () => {
      mountedRef.current = false
      const key = subscribedRef.current
      if (key) {
        sendUnsubscribe(key)
        subscribedRef.current = null
      }
      unsubscribe()
    }
  }, [symbol, period, sendUnsubscribe, subscribeCurrent])

  return { latestUpdate, connected, resubscribe }
}
