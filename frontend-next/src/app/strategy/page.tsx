/**
 * 旧路由跳转桩：`/strategy`（AI 策略）→ `/control?tab=sessions`（交易总控 · 会话管理）
 *
 * [2026-10-03 用户指令] 「ai 策略里只保留会话管理，其他的都没用。然后把会话管理并入交易总控…
 * 原来的 ai 策略就没有了」。AI 策略页已删除：
 *   · 会话管理    → `/control?tab=sessions`（`components/trading/SessionManager.tsx`）
 *   · 双周期总览/信号流/AI 决策日志 → 弃用（用户口径"没用"）
 *   · 其中的「车道配置」卡（原 /mid、/long 合并而来）**不能丢** → 迁到 `/control?tab=lanes`，
 *     所以本桩会把 `?cfg=`/`?sub=` 深链一并转过去（命令面板「提示词」依赖它）。
 *
 * 保留路由的理由同其它合并桩：书签/文档/旧 e2e 仍可能引用，静态导出下删掉就是 404。
 */
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Bot } from "lucide-react";
import { softNavigate } from "@/lib/app-nav";

export default function StrategyRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    const qs = typeof window !== "undefined" ? window.location.search : "";
    const params = new URLSearchParams(qs);
    const cfg = (params.get("cfg") || "").toLowerCase();
    const sub = (params.get("sub") || "").toLowerCase();
    const target = cfg
      ? `/control?tab=lanes&cfg=${encodeURIComponent(cfg)}${sub ? `&sub=${encodeURIComponent(sub)}` : ""}`
      : "/control?tab=sessions";
    softNavigate(target, (url) => router.replace(url));
  }, [router]);

  return (
    <div className="p-6 flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="w-4 h-4 animate-spin" />
      <Bot className="w-4 h-4" />
      <span>AI 策略已并入「交易总控」（会话管理 / 车道配置），正在跳转…</span>
      <a href="/control?tab=sessions" className="text-cyan-300 hover:underline">未自动跳转请点这里</a>
    </div>
  );
}
