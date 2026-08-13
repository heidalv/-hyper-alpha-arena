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
      if (!resp.ok) return "invalid";
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

// ═══ 类型定义 ═══

export interface Account {
  id: number;
  name: string;
  account_type: string;
  current_cash: number;
  frozen_cash: number;
  initial_capital: number;
  is_active: boolean;
  auto_trading_enabled: boolean;
  trading_mode: string;
  selected_exchange: string;
  llm_config_id_deep?: number | null;
  exchange?: string;
  keys_configured?: boolean;
  llm_config_name?: string;
  llm_config_name_deep?: string;
  llm_config_id?: number | null;
  model?: string;
  base_url?: string;
  api_key_set?: boolean;
  has_mainnet_wallet?: boolean;
  wallet_address?: string;
}

export interface PaperBalance {
  account_id: number;
  initial_balance: number;
  total_equity: number;
  available_balance?: number;
  available_cash?: number;
  frozen_margin?: number;
  used_margin?: number;
  unrealized_pnl: number;
  realized_pnl: number;
  total_pnl: number;
  total_fee_paid?: number;
  return_pct: number;
}

export interface Position {
  id: number;
  account_id: number;
  symbol: string;
  side: string;
  entry_price: number;
  mark_price?: number;
  quantity: number;
  filled_quantity?: number;
  leverage: number;
  unrealized_pnl: number;
  margin: number;
  trade_nature: string;
  status: string;
  opened_at: string;
  closed_at?: string;
  close_reason?: string;
  tp_price?: number;
  sl_price?: number;
  pnl_pct?: number;
  strategy_id?: string;
  fee?: number;
  health_score?: number;
  add_count?: number;
  // 持仓时限（tier 复审点 + AI 可延长），见 backend/services/position_hold_time.py
  hold_age_hours?: number | null;
  max_hold_hours?: number | null;
  hold_remaining_hours?: number | null;
  hold_progress_pct?: number | null;
  hold_expired?: boolean;
  hold_near_timeout?: boolean;
  hold_ai_extended?: boolean;
  hold_ai_reviewable?: boolean;
  review_hold_hours?: number | null;
  absolute_cap_hours?: number | null;
  extendable_hours?: number | null;
  extend_step_hours_min?: number | null;
  extend_step_hours_max?: number | null;
}

export interface PaperOrder {
  id: number;
  account_id: number;
  symbol: string;
  side: string;
  order_type: string;
  status: string;
  price: number;
  quantity: number;
  filled_price?: number;
  filled_quantity?: number;
  leverage?: number;
  pnl?: number;
  fee?: number;
  close_reason?: string;
  trade_nature?: string;
  strategy_id?: string;
  created_at: string;
  filled_at?: string;
  entry_price?: number;
}

export interface PaperSummary {
  total_trades: number;
  total_orders?: number;
  total_closes?: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  realized_pnl?: number;
  total_fees?: number;
  return_pct: number;
  profit_factor: number;
  max_drawdown_pct?: number;
  avg_win?: number;
  avg_loss?: number;
  open_positions?: number;
  open_losing?: number;
  open_winning?: number;
}

export interface SessionStatus {
  session_id: string;
  status: string;
  symbols: string[];
  active_count: number;
  trading_mode: string;
  account_id?: number;
  account_name?: string;
  paper_account_id?: number;
  paper_account_name?: string;
  trading_account_id?: number;
  total_pnl?: number;
  total_trades?: number;
  win_rate?: number;
  started_at?: string;
  stopped_at?: string;
  // 2026-07-20：补齐卡片展示与编辑所需字段
  auto_coin_enabled?: boolean;
  auto_coin_symbols?: string[];
  auto_coin_max_slots?: number; // 5~10，短线 AI，默认 5
  auto_coin_mid_enabled?: boolean;
  auto_coin_mid_max_slots?: number; // 1~5，中线 AI，默认 3
  auto_coin_mid_symbols?: string[];
  fixed_symbols_by_tier?: { short?: string[]; mid?: string[]; long?: string[] };
  backup_pool?: string[];
  risk_level?: string;
  risk_mode?: string;
  active_exchange?: string;
  arb_enabled?: boolean;
  paper_account_mode?: string;
  max_concurrent_strategies?: number;
  max_total_drawdown_pct?: number;
  daily_loss_limit_pct?: number;
  total_strategies_created?: number;
  created_at?: string;
}

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
  setBalance: (accountId: number, balance: number) =>
    apiRequest<any>(`/paper/set-balance`, { method: "POST", body: JSON.stringify({ account_id: accountId, initial_balance: balance }) }),

  // 平仓
  closePosition: (accountId: number, symbol: string, side: string, quantity?: number) =>
    apiRequest<any>(`/paper/close`, { method: "POST", body: JSON.stringify({ account_id: accountId, symbol, side, quantity }) }),

  // 手动下单
  placeOrder: (data: { account_id: number; symbol: string; side: string; quantity: number; leverage?: number; tp_price?: number; sl_price?: number }) =>
    apiRequest<any>(`/paper/order`, { method: "POST", body: JSON.stringify(data) }),

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
};

