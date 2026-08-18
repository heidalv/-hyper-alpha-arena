/**
 * useTradingData — React Query 封装的数据获取
 * 对齐旧前端 PaperTradingPanel 的全部数据流
 */
"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, paperApi, accountApi, sessionApi, fullAutoApi } from "@/lib/api";
import type { Account } from "@/types/api";

// Query Keys
export const QK = {
  accounts: ["accounts"] as const,
  balance: (accountId: number) => ["balance", accountId] as const,
  positions: (accountId: number, status?: string) => ["positions", accountId, status] as const,
  orders: (accountId: number) => ["orders", accountId] as const,
  summary: (accountId: number) => ["summary", accountId] as const,
  sessions: ["sessions"] as const,
  dashboard: ["dashboard"] as const,
  assetCurve: ["asset-curve"] as const,
  aiDecisions: (accountId: number) => ["ai-decisions", accountId] as const,
  scalpSignals: ["scalp-signals"] as const,
  scalpConfig: ["scalp-config"] as const,
  scalpPresets: ["scalp-presets"] as const,
  strategyConfig: (tier: string) => ["strategy-config", tier] as const,
  prompts: (tier: string) => ["prompts", tier] as const,
  marketOverview: (symbols: string[]) => ["market-overview", symbols] as const,
  marketHealth: ["market-health"] as const,
  watchlist: ["watchlist"] as const,
};

// ═══ 账户 ═══

export function useAccounts() {
  return useQuery({
    queryKey: QK.accounts,
    queryFn: api.getAccounts,
    staleTime: 30_000,
  });
}

// ═══ 模拟交易数据（对齐旧前端 loadData 的 5 个并行请求） ═══

export function usePaperBalance(accountId: number | null) {
  return useQuery({
    queryKey: QK.balance(accountId || 0),
    queryFn: () => paperApi.getBalance(accountId!),
    enabled: !!accountId,
    staleTime: 2_000,
    refetchInterval: 2_000,
    retry: 1, // balance 404 = 未初始化，不要疯狂重试
  });
}

export function usePositions(accountId: number | null, status?: "open" | "closed") {
  return useQuery({
    queryKey: QK.positions(accountId || 0, status),
    queryFn: () => paperApi.getPositions(accountId!, status),
    enabled: !!accountId,
    staleTime: 2_000,
    refetchInterval: 2_000,
  });
}

export function useOrders(accountId: number | null, limit: number = 50) {
  return useQuery({
    queryKey: QK.orders(accountId || 0),
    queryFn: () => paperApi.getOrders(accountId!, limit),
    enabled: !!accountId,
    staleTime: 5_000,
    refetchInterval: 5_000,
  });
}

export function usePaperSummary(accountId: number | null) {
  return useQuery({
    queryKey: QK.summary(accountId || 0),
    queryFn: () => paperApi.getSummary(accountId!),
    enabled: !!accountId,
    staleTime: 5_000,
    refetchInterval: 5_000,
  });
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

export function useScalpConfig() {
  return useQuery({
    queryKey: QK.scalpConfig,
    queryFn: api.getScalpConfig,
    staleTime: 60_000,
  });
}

export function useScalpPresets() {
  return useQuery({
    queryKey: QK.scalpPresets,
    queryFn: api.getScalpPresets,
    staleTime: 300_000,
  });
}

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
    staleTime: 1_500,
    refetchInterval: 2_000,
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

export function useUpdateScalpConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.updateScalpConfig,
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.scalpConfig }),
  });
}

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
      qc.invalidateQueries({ queryKey: QK.positions(variables.accountId) });
      qc.invalidateQueries({ queryKey: QK.balance(variables.accountId) });
      qc.invalidateQueries({ queryKey: QK.summary(variables.accountId) });
    },
  });
}

export function useResetBalance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: paperApi.resetBalance,
    onSuccess: (_data, accountId) => {
      qc.invalidateQueries({ queryKey: QK.balance(accountId) });
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
    mutationFn: accountApi.delete,
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.accounts }),
  });
}

export function useUpdateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<Account> }) => accountApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.accounts }),
  });
}
