/**
 * useLaneData — 套利中心数据获取 hooks（轮询，不引入 React Query）
 *
 * 每个 hook 返回 `{ data, error, loading, lastUpdated, refresh }`：
 *  - data：后端原始返回（T | null）；失败时保留上一次成功快照，绝不置 0
 *  - error：可读错误信息（null = 正常）
 *  - loading：首屏是否仍在加载
 *  - lastUpdated：最近一次**成功**拉取的时间戳（epoch ms）
 *  - refresh：立即重拉一次
 *
 * 轮询统一走 `@/hooks/usePolling`（不可见暂停 / in-flight 去重 / 卸载清理）。
 * 间隔：车道/组合 15s，归因/序列/配置 30–60s。
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePolling } from "./usePolling";
import { tradingApi } from "@/lib/trading-api";
import type {
  LanesResponse,
  PortfolioSummary,
  Attribution,
  DailySeries,
  LaneConfig,
  ShadowReport,
  LaneSummary,
  PromotionState,
  ShadowStatus,
  PositionsResponse,
  RiskSummary,
  OpportunitiesResponse,
  BreakersResponse,
  FeesResponse,
  DataSourcesResponse,
  CapitalPoolResponse,
  UnifiedAccountResponse,
  LaneReconcile,
} from "@/lib/trading-api";

export interface PollResource<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  lastUpdated: number | null;
  refresh: () => void;
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** 通用：单资源轮询的内部实现 */
function usePollingResource<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  enabled = true,
  reloadKey = ""
): PollResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const fetcherRef = useRef(fetcher);

  // 始终调用最新的 fetcher（不因引用变化重建定时器）
  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);

  const load = useCallback(async () => {
    try {
      const res = await fetcherRef.current();
      setData(res);
      setError(null);
      setLastUpdated(Date.now());
    } catch (e) {
      // 失败时保留上一次成功快照（只显示错误横幅，不置 0/空）
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  usePolling(load, intervalMs, { enabled, reloadKey });

  const refresh = useCallback(() => {
    void load();
  }, [load]);

  return { data, error, loading, lastUpdated, refresh };
}

/** 车道列表（15s） */
export function useLanes(): PollResource<LanesResponse> {
  return usePollingResource(() => tradingApi.lanes(), 15_000);
}

/** 组合总览（15s） */
export function usePortfolioSummary(): PollResource<PortfolioSummary> {
  return usePollingResource(() => tradingApi.portfolioSummary(), 15_000);
}

/** 近 N 天六维归因（60s） */
export function useAttribution(days: number, laneId?: string): PollResource<Attribution> {
  return usePollingResource(() => tradingApi.attribution(days, laneId), 60_000);
}

/** 近 N 天逐日序列（30s） */
export function useDailySeries(days: number, laneId?: string): PollResource<DailySeries> {
  return usePollingResource(() => tradingApi.dailySeries(days, laneId), 30_000);
}

/** 车道报价/风控参数（60s）；切换车道时立即重拉 */
export function useLaneConfig(laneId: string): PollResource<LaneConfig> {
  return usePollingResource(() => tradingApi.laneConfig(laneId), 60_000, !!laneId, laneId);
}

/** 车道详情（15s，车道页详情头） */
export function useLaneDetail(laneId: string): PollResource<LaneSummary> {
  return usePollingResource(() => tradingApi.lane(laneId), 15_000, !!laneId);
}

/** 车道晋升矩阵（15s） */
export function useLanePromotion(laneId: string): PollResource<{ lane_id: string; mode: string; promotion: PromotionState }> {
  return usePollingResource(() => tradingApi.promotion(laneId), 15_000, !!laneId);
}

/** 影子期实时状态（15s） */
export function useShadowStatus(laneId: string): PollResource<ShadowStatus> {
  return usePollingResource(() => tradingApi.shadowStatus(laneId), 15_000, !!laneId);
}

/** 影子期达标报告（30s） */
export function useShadowReport(laneId: string, days: number): PollResource<ShadowReport> {
  return usePollingResource(() => tradingApi.shadowReport(laneId, days), 30_000, !!laneId);
}

/** [F91] 双账对账（运行态 vs 账本重建，30s）：ok=false 必须显性告警 */
export function useReconcile(laneId: string): PollResource<LaneReconcile> {
  return usePollingResource(() => tradingApi.laneReconcile(laneId), 30_000, !!laneId);
}

/** 持仓（30s，可过滤车道/标的） */
export function usePositions(laneId?: string, symbol?: string, days = 30): PollResource<PositionsResponse> {
  return usePollingResource(() => tradingApi.positions(days, laneId, symbol), 30_000);
}

/** 风险速览（60s） */
export function useRiskSummary(days = 30): PollResource<RiskSummary> {
  return usePollingResource(() => tradingApi.riskSummary(days), 60_000);
}

/** 机会表（30s，支持 min_days / tradable_only 过滤 + 场地切换） */
export function useOpportunities(
  venue = "asterdex",
  minDays = 7,
  tradableOnly = true
): PollResource<OpportunitiesResponse> {
  const reloadKey = `${venue}|${minDays}|${tradableOnly}`;
  return usePollingResource(
    () => tradingApi.opportunities(venue, 40, minDays, tradableOnly),
    30_000,
    true,
    reloadKey
  );
}

/** 熔断矩阵（30s） */
export function useRiskBreakers(): PollResource<BreakersResponse> {
  return usePollingResource(() => tradingApi.riskBreakers(), 30_000);
}

/** 费率表（60s） */
export function useConfigFees(): PollResource<FeesResponse> {
  return usePollingResource(() => tradingApi.configFees(), 60_000);
}

/** 数据源健康（60s）：盘口/成交/资金费/现货数据年龄与断流标记 */
export function useConfigDataSources(): PollResource<DataSourcesResponse> {
  return usePollingResource(() => tradingApi.configDataSources(), 60_000);
}

/** 资金池（60s）：各模拟账户余额 */
export function useCapitalPool(): PollResource<CapitalPoolResponse> {
  return usePollingResource(() => tradingApi.capitalPool(), 60_000);
}

/** 统一模拟账户视图（30s）：账户/策略分账/交易所预算/做市持仓与敞口 */
export function useUnifiedAccount(accountId?: number, days = 30): PollResource<UnifiedAccountResponse> {
  const reloadKey = `${accountId ?? "default"}|${days}`;
  return usePollingResource(() => tradingApi.unifiedAccount(accountId, days), 30_000, true, reloadKey);
}
