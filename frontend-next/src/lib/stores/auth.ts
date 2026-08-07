/**
 * 认证状态 — JWT access/refresh + 当前用户
 */
import { create } from "zustand";
import { clearTokens, loadTokens, saveTokens } from "../auth-storage";
import { getBackendUrl } from "../backend-config";

export interface AuthUser {
  id: number;
  username: string;
  email?: string | null;
  tier?: string;
  role?: string; // user | admin
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

  hydrate: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  setSession: (tokens: TokenResponse) => Promise<void>;
  applyRefreshedTokens: (access: string, refresh: string) => Promise<void>;
}

function apiBase(): string {
  return getBackendUrl().replace(/\/$/, "") + "/api";
}

async function postAuth(path: string, body: Record<string, unknown>): Promise<TokenResponse> {
  const resp = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  accessToken: null,
  refreshToken: null,
  hydrated: false,
  hydrating: false,

  hydrate: async () => {
    if (get().hydrating || get().hydrated) return;
    set({ hydrating: true });
    try {
      const { accessToken, refreshToken } = await loadTokens();
      if (!accessToken && !refreshToken) {
        set({ hydrated: true, hydrating: false, user: null, accessToken: null, refreshToken: null });
        return;
      }
      set({ accessToken, refreshToken });

      // 用 /me 校验 access；失败则尝试 refresh
      let user: AuthUser | null = null;
      if (accessToken) {
        try {
          const meResp = await fetch(`${apiBase()}/auth/me`, {
            headers: { Authorization: `Bearer ${accessToken}` },
          });
          if (meResp.ok) {
            user = await meResp.json();
          }
        } catch {
          /* network */
        }
      }

      if (!user && refreshToken) {
        try {
          const tokens = await postAuth("/auth/refresh", { refresh_token: refreshToken });
          await saveTokens(tokens.access_token, tokens.refresh_token);
          set({
            accessToken: tokens.access_token,
            refreshToken: tokens.refresh_token,
            user: tokens.user,
            hydrated: true,
            hydrating: false,
          });
          return;
        } catch (err) {
          const msg = err instanceof Error ? err.message : "";
          const isInvalid = /refresh|expired|revoked|401|not authenticated/i.test(msg);
          if (isInvalid) {
            // refresh token 明确失效 → 清会话
            await clearTokens();
            set({
              user: null,
              accessToken: null,
              refreshToken: null,
              hydrated: true,
              hydrating: false,
            });
          } else {
            // 网络/后端暂时不可达 → 保留 token,本次视为未恢复,下次 hydrate 再试
            set({ hydrated: true, hydrating: false });
          }
          return;
        }
      }

      set({
        user,
        accessToken: user ? accessToken : null,
        refreshToken: user ? refreshToken : null,
        hydrated: true,
        hydrating: false,
      });
      if (!user) await clearTokens();
    } catch {
      set({ hydrated: true, hydrating: false });
    }
  },

  setSession: async (tokens) => {
    await saveTokens(tokens.access_token, tokens.refresh_token);
    set({
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
      user: tokens.user,
      hydrated: true,
    });
  },

  applyRefreshedTokens: async (access, refresh) => {
    await saveTokens(access, refresh);
    set({ accessToken: access, refreshToken: refresh });
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
    try {
      if (refresh) {
        await fetch(`${apiBase()}/auth/logout`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refresh }),
        });
      }
    } catch {
      /* ignore */
    }
    await clearTokens();
    set({ user: null, accessToken: null, refreshToken: null });
  },
}));

/** 给 api.ts 用的非 hook 读取 */
export function getAccessToken(): string | null {
  return useAuthStore.getState().accessToken;
}

export function getRefreshToken(): string | null {
  return useAuthStore.getState().refreshToken;
}
