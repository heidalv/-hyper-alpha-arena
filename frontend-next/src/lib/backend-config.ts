// frontend-next/src/lib/backend-config.ts
// 运行时后端地址管理:用户设置(localStorage) > env > 默认 localhost。
// 支持桌面应用在开发(localhost:8000)/生产(api.yourdomain.com)/用户自定义间切换。

const STORAGE_KEY = "arena_backend_url";

export function getBackendUrl(): string {
  // 服务端渲染时无 localStorage,用 env
  if (typeof window === "undefined") {
    return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  }
  // 客户端:优先用户设置
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored) return stored.replace(/\/$/, "");
  // [2026-08-05 浏览器直连] 当页面从非 localhost 域名访问（后端同源托管了前端，
  // 如手机浏览器打开 http://xxx.cpolar.top/login）时，自动以当前页面 origin
  // 作为后端地址——此时页面与后端同源，零配置即可登录使用。
  const host = window.location.hostname;
  const isLocal = host === "localhost" || host === "127.0.0.1";
  if (!isLocal) {
    return window.location.origin.replace(/\/$/, "");
  }
  return (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
}

export function setBackendUrl(url: string): void {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, url.replace(/\/$/, ""));
  }
}

export function getWsUrl(): string {
  // http(s)→ws(s), 末尾去斜杠, 加 /ws
  const base = getBackendUrl().replace(/^http/, "ws").replace(/\/$/, "");
  return `${base}/ws`;
}
