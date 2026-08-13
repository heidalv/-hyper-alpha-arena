/**
 * Token / 用户缓存持久化
 * - Electron: token 在主进程 safeStorage（IPC）；用户资料缓存 localStorage 供瞬时 UI
 * - 浏览器: token + 用户都走 localStorage
 *
 * 注意：Electron 下「localStorage 没有 token」≠ 未登录，必须等 IPC loadTokens。
 */
const LS_ACCESS = "arena_access_token";
const LS_REFRESH = "arena_refresh_token";
const LS_USER = "arena_auth_user";
const LS_HAS_SESSION = "arena_has_session";

export interface CachedAuthUser {
  id: number;
  username: string;
  email?: string | null;
  tier?: string;
  role?: string;
  coin_select_enabled?: boolean;
  coin_select_auto_follow?: boolean;
  coin_select_default_session?: string | null;
}

export function isElectronRuntime(): boolean {
  return typeof window !== "undefined" && !!window.electronAPI?.isElectron;
}

export function peekTokensSync(): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  if (typeof window === "undefined") {
    return { accessToken: null, refreshToken: null };
  }
  try {
    return {
      accessToken: localStorage.getItem(LS_ACCESS),
      refreshToken: localStorage.getItem(LS_REFRESH),
    };
  } catch {
    return { accessToken: null, refreshToken: null };
  }
}

export function peekUserSync(): CachedAuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(LS_USER);
    if (!raw) return null;
    const u = JSON.parse(raw) as CachedAuthUser;
    if (!u || typeof u.id !== "number" || !u.username) return null;
    return u;
  } catch {
    return null;
  }
}

function decodeJwtPayload(accessToken: string): Record<string, unknown> | null {
  try {
    const parts = accessToken.split(".");
    if (parts.length < 2) return null;
    const json = atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** access JWT 过期时间（ms epoch）；无法解析则 null */
export function getAccessTokenExpiryMs(accessToken: string | null): number | null {
  if (!accessToken) return null;
  const payload = decodeJwtPayload(accessToken);
  const exp = payload?.exp;
  if (typeof exp === "number" && Number.isFinite(exp)) return exp * 1000;
  if (typeof exp === "string" && exp.trim()) {
    const n = Number(exp);
    return Number.isFinite(n) ? n * 1000 : null;
  }
  return null;
}

/** 距过期不足 skewMs（默认 90s）或已过期 → 需要续期 */
export function isAccessTokenExpiringSoon(
  accessToken: string | null,
  skewMs = 90_000,
): boolean {
  const expMs = getAccessTokenExpiryMs(accessToken);
  if (expMs == null) return false;
  return Date.now() >= expMs - skewMs;
}

export function peekUserFromAccessToken(accessToken: string | null): CachedAuthUser | null {
  if (!accessToken) return null;
  try {
    const payload = decodeJwtPayload(accessToken);
    if (!payload) return null;
    const username = String(
      payload.username || payload.preferred_username || payload.sub || "",
    ).trim();
    if (!username) return null;
    const idRaw = payload.user_id ?? payload.uid ?? payload.sub;
    const id = typeof idRaw === "number" ? idRaw : parseInt(String(idRaw || "0"), 10) || 0;
    return {
      id,
      username,
      email: (payload.email as string) || null,
      tier: (payload.tier as string) || undefined,
      role: (payload.role as string) || undefined,
    };
  } catch {
    return null;
  }
}

export function peekHasSessionSync(): boolean {
  if (typeof window === "undefined") return false;
  try {
    if (localStorage.getItem(LS_HAS_SESSION) === "1") return true;
    const { accessToken, refreshToken } = peekTokensSync();
    return !!(accessToken || refreshToken);
  } catch {
    return false;
  }
}

export function saveUserCache(user: CachedAuthUser): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(LS_USER, JSON.stringify(user));
    localStorage.setItem(LS_HAS_SESSION, "1");
  } catch {
    /* ignore */
  }
}

export function clearUserCache(): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(LS_USER);
    localStorage.removeItem(LS_HAS_SESSION);
  } catch {
    /* ignore */
  }
}

const IPC_TIMEOUT_MS = 2500;

function withTimeout<T>(promise: Promise<T>, ms: number, fallback: T): Promise<T> {
  return new Promise((resolve) => {
    let done = false;
    const timer = window.setTimeout(() => {
      if (done) return;
      done = true;
      resolve(fallback);
    }, ms);
    promise
      .then((v) => {
        if (done) return;
        done = true;
        window.clearTimeout(timer);
        resolve(v);
      })
      .catch(() => {
        if (done) return;
        done = true;
        window.clearTimeout(timer);
        resolve(fallback);
      });
  });
}

export async function loadTokens(): Promise<{
  accessToken: string | null;
  refreshToken: string | null;
}> {
  if (typeof window === "undefined") {
    return { accessToken: null, refreshToken: null };
  }
  // 先读 localStorage 镜像（登录刚写入时最快），再补 Electron 安全存储
  const mirrored = peekTokensSync();
  if (window.electronAPI?.auth) {
    const fromIpc = await withTimeout(
      window.electronAPI.auth.getTokens(),
      IPC_TIMEOUT_MS,
      { accessToken: null, refreshToken: null },
    );
    if (fromIpc.accessToken || fromIpc.refreshToken) {
      try {
        if (fromIpc.accessToken) localStorage.setItem(LS_ACCESS, fromIpc.accessToken);
        if (fromIpc.refreshToken) localStorage.setItem(LS_REFRESH, fromIpc.refreshToken);
        localStorage.setItem(LS_HAS_SESSION, "1");
      } catch {
        /* ignore */
      }
      return fromIpc;
    }
  }
  return mirrored;
}

export async function saveTokens(accessToken: string, refreshToken: string): Promise<void> {
  if (typeof window === "undefined") return;
  // 必须先写 localStorage：Electron IPC 卡住时也不能堵死登录跳转
  try {
    localStorage.setItem(LS_ACCESS, accessToken);
    localStorage.setItem(LS_REFRESH, refreshToken);
    localStorage.setItem(LS_HAS_SESSION, "1");
  } catch {
    /* ignore */
  }
  if (window.electronAPI?.auth) {
    await withTimeout(
      window.electronAPI.auth.setTokens(accessToken, refreshToken),
      IPC_TIMEOUT_MS,
      { ok: false, error: "ipc_timeout" },
    );
  }
}

export async function clearTokens(): Promise<void> {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(LS_ACCESS);
    localStorage.removeItem(LS_REFRESH);
  } catch {
    /* ignore */
  }
  clearUserCache();
  // Electron IPC 后台清，不阻塞退出跳转
  if (window.electronAPI?.auth) {
    void withTimeout(window.electronAPI.auth.clearTokens(), IPC_TIMEOUT_MS, { ok: false });
  }
}
