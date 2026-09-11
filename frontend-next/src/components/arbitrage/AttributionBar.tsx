"use client";

/**
 * AttributionBar — 六维归因堆叠条（spread/funding/price/fee/slippage/points）
 *
 * 设计 §3.1：绿色=正贡献，红色=负贡献；带图例与数值。
 * 采用「双向往回」堆叠：正值从中心向右（绿），负值从中心向左（红），
 * 段宽按各自 |net_usd| 占最大侧的百分比缩放，直观显示净方向。
 */
import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { fmtUsd } from "@/lib/format";
import type { AttributionDim } from "@/lib/trading-api";

const DIMS: { key: keyof AttributionDim; label: string }[] = [
  { key: "spread_bp", label: "spread" },
  { key: "funding_bp", label: "funding" },
  { key: "price_bp", label: "price" },
  { key: "fee_bp", label: "fee" },
  { key: "slippage_bp", label: "slip" },
  { key: "points_usd", label: "points" },
];

export function AttributionBar({
  total,
  className,
}: {
  total?: AttributionDim | null;
  className?: string;
}) {
  const { pos, neg, maxAbs } = useMemo(() => {
    if (!total) return { pos: [] as { label: string; value: number }[], neg: [] as { label: string; value: number }[], maxAbs: 0 };
    const pos: { label: string; value: number }[] = [];
    const neg: { label: string; value: number }[] = [];
    for (const { key, label } of DIMS) {
      // points_usd 是美元维度；spread/funding 等是 bp 维度，需折算成美元贡献
      const value = key === "points_usd"
        ? (total.points_usd ?? 0)
        : bpToUsd(total, key);
      if (value >= 0) pos.push({ label, value });
      else neg.push({ label, value: Math.abs(value) });
    }
    const maxSide = Math.max(
      pos.reduce((s, x) => s + x.value, 0),
      neg.reduce((s, x) => s + x.value, 0)
    );
    return { pos, neg, maxAbs: maxSide || 0 };
  }, [total]);

  if (!total) return null;

  const totalNet = pos.reduce((s, x) => s + x.value, 0) - neg.reduce((s, x) => s + x.value, 0);
  const hasAny = maxAbs > 0;
  const pct = (v: number) => (hasAny ? (Math.abs(v) / maxAbs) * 50 : 0);

  return (
    <div className={cn("space-y-2", className)}>
      {/* 堆叠条 */}
      <div className="flex h-6 items-stretch overflow-hidden rounded-md border border-border/40 bg-muted/20">
        {/* 负值区（左，红） */}
        <div className="flex flex-1 items-stretch justify-end">
          {neg.map((x) => (
            <div
              key={`n-${x.label}`}
              className={cn("h-full", x.value === 0 && "hidden")}
              style={{ width: `${pct(x.value)}%`, backgroundColor: "var(--loss)", opacity: 0.85 }}
              title={`${x.label} ${fmtUsd(-x.value)}`}
            />
          ))}
        </div>
        {/* 中心刻度 */}
        <div className="w-px flex-shrink-0 bg-border" />
        {/* 正值区（右，绿） */}
        <div className="flex flex-1 items-stretch">
          {pos.map((x) => (
            <div
              key={`p-${x.label}`}
              className={cn("h-full", x.value === 0 && "hidden")}
              style={{ width: `${pct(x.value)}%`, backgroundColor: "var(--profit)", opacity: 0.85 }}
              title={`${x.label} ${fmtUsd(x.value)}`}
            />
          ))}
        </div>
      </div>

      {/* 净值方向提示 */}
      <div className={cn("text-xs font-mono tabular-nums", totalNet >= 0 ? "text-profit" : "text-loss")}>
        近 {total.n} 笔 · 净 {fmtUsd(totalNet)}（{totalNet >= 0 ? "+" : ""}
        {(total.net_bp ?? 0).toFixed(2)}bp）
      </div>

      {/* 图例 */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
        {DIMS.map(({ key, label }) => {
          const value = key === "points_usd" ? (total.points_usd ?? 0) : bpToUsd(total, key);
          const positive = value >= 0;
          return (
            <div key={key} className="flex items-center gap-1.5 text-[11px]">
              <span className={cn("h-2 w-2 flex-shrink-0 rounded-sm", positive ? "bg-profit" : "bg-loss")} />
              <span className="text-muted-foreground">{label}</span>
              <span className={cn("ml-auto font-mono tabular-nums", positive ? "text-profit" : "text-loss")}>
                {fmtUsd(value)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** 把 bp 维度按名义金额折算成美元贡献（bp / 10^4 × 名义） */
function bpToUsd(total: AttributionDim, key: keyof AttributionDim): number {
  const bp = Number(total[key] ?? 0);
  const notional = Number(total.notional ?? 0);
  return (bp / 10_000) * notional;
}
