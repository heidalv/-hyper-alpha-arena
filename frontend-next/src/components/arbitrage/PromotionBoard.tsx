"use client";

/**
 * PromotionBoard — 晋升判定矩阵
 *
 * 设计 §3.2/§4：每项 ✓/✗ + 中文标签 + 阈值，底部结论句。
 * 后端 labels 里部分 key 无中文（如 net_positive 回退为原始 key），
 * 前端用 `LABEL_FALLBACK` 兜底为中文，但优先采用后端 labels。
 */
import { useMemo } from "react";
import { CheckCircle2, XCircle, Sparkles, Clock } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtAgeMs, ageFromAsOf, type PromotionState, type PromotionCriteria } from "@/lib/trading-api";

/** 后端未提供中文 label 的 key 的前端兜底 */
const LABEL_FALLBACK: Record<string, string> = {
  edge_verified: "边际来源可信",
  min_trades: "成交 ≥200 笔",
  folds_positive: "滚动 4 折净期望全 > 0",
  fold_t: "每折 t > 2",
  max_drawdown_pct: "最大回撤 < 权益 1.5%",
  fill_rate_ratio: "真实成交率 ≥ 离线模拟 30%",
  net_positive: "净期望 > 0",
};

export function PromotionBoard({
  promotion,
  criteria,
  className,
}: {
  promotion?: PromotionState | null;
  criteria?: PromotionCriteria | null;
  className?: string;
}) {
  const rows = useMemo(() => {
    if (!promotion) return [];
    const keys = new Set<string>([...promotion.passed, ...promotion.failed]);
    return Array.from(keys).map((key) => {
      const passed = promotion.passed.includes(key);
      const label = promotion.labels?.[key] || LABEL_FALLBACK[key] || key;
      const entry = criteria?.[key];
      return {
        key,
        passed,
        label,
        threshold: entry ? entry.threshold : null,
      };
    });
  }, [promotion, criteria]);

  if (!promotion) {
    return (
      <div className={cn("flex items-center gap-2 rounded-lg border border-muted/40 bg-muted/20 px-3 py-3 text-xs text-muted-foreground", className)}>
        <Sparkles className="h-3.5 w-3.5" />
        尚无晋升判定（车道未产生可信边际数据）
      </div>
    );
  }

  const failedLabels = promotion.failed.map((k) => promotion.labels?.[k] || LABEL_FALLBACK[k] || k);

  return (
    <div className={cn("space-y-3", className)}>
      {/* 判定矩阵 */}
      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        {rows.map((r) => (
          <div
            key={r.key}
            className={cn(
              "flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs",
              r.passed ? "border-profit/25 bg-profit/8" : "border-loss/25 bg-loss/8"
            )}
          >
            {r.passed ? (
              <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0 text-profit" />
            ) : (
              <XCircle className="h-3.5 w-3.5 flex-shrink-0 text-loss" />
            )}
            <span className={cn("min-w-0 flex-1", r.passed ? "" : "text-muted-foreground")}>
              {r.label}
            </span>
            {r.threshold != null && (
              <span className="flex-shrink-0 font-mono text-[11px] text-muted-foreground">≈{r.threshold}</span>
            )}
          </div>
        ))}
      </div>

      {/* 结论句 */}
      <div
        className={cn(
          "flex items-center gap-2 rounded-lg border px-3 py-2 text-xs",
          promotion.ready ? "border-profit/30 bg-profit/10 text-profit" : "border-warning/25 bg-warning/8 text-warning"
        )}
      >
        {promotion.ready ? (
          <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0" />
        ) : (
          <XCircle className="h-3.5 w-3.5 flex-shrink-0" />
        )}
        <span className="min-w-0 flex-1">
          {promotion.ready
            ? "判定通过，可考虑晋级"
            : failedLabels.length === 0
              ? "暂无未通过项"
              : `当前未通过 ${failedLabels.length} 项：${failedLabels.join("、")}，继续观察`}
        </span>
        <span className="flex-shrink-0 font-mono tabular-nums">{promotion.progress_pct.toFixed(0)}%</span>
      </div>

      {/* 判定数据年龄（as_of） */}
      {promotion.as_of && (
        <div className="flex items-center justify-end gap-1 text-[11px] text-muted-foreground">
          <Clock className="h-3 w-3" />
          <span>判定时间 {new Date(promotion.as_of).toLocaleTimeString("zh-CN", { hour12: false })}</span>
          <span className="text-muted-foreground/70">（{fmtAgeMs(ageFromAsOf(promotion.as_of))}）</span>
        </div>
      )}
    </div>
  );
}
