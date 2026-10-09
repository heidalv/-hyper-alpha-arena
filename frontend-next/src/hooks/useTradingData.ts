/**
 * useTradingData — React Query 封装的数据获取
 * 对齐旧前端 PaperTradingPanel 的全部数据流
 */
"use client";

import { useEffect } from "react";
import {
  useQuery,
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { api, paperApi, accountApi, sessionApi, fullAutoApi } from "@/lib/api";
import { toast } from "@/lib/toast";
import type { Account } from "@/types/api";

// Query Keys
export const QK = {
  accounts: ["accounts"] as const,
  /** [2026-09-18] 一屏聚合键：模拟盘四条数据的**唯一**读取来源 */
  paperDashboard: (accountId: number) => ["paper-dashboard", accountId] as const,
  balance: (accountId: number) => ["balance", accountId] as const,
  positions: (accountId: number, status?: string) => ["positions", accountId, status] as const,
  orders: (accountId: number) => ["orders", accountId] as const,
  summary: (accountId: number) => ["summary", accountId] as const,
  sessions: ["sessions"] as const,
  dashboard: ["dashboard"] as const,
  assetCurve: ["asset-curve"] as const,
  aiDecisions: (accountId: number) => ["ai-decisions", accountId] as const,
  scalpSignals: ["scalp-signals"] as const,
  strategyConfig: (tier: string) => ["strategy-config", tier] as const,
  prompts: (tier: string) => ["prompts", tier] as const,
  marketOverview: (symbols: string[]) => ["market-overview", symbols] as const,
  marketHealth: ["market-health"] as const,
  watchlist: ["watchlist"] as const,
};

/**
 * 使某账户的模拟盘数据立即失效（聚合键 + 旧单端点键一起）。
 * [2026-09-18] 数据读取已收敛到聚合键，若只失效旧键 ⇒ "改了余额/平了仓页面不刷新"。
 */
export function invalidatePaperData(
  qc: QueryClient,
  accountId: number | null | undefined,
): void {
  if (!accountId) return;
  void qc.invalidateQueries({ queryKey: QK.paperDashboard(accountId) });
  void qc.invalidateQueries({ queryKey: QK.balance(accountId) });
  void qc.invalidateQueries({ queryKey: QK.positions(accountId) });
  void qc.invalidateQueries({ queryKey: QK.orders(accountId) });
  void qc.invalidateQueries({ queryKey: QK.summary(accountId) });
  // [2026-10-02 修复] 漏了权益曲线：账户操作（设置余额/软重置/完整重置）后，
  // 曲线仍显示旧序列（key 见 EquitySeriesCard: ["equity-series", source, accountId, period]）
  // ⇒ 用户看到"重置了但总收益没变"。按前缀失效，覆盖 7d/30d/90d/all 全部周期。
  void qc.invalidateQueries({ queryKey: ["equity-series"] });
  // 账户列表里 initial_capital 现在会随「设置余额」同步（后端已修），账户缓存也要失效
  void qc.invalidateQueries({ queryKey: QK.accounts });
}

// ═══ 账户 ═══

export function useAccounts() {
  return useQuery({
    queryKey: QK.accounts,
    queryFn: api.getAccounts,
    staleTime: 30_000,
  });
}

// ═══ 模拟交易数据 ═══
//
// [2026-09-18 前端刷新慢治理] 原为「4 个端点各 5s 轮询」（真实访问日志里 `/paper/*`
// 占全站请求 50.7%）。后端 GIL 长期贴 1 核（`backend/services/gil_watch.py` 实测：
// 无请求时进程 CPU 中位 ≈99%），**请求条数本身就是排队成本** ⇒ 收敛为
// 「1 个聚合请求 / 5s」。四节口径与单端点一致（后端同一份 helper + 对拍测试）。
//
// 为什么不用"多个 useQuery 共享同一 key + 各自 refetchInterval"：
//   TanStack Query v5 的 `Query.fetch()` 在**已有数据**且 `cancelRefetch` 为真
//   （`refetch()` 默认）时会取消在飞请求并重发 ⇒ N 个观察者 = 每间隔 N 次同端点请求。
//   故这里用**模块级引用计数的单一轮询器**：任何派生 hook 挂载即计数 +1，
//   同一账号永远只有一个 setInterval、每次 tick 只发 1 个请求（单飞去重），
//   卸载到 0 才停 —— 因此 strategy 这类只挂 `usePositions` 的页面**不会静默失去轮询**。

const PAPER_DASHBOARD_POLL_MS = 5_000;

const _pollers = new Map<number, { count: number; timer: ReturnType<typeof setInterval> | null }>();
const _inFlight = new Set<number>();

function _releasePoll(accountId: number): void {
  const reg = _pollers.get(accountId);
  if (!reg) return;
  reg.count -= 1;
  if (reg.count <= 0) {
    if (reg.timer) clearInterval(reg.timer);
    _pollers.delete(accountId);
  }
}

/**
 * 注册"该账号需要 5s 轮询聚合端点"。引用计数：多个派生 hook 同时挂载也只会有 1 个定时器。
 * 页面不可见时跳过 tick（与 `usePolling` 同语义），回到可见时立即补一次。
 */
function usePaperDashboardPoller(accountId: number | null, enabled: boolean): void {
  const qc = useQueryClient();
  useEffect(() => {
    if (!accountId || !enabled) return;
    const reg = _pollers.get(accountId) ?? { count: 0, timer: null };
    reg.count += 1;
    _pollers.set(accountId, reg);

    const tick = () => {
      if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
      if (_inFlight.has(accountId)) return; // 单飞：慢响应期间不叠加
      _inFlight.add(accountId);
      void qc
        .fetchQuery({
          queryKey: QK.paperDashboard(accountId),
          queryFn: () => paperApi.getDashboard(accountId, "open"),
          staleTime: 0,
        })
        .catch(() => {
          /* 轮询失败保留旧值，下个周期再试 */
        })
        .finally(() => _inFlight.delete(accountId));
    };

    if (!reg.timer) reg.timer = setInterval(tick, PAPER_DASHBOARD_POLL_MS);

    const onVisible = () => {
      if (document.visibilityState === "visible") tick();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      _releasePoll(accountId);
    };
  }, [accountId, enabled, qc]);
}

/** 聚合查询（不轮询；轮询由 `usePaperDashboardPoller` 的单一计时器负责）。 */
function usePaperDashboardQuery(accountId: number | null, poll: boolean, enabled = true) {
  usePaperDashboardPoller(accountId, poll && enabled);
  return useQuery({
    queryKey: QK.paperDashboard(accountId || 0),
    queryFn: () => paperApi.getDashboard(accountId!, "open"),
    enabled: !!accountId && enabled,
    staleTime: PAPER_DASHBOARD_POLL_MS,
    refetchInterval: false, // 见文件下方说明：轮询必须集中在单一计时器
  });
}

export function usePaperBalance(accountId: number | null) {
  const q = usePaperDashboardQuery(accountId, true);
  return { ...q, data: q.data ? q.data.balance ?? undefined : undefined };
}

export function usePositions(accountId: number | null, status?: "open" | "closed") {
  // 聚合端点只覆盖 open；closed 仍走原端点（语义不缩水）
  const isOpen = !status || status === "open";
  const agg = usePaperDashboardQuery(accountId, isOpen, isOpen);
  const standalone = useQuery({
    queryKey: QK.positions(accountId || 0, status),
    queryFn: () => paperApi.getPositions(accountId!, status),
    enabled: !!accountId && !isOpen,
    staleTime: 5_000,
    refetchInterval: isOpen ? false : 5_000,
  });
  return isOpen ? { ...agg, data: agg.data?.positions } : standalone;
}

export function useOrders(accountId: number | null, limit: number = 50) {
  // 聚合里固定 limit=50；其它 limit 仍走原端点
  const covered = limit === 50;
  const agg = usePaperDashboardQuery(accountId, covered, covered);
  const standalone = useQuery({
    queryKey: QK.orders(accountId || 0),
    queryFn: () => paperApi.getOrders(accountId!, limit),
    enabled: !!accountId && !covered,
    staleTime: 5_000,
    refetchInterval: covered ? false : 5_000,
  });
  return covered ? { ...agg, data: agg.data?.orders } : standalone;
}

export function usePaperSummary(accountId: number | null) {
  const q = usePaperDashboardQuery(accountId, true);
  return { ...q, data: q.data?.summary };
}

/** 一屏原始数据（页面如需自行取用，可直接调它；同样只会有 1 个轮询器）。 */
export function usePaperDashboard(accountId: number | null) {
  return usePaperDashboardQuery(accountId, true);
}

// ═══ 会话 ═══

export function useSessions() {
  return useQuery({
    queryKey: QK.sessions,
    queryFn: api.getSessions,
    // 2026-07-20：缩短缓存新鲜期。原 10s 内删除/新增币种后仍可能展示旧列表，
    // 用户误以为"删除后又刷新回来"。2s 既避免高频请求又保证数据及时。
    staleTime: 2_000,
    refetchInterval: 5_000,
  });
}

// ═══ 全自动会话：tier 状态/活动/策略（R4 新增，dashboard 专用） ═══

export function useTierStatus(sessionId: string | undefined) {
  return useQuery({
    queryKey: ["tier-status", sessionId],
    queryFn: () => fullAutoApi.tierStatus(sessionId!),
    enabled: !!sessionId,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useTierActivity(sessionId: string | undefined) {
  return useQuery({
    queryKey: ["tier-activity", sessionId],
    queryFn: () => fullAutoApi.tierActivity(sessionId!),
    enabled: !!sessionId,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

export function useStrategies(accountId: number | null) {
  return useQuery({
    queryKey: ["strategies", accountId],
    queryFn: () => fullAutoApi.strategies(accountId!),
    enabled: !!accountId,
    staleTime: 30_000,
  });
}

// ═══ 仪表盘 ═══

export function useDashboard() {
  return useQuery({
    queryKey: QK.dashboard,
    queryFn: api.getDashboard,
    staleTime: 15_000,
    refetchInterval: 10_000,
  });
}

export function useAssetCurve() {
  return useQuery({
    queryKey: QK.assetCurve,
    queryFn: api.getAssetCurve,
    staleTime: 60_000,
    refetchInterval: 120_000,
  });
}

// ═══ AI 决策 + 信号 ═══

export function useAiDecisions(accountId: number | null, limit: number = 20) {
  return useQuery({
    queryKey: QK.aiDecisions(accountId || 0),
    queryFn: () => api.getAiDecisions(accountId!, limit),
    enabled: !!accountId,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useScalpSignals(limit: number = 30) {
  return useQuery({
    queryKey: QK.scalpSignals,
    queryFn: () => api.getScalpSignals(limit),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

// ═══ 策略配置 ═══
// [2026-09-17] useScalpConfig/useScalpPresets/useUpdateScalpConfig 已随短线车道停用移除。

export function useStrategyConfig(tier: "mid" | "long") {
  return useQuery({
    queryKey: QK.strategyConfig(tier),
    queryFn: () => api.getStrategyConfig(tier),
    staleTime: 60_000,
  });
}

export function usePrompts(tier: "mid" | "long") {
  return useQuery({
    queryKey: QK.prompts(tier),
    queryFn: () => api.getPrompts(tier),
    staleTime: 60_000,
  });
}

// ═══ 市场数据 ═══

export function useMarketOverview(symbols: string[]) {
  return useQuery({
    queryKey: QK.marketOverview(symbols),
    queryFn: () => api.getMarketOverview(symbols),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}

export function useMarketHealth() {
  return useQuery({
    queryKey: QK.marketHealth,
    queryFn: api.getMarketHealth,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useMarketOverviewAll(exchange?: string) {
  return useQuery({
    queryKey: ["market-overview-all", exchange || "auto"] as const,
    queryFn: () => api.getMarketOverviewAll(exchange),
    // [2026-09-09] 2s/1.5s 是全站最激进的轮询，且与 intel 页的
    // useMarketOverview(30s)/useMarketHealth(60s) 重复拉同一批行情 → 收敛到 10s。
    staleTime: 8_000,
    refetchInterval: 10_000,
  });
}

export function useWatchlist() {
  return useQuery({
    queryKey: QK.watchlist,
    queryFn: api.getWatchlist,
    staleTime: 60_000,
    refetchInterval: 120_000,
  });
}

// ═══ 变更 ═══

export function useUpdateStrategyConfig(tier: "mid" | "long") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (updates: Record<string, unknown>) => api.updateStrategyConfig(tier, updates),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.strategyConfig(tier) }),
  });
}

// ═══ 会话管理 ═══

export function useStartSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.startSession,
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.sessions }),
  });
}

export function useStopSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.stopSession,
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.sessions }),
  });
}

export function usePauseSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.pauseSession,
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.sessions }),
  });
}

export function useResumeSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.resumeSession,
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.sessions }),
  });
}

export function useDeleteSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => sessionApi.delete(sessionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.sessions }),
  });
}

// ═══ 模拟交易操作 ═══

export function useClosePosition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ accountId, symbol, side, quantity }: { accountId: number; symbol: string; side: string; quantity?: number }) =>
      paperApi.closePosition(accountId, symbol, side, quantity),
    onSuccess: (_data, variables) => {
      invalidatePaperData(qc, variables.accountId);
    },
  });
}

export function useResetBalance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: paperApi.resetBalance,
    onSuccess: (_data, accountId) => {
      invalidatePaperData(qc, accountId);
    },
  });
}

// ═══ 账户操作 ═══

export function useCreateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: accountApi.create,
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.accounts }),
  });
}

export function useDeleteAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => accountApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.accounts }),
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      const authHint =
        /401|Not authenticated|登录|过期/i.test(msg)
          ? "（登录态已失效：请重新登录，或刷新页面后重试）"
          : "";
      toast.error(`账户删除失败：${msg}${authHint}`);
    },
  });
}

export function useUpdateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<Account> }) => accountApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.accounts }),
  });
}
