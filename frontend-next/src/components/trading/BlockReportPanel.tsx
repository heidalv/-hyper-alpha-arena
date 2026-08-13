"use client";

/**
 * BlockReportPanel — 「为何不开单」门禁拦截 Top3（R8）
 * 消费 GET /api/system/block-report-top（进程内阻断事件聚合，24h 窗口）。
 */
import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/ui/card";
import { ShieldX } from "lucide-react";
import { apiRequest } from "@/lib/api";
import { cn } from "@/lib/utils";

interface BlockTopItem {
  reason?: string;
  code?: string;
  count?: number;
  last_at?: string;
  samples?: string[];
}

interface BlockTopResponse {
  window_sec?: number;
  total?: number;
  top?: BlockTopItem[];
}

export function BlockReportPanel() {
  const { data } = useQuery({
    queryKey: ["block-report-top"],
    queryFn: () => apiRequest<BlockTopResponse>("/system/block-report-top?n=3&hours=24"),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

  const items = data?.top ?? [];
  const total = data?.total ?? 0;

  return (
    <Card className="p-2.5 border-border flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-[13px] font-medium flex items-center gap-1.5">
          <ShieldX className="w-3.5 h-3.5 text-warning" />
          门禁拦截 Top3
        </h2>
        <span className="text-[9px] text-muted-foreground font-mono">24h 共 {total} 次</span>
      </div>
      {items.length === 0 ? (
        <div className="text-center text-muted-foreground text-xs py-4">近 24h 无阻断记录</div>
      ) : (
        <div className="flex flex-col gap-1">
          {items.map((it, i) => {
            const count = Number(it.count ?? 0);
            const pct = total > 0 ? Math.round((count / total) * 100) : 0;
            return (
              <div key={it.code ?? i} className="py-1.5 border-b border-border/20 last:border-0">
                <div className="flex items-center justify-between gap-2 mb-0.5">
                  <span className="text-xs font-medium truncate" title={it.reason ?? it.code}>
                    {it.reason ?? it.code ?? "—"}
                  </span>
                  <span className={cn("text-[10px] font-mono shrink-0", count > 0 ? "text-warning" : "text-muted-foreground")}>
                    {count} 次 · {pct}%
                  </span>
                </div>
                <div className="flex h-1 rounded-sm overflow-hidden bg-muted/20">
                  <div className="bg-warning/70" style={{ width: `${Math.max(pct, 2)}%` }} />
                </div>
                {it.last_at && (
                  <div className="text-[9px] text-muted-foreground font-mono mt-0.5">最近 {it.last_at}</div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
