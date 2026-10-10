/**
 * [F250] 中短期高频交易模块 · 数据 hooks
 *
 * 与套利中心共享 `@/hooks/usePolling`（不可见暂停 / in-flight 去重 / 卸载清理），
 * 但**不复用 `useLaneData`**：那是套利中心的数据层，绑定 `/api/trading/*`。
 * 本模块独立，避免"入口被隐藏 ⇒ 连数据层也一起被牵连"。
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePolling } from "./usePolling";
import { hftApi } from "@/lib/hft-api";
import type {
  HftBoardResponse,
  HftOverview,
  HftUniverseScoreResponse,
  HftUniverseLiveResponse,
  HftAccount,
  HftConfig,
  HftFillsResponse,
  HftEvolution,
  HftHero,
} from "@/lib/hft-api";

export interface HftResource<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  lastUpdated: number | null;
  refresh: () => void;
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function useHftResource<T>(fetcher: () => Promise<T>, intervalMs: number, enabled = true): HftResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const fetcherRef = useRef(fetcher);

  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);

  const load = useCallback(async () => {
    try {
      setData(await fetcherRef.current());
      setError(null);
      setLastUpdated(Date.now());
    } catch (e) {
      // 切页取消的轮询不是失败，不要刷成错误提示
      if (e instanceof DOMException && e.name === "AbortError") return;
      // 失败保留上一次成功快照（不置空、不置 0）
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  usePolling(load, intervalMs, { enabled });
  const refresh = useCallback(() => { void load(); }, [load]);
  return { data, error, loading, lastUpdated, refresh };
}

/**
 * 模块总览（60s）。
 *
 * 间隔取 60s 而不是盘口级的 2s：该端点会做一次深度名单全表发现（实测 ≈9s），
 * 高频轮询既无必要也会压后端。宇宙是 **每 30 分钟**才重算一次的。
 */
export function useHftOverview(): HftResource<HftOverview> {
  return useHftResource(() => hftApi.overview(), 60_000);
}

/**
 * 实时深度看板（**5s**）。
 *
 * 深度本身不重，但 2s 一轮再叠上账户/总览，会把浏览器仅有的几个连接占满，
 * 切到别的页面要等将近 8 秒。5s 仍然能看出价格梯在动。
 */
export function useHftBoard(symbols?: string[], depth = 20): HftResource<HftBoardResponse> {
  // 2s 轮询时这个接口经常要 3~7 秒才返回，请求叠在一起，把别的页面也堵住。
  // 5s 一轮，画面仍然在动，后端队列能空出来给正在打开的页面。
  return useHftResource(() => hftApi.board(symbols, depth), 5_000);
}

/** 选币评分明细（120s；每 30 分钟才重算一次，不需要更快） */
export function useHftUniverseScore(aiSlots = 5, enabled = true): HftResource<HftUniverseScoreResponse> {
  return useHftResource(() => hftApi.universeScore(aiSlots), 120_000, enabled);
}

/**
 * [2026-09-27] 分币种实盘判定（20s）——现行选币口径的实时事实。
 *
 * 取代旧的评分/硬闸两张卡：那两张属已停用的机械选币口径，且与现行宇宙
 * 自相矛盾（ETH/ARB 既在宇宙又被列"拒绝"）。本表就是 #2 判定要用的数字。
 */
export function useHftUniverseLive(enabled = true): HftResource<HftUniverseLiveResponse> {
  return useHftResource(() => hftApi.universeLive(), 20_000, enabled);
}

/**
 * 模拟账户 + 运行状态 + 实盘就绪度（**5s**）。
 *
 * 和深度梯同一拍（5s）。再快时这个接口要扫手续费账本，会堵住别的页面。
 */
export function useHftAccount(): HftResource<HftAccount> {
  return useHftResource(() => hftApi.account(), 5_000);
}

/** 报价参数与风控限额（只读，60s） */
export function useHftConfig(enabled = true): HftResource<HftConfig> {
  return useHftResource(() => hftApi.config(), 60_000, enabled);
}

/** 成交记录（20s；新成交要能较快出现） */
export function useHftFills(limit = 50, hours = 24): HftResource<HftFillsResponse> {
  return useHftResource(() => hftApi.fills(limit, hours), 20_000);
}

/** [h722] 自进化层状态（120s；底层文件每 2-6h 才刷新，不需要更快） */
export function useHftEvolution(): HftResource<HftEvolution> {
  return useHftResource(() => hftApi.evolution(), 120_000);
}

/** [hero 2026-10-07 → 2026-10-10 提速] 顶部大字区（**1s**）。
 *  后端快路径：worker 每 tick 把今日腿级统计写进心跳文件，/hft/hero 直接读文件
 *  （零 SQL）⇒ 数值与抓取同频。回退：后端缓存 TTL=1s，最坏情况仍走 SQL 兜底。 */
export function useHftHero(): HftResource<HftHero> {
  return useHftResource(() => hftApi.hero(), 1_000);
}
