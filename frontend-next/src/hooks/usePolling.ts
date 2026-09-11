/**
 * usePolling — 统一的可见性感知轮询
 *
 * 背景（2026-09-09 监控实测）：全站 25+ 处裸 setInterval 轮询，
 *   - 窗口最小化 / 切到后台后照常打后端（React Query 的 refetchInterval 会暂停，裸 setInterval 不会）；
 *   - 后端接口偶发 3~45s 慢响应时，下一次 tick 不会等待上一次结束 → 请求堆积；
 *   - 各页面各写一套，间隔/清理逻辑重复。
 *
 * 本 hook 统一三点：
 *   1. 页面不可见时暂停，回到可见时立即补一次（用户回到窗口看到的是新数据，不是旧快照）；
 *   2. 单飞（in-flight 去重）：上一次未结束则跳过本次 tick，避免慢接口下雪崩；
 *   3. 卸载/依赖变化时彻底清理。
 */
"use client";

import { useEffect, useRef } from "react";

export function usePolling(
  fn: () => void | Promise<void>,
  intervalMs: number,
  options?: { enabled?: boolean; runOnMount?: boolean; reloadKey?: string | number }
) {
  const enabled = options?.enabled ?? true;
  const runOnMount = options?.runOnMount ?? true;
  // reloadKey 变化时立即重拉一次（如切换账户/会话导致 URL 变化）。
  // 之所以不用 fn 本身做依赖：调用方若不 useCallback 会导致每次渲染重建定时器。
  const reloadKey = options?.reloadKey ?? "";

  // 始终调用最新的 fn，避免因 fn 引用变化重建定时器。
  // 注意：ref 的写入必须放在 effect 里（React 19 / react-hooks/refs 禁止在渲染期写 ref）；
  // 定时器回调是异步的，effect 先于首个 tick 完成，语义不变。
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  }, [fn]);

  const inFlightRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!enabled || intervalMs <= 0) return;

    const tick = () => {
      if (inFlightRef.current) return; // 单飞：慢响应期间不叠加请求
      if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
      inFlightRef.current = true;
      void (async () => {
        try {
          await fnRef.current();
        } finally {
          inFlightRef.current = false;
        }
      })();
    };

    const start = () => {
      if (timerRef.current) return;
      timerRef.current = setInterval(tick, intervalMs);
    };
    const stop = () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        tick(); // 回到前台立即刷新一次
        start();
      } else {
        stop();
      }
    };

    if (typeof document === "undefined" || document.visibilityState === "visible") {
      if (runOnMount) tick();
      start();
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [intervalMs, enabled, runOnMount, reloadKey]);
}
