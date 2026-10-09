/**
 * 旧路由跳转桩：`/factors-lab` → `/factors?tab=lab`
 *
 * [2026-10-01 合并] 用户指令：「合并 因子系统 因子研究中心」。
 * 「因子研究中心」已并入「因子系统」（`/factors`）作为 Tab「研究中心」，
 * 面板实现在 `src/components/factors/FactorLabPanel.tsx`，宿主页 `src/app/factors/page.tsx`。
 *
 * 为什么保留这条路由而不是删掉：
 *   外部书签 / 文档 / 旧 e2e 仍可能引用 `/factors-lab`，删掉会 404（Electron 静态导出下更明显）。
 *   静态导出（`output: "export"`）不支持 `next.config` 的 `redirects()`，也不支持服务端
 *   `redirect()`，所以用客户端跳转；复用仓库既有 `softNavigate`：软跳失败会在 1.5s 后硬跳兜底。
 *
 * 后端 `/api/factors-lab/*` 接口**不变**（见 `src/lib/api.ts` 的 `factorsLabApi`）。
 */
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { FlaskConical, Loader2 } from "lucide-react";
import { softNavigate } from "@/lib/app-nav";

const TARGET = "/factors?tab=lab";

export default function FactorsLabRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    softNavigate(TARGET, (url) => router.replace(url));
  }, [router]);

  return (
    <div className="p-6 flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="w-4 h-4 animate-spin" />
      <FlaskConical className="w-4 h-4" />
      <span>因子研究中心已并入「因子系统 · 研究中心」，正在跳转…</span>
      <a href={TARGET} className="text-cyan-300 hover:underline">未自动跳转请点这里</a>
    </div>
  );
}
