/**
 * 旧路由跳转桩：`/reports`（周期报告）→ `/intelligent-learning?tab=reports`（智能学习 · 周期报告）
 *
 * [2026-10-03 用户指令] 「周期报告 并入 智能学习」。原页面只有 PageHeader + `LongReportsPanel`，
 * 现整块成为「智能学习中心」的第八个 Tab（周期报告），侧栏不再单列入口。
 *
 * 保留路由的理由同其它合并桩：书签/文档/旧 e2e 仍可能引用，静态导出（`output: "export"`）下
 * 删掉就是 404，且静态导出不支持 `redirects()` / 服务端 `redirect()`，只能用客户端跳转。
 */
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Activity } from "lucide-react";
import { softNavigate } from "@/lib/app-nav";

const TARGET = "/intelligent-learning?tab=reports";

export default function ReportsRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    softNavigate(TARGET, (url) => router.replace(url));
  }, [router]);

  return (
    <div className="p-6 flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="w-4 h-4 animate-spin" />
      <Activity className="w-4 h-4" />
      <span>周期报告已并入「智能学习中心 · 周期报告」，正在跳转…</span>
      <a href={TARGET} className="text-cyan-300 hover:underline">未自动跳转请点这里</a>
    </div>
  );
}
