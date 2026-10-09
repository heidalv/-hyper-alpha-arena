/**
 * 旧路由跳转桩：`/mid` → `/control?tab=lanes&cfg=mid`
 *
 * [2026-10-01 合并] 用户指令：「中线配置 长线配置 并入 ai策略 作为页内的卡片」。
 * 中线配置已作为**页内卡片**并入「AI 策略」（`/strategy` 双周期总览 Tab）——
 * 卡片实现 `src/components/config/TierConfigCard.tsx`。
 *
 * 保留本路由：外部书签 / 文档 / 旧链接仍可能引用 `/mid`，删掉会 404（Electron 静态导出下更明显）。
 * 静态导出不支持 next.config 的 redirects() 与服务器 redirect()，故客户端跳转；
 * 复用既有 `softNavigate`（软跳失败 1.5s 后硬跳兜底）。`?tab=prompts|reports` 会映射成
 * `&sub=prompts|reports`，直达卡内对应子页签。
 */
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Boxes, Loader2 } from "lucide-react";
import { softNavigate } from "@/lib/app-nav";

/** 把旧页的 `?tab=` 映射为卡内子页签；无匹配则不带 sub（落参数配置） */
function legacySub(tab: string): string {
  const t = tab.toLowerCase();
  if (t === "prompts" || t === "prompt" || t === "提示词") return "&sub=prompts";
  if (t === "reports" || t === "report" || t === "报告") return "&sub=reports";
  return "";
}

export default function MidRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    const tab = new URLSearchParams(window.location.search).get("tab") || "";
    softNavigate(`/control?tab=lanes&cfg=mid${legacySub(tab)}`, (url) => router.replace(url));
  }, [router]);

  return (
    <div className="p-6 flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="w-4 h-4 animate-spin" />
      <Boxes className="w-4 h-4" />
      <span>中线配置已并入「策略配置 → 交易总控 · 车道配置」页内卡片，正在跳转…</span>
      <a href="/control?tab=lanes&cfg=mid" className="text-cyan-300 hover:underline">未自动跳转请点这里</a>
    </div>
  );
}