// 鈺愨晲鈺?瀹炵洏浜ゆ槗 鈺愨晲鈺?
export const liveApi = {
  getAccounts: () => apiRequest<{ accounts: any[] }>("/live/accounts"),
  getBalance: (accountId: number) => apiRequest<any>(`/live/balance/${accountId}`),
  getPositions: (accountId: number) => apiRequest<any>(`/live/positions/${accountId}`),
  getOrders: (accountId: number) => apiRequest<any>(`/live/orders/${accountId}`),
  getAsterdexPoints: (accountId: number) => apiRequest<any>(`/live/asterdex/points/${accountId}`),
  placeOrder: (data: any) =>
    apiRequest<any>("/live/order", { method: "POST", body: JSON.stringify(data) }),
  closePosition: (data: any) =>
    apiRequest<any>("/live/close", { method: "POST", body: JSON.stringify(data) }),
};

// ═══ 账户管理 ═══

export const accountApi = {
  list: () => apiRequest<Account[]>("/account/list"),
  create: (data: Partial<Account>) =>
    apiRequest<Account>("/account/", { method: "POST", body: JSON.stringify(data) }),
  update: (id: number, data: Partial<Account>) =>
    apiRequest<Account>(`/account/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  delete: (id: number) =>
    apiRequest<any>(`/account/${id}`, { method: "DELETE" }),
};

// ═══ AI 会话 ═══

export const sessionApi = {
  list: () => apiRequest<SessionStatus[]>("/full-auto/sessions"),
  start: (data: { account_id: number; symbols: string[]; mode?: string }) =>
    apiRequest<any>("/full-auto/start", { method: "POST", body: JSON.stringify(data) }),
  stop: (sessionId: string) =>
    apiRequest<any>(`/full-auto/stop/${sessionId}`, { method: "POST" }),
  delete: (sessionId: string) =>
    apiRequest<any>(`/full-auto/${sessionId}`, { method: "DELETE" }),
  pause: (sessionId: string) =>
    apiRequest<any>(`/full-auto/pause/${sessionId}`, { method: "POST" }),
  resume: (sessionId: string) =>
    apiRequest<any>(`/full-auto/resume/${sessionId}`, { method: "POST" }),
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

// ═══ 自动选币 ═══
export const autoCoinApi = {
  activeSymbols: () => apiRequest<any>("/auto-coin/active-symbols"),
  status: (sessionId: string) => apiRequest<any>(`/auto-coin/${sessionId}/status`),
  start: (sessionId: string) => apiRequest<any>(`/auto-coin/${sessionId}/start`, { method: "POST" }),
  stop: (sessionId: string) => apiRequest<any>(`/auto-coin/${sessionId}/stop`, { method: "POST" }),
  scanNow: (sessionId: string) => apiRequest<any>(`/auto-coin/${sessionId}/scan-now`, { method: "POST" }),
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

export const scalpConfigApi = {
  get: () => apiRequest<any>("/scalp-config/"),
  update: (updates: Record<string, any>) =>
    apiRequest<any>("/scalp-config/", { method: "PUT", body: JSON.stringify(updates) }),
  presets: () => apiRequest<any>("/scalp-config/presets"),
  currentPreset: () => apiRequest<any>("/scalp-config/current-preset"),
  saveCustomPreset: (name: string, params: Record<string, any>, description?: string) =>
    apiRequest<any>("/scalp-config/presets/custom", { method: "POST", body: JSON.stringify({ name, params, description }) }),
  simulate: (params: any) =>
    apiRequest<any>("/scalp-config/simulate", { method: "POST", body: JSON.stringify(params) }),
};

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
export const coinSelectApi = {
  settings: () => apiRequest<any>("/coin-select/settings"),
  patchSettings: (body: Record<string, unknown>) =>
    apiRequest<any>("/coin-select/settings", { method: "PATCH", body: JSON.stringify(body) }),
  board: (horizon?: "scalp" | "midlong", opts?: {
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
  startSession: (data: { account_id: number; symbols: string[]; mode?: string }) => sessionApi.start(data),
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
  getScalpConfig: () => scalpConfigApi.get(),
  updateScalpConfig: (updates: Record<string, any>) => scalpConfigApi.update(updates),
  getScalpPresets: () => scalpConfigApi.presets(),
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
