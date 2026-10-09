/**
 * API 客户端 — 与后端 FastAPI (:8000) 通信
 * 开发:可走 Next rewrites 或绝对 URL；生产 Electron: NEXT_PUBLIC_API_URL
 * 鉴权: Authorization Bearer + 401 时自动 refresh 一次
 *
 * 闲置卡死修复：
 * - access 默认 15 分钟过期；请求前主动续期
 * - 401 重试用全新 AbortSignal，避免原请求超时信号误杀重试
 * - refresh 自带超时；网络失败保留会话并派发降级事件
 */

import { getBackendUrl } from "./backend-config";
import { isAccessTokenExpiringSoon } from "./auth-storage";
import {
  getAccessToken,
  getRefreshToken,
  useAuthStore,
} from "./stores/auth";

const CLIENT_VERSION = process.env.NEXT_PUBLIC_VERSION || "0.0.0";
const REFRESH_TIMEOUT_MS = 12_000;

export const AUTH_REFRESHED_EVENT = "arena-auth-refreshed";
export const AUTH_DEGRADED_EVENT = "arena-auth-degraded";

function apiBase(): string {
  return getBackendUrl().replace(/\/$/, "") + "/api";
}

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

const AUTH_WHITELIST = [
  "/auth/login",
  "/auth/register",
  "/auth/refresh",
  "/auth/logout",
];

function isAuthWhitelisted(endpoint: string): boolean {
  const path = endpoint.split("?")[0];
  return AUTH_WHITELIST.some((p) => path === p || path.startsWith(p + "/"));
}

let refreshInFlight: Promise<"ok" | "invalid" | "network"> | null = null;

function dispatchAuthEvent(name: string, detail?: Record<string, unknown>) {
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  } catch {
    /* ignore */
  }
}

