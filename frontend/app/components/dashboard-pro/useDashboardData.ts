/**
 * useDashboardData — 多账户仪表盘数据源
 *
 * - HTTP 轮询 /api/dashboard/overview 做兜底刷新（10s）
 * - 为每个已选账户开一条独立 WS 连接（复用现有 bootstrap 无关协议：switch_account + get_snapshot），
 *   收到任意 snapshot 类消息时视为"该账户有变化"，立即触发一次全量 HTTP 聚合刷新（防抖），
 *   不解析 snapshot 增量结构本身 —— 聚合器是唯一数据口径，WS 只是"有变化"的低延迟信号。
 * - 组件卸载 / 选择变更时彻底关闭所有连接，避免连接泄漏。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchAccountsOverview } from '@/lib/dashboardApi'
import type { AccountOverview, AccountSelection, WsConnStatus } from './types'

const POLL_MS = 10000
const MAX_RECOMMENDED_CONNECTIONS = 20
const WS_TRIGGER_DEBOUNCE_MS = 800

function resolveWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws`
}

export function useDashboardData(selections: AccountSelection[]) {
  const [overviews, setOverviews] = useState<AccountOverview[]>([])
  const [wsStatusByAccount, setWsStatusByAccount] = useState<Record<number, WsConnStatus>>({})
  const [loading, setLoading] = useState(false)
  const selectionsRef = useRef(selections)
  selectionsRef.current = selections

  const refresh = useCallback(async () => {
    const current = selectionsRef.current
    if (current.length === 0) {
      setOverviews([])
      return
    }
    setLoading(true)
    try {
      const data = await fetchAccountsOverview(current)
      setOverviews(data)
    } catch (err) {
      console.warn('[useDashboardData] overview 拉取失败:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  // HTTP 轮询兜底
  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, POLL_MS)
    return () => clearInterval(timer)
  }, [refresh, JSON.stringify(selections.map((s) => `${s.account_id}:${s.exchange}:${s.trading_mode}`))])

  // 逐账户 WS 连接（仅信号触发刷新，不解析增量）
  useEffect(() => {
    if (selections.length === 0) {
      setWsStatusByAccount({})
      return
    }
    if (selections.length > MAX_RECOMMENDED_CONNECTIONS) {
      console.warn(
        `[useDashboardData] 已选账户数 (${selections.length}) 超过建议上限 ${MAX_RECOMMENDED_CONNECTIONS}，` +
          '过多 WS 连接可能影响性能，建议合并查看。',
      )
    }

    const sockets: WebSocket[] = []
    let debounceTimer: ReturnType<typeof setTimeout> | null = null
    const triggerRefresh = () => {
      if (debounceTimer) clearTimeout(debounceTimer)
      debounceTimer = setTimeout(refresh, WS_TRIGGER_DEBOUNCE_MS)
    }

    setWsStatusByAccount((prev) => {
      const next: Record<number, WsConnStatus> = {}
      for (const s of selections) next[s.account_id] = prev[s.account_id] || 'connecting'
      return next
    })

    for (const sel of selections) {
      let ws: WebSocket
      try {
        ws = new WebSocket(resolveWsUrl())
      } catch (err) {
        console.warn(`[useDashboardData] 账户 ${sel.account_id} WS 建连失败:`, err)
        setWsStatusByAccount((prev) => ({ ...prev, [sel.account_id]: 'error' }))
        continue
      }
      sockets.push(ws)

      setWsStatusByAccount((prev) => ({ ...prev, [sel.account_id]: 'connecting' }))

      ws.addEventListener('open', () => {
        setWsStatusByAccount((prev) => ({ ...prev, [sel.account_id]: 'open' }))
        ws.send(JSON.stringify({ type: 'switch_account', account_id: sel.account_id }))
        ws.send(JSON.stringify({ type: 'get_snapshot', account_id: sel.account_id, trading_mode: sel.trading_mode }))
      })

      ws.addEventListener('message', (evt) => {
        try {
          const msg = JSON.parse(evt.data as string)
          if (typeof msg?.type === 'string' && msg.type.startsWith('snapshot')) {
            triggerRefresh()
          }
        } catch {
          // 忽略非 JSON 消息
        }
      })

      ws.addEventListener('error', () => {
        setWsStatusByAccount((prev) => ({ ...prev, [sel.account_id]: 'error' }))
      })

      ws.addEventListener('close', () => {
        setWsStatusByAccount((prev) => ({ ...prev, [sel.account_id]: 'closed' }))
      })
    }

    return () => {
      if (debounceTimer) clearTimeout(debounceTimer)
      for (const ws of sockets) {
        try {
          ws.close()
        } catch {
          // 忽略关闭异常
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(selections.map((s) => `${s.account_id}:${s.trading_mode}`)), refresh])

  return { overviews, wsStatusByAccount, loading, refresh }
}
