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
 * 实时深度看板（**2s**）。
 *
 * 为什么是 2s：深度是 p50 **105ms** 的 20 档快照，15s 轮询会让价格梯看起来静止、
 * 完全失去"实时深度"的意义。2s 是「看得出变化」与「不打爆后端」的折中。
 */
export function useHftBoard(symbols?: string[], depth = 20): HftResource<HftBoardResponse> {
  return useHftResource(() => hftApi.board(symbols, depth), 2_000);
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
 * 模拟账户 + 运行状态 + 实盘就绪度（**2s**）。
 *
 * 为什么是 2s 而不是 30s：账户里的**浮动盈亏随行情实时变化**，
 * 而页面上的深度梯/持仓都是 2s。若账户用 10s，会出现
 * 「挂单价动了、持仓浮盈没动」的错觉，用户会认为界面是死的
 * （实测已发生过一次："账户数据没有任何变化"）。
 * 后端该端点实测 ~230ms，2s 轮询可承受。
 */
export function useHftAccount(): HftResource<HftAccount> {
  return useHftResource(() => hftApi.account(), 2_000);
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

/** [hero 2026-10-07] 顶部大字区（2s；轻量聚合，一眼看清"今天赚没赚"） */
export function useHftHero(): HftResource<HftHero> {
  return useHftResource(() => hftApi.hero(), 2_000);
}
