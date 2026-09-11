"use client";

/**
 * EdgeBadge — 净期望 + t + n（含置信提示）
 *
 * 设计 §4：edge 为 null 时显示「未验证」而不是 0。
 * 置信提示取自 edge.note / edge.source（如「实测净期望 +1.72bp」或「逆选择未验证」）。
 */
import { useMemo } from "react";
import { HelpCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtNum } from "@/lib/format";
import type { LaneEdge } from "@/lib/trading-api";

function fmtBp(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}bp`;
}

export function EdgeBadge({
  edge,
  className,
}: {
  edge?: LaneEdge | null;
  className?: string;
}) {
  const { hasEdge, hasNet, netBp, t, n, note } = useMemo(() => {
    const e = edge ?? null;
    return {
      hasEdge: !!e,
      hasNet: !!e && typeof e.net_bp === "number" && Number.isFinite(e.net_bp),
      netBp: typeof e?.net_bp === "number" ? e.net_bp : null,
      t: typeof e?.t === "number" ? e.t : null,
      n: typeof e?.n === "number" ? e.n : null,
      note: e?.note ?? e?.source ?? null,
    };
  }, [edge]);

  if (!hasEdge || !hasNet) {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 rounded-full border border-muted bg-muted/30 px-2 py-0.5 text-[11px] text-muted-foreground",
          className
        )}
        title={note ?? "缺少可靠的扣费后净期望数据"}
      >
        <HelpCircle className="h-3 w-3" />
        未验证
      </span>
    );
  }

  const positive = (netBp as number) >= 0;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-mono tabular-nums",
        positive
          ? "border-profit/30 bg-profit/10 text-profit"
          : "border-loss/30 bg-loss/10 text-loss",
        className
      )}
      title={note ? `注：${note}` : undefined}
    >
      <span className="font-semibold">{fmtBp(netBp as number)}</span>
      {(t != null || n != null) && (
        <span className="text-muted-foreground">
          {t != null ? `t=${t.toFixed(1)}` : ""}
          {t != null && n != null ? "，" : ""}
          {n != null ? `n=${fmtNum(n, 0)}` : ""}
        </span>
      )}
    </span>
  );
}
