/**
 * 全局 WebSocket 管理器
 *
 * - 单连接复用，避免 StrictMode / HMR 重复建连
 * - 消息广播：订阅者用 subscribe() 注册，不会因组件卸载而拆掉底层 listener
 * - 与 useKlinesWebSocket、main.tsx 共用同一连接
 */

export type WsMessage = Record<string, unknown>
type Subscriber = (msg: WsMessage) => void

let socket: WebSocket | null = null
let listenersAttached = false
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let intentionalClose = false
const subscribers = new Set<Subscriber>()
const onOpenCallbacks = new Set<() => void>()

function resolveWsUrl(): string {
  const wsUrl = import.meta.env.VITE_WS_URL
  if (wsUrl) return wsUrl
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws`
}

function publishSocket() {
  if (typeof window !== 'undefined') {
    ;(window as unknown as { __WS_INSTANCE__?: WebSocket | null }).__WS_INSTANCE__ = socket
  }
}

function dispatchMessage(raw: string) {
  try {
    const msg = JSON.parse(raw) as WsMessage
    subscribers.forEach((fn) => {
      try {
        fn(msg)
      } catch (err) {
        console.error('[wsManager] subscriber error:', err)
      }
    })
  } catch (err) {
    console.error('[wsManager] failed to parse message:', err)
  }
}

function attachSocketListeners(ws: WebSocket) {
  if (listenersAttached && socket === ws) return
  listenersAttached = true

  ws.addEventListener('message', (e) => dispatchMessage(e.data as string))

  ws.addEventListener('close', (event) => {
    console.log('[wsManager] WebSocket closed:', event.code, event.reason)
    socket = null
    listenersAttached = false
    publishSocket()
    if (!intentionalClose) scheduleReconnect()
  })

  ws.addEventListener('error', (event) => {
    console.error('[wsManager] WebSocket error:', event)
  })
}

function notifyOpen() {
  onOpenCallbacks.forEach((cb) => {
    try {
      cb()
    } catch (err) {
      console.error('[wsManager] onOpen callback error:', err)
    }
  })
}

function scheduleReconnect() {
  if (reconnectTimer || intentionalClose) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    wsConnect()
  }, 3000)
}

/** 建立或复用 WebSocket 连接 */
export function wsConnect(onOpen?: () => void): WebSocket | null {
  if (onOpen) onOpenCallbacks.add(onOpen)

  if (socket?.readyState === WebSocket.OPEN) {
    onOpen?.()
    return socket
  }

  if (socket?.readyState === WebSocket.CONNECTING) {
    if (onOpen) {
      socket.addEventListener('open', () => onOpen(), { once: true })
    }
    return socket
  }

  intentionalClose = false
  try {
    socket = new WebSocket(resolveWsUrl())
    publishSocket()
    attachSocketListeners(socket)

    socket.addEventListener('open', () => {
      console.log('WebSocket connected')
      notifyOpen()
    })
  } catch (err) {
    console.error('[wsManager] failed to create WebSocket:', err)
    scheduleReconnect()
    return null
  }

  return socket
}

/** 订阅消息；返回取消订阅函数 */
export function wsSubscribe(handler: Subscriber): () => void {
  subscribers.add(handler)
  wsConnect()
  return () => subscribers.delete(handler)
}

/** 发送 JSON 消息（连接未就绪时排队到 open） */
export function wsSend(payload: object): void {
  const data = JSON.stringify(payload)
  const ws = socket ?? wsConnect()
  if (!ws) return

  if (ws.readyState === WebSocket.OPEN) {
    ws.send(data)
    return
  }

  if (ws.readyState === WebSocket.CONNECTING) {
    ws.addEventListener('open', () => ws.send(data), { once: true })
  }
}

export function wsGetSocket(): WebSocket | null {
  return socket?.readyState === WebSocket.OPEN ? socket : socket
}

export function wsIsOpen(): boolean {
  return socket?.readyState === WebSocket.OPEN
}

/** 主动关闭（一般仅测试用） */
export function wsDisconnect(): void {
  intentionalClose = true
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  socket?.close()
  socket = null
  listenersAttached = false
  publishSocket()
}
