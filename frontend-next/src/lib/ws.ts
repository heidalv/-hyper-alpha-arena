/**
 * WebSocket 管理器 — 单例连接，发布订阅模式
 * 连接后端 /ws 端点，实时推送行情/持仓/信号
 */

import { getWsUrl } from "./backend-config";

type WsMessageHandler = (data: any) => void;

class WsManager {
  private socket: WebSocket | null = null;
  private subscribers = new Set<WsMessageHandler>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private intentionalClose = false;
  private url: string;

  constructor() {
    // 运行时后端地址:用户设置(localStorage) > NEXT_PUBLIC_WS_URL > 默认 ws://localhost:8000/ws。
    // 单例首次实例化时求值;改地址后新会话生效(getWs() 首次调用,通常页面加载时)。
    this.url = getWsUrl();
  }

  connect() {
    if (this.socket?.readyState === WebSocket.OPEN) return;
    if (this.socket?.readyState === WebSocket.CONNECTING) return;

    this.intentionalClose = false;
    try {
      this.socket = new WebSocket(this.url);

      this.socket.onopen = () => {
        console.log("[WS] Connected");
      };

      this.socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.subscribers.forEach((fn) => {
            try { fn(data); } catch (e) { console.error("[WS] Handler error:", e); }
          });
        } catch (e) {
          console.error("[WS] Parse error:", e);
        }
      };

      this.socket.onclose = () => {
        console.log("[WS] Disconnected");
        this.socket = null;
        if (!this.intentionalClose) {
          this.scheduleReconnect();
        }
      };

      this.socket.onerror = () => {
        // onclose 会处理重连
      };
    } catch (e) {
      console.error("[WS] Connect failed:", e);
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 3000);
  }

  subscribe(handler: WsMessageHandler): () => void {
    this.subscribers.add(handler);
    if (this.socket?.readyState !== WebSocket.OPEN) {
      this.connect();
    }
    return () => {
      this.subscribers.delete(handler);
    };
  }

  send(data: any) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(data));
    }
  }

  get connected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  disconnect() {
    this.intentionalClose = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.socket?.close();
    this.socket = null;
  }
}

// 单例
let instance: WsManager | null = null;

export function getWs(): WsManager {
  if (!instance) {
    instance = new WsManager();
  }
  return instance;
}
