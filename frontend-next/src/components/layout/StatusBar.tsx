"use client";

import { useEffect, useState } from "react";
import { useMarketStore } from "@/lib/stores/market";

/**
 * 底部状态栏（Aurora 设计稿签名元素）
 * 左：WS 状态 · 系统运行中；右：实时时钟
 */
export function StatusBar() {
  const wsConnected = useMarketStore((s) => s.wsConnected);
  const [time, setTime] = useState(() =>
    new Date().toLocaleTimeString("zh-CN", { hour12: false })
  );

  useEffect(() => {
    const id = setInterval(
      () => setTime(new Date().toLocaleTimeString("zh-CN", { hour12: false })),
      1000
    );
    return () => clearInterval(id);
  }, []);

  return (
    <footer className="h-8 flex-shrink-0 flex items-center gap-5 px-5 bg-[#090d18]/85 backdrop-blur-sm border-t border-border text-[11px] text-slate-400">
      <span className="flex items-center gap-1.5">
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            wsConnected
              ? "bg-profit shadow-[0_0_6px_rgba(52,211,153,0.8)]"
              : "bg-warning shadow-[0_0_6px_rgba(251,191,36,0.8)]"
          }`}
        />
        {wsConnected ? "行情已连接" : "行情轮询中"}
      </span>
      <span className="flex items-center gap-1.5">
        <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 shadow-[0_0_6px_rgba(34,211,238,0.8)] animate-pulse" />
        系统运行中
      </span>
      <span className="ml-auto font-mono tabular-nums">{time}</span>
    </footer>
  );
}
