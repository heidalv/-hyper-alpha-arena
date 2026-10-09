/**
 * 旧路由跳转桩：`/exchange`（交易所管理）→ `/control?tab=exchange`（交易总控 · 交易所管理）
 *
 * [2026-10-03 用户指令] 「交易所管理页并入 交易总控」。原页面内容原样抽成
 * `components/exchange/ExchangeManagerPanel.tsx`（内部 4 个子页 账户管理 / API 凭证 /
 * 交易所监控 / 积分账本 全保留），宿主页是 `src/app/control/page.tsx` 的第二个页签。
 *
 * 保留路由的理由同其它合并桩：书签/文档/旧 e2e 仍可能引用，静态导出下删掉就是 404。
 */
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Server } from "lucide-react";
import { softNavigate } from "@/lib/app-nav";

const TARGET = "/control?tab=exchange";

export default function ExchangeRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    softNavigate(TARGET, (url) => router.replace(url));
  }, [router]);

  return (
    <div className="p-6 flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="w-4 h-4 animate-spin" />
      <Server className="w-4 h-4" />
      <span>交易所管理已并入「交易总控」，正在跳转…</span>
      <a href={TARGET} className="text-cyan-300 hover:underline">未自动跳转请点这里</a>
    </div>
  );
}
