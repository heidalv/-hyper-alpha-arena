/**
 * 旧路由跳转桩：`/agent-wall` → `/agent-monitor?tab=wall`
 *
 * [2026-10-01 合并] 用户指令：「把 agent监控 和 agent wall 进行合并，新的还叫 agent 监控，
 * 但是放在 市场&分析 组块里去」。画布实现已迁到
 * `src/components/monitor/AgentWallCanvas.tsx`，宿主页是 `src/app/agent-monitor/page.tsx`
 * （第一个 Tab「分析墙（画布）」）。
 *
 * 为什么保留这条路由而不是删掉：
 *   外部书签 / 文档 / 旧 e2e 里仍可能引用 `/agent-wall`，删掉会 404（Electron 静态导出下更明显）。
 *   静态导出（`output: "export"`）不支持 `next.config` 的 `redirects()`，也不支持服务端
 *   `redirect()`，所以只能用客户端跳转。这里复用仓库既有的 `softNavigate`：软跳失败会
 *   在 1.5s 后硬跳兜底，不会卡在中间页。
 */
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Network } from "lucide-react";
import { softNavigate } from "@/lib/app-nav";

const TARGET = "/agent-monitor?tab=wall";

export default function AgentWallRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    softNavigate(TARGET, (url) => router.replace(url));
  }, [router]);

  return (
    <div className="p-6 flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="w-4 h-4 animate-spin" />
      <Network className="w-4 h-4" />
      <span>Agent Wall 已并入「Agent 监控 · 分析墙（画布）」，正在跳转…</span>
      <a href={TARGET} className="text-cyan-300 hover:underline">未自动跳转请点这里</a>
    </div>
  );
}
