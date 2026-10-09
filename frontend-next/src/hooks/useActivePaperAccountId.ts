/**
 * 模拟盘「当前账户 id」解析 —— 去掉切页时的两级瀑布。
 *
 * ## 背景（2026-09-18 前端刷新慢取证，实测）
 * 旧实现：`const activeAccountId = selectedAccountId ?? paperAccounts[0]?.id ?? null`
 * 而 `paperAccounts` 来自 `useAccounts()`（`/api/account/list`，staleTime 30s）。
 * 四个数据 hook 都是 `enabled: !!accountId`，于是切页必须**等账户列表返回后**
 * 才发出数据请求（波1→波2 串行）。实测切页"数据齐全"因此为 0.6~1.8s
 * （`scripts/reproduce_switch_with_waterfall.py`）。
 *
 * ## 本实现
 * 仍然用原判定，只在**账户列表尚未返回**这一小段窗口里，用"上次用过的账户 id"
 * 乐观兜底，让波2 与波1 **并行**发出；列表到达后立刻回到原判定，
 * 因此不会把已删除账户一直沿用（列表一到 `paperAccounts[0]` 即生效）。
 *
 * 为什么不用 `useState(() => read())`：那会让客户端首次渲染与服务端 HTML 不一致
 * （SSR 无 localStorage）→ hydration 不匹配。
 * 这里用 `useSyncExternalStore`（React 读取外部存储的标准方式）：
 * 服务端快照恒为 `null`，水合完成后自动切到真实值，**无需 effect、无 hydration 警告**。
 */
"use client";

import { useEffect, useMemo, useSyncExternalStore } from "react";
import type { Account } from "@/types/api";

const KEY = "arena_last_paper_account_id";

/** 同步读上次用过的模拟盘账户 id；服务端/无存储时返回 null。 */
export function readLastPaperAccountId(): number | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

export function writeLastPaperAccountId(id: number | null): void {
  if (typeof window === "undefined") return;
  if (id == null || !Number.isFinite(id) || id <= 0) return;
  try {
    window.localStorage.setItem(KEY, String(id));
  } catch {
    /* 隐私模式等场景忽略 */
  }
}

/** 订阅外部存储变化（含其它标签页修改）。 */
function subscribeStorage(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

/** 服务端快照：恒定 null（水合期用，避免与 SSR HTML 不一致）。 */
function serverSnapshot(): null {
  return null;
}

/** 上次用过的模拟盘账户 id（首帧/SSR 为 null，水合后自动取真实值）。 */
export function useLastPaperAccountId(): number | null {
  return useSyncExternalStore(subscribeStorage, readLastPaperAccountId, serverSnapshot);
}

/**
 * 解析当前模拟盘账户 id，并把结果记到 localStorage 供下次切页乐观使用。
 *
 * 判定顺序（与旧实现一致，仅多一层"列表未到达"的兜底）：
 *   1. `selectedAccountId`（用户显式选择）
 *   2. 账户列表已到达 → 列表中 id 最大的 paper 账户，没有则 `null`
 *   3. 账户列表未到达 → 上次用过的账户 id（乐观；列表一到即失效）
 */
export function useActivePaperAccountId(
  accounts: Account[] | undefined,
  selectedAccountId: number | null,
): number | null {
  const cached = useLastPaperAccountId();

  const activeAccountId = useMemo(() => {
    if (selectedAccountId != null) return selectedAccountId;
    if (accounts) {
      const paper = accounts
        .filter((a) => a.trading_mode === "paper")
        .sort((a, b) => b.id - a.id);
      return paper[0]?.id ?? null;
    }
    return cached;
  }, [accounts, selectedAccountId, cached]);

  useEffect(() => {
    writeLastPaperAccountId(activeAccountId);
  }, [activeAccountId]);

  return activeAccountId;
}
