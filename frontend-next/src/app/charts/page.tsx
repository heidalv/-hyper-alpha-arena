"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

/** K 线图表已并入全市场数据中台 → /intel?tab=kline */
export default function ChartsPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/intel?tab=kline");
  }, [router]);

  return (
    <div className="flex items-center justify-center h-40 gap-2 text-sm text-muted-foreground">
      <Loader2 className="w-4 h-4 animate-spin" />
      K 线已并入全市场数据中台，正在跳转…
    </div>
  );
}
