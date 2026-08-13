/**
 * 认证状态 — JWT + 当前用户
 *
 * 关键修复：
 * - hydrate 与 login 并发时，禁止用「空 token」结果把刚登录的会话清掉
 * - login/refresh 请求加超时，避免按钮一直转圈
 */
import { create } from "zustand";
import {
  clearTokens,
  getAccessTokenExpiryMs,
  isAccessTokenExpiringSoon,
  isElectronRuntime,
  loadTokens,
  peekHasSessionSync,
  peekTokensSync,
  peekUserFromAccessToken,
  peekUserSync,
  saveTokens,
  saveUserCache,
  type CachedAuthUser,
} from "../auth-storage";
import { getBackendUrl } from "../backend-config";

/** 提前多久触发后台续期（与 api ensureFresh 的 90s 对齐） */
const KEEPALIVE_SKEW_MS = 90_000;
let authKeepaliveTimer: ReturnType<typeof setTimeout> | null = null;

export interface AuthUser {
  id: number;
  username: string;
  email?: string | null;
  tier?: string;
  role?: string;
  coin_select_enabled?: boolean;
  coin_select_auto_follow?: boolean;
  coin_select_default_session?: string | null;
}

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type?: string;
  user: AuthUser;
}

interface AuthState {
  user: AuthUser | null;
  accessToken: string | null;
  refreshToken: string | null;
  hydrated: boolean;
  hydrating: boolean;
  bootstrapped: boolean;
  /** 递增：login/setSession 时 bump，过期的 hydrate 结果丢弃 */
  sessionEpoch: number;

  bootstrapSync: () => void;
  hydrate: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  setSession: (tokens: TokenResponse) => Promise<void>;
  applyRefreshedTokens: (access: string, refresh: string) => Promise<void>;
  /** 在 access 过期前主动续期，避免闲置后整页无数据 */
  armAuthKeepalive: () => void;
  stopAuthKeepalive: () => void;
}

const AUTH_TIMEOUT_MS = 20000;

function apiBase(): string {
  return getBackendUrl().replace(/\/$/, "") + "/api";
}

function abortAfter(ms: number): { signal: AbortSignal; cancel: () => void } {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), ms);
  return {
    signal: ctrl.signal,
    cancel: () => window.clearTimeout(timer),
  };
}

