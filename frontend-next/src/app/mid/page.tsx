"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

/**
 * 中线配置已并入长线（中长线合并），入口已从侧栏隐藏。
 * 旧书签 /mid 自动跳转到长线配置。
 */
export default function MidPageRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/long");
  }, [router]);

  return (
    <div className="flex flex-col items-center justify-center h-40 gap-2 text-muted-foreground text-sm">
      <Loader2 className="w-5 h-5 animate-spin" />
      <span>中线已并入长线配置，正在跳转…</span>
    </div>
  );
}
