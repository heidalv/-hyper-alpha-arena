/**
 * Token 持久化适配层
 * - Electron: 主进程 safeStorage 加密落盘
 * - 浏览器开发: localStorage 兜底
 */
const LS_ACCESS = "arena_access_token";
const LS_REFRESH = "arena_refresh_token";

export function isElectronRuntime(): boolean {
  return typeof window !== "undefined" && !!window.electronAPI?.isElectron;
}

export async function loadTokens(): Promise<{
  accessToken: string | null;
  refreshToken: string | null;
}> {
  if (typeof window === "undefined") {
    return { accessToken: null, refreshToken: null };
  }
  if (window.electronAPI?.auth) {
    try {
      return await window.electronAPI.auth.getTokens();
    } catch {
      return { accessToken: null, refreshToken: null };
    }
  }
  return {
    accessToken: localStorage.getItem(LS_ACCESS),
    refreshToken: localStorage.getItem(LS_REFRESH),
  };
}

export async function saveTokens(accessToken: string, refreshToken: string): Promise<void> {
  if (typeof window === "undefined") return;
  if (window.electronAPI?.auth) {
    await window.electronAPI.auth.setTokens(accessToken, refreshToken);
    return;
  }
  localStorage.setItem(LS_ACCESS, accessToken);
  localStorage.setItem(LS_REFRESH, refreshToken);
}

export async function clearTokens(): Promise<void> {
  if (typeof window === "undefined") return;
  if (window.electronAPI?.auth) {
    await window.electronAPI.auth.clearTokens();
    return;
  }
  localStorage.removeItem(LS_ACCESS);
  localStorage.removeItem(LS_REFRESH);
}
