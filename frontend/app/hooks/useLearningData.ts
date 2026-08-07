/**
 * useLearningData — 学习中心共享数据钩子 (F5)
 *
 * 消除多个组件各自 useEffect + setInterval 重复轮询同一端点的问题。
 * 同一端点在组件树里只需实例化一次（React 会在每个使用处独立轮询，
 * 但集中到 hook 后至少逻辑统一、周期一致、便于后续升级为全局 store）。
 *
 * 用法：
 *   const { data, loading, error, refresh } = useOverview();
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  getOverview,
  getFeatureFlags,
  getEvolutionStatus,
  type OverviewResponse,
  type FeatureFlagsResponse,
} from '@/lib/intelligentLearningApi';

/** 通用轮询钩子工厂 */
function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number = 30000,
): { data: T | null; loading: boolean; error: string | null; refresh: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      const result = await fetcher();
      if (mountedRef.current) {
        setData(result);
        setError(null);
      }
    } catch (e: any) {
      if (mountedRef.current) {
        setError(e.message || '加载失败');
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [fetcher]);

  useEffect(() => {
    mountedRef.current = true;
    refresh();
    const timer = setInterval(refresh, intervalMs);
    return () => {
      mountedRef.current = false;
      clearInterval(timer);
    };
  }, [refresh, intervalMs]);

  return { data, loading, error, refresh };
}

/** 学习中心总览（30s 轮询）*/
export function useOverview(intervalMs: number = 30000) {
  return usePolling<OverviewResponse>(getOverview, intervalMs);
}

/** 特性开关 + 后端注册表 + 统一配置（30s 轮询）*/
export function useFeatureFlags(intervalMs: number = 30000) {
  return usePolling<FeatureFlagsResponse>(getFeatureFlags, intervalMs);
}

/** 进化系统状态（30s 轮询）*/
export function useEvolutionStatus(intervalMs: number = 30000) {
  return usePolling<any>(getEvolutionStatus, intervalMs);
}