async function postAuth(path: string, body: Record<string, unknown>): Promise<TokenResponse> {
  const { signal, cancel } = abortAfter(AUTH_TIMEOUT_MS);
  let resp: Response;
  try {
    resp = await fetch(`${apiBase()}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    const name = err instanceof Error ? err.name : "";
    if (name === "TimeoutError" || name === "AbortError") {
      const hint = apiBase().replace(/\/api$/, "") || "http://主机:8000";
      throw new Error(`登录超时：后端忙或无响应。请确认后端已启动，地址应为 ${hint}`);
    }
    throw new Error("无法连接后端，请检查后端地址与网络");
  } finally {
    cancel();
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const err = await resp.json();
      detail = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail) || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return resp.json();
}

function syncBootstrap(): Pick<
  AuthState,
  "user" | "accessToken" | "refreshToken" | "hydrated" | "hydrating" | "bootstrapped"
> {
  if (typeof window === "undefined") {
    return {
      user: null,
      accessToken: null,
      refreshToken: null,
      hydrated: false,
      hydrating: false,
      bootstrapped: false,
    };
  }

  const tokens = peekTokensSync();
  const hasSession = peekHasSessionSync();
  const cachedUser =
    (peekUserSync() as AuthUser | null) ||
    (peekUserFromAccessToken(tokens.accessToken) as AuthUser | null);
  const electron = isElectronRuntime();

  if (cachedUser && (tokens.accessToken || tokens.refreshToken || hasSession || electron)) {
    try {
      saveUserCache(cachedUser as CachedAuthUser);
    } catch {
      /* ignore */
    }
    return {
      user: cachedUser,
      accessToken: tokens.accessToken,
      refreshToken: tokens.refreshToken,
      hydrated: false,
      hydrating: false,
      bootstrapped: true,
    };
  }

  // Electron：不得因 localStorage 空而判定未登录
  if (electron) {
    return {
      user: cachedUser,
      accessToken: tokens.accessToken,
      refreshToken: tokens.refreshToken,
      hydrated: false,
      hydrating: false,
      bootstrapped: true,
    };
  }

  if (!tokens.accessToken && !tokens.refreshToken && !hasSession) {
    return {
      user: null,
      accessToken: null,
      refreshToken: null,
      hydrated: true,
      hydrating: false,
      bootstrapped: true,
    };
  }

  return {
    user: null,
    accessToken: tokens.accessToken,
    refreshToken: tokens.refreshToken,
    hydrated: false,
    hydrating: false,
    bootstrapped: true,
  };
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  accessToken: null,
  refreshToken: null,
  hydrated: false,
  hydrating: false,
  bootstrapped: false,
  sessionEpoch: 0,

  bootstrapSync: () => {
    if (typeof window === "undefined") return;
    if (get().bootstrapped) return;
    set(syncBootstrap());
  },

  hydrate: async () => {
    if (get().hydrating) return;
    // 已有完整会话则跳过
    if (get().hydrated && get().user && get().accessToken) return;

    const epochAtStart = get().sessionEpoch;
    set({ hydrating: true, bootstrapped: true });

    const stillCurrent = () => get().sessionEpoch === epochAtStart;

    try {
      const { accessToken, refreshToken } = await loadTokens();

      // 登录已在 hydrate 途中完成 → 丢弃本次 hydrate，避免清空新会话
      if (!stillCurrent() || (get().user && get().accessToken && get().hydrated)) {
        set({ hydrating: false });
        return;
      }

      if (accessToken && refreshToken) {
        try {
          localStorage.setItem("arena_access_token", accessToken);
          localStorage.setItem("arena_refresh_token", refreshToken);
          localStorage.setItem("arena_has_session", "1");
        } catch {
          /* ignore */
        }
      }

      if (!accessToken && !refreshToken) {
        // 若用户已通过 login 写入内存会话，绝不清空
        if (get().user && get().accessToken) {
          set({ hydrated: true, hydrating: false });
          return;
        }
        set({
          hydrated: true,
          hydrating: false,
          user: null,
          accessToken: null,
          refreshToken: null,
        });
        return;
      }

      if (!stillCurrent()) {
        set({ hydrating: false });
        return;
      }

      set({ accessToken, refreshToken });

      // access 已过期/将过期：先 refresh，再 /me（避免刷新页面后僵尸会话）
      let workingAccess = accessToken;
      let workingRefresh = refreshToken;
      if (
        refreshToken &&
        (!accessToken || isAccessTokenExpiringSoon(accessToken, KEEPALIVE_SKEW_MS))
      ) {
        try {
          const tokens = await postAuth("/auth/refresh", { refresh_token: refreshToken });
          if (!stillCurrent()) {
            set({ hydrating: false });
            return;
          }
          await saveTokens(tokens.access_token, tokens.refresh_token);
          saveUserCache(tokens.user as CachedAuthUser);
          workingAccess = tokens.access_token;
          workingRefresh = tokens.refresh_token;
          set({
            accessToken: workingAccess,
            refreshToken: workingRefresh,
            user: tokens.user,
          });
        } catch {
          /* 下面仍可尝试 /me 或缓存兜底 */
        }
      }

      let user: AuthUser | null = get().user;
      if (workingAccess) {
        const meAbort = abortAfter(5000);
        try {
          const meResp = await fetch(`${apiBase()}/auth/me`, {
            headers: { Authorization: `Bearer ${workingAccess}` },
            signal: meAbort.signal,
          });
          if (meResp.ok) {
            user = await meResp.json();
          } else if (meResp.status === 401 && workingRefresh) {
            user = null;
          }
        } catch {
          /* network / timeout */
        } finally {
          meAbort.cancel();
        }
      }

      if (!stillCurrent()) {
        set({ hydrating: false });
        return;
      }

      if (!user && workingRefresh) {
        try {
          const tokens = await postAuth("/auth/refresh", { refresh_token: workingRefresh });
          if (!stillCurrent()) {
            set({ hydrating: false });
            return;
          }
          await saveTokens(tokens.access_token, tokens.refresh_token);
          saveUserCache(tokens.user as CachedAuthUser);
          set({
            accessToken: tokens.access_token,
            refreshToken: tokens.refresh_token,
            user: tokens.user,
            hydrated: true,
            hydrating: false,
          });
          get().armAuthKeepalive();
          return;
        } catch (err) {
          if (!stillCurrent()) {
            set({ hydrating: false });
            return;
          }
          const msg = err instanceof Error ? err.message : "";
          const isInvalid = /refresh|expired|revoked|401|not authenticated/i.test(msg);
          if (isInvalid) {
            // 不覆盖刚登录的会话
            if (get().user && get().sessionEpoch !== epochAtStart) {
              set({ hydrating: false });
              return;
            }
            await clearTokens();
            set({
              user: null,
              accessToken: null,
              refreshToken: null,
              hydrated: true,
              hydrating: false,
            });
          } else {
            const cached = get().user || (peekUserSync() as AuthUser | null);
            set({
              user: cached,
              accessToken: workingAccess,
              refreshToken: workingRefresh,
              hydrated: true,
              hydrating: false,
            });
            get().armAuthKeepalive();
          }
          return;
        }
      }

      if (!stillCurrent()) {
        set({ hydrating: false });
        return;
      }

      if (user) {
        saveUserCache(user as CachedAuthUser);
        set({
          user,
          accessToken: workingAccess,
          refreshToken: workingRefresh,
          hydrated: true,
          hydrating: false,
        });
        get().armAuthKeepalive();
        return;
      }

      const cached = get().user || (peekUserSync() as AuthUser | null);
      if (cached && (workingAccess || workingRefresh)) {
        set({
          user: cached,
          accessToken: workingAccess,
          refreshToken: workingRefresh,
          hydrated: true,
          hydrating: false,
        });
        get().armAuthKeepalive();
        return;
      }

      // 再次确认没有新登录
      if (get().user && get().accessToken) {
        set({ hydrated: true, hydrating: false });
        return;
      }

      await clearTokens();
      set({
        user: null,
        accessToken: null,
        refreshToken: null,
        hydrated: true,
        hydrating: false,
      });
    } catch {
      if (stillCurrent()) {
        set({ hydrated: true, hydrating: false });
      } else {
        set({ hydrating: false });
      }
    }
  },

  setSession: async (tokens) => {
    await saveTokens(tokens.access_token, tokens.refresh_token);
    saveUserCache(tokens.user as CachedAuthUser);
    set((s) => ({
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
      user: tokens.user,
      hydrated: true,
      hydrating: false,
      bootstrapped: true,
      sessionEpoch: s.sessionEpoch + 1,
    }));
    get().armAuthKeepalive();
  },

  applyRefreshedTokens: async (access, refresh) => {
    await saveTokens(access, refresh);
    set({ accessToken: access, refreshToken: refresh });
    get().armAuthKeepalive();
  },

  armAuthKeepalive: () => {
    if (typeof window === "undefined") return;
    if (authKeepaliveTimer) {
      clearTimeout(authKeepaliveTimer);
      authKeepaliveTimer = null;
    }
    const { accessToken, refreshToken, user } = get();
    if (!user || !refreshToken) return;
    const expMs = getAccessTokenExpiryMs(accessToken);
    // 无法解析过期时间时，每 10 分钟探活一次
    const delay =
      expMs == null
        ? 10 * 60_000
        : Math.max(5_000, expMs - Date.now() - KEEPALIVE_SKEW_MS);
    authKeepaliveTimer = setTimeout(() => {
      authKeepaliveTimer = null;
      void (async () => {
        try {
          // 动态导入，避免 auth ↔ api 循环依赖
          const { ensureFreshAccessToken } = await import("../api");
          await ensureFreshAccessToken();
        } catch {
          /* 下次再试 */
        }
        if (get().user && get().refreshToken) {
          get().armAuthKeepalive();
        }
      })();
    }, delay);
  },

  stopAuthKeepalive: () => {
    if (authKeepaliveTimer) {
      clearTimeout(authKeepaliveTimer);
      authKeepaliveTimer = null;
    }
  },

  login: async (username, password) => {
    const tokens = await postAuth("/auth/login", { username, password });
    await get().setSession(tokens);
  },

  register: async (username, email, password) => {
    const tokens = await postAuth("/auth/register", { username, email, password });
    await get().setSession(tokens);
  },

  logout: async () => {
    const refresh = get().refreshToken;
    get().stopAuthKeepalive();

    // 先清本地（localStorage 同步清掉），再改内存，避免 hydrate 把会话捞回来
    await clearTokens();
    set((s) => ({
      user: null,
      accessToken: null,
      refreshToken: null,
      hydrated: true,
      hydrating: false,
      bootstrapped: true,
      sessionEpoch: s.sessionEpoch + 1,
    }));

    if (refresh) {
      const { signal, cancel } = abortAfter(3000);
      void fetch(`${apiBase()}/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
        signal,
      })
        .catch(() => {})
        .finally(cancel);
    }
  },
}));

export function getAccessToken(): string | null {
  return useAuthStore.getState().accessToken;
}

export function getRefreshToken(): string | null {
  return useAuthStore.getState().refreshToken;
}
