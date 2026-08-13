"use client";

/**
 * DecisionTimeline — AI 决策时间线（R8）
 * 把三周期活动流从「80 字文本列表」升级为分组时间线：
 * 时间 + tier 徽章 + 标的 + 动作 + 推理摘要；点击展开完整 reasoning。
 */
import { useState } from "react";
import { Card } from "@/components/ui/card";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import type { TierActivity, TierActivityItem } from "@/types/api";

const TIER_META: Record<"short" | "mid" | "long", { label: string; color: string; bg: string }> = {
  short: { label: "短线", color: "#6366f1", bg: "rgba(99,102,241,0.15)" },
  mid: { label: "中线", color: "#22c55e", bg: "rgba(34,197,94,0.12)" },
  long: { label: "长线", color: "#eab308", bg: "rgba(234,179,8,0.12)" },
};

interface Row {
  key: string;
  tier: "short" | "mid" | "long";
  item: TierActivityItem;
}

function flatten(activity: TierActivity | undefined): Row[] {
  if (!activity) return [];
  const rows: Row[] = [];
  for (const tier of ["short", "mid", "long"] as const) {
    for (const item of activity[tier] ?? []) {
      rows.push({ key: `${tier}-${item.time}-${item.symbol}-${item.action}`, tier, item });
    }
  }
  return rows;
}

function actionTone(action: string): string {
  if (action.includes("开多")) return "bg-profit/15 text-profit";
  if (action.includes("开空")) return "bg-loss/15 text-loss";
  return "bg-muted/30 text-muted-foreground";
}

export function DecisionTimeline({
  activity,
  loading,
}: {
  activity: TierActivity | undefined;
  loading?: boolean;
}) {
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const rows = flatten(activity);

  return (
    <Card className="p-2.5 border-border flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-[13px] font-medium">AI 决策时间线</h2>
        <span className="text-[9px] text-muted-foreground font-mono">{rows.length} 条</span>
      </div>
      <div className="flex flex-col gap-0.5 overflow-y-auto max-h-[280px]">
        {loading && <div className="text-center text-muted-foreground text-xs py-4">加载中...</div>}
        {!loading && rows.length === 0 && (
          <div className="text-center text-muted-foreground text-xs py-4">暂无决策记录</div>
        )}
        {rows.map((row) => {
          const meta = TIER_META[row.tier];
          const expanded = expandedKey === row.key;
          const reasoning = row.item.reasoning || "";
          return (
            <button
              key={row.key}
              type="button"
              onClick={() => setExpandedKey(expanded ? null : row.key)}
              className="w-full text-left py-1.5 px-1 rounded hover:bg-muted/30 transition-colors"
              aria-expanded={expanded}
            >
              <div className="flex items-center justify-between mb-0.5 gap-1">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className="text-[9px] font-mono text-muted-foreground shrink-0">{row.item.time}</span>
                  <span
                    className="text-[9px] px-1 py-0.5 rounded font-medium shrink-0"
                    style={{ color: meta.color, background: meta.bg }}
                  >
                    {meta.label}
                  </span>
                  <span className="text-[10px] font-semibold font-mono shrink-0">{row.item.symbol}</span>
                </div>
                <span className={cn("text-[9px] px-1 py-0.5 rounded font-medium shrink-0", actionTone(row.item.action))}>
                  {row.item.action}
                </span>
              </div>
              {reasoning && (
                <div className="flex items-start gap-1 pl-1">
                  <p
                    className={cn(
                      "text-xs leading-snug text-foreground/85 flex-1",
                      !expanded && "line-clamp-2"
                    )}
                  >
                    {reasoning}
                  </p>
                  {reasoning.length > 90 && (
                    <ChevronDown
                      className={cn(
                        "w-3 h-3 text-muted-foreground shrink-0 mt-0.5 transition-transform",
                        expanded && "rotate-180"
                      )}
                    />
                  )}
                </div>
              )}
            </button>
          );
        })}
      </div>
    </Card>
  );
}