async function tryRefreshAccessToken(): Promise<"ok" | "invalid" | "network"> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    const refresh = getRefreshToken();
    if (!refresh) return "invalid";
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), REFRESH_TIMEOUT_MS);
    try {
      const resp = await fetch(`${apiBase()}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
        signal: ctrl.signal,
      });
      if (!resp.ok) {
        // [2026-09-01] 无效 refresh token（被轮换/过期/撤销）→ 立即本地登出：
        // 旧实现只 return "invalid"，keepalive 回调检查 user&&refreshToken 仍为真
        // → 每 5s 重新武装 → POST /auth/refresh 401 无限循环（实测 964 次/小时，
        // 拖慢同页所有请求的 ensureFreshAccessToken 前置）。logout 会
        // clearTokens + stopAuthKeepalive，终止循环并引导重新登录。
        // [2026-09-09] 改为 await：原先 `void logout()` 不等清理完成就返回，
        // 调用方（keepalive）紧接着检查 get().refreshToken 时可能仍为真 → 重新武装，
        // 实测 401 循环依旧存在（3 小时 210 次）。等待登出落地可彻底关闭竞态。
        await useAuthStore.getState().logout();
        return "invalid";
      }
      const data = await resp.json();
      await useAuthStore.getState().applyRefreshedTokens(
        data.access_token,
        data.refresh_token,
      );
      if (data.user) {
        useAuthStore.setState({ user: data.user, hydrated: true, hydrating: false });
      }
      dispatchAuthEvent(AUTH_REFRESHED_EVENT);
      useAuthStore.getState().armAuthKeepalive();
      return "ok";
    } catch {
      // 网络错误 / 超时 ≠ token 失效
      dispatchAuthEvent(AUTH_DEGRADED_EVENT, { reason: "refresh_network" });
      return "network";
    } finally {
      clearTimeout(timer);
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

/** 请求前：若 access 将过期则先续期 */
export async function ensureFreshAccessToken(): Promise<boolean> {
  const access = getAccessToken();
  if (!access) return !!getRefreshToken();
  if (!isAccessTokenExpiringSoon(access, 90_000)) return true;
  const status = await tryRefreshAccessToken();
  return status === "ok";
}

export async function apiRequest<T = any>(
  endpoint: string,
  options?: RequestInit & { timeout?: number; skipAuth?: boolean }
): Promise<T> {
  const url = `${apiBase()}${endpoint}`;
  const timeout = options?.timeout ?? 30000;
  const skipAuth = options?.skipAuth || isAuthWhitelisted(endpoint);

  const buildHeaders = (token: string | null): HeadersInit => {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-Client-Version": CLIENT_VERSION,
      ...(options?.headers as Record<string, string> | undefined),
    };
    if (!skipAuth && token) {
      headers.Authorization = `Bearer ${token}`;
    }
    return headers;
  };

  const doFetch = async (token: string | null, ms: number): Promise<Response> => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ms);
    try {
      return await fetch(url, {
        ...options,
        signal: controller.signal,
        headers: buildHeaders(token),
      });
    } catch (e) {
      // ⚠️ [F329 2026-09-21] 超时/中断必须包成 ApiError，不能把 DOMException 原样漏出去。
      //
      // 现场：HFT 面板「选币评分」「硬性拒绝」两张卡片都显示
      //     `刷新失败: signal is aborted without reason`
      // 那是 `AbortController.abort()` 在**无参数**调用时，浏览器给出的
      // DOMException.message 默认文案 —— 对用户完全无意义（看不出是超时、
      // 也不知道该等还是该重试）。
      //
      // 根因链：`/hft/universe/score` 实测 **119.5s**（见 H192 profiling），
      // 而本函数默认 timeout 只有 90s ⇒ 必然 abort。
      // 调用方（`useHftResource`）只做 `e.message` 透传，所以漏到了界面上。
      if (e instanceof DOMException && e.name === "AbortError") {
        throw new ApiError(
          0,
          `请求超时（${Math.round(ms / 1000)}s）— 该接口较慢，稍后会自动重试`
        );
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }
  };

  if (!skipAuth) {
    await ensureFreshAccessToken();
  }

  let resp = await doFetch(getAccessToken(), timeout);

  if (resp.status === 401 && !skipAuth) {
    const status = await tryRefreshAccessToken();
    if (status === "ok") {
      resp = await doFetch(getAccessToken(), timeout);
    } else if (status === "invalid") {
      await useAuthStore.getState().logout();
    } else {
      throw new ApiError(0, "后端暂不可达，登录态已保留，请稍后重试或刷新");
    }
  }

  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const err = await resp.json();
      detail =
        typeof err.detail === "string"
          ? err.detail
          : err.message || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }

  const ct = resp.headers.get("content-type") || "";
  if (!ct.includes("application/json")) {
    throw new ApiError(0, "Response is not JSON");
  }

  return resp.json() as Promise<T>;
}

/**
 * 公共端点直连：走运行时配置的后端地址（getBackendUrl），不挂鉴权、不抛业务错误。
 *
 * 桌面端 / 远端（Tailscale）必须用本函数或 apiRequest——裸 fetch("/api/...")
 * 相对路径会打到本地静态壳自身（Next 静态导出无 rewrites），导致头部行情条、
 * 因子页等「一直是空的」。语义与旧裸 fetch(...).then(r=>r.json()) 对齐：
 * 非 JSON / 网络失败抛错，HTTP 错误状态只返回体不抛。
 */
export async function fetchPublic<T = any>(
  endpoint: string,
  init?: RequestInit & { timeout?: number }
): Promise<T> {
  const { timeout, ...rest } = init ?? {};
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout ?? 15000);
  try {
    const resp = await fetch(`${apiBase()}${endpoint}`, {
      ...rest,
      signal: ctrl.signal,
      headers: { "X-Client-Version": CLIENT_VERSION, ...(rest.headers as Record<string, string> | undefined) },
    });
    return (await resp.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

// ═══ 类型定义 ═══
// ═══ 类型定义（已迁至 src/types/api.ts，此处保持重导出兼容） ═══

import type {
  Account, PaperBalance, Position, PaperOrder, PaperSummary, PaperDashboard, SessionStatus,
  TierInfo, TierStatus, TierActivityItem, TierActivity, StrategyRecord,
  LiveBalance, LivePosition, LiveOrder, LiveOrderResult, AsterLedgerResponse,
} from "@/types/api";

export type {
  Account, PaperBalance, Position, PaperOrder, PaperSummary, PaperDashboard, SessionStatus,
  TierInfo, TierStatus, TierActivityItem, TierActivity, StrategyRecord,
  LiveBalance, LivePosition, LiveOrder, LiveOrderResult, AsterLedgerResponse,
};
// ═══ 模拟交易 API（对齐旧前端 PaperTradingPanel） ═══

// 余额
export const paperApi = {
  getBalance: (accountId: number) =>
    apiRequest<PaperBalance>(`/paper/balance/${accountId}`),

  // 持仓（open + closed）
  getPositions: (accountId: number, status?: "open" | "closed") =>
    apiRequest<Position[]>(`/paper/positions/${accountId}${status ? `?status=${status}` : ""}`),

  // 订单历史
  getOrders: (accountId: number, limit: number = 50) =>
    apiRequest<PaperOrder[]>(`/paper/orders/${accountId}?limit=${limit}`),

  // 统计摘要
  getSummary: (accountId: number) =>
    apiRequest<PaperSummary>(`/paper/summary/${accountId}`),

  /**
   * 一屏数据（余额/持仓/订单/统计）——**单次请求**。
   * [2026-09-18 前端刷新慢治理] 后端 `/api/paper/dashboard/{id}` 四节调用与单端点
   * **同一份 helper**（口径一致，有对拍测试）。前端由 `useTradingData` 的单一轮询器
   * 每 5s 调它一次，替代原先 4 个端点各 5s 轮询。
   * 未初始化账户：`balance` 为 null 且带 `warnings`（不是 4xx）。
   */
  getDashboard: (accountId: number, status: "open" | "closed" = "open") =>
    apiRequest<PaperDashboard>(`/paper/dashboard/${accountId}?status=${status}`),

  // 初始化账户
  initialize: (accountId: number, initialBalance: number) =>
    apiRequest<any>(`/paper/initialize`, { method: "POST", body: JSON.stringify({ account_id: accountId, initial_balance: initialBalance }) }),

  // 重置余额（软重置，保留持仓）
  resetBalance: (accountId: number) =>
    apiRequest<any>(`/paper/reset-balance/${accountId}`, { method: "POST" }),

  // 硬重置（清空一切）
  reset: (accountId: number) =>
    apiRequest<any>(`/paper/reset/${accountId}`, { method: "POST" }),

  // 设置初始余额
  // [2026-10-03 用户口径] 默认 resetPositions=true ⇒ 改金额**连带重置**（清持仓/订单）；
  // 后端 `set_initial_balance(reset_positions=...)` 的默认值也是 True。
  setBalance: (accountId: number, balance: number, resetPositions: boolean = true) =>
    apiRequest<any>(`/paper/set-balance`, {
      method: "POST",
      body: JSON.stringify({ account_id: accountId, initial_balance: balance, reset_positions: resetPositions }),
    }),

  // 平仓
  closePosition: (accountId: number, symbol: string, side: string, quantity?: number) =>
    apiRequest<any>(`/paper/close`, { method: "POST", body: JSON.stringify({ account_id: accountId, symbol, side, quantity }) }),

  // 手动下单
  placeOrder: (data: { account_id: number; symbol: string; side: string; quantity: number; leverage?: number; tp_price?: number; sl_price?: number }) =>
    apiRequest<any>(`/paper/order`, { method: "POST", body: JSON.stringify(data) }),

  // [2026-10-03 用户需求] 一键平仓：平掉该账户全部 open 持仓（服务端逐笔走 close_position）
  closeAll: (accountId: number) =>
    apiRequest<{
      ok: boolean; closed_count: number; failed_count: number; target_count: number;
      closed: { position_id: number; symbol: string; side: string }[];
      failed: { position_id: number; symbol: string; side: string; error: string }[];
    }>(`/paper/close-all/${accountId}`, { method: "POST" }),

  // 完整重置（清空一切）
  fullReset: (accountId: number) =>
    apiRequest<any>(`/paper/reset/${accountId}`, { method: "POST" }),

  // 权益曲线（与仪表盘数字同源）
  getEquityCurve: (accountId: number, period: "7d" | "30d" | "all" = "7d") =>
    apiRequest<{
      account_id: number;
      period: string;
      source: string;
      current_equity: number;
      initial_balance: number;
      points: { time: number; value: number }[];
    }>(`/paper/equity-curve/${accountId}?period=${period}`),

  // [2026-09-23 新方法] 已实现权益事件溯源序列（权威口径 + 末点对账 + 保极值降采样）
  getEquitySeries: (accountId: number, period: "7d" | "30d" | "90d" | "all" = "30d") =>
    apiRequest<{
      account_id: number;
      period: string;
      source: string;
      timezone: string;
      initial_balance: number;
      start_equity: number;
      realized_end: number;
      floating_now: number;
      equity_total_now: number;
      balance_total_equity: number;
      peak_equity: number;
      peak_at: number | null;
      max_drawdown_usd: number;
      max_drawdown_pct: number;
      counts: { closed_in_window: number; closed_before_window: number; open_now: number };
      costs_in_window: { fees: number; funding_net: number };
      points: { t: number; v: number }[];
      reconcile: {
        realized_end: number;
        floating_now: number;
        realized_plus_floating: number;
        balance_total_equity: number;
        diff: number;
        ok: boolean;
      };
    }>(`/paper/equity-series/${accountId}?period=${period}`),
};

// ═══ 实盘交易 ═══
export const liveApi = {
  getAccounts: () => apiRequest<{ accounts: Account[] }>("/live/accounts"),
  getBalance: (accountId: number) => apiRequest<LiveBalance>(`/live/balance/${accountId}`),
  setMarginType: (accountId: number, symbol: string, margin_type: string) =>
    apiRequest<any>(`/live/margin-type/${accountId}`, {
      method: "POST",
      body: JSON.stringify({ symbol, margin_type }),
    }),
  getPositions: (accountId: number) => apiRequest<{ positions: LivePosition[] }>(`/live/positions/${accountId}`),
  getOrders: (accountId: number) => apiRequest<{ orders: LiveOrder[] }>(`/live/orders/${accountId}`),
  // [2026-09-03] 路径沿用，但返回体已重做为「真实收入账本」（Stage 6 积分已结束）
  getAsterdexPoints: (accountId: number, days = 7) =>
    apiRequest<AsterLedgerResponse>(`/live/asterdex/points/${accountId}?days=${days}`),
  placeOrder: (data: Record<string, unknown>) =>
    apiRequest<LiveOrderResult>("/live/order", { method: "POST", body: JSON.stringify(data) }),
  closePosition: (data: Record<string, unknown>) =>
    apiRequest<LiveOrderResult>("/live/close", { method: "POST", body: JSON.stringify(data) }),
};

// ═══ 账户管理 ═══

export const accountApi = {
  list: () => apiRequest<Account[]>("/account/list"),
  create: (data: Partial<Account>) =>
    apiRequest<Account>("/account/", { method: "POST", body: JSON.stringify(data) }),
  update: (id: number, data: Partial<Account>) =>
    apiRequest<Account>(`/account/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  delete: (id: number, opts?: { hard?: boolean }) =>
    apiRequest<any>(`/account/${id}${opts?.hard ? "?hard=true" : ""}`, { method: "DELETE" }),
};

// [2026-08-28] Socks5 代理配置(交易所API IP白名单出口; 设置页维护, 凭证表单下拉选择)
export const proxyConfigApi = {
  list: () => apiRequest<any[]>("/exchange/proxy-configs"),
  create: (data: { name: string; proxy_url: string; note?: string }) =>
    apiRequest<any>("/exchange/proxy-configs", { method: "POST", body: JSON.stringify(data) }),
  remove: (id: number) =>
    apiRequest<any>(`/exchange/proxy-configs/${id}`, { method: "DELETE" }),
  test: (id: number) =>
    apiRequest<any>(`/exchange/proxy-configs/${id}/test`, { method: "POST" }),
};

// ═══ AI 会话 ═══

export const sessionApi = {
  list: () => apiRequest<SessionStatus[]>("/full-auto/sessions"),
  start: (data: {
    account_id: number;
    paper_account_id?: number;
    symbols: string[];
    trading_mode?: string;
    risk_level?: string;
    risk_mode?: string;
    active_exchange?: string;
    auto_coin_enabled?: boolean;
  }) =>
    apiRequest<any>("/full-auto/start", { method: "POST", body: JSON.stringify(data) }),
  stop: (sessionId: string) =>
    apiRequest<any>(`/full-auto/stop/${sessionId}`, { method: "POST" }),
  delete: (sessionId: string) =>
    apiRequest<any>(`/full-auto/${sessionId}`, { method: "DELETE" }),
  pause: (sessionId: string) =>
    apiRequest<any>(`/full-auto/pause/${sessionId}`, { method: "POST" }),
  resume: (sessionId: string) =>
    apiRequest<any>(`/full-auto/resume/${sessionId}`, { method: "POST" }),
  // [2026-10-03 用户需求] 一键停止全部自动交易：停掉所有 running/defensive/paused 会话，
  // 并把所有账户的 auto_trading_enabled 置 false（统一总控用）。
  stopAll: (alsoDisableAccounts: boolean = true) =>
    apiRequest<{ success: boolean; stopped_sessions: string[]; accounts_disabled: number[]; failed: any[] }>(
      `/full-auto/stop-all?also_disable_accounts=${alsoDisableAccounts ? "true" : "false"}`,
      { method: "POST" },
    ),
  status: (sessionId: string) =>
    apiRequest<any>(`/full-auto/status/${sessionId}`),
  healthCheck: (sessionId: string) =>
    apiRequest<any>(`/full-auto/health-check/${sessionId}`, { method: "POST" }),
  addSymbols: (sessionId: string, symbols: string[], tier?: string) =>
    apiRequest<any>(`/full-auto/add-symbols/${sessionId}`, {
      method: "POST",
      body: JSON.stringify({ symbols, ...(tier ? { tier } : {}) }),
    }),
  removeSymbols: (sessionId: string, symbols: string[]) =>
    apiRequest<any>(`/full-auto/remove-symbols/${sessionId}`, { method: "POST", body: JSON.stringify({ symbols }) }),
  setFixedSymbolsByTier: (
    sessionId: string,
    data: { short?: string[]; mid?: string[]; long?: string[] },
  ) =>
    apiRequest<any>(`/full-auto/fixed-symbols-by-tier/${sessionId}`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  tierStatus: (sessionId: string) =>
    apiRequest<any>(`/full-auto/tier-status/${sessionId}`),
  getStatus: (sessionId: string) =>
    apiRequest<any>(`/full-auto/status/${sessionId}`),
  updateConfig: (sessionId: string, data: {
    risk_level?: string;
    risk_mode?: string;
    max_concurrent_strategies?: number;
    max_total_drawdown_pct?: number;
    daily_loss_limit_pct?: number;
    active_exchange?: string;
    auto_coin_max_slots?: number;
    auto_coin_mid_enabled?: boolean;
    auto_coin_mid_max_slots?: number;
  }) =>
    apiRequest<any>(`/full-auto/update-config/${sessionId}`, { method: "POST", body: JSON.stringify(data) }),
  enableAutoCoinMid: (sessionId: string) =>
    apiRequest<any>(`/full-auto/auto-coin-mid/${sessionId}/enable`, { method: "POST" }),
  disableAutoCoinMid: (sessionId: string) =>
    apiRequest<any>(`/full-auto/auto-coin-mid/${sessionId}/disable`, { method: "POST" }),
};

// ═══ 全自动会话：tier 状态/活动/策略（R4 新增，供 dashboard 使用） ═══

export const fullAutoApi = {
  tierStatus: (sessionId: string) =>
    apiRequest<TierStatus>(`/full-auto/tier-status/${sessionId}`),

  tierActivity: (sessionId: string) =>
    apiRequest<TierActivity>(`/full-auto/tier-activity/${sessionId}`),

  strategies: (accountId: number) =>
    apiRequest<StrategyRecord[]>(`/strategies?account_id=${accountId}&status=active`),
};

// ═══ AI 决策 ═══

export const decisionApi = {
  // 对齐旧前端: /arena/model-chat 返回 {entries:[]}
  list: (accountId?: number, limit: number = 20) =>
    apiRequest<{ entries: any[]; generated_at?: string }>(
      `/arena/model-chat?limit=${limit}${accountId ? `&account_id=${accountId}` : ""}`
    ),
  // ATAS 决策
  atasDecisions: (limit: number = 20) =>
    apiRequest<{ decisions: any[]; count: number }>(`/atas/decisions?limit=${limit}`),
};

// ═══ 仪表盘 ═══

export const dashboardApi = {
  overview: () => apiRequest<any>("/account/overview"),
  assetCurve: () => apiRequest<any[]>("/account/asset-curve/timeframe"),
  overviewPost: (selections: any[]) =>
    apiRequest<any>("/dashboard/overview", { method: "POST", body: JSON.stringify({ selections }) }),
};

// ═══ 策略配置 ═══
// [2026-09-17] scalpConfigApi 已随短线车道停用移除（后端 /api/scalp-config 同步下架）。

export const strategyConfigApi = {
  get: (tier: "mid" | "long") => apiRequest<any>(`/strategy-config/${tier}`),
  update: (tier: "mid" | "long", updates: Record<string, any>) =>
    apiRequest<any>(`/strategy-config/${tier}`, { method: "PUT", body: JSON.stringify({ updates }) }),
  presets: (tier: "mid" | "long") => apiRequest<any>(`/strategy-config/${tier}/presets`),
};

export const promptApi = {
  get: (tier: "mid" | "long") => apiRequest<any>(`/strategy-prompt/${tier}`),
  update: (tier: "mid" | "long", data: { task_id: string; system_prompt: string; task_prompt: string }) =>
    apiRequest<any>(`/strategy-prompt/${tier}`, { method: "PUT", body: JSON.stringify(data) }),
  test: (tier: "mid" | "long", data: any) =>
    apiRequest<any>(`/strategy-prompt/${tier}/test`, { method: "POST", body: JSON.stringify(data) }),
  reset: (tier: "mid" | "long", taskId: string) =>
    apiRequest<any>(`/strategy-prompt/${tier}/reset`, { method: "POST", body: JSON.stringify({ task_id: taskId }) }),
};

/** VIP 共用 AI 选币看板 */
export const factorsLabApi = {
  status: () => apiRequest<any>("/factors-lab/status"),
  config: () => apiRequest<any>("/factors-lab/config"),
  library: (limit = 100) => apiRequest<any>(`/factors-lab/library?limit=${limit}`),
  report: () => apiRequest<any>("/factors-lab/report"),
  diversity: () => apiRequest<any>("/factors-lab/diversity"),
  calibrate: () => apiRequest<any>("/factors-lab/calibrate", { method: "POST" }),
  runRound: (scan = true) =>
    apiRequest<any>("/factors-lab/round", {
      method: "POST",
      body: JSON.stringify({ scan }),
    }),
  scanArxiv: () => apiRequest<any>("/factors-lab/scan", { method: "POST" }),
  feed: (body: { title: string; abstract: string; source?: string; url?: string }) =>
    apiRequest<any>("/factors-lab/feed", { method: "POST", body: JSON.stringify(body) }),
  unifiedReport: () => apiRequest<any>("/factors-lab/unified-strategy/report"),
  unifiedRun: () => apiRequest<any>("/factors-lab/unified-strategy/run", { method: "POST" }),
};
export const coinSelectApi = {
  settings: () => apiRequest<any>("/coin-select/settings"),
  patchSettings: (body: Record<string, unknown>) =>
    apiRequest<any>("/coin-select/settings", { method: "PATCH", body: JSON.stringify(body) }),
  board: (horizon?: "mid" | "long", opts?: {
    min_score?: number;
    max_trap?: number;
    verdict?: string;
    min_liquidity?: number;
    sort_by?: string;
  }) => {
    const q = new URLSearchParams();
    if (horizon) q.set("horizon", horizon);
    if (opts?.min_score != null) q.set("min_score", String(opts.min_score));
    if (opts?.max_trap != null) q.set("max_trap", String(opts.max_trap));
    if (opts?.verdict) q.set("verdict", opts.verdict);
    if (opts?.min_liquidity != null) q.set("min_liquidity", String(opts.min_liquidity));
    if (opts?.sort_by) q.set("sort_by", opts.sort_by);
    const qs = q.toString();
    return apiRequest<any>(`/coin-select/board${qs ? `?${qs}` : ""}`);
  },
  sessions: () => apiRequest<{ sessions: any[]; hint?: string | null }>("/coin-select/sessions"),
  // [2026-09-18] 当前 AI 池全景（看板页与会话页共用的同一份账）
  aiPools: () => apiRequest<any>("/coin-select/ai-pools"),
  adopt: (body: { symbol: string; horizon: string; session_id: string; candidate_id?: number }) =>
    apiRequest<any>("/coin-select/adopt", { method: "POST", body: JSON.stringify(body) }),
  scanNow: () => apiRequest<any>("/coin-select/scan-now", { method: "POST", timeout: 180000 }),
  adminDetail: () => apiRequest<any>("/coin-select/admin/detail"),
  delist: (candidate_id: number, listed: boolean) =>
    apiRequest<any>("/coin-select/admin/delist", {
      method: "POST",
      body: JSON.stringify({ candidate_id, listed }),
    }),
};

// ═══ 市场数据 ═══

export const marketApi = {
  intelOverview: (symbols: string[]) => apiRequest<any>(`/market-intel/overview?symbols=${symbols.join(",")}`),
  intelHealth: () => apiRequest<any>("/market-intel/data-health"),
  intelWatchlist: () => apiRequest<any>("/market-intel/watchlist"),
  overviewAll: (exchange?: string) =>
    apiRequest<any>(`/market/overview/all${exchange ? `?exchange=${exchange}` : ""}`),
  klines: (symbol: string, period: string, count: number = 300, market?: string, end?: number) => {
    const qs = new URLSearchParams({
      symbol,
      period,
      count: String(count),
      purpose: "research",
    });
    if (market) qs.set("market", market);
    if (end && end > 0) qs.set("end", String(end));
    return apiRequest<{ data: any[] }>(`/market/klines?${qs.toString()}`);
  },
};

// ═══ 信号 ═══

export const signalApi = {
  // 对齐后端: /atas/signals 返回 {signals:[], count}
  list: (limit: number = 30) =>
    apiRequest<{ signals: any[]; count: number; error?: string }>(`/atas/signals?limit=${limit}`),
};

// ═══ 健康 ═══

export const healthApi = {
  check: () => apiRequest<any>("/health", { timeout: 5000 }),
};

// ═══ 设置/配置 ═══

export const configApi = {
  // LLM 配置
  llmList: () => apiRequest<{ total: number; items: any[] }>("/llm-configs"),
  llmListAll: () => apiRequest<any>("/llm-configs/all"),
  llmProviders: () => apiRequest<any>("/llm-configs/providers"),
  llmUsages: () => apiRequest<any>("/llm-configs/usages"),
  llmSetDefault: (id: number) => apiRequest<any>(`/llm-configs/${id}/set-default`, { method: "POST" }),
  llmCreate: (data: any) => apiRequest<any>("/llm-configs", { method: "POST", body: JSON.stringify(data) }),
  llmUpdate: (id: number, data: any) => apiRequest<any>(`/llm-configs/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  llmDelete: (id: number, force = false) => apiRequest<any>(`/llm-configs/${id}?force=${force}`, { method: "DELETE" }),
  llmTest: (data: any) => apiRequest<any>("/account/test-llm", { method: "POST", body: JSON.stringify(data) }),
  llmConsolidate: () => apiRequest<any>("/llm-configs/consolidate-deepseek", { method: "POST" }),

  // 交易对
  tradingPairs: () => apiRequest<any>("/config/trading-pairs"),
  saveTradingPairs: (symbols: string[]) =>
    apiRequest<any>("/config/trading-pairs", { method: "PUT", body: JSON.stringify({ symbols }) }),
  refreshExchangeSymbols: () =>
    apiRequest<any>("/config/trading-pairs/refresh-exchange", { method: "POST" }),

  // 必需配置检查
  checkRequired: () => apiRequest<any>("/config/check-required"),

  // 全局默认交易所（新建账户表单默认值）
  defaultExchange: () => apiRequest<any>("/config/default-exchange"),

  // 外部 API 密钥
  externalKeys: () => apiRequest<any>("/config/external-keys"),
  saveExternalKey: (key: string, value: string) =>
    apiRequest<any>("/config/external-keys", { method: "POST", body: JSON.stringify({ key, value }) }),

  // 交易门禁
  tradingGates: () => apiRequest<any>("/config/trading-gates"),
  saveTradingGates: (gates: any) =>
    apiRequest<any>("/config/trading-gates", { method: "PUT", body: JSON.stringify(gates) }),

  // margin 模式
  marginMode: () => apiRequest<any>("/config/margin-mode"),
  saveMarginMode: (mode: string) =>
    apiRequest<any>("/config/margin-mode", { method: "PUT", body: JSON.stringify({ mode }) }),

  // 人格预设
  personalityPresets: () => apiRequest<any[]>("/account/personality-presets"),

  // 全局采样
  globalSampling: () => apiRequest<any>("/config/global-sampling"),
  saveGlobalSampling: (data: any) =>
    apiRequest<any>("/config/global-sampling", { method: "PUT", body: JSON.stringify(data) }),
};

// ═══ 兼容旧导出（hooks/useTradingData 使用） ═══

export const api = {
  // 账户
  getAccounts: () => accountApi.list(),

  // 持仓（兼容旧 hook 调用）
  getPositions: (accountId: number) => paperApi.getPositions(accountId),

  // 会话
  getSessions: () => sessionApi.list(),
  startSession: (data: { account_id: number; paper_account_id?: number; symbols: string[]; trading_mode?: string; risk_level?: string; risk_mode?: string; active_exchange?: string; auto_coin_enabled?: boolean }) => sessionApi.start(data),
  stopSession: (sessionId: string) => sessionApi.stop(sessionId),
  pauseSession: (sessionId: string) => sessionApi.pause(sessionId),
  resumeSession: (sessionId: string) => sessionApi.resume(sessionId),

  // 仪表盘
  getDashboard: () => dashboardApi.overview(),
  getAssetCurve: () => dashboardApi.assetCurve(),

  // AI 决策 — 返回 {entries:[]}
  getAiDecisions: (accountId: number, limit: number = 20) => decisionApi.list(accountId, limit),
  // 信号 — 返回 {signals:[]}
  getScalpSignals: (limit: number = 20) => signalApi.list(limit),

  // 策略配置
  getStrategyConfig: (tier: "mid" | "long") => strategyConfigApi.get(tier),
  updateStrategyConfig: (tier: "mid" | "long", updates: Record<string, any>) => strategyConfigApi.update(tier, updates),

  // 提示词
  getPrompts: (tier: "mid" | "long") => promptApi.get(tier),
  updatePrompt: (tier: "mid" | "long", data: { task_id: string; system_prompt: string; task_prompt: string }) => promptApi.update(tier, data),
  testPrompt: (tier: "mid" | "long", data: any) => promptApi.test(tier, data),

  // 市场
  getMarketOverview: (symbols: string[]) => marketApi.intelOverview(symbols),
  getMarketHealth: () => marketApi.intelHealth(),
  getWatchlist: () => marketApi.intelWatchlist(),
  getKlines: (symbol: string, period: string, count?: number, market?: string, end?: number) =>
    marketApi.klines(symbol, period, count ?? 300, market, end),
  getMarketOverviewAll: (exchange?: string) => marketApi.overviewAll(exchange),

  // 健康
  getHealth: () => healthApi.check(),
};
