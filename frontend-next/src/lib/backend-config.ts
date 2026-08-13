// frontend-next/src/lib/backend-config.ts
// 运行时后端地址：用户设置 > 智能推断 > env > 默认。
//
// 内网场景（如 http://192.168.1.8:5273）：
//   前端在 5273，API 必须走同主机 8000，不能用页面 origin（否则打到 Next 自己）。

const STORAGE_KEY = "arena_backend_url";

/** 前端独立端口：此时 API 默认同主机 :8000 */
const FRONTEND_PORTS = new Set(["5273", "3000", "5173", "4173"]);

function isLoopbackHost(host: string): boolean {
  return host === "localhost" || host === "127.0.0.1" || host === "[::1]";
}

function inferBackendUrl(): string {
  if (typeof window === "undefined") {
    return (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  }

  const { protocol, hostname, port } = window.location;
  const envDefault = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");

  // Next / Vite 等独立前端端口 → 同主机后端 8000（含局域网 IP）
  if (FRONTEND_PORTS.has(port)) {
    return `${protocol}//${hostname}:8000`;
  }

  // 后端同源托管前端（:8000 / 反代 80/443）→ 用当前 origin
  if (port === "8000" || port === "" || port === "80" || port === "443") {
    return window.location.origin.replace(/\/$/, "");
  }

  if (isLoopbackHost(hostname) && envDefault) {
    return envDefault;
  }

  // 其它端口：优先同主机 8000
  return `${protocol}//${hostname}:8000`;
}

export function getBackendUrl(): string {
  if (typeof window === "undefined") {
    return (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  }

  const stored = window.localStorage.getItem(STORAGE_KEY)?.replace(/\/$/, "") || "";
  if (stored) {
    const pageHost = window.location.hostname;
    const storedIsLoopback = /localhost|127\.0\.0\.1|\[::1\]/i.test(stored);
    // 手机/内网打开时，忽略本机留下的 localhost 后端地址
    if (!(storedIsLoopback && !isLoopbackHost(pageHost))) {
      return stored;
    }
  }

  return inferBackendUrl();
}

export function setBackendUrl(url: string): void {
  if (typeof window !== "undefined") {
    const cleaned = url.replace(/\/$/, "");
    const pageHost = window.location.hostname;
    const cleanedIsLoopback = /localhost|127\.0\.0\.1|\[::1\]/i.test(cleaned);
    // Electron 壳在 127.0.0.1 本地静态服上跑：禁止用 loopback 覆盖已有远程后端
    if (
      typeof (window as any).electronAPI !== "undefined" &&
      cleanedIsLoopback &&
      isLoopbackHost(pageHost)
    ) {
      const existing = window.localStorage.getItem(STORAGE_KEY)?.replace(/\/$/, "") || "";
      const existingRemote =
        existing && !/localhost|127\.0\.0\.1|\[::1\]/i.test(existing);
      if (existingRemote) {
        try {
          void window.electronAPI?.config?.setBackendUrl?.(existing);
        } catch {
          /* ignore */
        }
        return;
      }
    }
    window.localStorage.setItem(STORAGE_KEY, cleaned);
    // Electron：同步到主进程，供 autoUpdater 拼 /arena-updates/
    try {
      void window.electronAPI?.config?.setBackendUrl?.(cleaned);
    } catch {
      /* ignore */
    }
  }
}

export function getWsUrl(): string {
  const base = getBackendUrl().replace(/^http/, "ws").replace(/\/$/, "");
  return `${base}/ws`;
}
